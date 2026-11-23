"""LLM 适配层契约（TDD §4.3 / CURRENT_PLAN 修正 5）。

两处刻意的设计取舍
----------------
1. **ChatProvider 与 EmbeddingProvider 分离**（CURRENT_PLAN 修正 5）。
   TDD §4.3 原文把 chat 与 embed 放在同一个 LLMProvider Protocol 里，但本项目
   的 chat 走云端 OpenAI 兼容端点、embedding 走本地 BGE-M3 服务（TDD §1.4），
   两者的 base_url、超时、重试策略、失败降级话术完全不同。合并成一个 Protocol
   会逼着实现类持有两套客户端配置，也会让「换个 embedding 模型」牵动 chat 路径。
2. **chat() 的工具参数是已转换好的 OpenAI tool dict，不是 ToolSpec 对象**。
   分层规则（TDD §3.1）规定 agent -> llm 单向依赖，llm 层不得 import agent。
   若签名里出现 ToolSpec 就形成反向依赖；改收 Mapping 后，tool_registry 负责
   生成 JSON Schema，llm 层只管透传，依赖方向保持干净。

所有数据类都是 frozen + slots：消息对象会在编排循环里被反复传递与拼接，
不可变能杜绝「上一轮的 messages 被下一轮就地改掉」这类难查的串话问题。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

#: OpenAI function calling 的工具描述结构（由 agent.tool_registry 生成）
ToolSchema = Mapping[str, Any]

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    arguments 保留**原始 JSON 字符串**：解析与 Pydantic 校验是 agent 层的职责
    （TDD §4.5.1 invoke 四步），llm 层不做半成品解析，避免两处各解一次产生分歧。
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。role=tool 时必须带 tool_call_id 以便与请求配对。"""

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token 用量。

    estimated=True 表示供应商没回 usage（Ollama 等本地端点常见），数值由
    tokenizer.estimate_tokens 估算而来。后台展示成本时必须区分这两种来源，
    否则会把估算值当成账单口径（FR-5-10）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    estimated: bool = False


@dataclass(frozen=True, slots=True)
class LLMResult:
    """一次非流式调用的完整结果。finish_reason 用于区分 stop / tool_calls / length。"""

    message: ChatMessage
    finish_reason: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True, slots=True)
class StreamToolCallDelta:
    """流式帧里的一个工具调用增量。

    上游把一次工具调用拆成多帧：首帧带 id 与 name，后续帧只带 arguments 片段。
    index 是上游给的调用序号，编排层按它把片段拼回完整 ToolCall。
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """流式增量帧。

    🔴 delta 与 reasoning_delta 分开（TDD §1.3 实测约束）：上游 qwen3.8-max 的
    推理内容走 delta.reasoning_content 且**先于**正文到达，两者无脑拼在一起会
    把思维链直接吐给用户。分流后 reasoning 只用于计时与调试，不进答案正文。

    tool_call_deltas 让流式路径也能承载工具调用：编排层因此可以每轮都流式，
    既保住最后一轮的逐 token 体验（FR-2-07），又不必为了拿完整 tool_calls 结构
    而在工具轮退回非流式。
    """

    delta: str = ""
    reasoning_delta: str = ""
    tool_call_deltas: tuple[StreamToolCallDelta, ...] = ()
    finish_reason: str | None = None
    usage: TokenUsage | None = None


@runtime_checkable
class ChatProvider(Protocol):
    """对话模型提供者。换供应商只换 base_url 与 model，不改调用方代码。"""

    model: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """非流式调用。工具调用轮次用这条路径，因为需要完整的 tool_calls 结构。"""
        ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用。最后一轮（模型不再请求工具）用它逐 token 推送（FR-2-07）。

        刻意是**同步方法返回异步迭代器**，而不是 async def：调用方用
        async for 消费，异常在迭代过程中抛出，能被编排层的降级逻辑捕获。
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化提供者。与 ChatProvider 分离，见模块 docstring 取舍 1。"""

    model: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化。返回顺序必须与入参一致（RAG 分块与向量的对应关系依赖它）。"""
        ...
