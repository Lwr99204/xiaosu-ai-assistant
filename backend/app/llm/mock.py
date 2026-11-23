"""确定性 Mock LLM 提供者，测试基础设施（TDD §10.2）。

Agent 编排循环、引用后校验、降级路径是本项目最容易出错的部分，而这些路径与
模型本身答得好不好无关，关键是「模型请求某个工具时，我们是否执行并回灌了结果」。
用真实 LLM 驱动编排层会让断言随模型输出漂移，还会在每次 pytest 时消耗 API Key
配额。MockLLMProvider 按脚本队列返回预设响应，配合 pytest-socket 禁网，从机制上
保证「测试不依赖真实 API」。

脚本项的三种形态：
- LLMResult → 正常返回
- 异常实例 → 原样 raise，用于模拟超时 / 429 / 无效 Key
- 队列耗尽 → 抛 ScriptExhausted，让编排层多绕一圈时测试**立即失败**，
  而不是静默返回一个空答案糊弄过去
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from app.core.errors import LLMAuthError, LLMTimeoutError
from app.llm.base import (
    ChatMessage,
    LLMResult,
    StreamChunk,
    StreamToolCallDelta,
    TokenUsage,
    ToolCall,
)
from app.llm.tokenizer import estimate_tokens

MOCK_MODEL = "mock-llm"
MOCK_EMBED_MODEL = "mock-embed"

_HASH_BYTES = 4
_UINT32_MAX = 0xFFFFFFFF


class ScriptExhausted(RuntimeError):
    """脚本队列已耗尽仍被调用，几乎必然是编排层的死循环 bug。"""


def text_reply(
    content: str,
    *,
    finish_reason: str = "stop",
    model: str = MOCK_MODEL,
) -> LLMResult:
    """构造一条纯文本回答。是否带 [n] 引用由调用方在 content 里自己写。"""
    return LLMResult(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish_reason,
        usage=_estimated_usage(model, content),
    )


def tool_reply(
    *calls: tuple[str, Any],
    finish_reason: str = "tool_calls",
    model: str = MOCK_MODEL,
) -> LLMResult:
    """构造一条请求工具调用的回答。calls 为 (工具名, 参数字典) 序列。

    id 刻意用 call_1 / call_2 这种可预测值：断言里要按 id 把 tool 消息与
    assistant 的 tool_calls 配对，随机 id 会让测试无法写死预期。
    """
    tool_calls = tuple(
        ToolCall(
            id=f"call_{index + 1}",
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        )
        for index, (name, arguments) in enumerate(calls)
    )
    return LLMResult(
        message=ChatMessage(role="assistant", content=None, tool_calls=tool_calls),
        finish_reason=finish_reason,
        usage=_estimated_usage(model, ""),
    )


def auth_error(message: str = "invalid api key") -> LLMAuthError:
    """无效 Key。不可重试，编排层应直接走友好降级。"""
    return LLMAuthError(message)


def timeout_error(message: str = "upstream timeout") -> LLMTimeoutError:
    """上游超时。可重试，重试用尽后同样落到降级话术。"""
    return LLMTimeoutError(message)


def _estimated_usage(model: str, completion_text: str) -> TokenUsage:
    completion = estimate_tokens(completion_text)
    return TokenUsage(
        prompt_tokens=0,
        completion_tokens=completion,
        total_tokens=completion,
        model=model,
        estimated=True,
    )


def _split_text(content: str, parts: int) -> Iterator[str]:
    if not content:
        return iter(())
    size = max(1, math.ceil(len(content) / parts))
    return iter(content[start : start + size] for start in range(0, len(content), size))


class MockLLMProvider:
    """按脚本返回响应。chat 与 stream 都实现，让编排层在测试里也走流式路径。"""

    model = MOCK_MODEL

    def __init__(
        self,
        script: Sequence[LLMResult | BaseException] = (),
        *,
        stream_splits: int = 4,
    ) -> None:
        self._script = list(script)
        self._cursor = 0
        self._stream_splits = max(1, stream_splits)
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        """脚本里还没被消费的条数。"""
        return len(self._script) - self._cursor

    def tool_names(self) -> list[str]:
        """脚本里实际请求过的工具名（按调用顺序展平），断言「该调工具时调了」用它。"""
        names: list[str] = []
        for call in self.calls:
            reply = call["reply"]
            if isinstance(reply, LLMResult):
                names.extend(tool_call.name for tool_call in reply.message.tool_calls)
        return names

    def _take(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Any] | None,
        temperature: float | None,
    ) -> LLMResult:
        if self._cursor >= len(self._script):
            raise ScriptExhausted(
                f"MockLLM 脚本只有 {len(self._script)} 条，却被调用了第 {self._cursor + 1} 次"
            )
        item = self._script[self._cursor]
        self._cursor += 1
        self.calls.append(
            {
                "messages": tuple(messages),
                "tools": tuple(tools or ()),
                "temperature": temperature,
                "reply": item,
            }
        )
        if isinstance(item, BaseException):
            raise item
        return item

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        return self._take(messages, tools, temperature)

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        return self._chunks(self._take(messages, tools, temperature))

    async def _chunks(self, result: LLMResult) -> AsyncIterator[StreamChunk]:
        message = result.message
        if message.tool_calls:
            # 一次性发完整 id/name/arguments：编排层要测的是「按 index 把增量累加回
            # 完整 ToolCall」这段逻辑，单帧足以暴露 index 错配，拆多帧只是拖慢测试。
            for index, call in enumerate(message.tool_calls):
                yield StreamChunk(
                    tool_call_deltas=(
                        StreamToolCallDelta(
                            index=index,
                            id=call.id,
                            name=call.name,
                            arguments_delta=call.arguments,
                        ),
                    )
                )
        else:
            for piece in _split_text(message.content or "", self._stream_splits):
                yield StreamChunk(delta=piece)
        yield StreamChunk(finish_reason=result.finish_reason, usage=result.usage)


def fake_vector(text: str, dim: int) -> list[float]:
    """SHA-256(text) → L2 归一化伪向量。

    不用随机数：随机向量让检索测试写不出「该命中 / 不该命中」的稳定断言。
    哈希保证同一文本永远得到同一向量，不同文本近似正交（期望内积趋近 0），
    足以模拟带阈值的相似度检索行为。
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    round_index = 0
    while len(values) < dim:
        block = hashlib.sha256(digest + round_index.to_bytes(4, "big")).digest()
        values.extend(
            int.from_bytes(block[offset : offset + _HASH_BYTES], "big") / _UINT32_MAX - 0.5
            for offset in range(0, len(block), _HASH_BYTES)
        )
        round_index += 1
    trimmed = values[:dim]
    norm = math.sqrt(sum(value * value for value in trimmed)) or 1.0
    return [value / norm for value in trimmed]


class MockEmbedder:
    """确定性伪向量化器，实现 EmbeddingProvider 协议。"""

    model = MOCK_EMBED_MODEL

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.embedded: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [fake_vector(item, self.dim) for item in texts]
