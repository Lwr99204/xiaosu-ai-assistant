"""OpenAI 兼容协议实现（TDD §4.3 / §1.3 实测约束）。

一套代码覆盖通义 / 智谱 / DeepSeek / OpenAI / Ollama / SiliconFlow：它们都暴露
/chat/completions 与 /embeddings，差异只在 base_url 与 model 名。所谓「多模型
适配」的成本就是把这点差异吃在这一层（CURRENT_PLAN 修正 4：只换 base_url 不叫
多供应商，能吃掉协议差异与错误语义差异才算）。

三条实测约束落在代码里，不是注释里的愿望
--------------------------------------
1. 流式请求必须带 stream_options={"include_usage": true}，否则 usage 帧永远
   为 0（TDD §1.3）。
2. delta.reasoning_content 与 delta.content 必须分流：qwen3.8-max 的推理内容
   **先于**正文到达，无脑拼接会把思维链直接吐给用户。
3. SDK 自带重试关掉（max_retries=0），重试统一走 core.retry.retry_async。
   两层重试相乘的话，最坏一次提问会打 3x3=9 次上游，既烧配额又拖长等待。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from app.core.errors import (
    EmbeddingError,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.core.retry import compute_backoff, retry_async
from app.llm.base import (
    ChatMessage,
    LLMResult,
    StreamChunk,
    StreamToolCallDelta,
    TokenUsage,
    ToolCall,
    ToolSchema,
)
from app.llm.tokenizer import usage_or_estimate

logger = logging.getLogger(__name__)

#: 上游错误文本可能很长且含请求 URL，截断后再进日志与异常 context
_MAX_ERROR_DETAIL = 300


def _clip(text: object) -> str:
    """把上游错误信息截断成可入日志的长度。"""
    raw = str(text or "")
    return raw[:_MAX_ERROR_DETAIL]


def _to_tool_deltas(raw_deltas: Any) -> tuple[StreamToolCallDelta, ...]:
    """把一帧里的 delta.tool_calls 转成 StreamToolCallDelta 元组。

    上游把一次工具调用拆成多帧下发，arguments 是一段段 JSON 文本；这里只做
    透传，拼接与解析交给编排层（agent.orchestrator），保持 llm 层不依赖 agent。
    """
    result: list[StreamToolCallDelta] = []
    for item in raw_deltas or []:
        fn = getattr(item, "function", None)
        result.append(
            StreamToolCallDelta(
                index=getattr(item, "index", 0) or 0,
                id=getattr(item, "id", None),
                name=getattr(fn, "name", None),
                arguments_delta=getattr(fn, "arguments", None) or "",
            )
        )
    return tuple(result)


def map_llm_error(exc: Exception) -> LLMError:
    """把 openai SDK 异常翻译成项目异常体系（TDD §4.9 降级链路）。

    翻译规则直接决定重试行为，因此按「重试有没有意义」分类：
    - 401/403 鉴权：重试 100 次还是 401 -> LLMAuthError(retryable=False)
    - 404 模型名错：改配置才能解决 -> LLMError(retryable=False)
    - 400 参数错：多半是 prompt 或工具 schema 问题 -> LLMError(retryable=False)
    - 429 限流：退避后大概率成功 -> LLMRateLimitError(retryable=True)
    - 超时 / 连接失败 / 5xx：瞬时故障 -> retryable=True
    """
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        status = getattr(exc, "status_code", 401)
        return LLMAuthError(f"模型鉴权失败 HTTP {status}: {_clip(exc)}", cause=exc)
    if isinstance(exc, openai.NotFoundError):
        return LLMError(
            f"模型或端点不存在: {_clip(exc)}",
            retryable=False,
            user_facing_msg="AI 服务配置有误（模型名或地址），请联系管理员",
            cause=exc,
        )
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(f"模型限流: {_clip(exc)}", cause=exc)
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeoutError(f"模型请求超时: {_clip(exc)}", cause=exc)
    if isinstance(exc, openai.APIConnectionError):
        return LLMUnavailableError(f"模型服务连接失败: {_clip(exc)}", cause=exc)
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 500)
        if status >= 500:
            return LLMUnavailableError(f"模型服务 HTTP {status}: {_clip(exc)}", cause=exc)
        return LLMError(f"模型请求被拒 HTTP {status}: {_clip(exc)}", retryable=False, cause=exc)
    if isinstance(exc, LLMError):
        return exc
    return LLMUnavailableError(f"模型调用未知错误: {_clip(exc)}", cause=exc)


def to_openai_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """ChatMessage -> OpenAI 协议 dict。

    content 一律显式写出（assistant 带 tool_calls 时协议要求 content 为 null），
    省掉这个键在部分兼容网关上会报 400。
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.name:
            item["name"] = message.name
        payload.append(item)
    return payload


def _request_kwargs(
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolSchema] | None,
    temperature: float,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": to_openai_messages(messages),
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = list(tools)
        kwargs["tool_choice"] = "auto"
    return kwargs


class OpenAICompatProvider:
    """生产用对话 Provider。实现 base.ChatProvider。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        temperature: float = 0.1,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
        )

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """非流式调用，带指数退避重试（0.5s / 1s + 抖动，最多 2 次）。"""
        kwargs = _request_kwargs(
            self.model, messages, tools, self._temperature if temperature is None else temperature
        )
        try:
            response = await retry_async(
                lambda: self._client.chat.completions.create(**kwargs),
                operation=f"llm.chat[{self.model}]",
                max_retries=self._max_retries,
            )
        except Exception as exc:
            raise map_llm_error(exc) from exc
        return self._to_result(response, messages)

    def _to_result(self, response: Any, messages: Sequence[ChatMessage]) -> LLMResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            # 上游偶发返回空 choices（限流或内容审核拦截），当作可重试故障
            raise LLMUnavailableError("模型返回空 choices", retryable=True)
        choice = choices[0]
        raw_message = choice.message
        tool_calls = tuple(
            ToolCall(
                id=getattr(call, "id", "") or "",
                name=(getattr(call, "function", None) and call.function.name) or "",
                arguments=(getattr(call, "function", None) and call.function.arguments) or "{}",
            )
            for call in (getattr(raw_message, "tool_calls", None) or [])
        )
        content = getattr(raw_message, "content", None)
        message = ChatMessage(role="assistant", content=content, tool_calls=tool_calls)
        usage = usage_or_estimate(
            getattr(response, "usage", None),
            model=self.model,
            messages=messages,
            completion_text=content,
        )
        return LLMResult(
            message=message,
            finish_reason=getattr(choice, "finish_reason", None) or "stop",
            usage=usage,
        )

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        return self._stream(messages, tools, temperature)

    async def _stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema] | None,
        temperature: float | None,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用。

        只在**尚未吐出任何正文**时重试：一旦开始推送，重试会让用户看到重复内容。
        已经开始就直接把映射后的异常抛给编排层走降级。
        """
        kwargs = _request_kwargs(
            self.model, messages, tools, self._temperature if temperature is None else temperature
        )
        kwargs["stream"] = True
        # 🔴 TDD §1.3：不带这个参数，上游永远不回 usage，后台成本列会全是 0
        kwargs["stream_options"] = {"include_usage": True}

        attempt = 0
        emitted = False
        while True:
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                async for event in stream:
                    chunk = self._to_chunk(event)
                    # 工具调用帧也算「已开始推送」：半途重试会让编排层收到
                    # 两段拼在一起的 arguments，JSON 直接解析失败
                    if chunk.delta or chunk.tool_call_deltas:
                        emitted = True
                    yield chunk
                return
            except Exception as exc:
                mapped = map_llm_error(exc)
                if emitted or attempt >= self._max_retries or not mapped.retryable:
                    raise mapped from exc
                delay = compute_backoff(attempt)
                attempt += 1
                logger.warning(
                    "llm.stream[%s] 第 %d/%d 次重试，%.2fs 后执行（%s）",
                    self.model,
                    attempt,
                    self._max_retries,
                    delay,
                    mapped.code,
                )
                await asyncio.sleep(delay)

    def _to_chunk(self, event: Any) -> StreamChunk:
        """把一个 SSE 事件转成 StreamChunk，正文、推理内容、工具调用三路分流。"""
        choices = getattr(event, "choices", None) or []
        delta_text = ""
        reasoning = ""
        tool_deltas: tuple[StreamToolCallDelta, ...] = ()
        finish_reason = None
        if choices:
            delta = getattr(choices[0], "delta", None)
            # 🔴 分流：reasoning_content 是思维链，绝不能混进给用户的答案
            delta_text = getattr(delta, "content", None) or ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            tool_deltas = _to_tool_deltas(getattr(delta, "tool_calls", None))
            finish_reason = getattr(choices[0], "finish_reason", None)
        usage: TokenUsage | None = None
        raw_usage = getattr(event, "usage", None)
        if raw_usage is not None:
            usage = usage_or_estimate(raw_usage, model=self.model)
        return StreamChunk(
            delta=delta_text,
            reasoning_delta=reasoning,
            tool_call_deltas=tool_deltas,
            finish_reason=finish_reason,
            usage=usage,
        )


class OpenAICompatEmbedder:
    """向量化 Provider，走本地 BGE-M3 服务（TDD §1.4）。实现 base.EmbeddingProvider。

    与对话 Provider 分开：端点、超时、失败话术都不同，本地服务没起来时的提示
    是「请确认 8001 端口已启动」，跟「API Key 无效」完全是两回事。
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int,
        api_key: str = "not-needed",
        timeout_s: float = 60.0,
        batch_size: int = 16,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.dim = dim
        self._batch_size = max(1, batch_size)
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化。分批发送避免一次请求体过大；返回顺序与入参一致。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            try:
                response = await retry_async(
                    lambda batch=batch: self._client.embeddings.create(
                        model=self.model, input=batch
                    ),
                    operation=f"embed[{self.model}]",
                    max_retries=self._max_retries,
                )
            except Exception as exc:
                raise EmbeddingError(f"向量化失败: {_clip(exc)}", cause=exc) from exc
            ordered = sorted(response.data, key=lambda item: getattr(item, "index", 0))
            vectors.extend([list(item.embedding) for item in ordered])

        if vectors and len(vectors[0]) != self.dim:
            # 🔴 维度不匹配会让 Chroma 里新旧向量混存，检索结果全乱且极难排查。
            #    宁可启动即失败，也不要静默写坏索引（TDD R8）。
            raise EmbeddingError(
                f"embedding 维度不匹配：期望 {self.dim}，实际 {len(vectors[0])}",
                retryable=False,
                user_facing_msg="向量模型维度与配置不一致，请检查 EMBED_DIM 或重建索引",
            )
        return vectors
