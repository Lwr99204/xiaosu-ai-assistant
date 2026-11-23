"""DB 层内部实体（TDD §4.7「内部实体与外部契约分离」）。

DB 用 dataclass、API 层用 Pydantic：DB 字段调整不会直接冲击前端契约。
本模块是依赖叶子，不 import 项目内其他模块（连 agent 层的事件类也不引，
转换在 services 层做）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ConversationRecord:
    """conversations 表一行 = 一轮问答。字段与 schema.sql 一一对应。"""

    request_id: str
    session_key: str
    platform: str
    user_id: str
    question: str
    created_at: str
    user_name: str = ""
    answer: str | None = None
    citations_json: str | None = None
    refused_by: str | None = None
    status: str = "ok"
    error_code: str | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0
    latency_ms: int = 0
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ToolCallEntry:
    """tool_calls 表一行。

    与 agent.events.ToolCallRecord 分开定义：db 层不得 import agent 层，
    services 层负责把事件对象转成这里的实体。
    """

    round: int
    tool_name: str
    arguments_json: str
    result_summary: str
    ok: bool
    error: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class QAPair:
    """从历史读回的一问一答，供 services 层转成 ChatMessage。"""

    question: str
    answer: str
