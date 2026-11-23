"""Agent 编排器：流式工具循环 + 三层防线（TDD §4.5.3 / §4.4.4）。

一次 ask() 的完整路径

    L1 敏感主题前置拦截 → 不进模型，直接拒答
    工具循环（≤ max_tool_rounds 轮）→ 每轮流式，模型请求工具就执行并回灌
    L3 引用后校验 → 只对用过知识库的答案生效

🔴 编排器**不向上抛异常**。验收题 Q10 要求把 API Key 改成无效值后，IM 里
必须收到友好回复，不能让用户那边一直转圈或永久无响应。所有上游故障都在
这里被吞掉并转成 status="degraded" + LLM_FAILURE_REPLY。

🔴 工具路由完全交给模型的 function calling。本文件里没有任何「问题含某关键词
就调某工具」的 if-else 分支，唯一出现的工具名常量是 KNOWLEDGE_TOOL_NAME，
用途是判定 L3 引用校验的**适用范围**（这轮答案是不是来自知识库），不参与路由。

关于流式：TDD §4.5.3 原文主张「工具轮走非流式、最后一轮才流式」，因为非流式
更容易拿到完整的 tool_calls 结构。实测 qwen3.8-max 的流式帧会按 index 分片下发
工具调用（首帧带 id/name、后续帧只带 arguments 片段），因此 StreamChunk 增加了
tool_call_deltas 字段承载分片，这里按 index 累加即可拼回完整 ToolCall。结果是
每一轮都能流式，用户不会在长工具链里面对一段空白等待。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.agent.events import KNOWLEDGE_TOOL_NAME, AgentEvent, AgentReply, ToolCallRecord
from app.agent.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION
from app.agent.refusal import (
    LLM_FAILURE_REPLY,
    NOT_FOUND_REPLY,
    PRIVACY_REFUSAL,
    TOOL_ROUNDS_EXCEEDED_REPLY,
    check_citations,
    detect_sensitive,
    pick_citations,
)
from app.agent.tool_registry import ToolRegistry
from app.config import Settings
from app.core.timing import Timer, start_timer
from app.llm.base import ChatMessage, ChatProvider, TokenUsage, ToolCall
from app.llm.tokenizer import usage_or_estimate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PendingCall:
    """流式帧里正在拼装的工具调用。按上游给的 index 归组。"""

    id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)

    def build(self, fallback_index: int) -> ToolCall:
        """拼成完整 ToolCall。

        id 缺失时用 call_{n} 兜底：部分兼容端点不下发 id，但 OpenAI 协议要求
        role="tool" 消息带 tool_call_id 与 assistant 的调用配对，缺了会被网关拒。
        arguments 为空时补 "{}"，让下游 json.loads 拿到合法对象而不是空串报错。
        """
        return ToolCall(
            id=self.id or f"call_{fallback_index + 1}",
            name=self.name or "",
            arguments="".join(self.arguments) or "{}",
        )


def _merge_usage(total: TokenUsage, added: TokenUsage) -> TokenUsage:
    """逐字段相加。多轮工具调用各自计费，相加才是真实账单口径（FR-5-10）。"""
    return TokenUsage(
        prompt_tokens=total.prompt_tokens + added.prompt_tokens,
        completion_tokens=total.completion_tokens + added.completion_tokens,
        total_tokens=total.total_tokens + added.total_tokens,
        model=added.model or total.model,
        estimated=total.estimated or added.estimated,
    )


def _trim_history(history: Sequence[ChatMessage], max_turns: int) -> list[ChatMessage]:
    """裁掉长期历史里的工具消息并限制轮数。

    只保留 user/assistant：role="tool" 的消息是当轮工具链的中间产物，跨轮带进
    历史既浪费上下文，也容易让模型误以为旧工具结果仍然新鲜。

    超预算时保住开头两条（TDD §4.5.3）：首轮常含身份线索，比如「我是技术部的
    张三」，一刀切掉尾部会让后续指代「我的部门」失去依据。
    """
    usable = [
        message
        for message in history
        if message.role in ("user", "assistant") and (message.content or "").strip()
    ]
    budget = max_turns * 2
    if len(usable) <= budget:
        return usable
    if budget <= 2:
        return usable[:budget]
    return [*usable[:2], *usable[-(budget - 2) :]]


def _extract_hits(output: str) -> list[Mapping[str, Any]]:
    """从工具输出里取出命中列表，供 L3 引用校验。

    刻意写成「解析 JSON 后看有没有 list 型 hits 键」这种数据驱动形式，而不是在
    编排器里硬编码知识库的返回结构：tools/knowledge.py 的输出字段以后调整，
    这里不用跟着改，也就不会形成 agent → tools 的反向依赖。
    """
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, Mapping):
        return []
    hits = payload.get("hits")
    return list(hits) if isinstance(hits, list) else []


def _finalize(text: str, hits: Sequence[Mapping[str, Any]], knowledge_used: bool) -> tuple:
    """终局处理。返回 (answer, status, citations, refused_by)。

    L3 引用后校验只对知识库来源的答案生效（CURRENT_PLAN 修正 1）。员工、考勤、
    订单、时间、计算器的答案根本没有「文档引用」这个概念，若一律要求带 [n] 才
    放行，验收题 Q4-Q7 会被自己的校验逻辑拒掉。
    """
    answer = text.strip()
    if not knowledge_used:
        return answer, "ok", (), None

    reason = check_citations(answer, hits)
    if reason:
        logger.warning("citation check failed: %s", reason)
        return NOT_FOUND_REPLY, "refused", (), f"citation:{reason}"
    return answer, "ok", pick_citations(answer, hits), None


def _degraded_events(
    exc: BaseException,
    timer: Timer,
    usage: TokenUsage,
    tool_records: Sequence[ToolCallRecord],
) -> list[AgentEvent]:
    """上游故障 → 友好降级（验收题 Q10）。

    日志里只记异常类型与消息，不记 messages 内容：system prompt 与用户提问都
    可能含敏感信息，落盘等于绕过 FR-6-08 的脱敏要求。
    """
    logger.error("LLM call failed, degrading: %s: %s", type(exc).__name__, exc)
    reply = AgentReply(
        answer=LLM_FAILURE_REPLY,
        status="degraded",
        tool_calls=tuple(tool_records),
        usage=usage,
        refused_by=f"llm:{type(exc).__name__}",
        latency_ms=timer.stop(),
        prompt_version=SYSTEM_PROMPT_VERSION,
    )
    return [
        AgentEvent(kind="error", text=LLM_FAILURE_REPLY),
        AgentEvent(kind="usage", usage=usage),
        AgentEvent(kind="done", reply=reply),
    ]


class Orchestrator:
    """Agent 编排器。无状态、无会话存储，会话历史由调用方（IM / API）持有。

    这样设计是因为会话隔离维度不止一个：企微要按「群 + 用户」隔离（验收题 Q7
    要求「他」能被正确理解成上一轮提到的员工 001，但不同人问同一句话不能串），
    Web 后台按 session_id 隔离。编排器若自己存历史，就得同时理解这两套 key。
    """

    def __init__(
        self,
        *,
        llm: ChatProvider,
        registry: ToolRegistry,
        max_tool_rounds: int,
        history_max_turns: int,
        temperature: float | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._max_tool_rounds = max(1, max_tool_rounds)
        self._history_max_turns = max(0, history_max_turns)
        self._temperature = temperature

    async def ask(
        self,
        question: str,
        *,
        history: Sequence[ChatMessage] = (),
    ) -> AsyncIterator[AgentEvent]:
        """流式回答一个问题。

        🔴 done.reply.answer 是**权威最终答案**。delta 事件里的文本已经推给用户了，
        若 L3 校验在最后一步撤回答案，适配层必须用 reply.answer 去替换已发送内容，
        而不是把它追加在后面。
        """
        timer = start_timer()
        tool_records: list[ToolCallRecord] = []
        hits: list[Mapping[str, Any]] = []
        knowledge_used = False
        usage = TokenUsage(model=self._llm.model)

        # L1：隐私问题不进模型。规则拦截是唯一能保证 100% 不泄露的手段，
        # system prompt 第 7 条的约束只是第二道防线（TDD §4.4.4）。
        sensitive = detect_sensitive(question)
        if sensitive:
            logger.info("privacy refusal triggered")
            reply = AgentReply(
                answer=PRIVACY_REFUSAL,
                status="refused",
                refused_by=f"sensitive:{sensitive}",
                usage=usage,
                latency_ms=timer.stop(),
                prompt_version=SYSTEM_PROMPT_VERSION,
            )
            yield AgentEvent(kind="delta", text=PRIVACY_REFUSAL)
            yield AgentEvent(kind="done", reply=reply)
            return

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            *_trim_history(history, self._history_max_turns),
            ChatMessage(role="user", content=question),
        ]

        for round_index in range(1, self._max_tool_rounds + 1):
            text_parts: list[str] = []
            pending: dict[int, _PendingCall] = {}
            round_usage: TokenUsage | None = None

            try:
                stream = self._llm.stream(
                    messages,
                    tools=self._registry.to_openai_tools(),
                    temperature=self._temperature,
                )
                async for chunk in stream:
                    for tool_delta in chunk.tool_call_deltas:
                        call = pending.setdefault(tool_delta.index, _PendingCall())
                        call.id = call.id or tool_delta.id
                        call.name = call.name or tool_delta.name
                        if tool_delta.arguments_delta:
                            call.arguments.append(tool_delta.arguments_delta)
                    if chunk.delta:
                        text_parts.append(chunk.delta)
                        yield AgentEvent(kind="delta", text=chunk.delta)
                    # 🔴 usage 直接取上游给的 dataclass。tokenizer.usage_or_estimate
                    # 只认 dict / model_dump()，把 TokenUsage 传进去会被当成缺失而
                    # 重新估算，等于丢掉真实用量。
                    if chunk.usage is not None:
                        round_usage = chunk.usage
            except Exception as exc:
                for event in _degraded_events(exc, timer, usage, tool_records):
                    yield event
                return

            text = "".join(text_parts)
            usage = _merge_usage(
                usage,
                round_usage
                or usage_or_estimate(
                    None,
                    model=self._llm.model,
                    messages=messages,
                    completion_text=text,
                ),
            )
            tool_calls = tuple(
                pending[index].build(position) for position, index in enumerate(sorted(pending))
            )

            if not tool_calls:
                answer, status, citations, refused_by = _finalize(text, hits, knowledge_used)
                yield AgentEvent(kind="citations", citations=citations)
                yield AgentEvent(kind="usage", usage=usage)
                yield AgentEvent(
                    kind="done",
                    reply=AgentReply(
                        answer=answer,
                        status=status,
                        citations=citations,
                        tool_calls=tuple(tool_records),
                        usage=usage,
                        refused_by=refused_by,
                        latency_ms=timer.stop(),
                        prompt_version=SYSTEM_PROMPT_VERSION,
                    ),
                )
                return

            messages.append(
                ChatMessage(role="assistant", content=text or None, tool_calls=tool_calls)
            )
            for call in tool_calls:
                result = await self._registry.invoke(call.name, call.arguments)
                record = ToolCallRecord(
                    round=round_index,
                    tool_name=call.name,
                    arguments=call.arguments,
                    result_summary=result.output,
                    ok=result.ok,
                    error=result.error,
                    latency_ms=result.latency_ms,
                )
                tool_records.append(record)
                yield AgentEvent(kind="tool_call", tool_call=record)
                if call.name == KNOWLEDGE_TOOL_NAME:
                    knowledge_used = True
                    hits.extend(_extract_hits(result.output))
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=result.output,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        # 循环走完仍没拿到终局答案：模型在工具之间来回打转。不能无限重试，
        # 也不能把最后一轮的半截正文当答案（可能是「我来查一下」这种过程性文本）。
        logger.warning("tool rounds exceeded: %s", self._max_tool_rounds)
        yield AgentEvent(kind="delta", text=TOOL_ROUNDS_EXCEEDED_REPLY)
        yield AgentEvent(kind="usage", usage=usage)
        yield AgentEvent(
            kind="done",
            reply=AgentReply(
                answer=TOOL_ROUNDS_EXCEEDED_REPLY,
                status="degraded",
                tool_calls=tuple(tool_records),
                usage=usage,
                refused_by=f"tool_rounds_exceeded:{self._max_tool_rounds}",
                latency_ms=timer.stop(),
                prompt_version=SYSTEM_PROMPT_VERSION,
            ),
        )


def build_orchestrator(
    settings: Settings,
    llm: ChatProvider,
    registry: ToolRegistry,
) -> Orchestrator:
    """按配置组装编排器。temperature 传 None 交给 llm 层用配置默认值。"""
    return Orchestrator(
        llm=llm,
        registry=registry,
        max_tool_rounds=settings.agent_max_tool_rounds,
        history_max_turns=settings.agent_history_max_turns,
    )
