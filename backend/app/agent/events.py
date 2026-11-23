"""Agent 层对外事件契约（TDD §4.5.3 / §5 的 ChatFrame 前身）。

编排器是异步生成器，边想边吐：IM 适配层（M3）与 SSE 接口（M6）消费同一套
AgentEvent，各自决定怎么渲染。刻意不在这里定义 ChatFrame：帧结构属于 API 契约，
放 app/schemas/chat.py，等 M6 真要写 REST 时再从 AgentEvent 映射过去。现在
提前建 schemas 会造出一个只有字段、没有序列化验证的空壳。

🔴 权威答案在 done.reply.answer，不在 delta 事件里。delta 是「已经推给用户看的
增量文本」，而 L3 引用后校验可能在全部 delta 发完之后才撤回答案（把 [99] 这种
编造引用换成 NOT_FOUND_REPLY）。适配层必须以 done.reply.answer 为最终值去
**替换**已发送内容，而不是追加。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.llm.base import TokenUsage

EventKind = Literal["delta", "reasoning", "tool_call", "citations", "usage", "done", "error"]

ReplyStatus = Literal["ok", "degraded", "refused"]

KNOWLEDGE_TOOL_NAME = "search_knowledge_base"


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """一次工具调用的完整留痕。

    arguments 是模型给的原始 JSON 字符串（不是解析后的对象），因为落库与后台
    展示需要原样重现「模型当时到底传了什么」，而参数解析失败时也只有原始串可存。
    result_summary 是截断后的工具输出，够人工判断结果对不对即可。
    """

    round: int
    tool_name: str
    arguments: str
    result_summary: str
    ok: bool
    error: str | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentReply:
    """一轮问答的最终结果。

    status 三态对应三种后台统计口径：ok 正常回答，degraded 上游故障降级（Q10），
    refused 主动拒答（隐私 L1 或引用校验 L3）。refused_by 记录拒答原因，其中
    隐私类只写命中的敏感词、不写用户原话（FR-6-08 日志脱敏）。
    """

    answer: str
    status: ReplyStatus
    citations: tuple[dict[str, Any], ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    refused_by: str | None = None
    latency_ms: float = 0.0
    prompt_version: str = ""

    @property
    def knowledge_used(self) -> bool:
        """本轮是否动过知识库。只有它为真时 L3 引用校验才生效（CURRENT_PLAN 修正 1）。"""
        return any(record.tool_name == KNOWLEDGE_TOOL_NAME for record in self.tool_calls)


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """编排器吐出的一个事件。kind 决定哪些字段有值。

    text 同时服务 delta（增量正文）与 error（错误说明），不再分两个字段：
    适配层按 kind 分支渲染，字段语义由 kind 唯一确定，避免「两个字段只有一个
    非空、还得判空」的写法。
    """

    kind: EventKind
    text: str = ""
    tool_call: ToolCallRecord | None = None
    citations: Sequence[dict[str, Any]] = ()
    usage: TokenUsage | None = None
    reply: AgentReply | None = None


def delta(text: str) -> AgentEvent:
    """正文增量事件。IM 攒批与 SSE 帧都消费它。"""
    return AgentEvent(kind="delta", text=text)


def done(reply: AgentReply) -> AgentEvent:
    """终局事件。每次 ask() 有且仅有一个，携带权威答案。"""
    return AgentEvent(kind="done", reply=reply)
