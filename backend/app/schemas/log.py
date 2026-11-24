"""Web 管理后台对话日志契约（HTML 7.6 / PRD FR-5-03~05）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CitationView(BaseModel):
    ref_no: int
    doc_id: str = ""
    filename: str
    heading_path: list[str] | str = Field(default_factory=list)
    snippet: str
    char_start: int
    char_end: int
    page: int | None = None
    score: float


class ToolCallView(BaseModel):
    round: int
    tool_name: str
    arguments: dict[str, Any]
    result_summary: str
    ok: bool
    error: str | None = None
    latency_ms: int


class UsageView(BaseModel):
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float


class ConversationLogItem(BaseModel):
    id: int
    request_id: str
    platform: str
    user_id: str
    user_name: str
    question: str
    answer: str | None
    status: Literal["ok", "degraded", "refused", "error"]
    refused_by: str | None
    tool_count: int
    total_tokens: int
    latency_ms: int
    created_at: datetime


class ConversationLogView(ConversationLogItem):
    session_key: str
    citations: list[CitationView]
    tool_calls: list[ToolCallView]
    usage: UsageView


class ConversationLogList(BaseModel):
    items: list[ConversationLogItem]
    total: int
    page: int
    size: int


class StatsValue(BaseModel):
    count: int
    tokens: int
    cost_cny: float


class ConversationStats(BaseModel):
    today: StatsValue
    total: StatsValue
