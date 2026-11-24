"""管理员对话日志 API：列表筛选、详情工具链与 Token 统计。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.core.errors import NotFoundError
from app.db.entities import ConversationRecord, ToolCallEntry, UsageStats
from app.db.repositories import ConversationRepo
from app.schemas.log import (
    CitationView,
    ConversationLogItem,
    ConversationLogList,
    ConversationLogView,
    ConversationStats,
    StatsValue,
    ToolCallView,
    UsageView,
)

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _repo(request: Request) -> ConversationRepo:
    return ConversationRepo(request.app.state.settings.sqlite_path)


def _item(record: ConversationRecord, tool_count: int) -> ConversationLogItem:
    return ConversationLogItem(
        id=int(record.id or 0),
        request_id=record.request_id,
        platform=record.platform,
        user_id=record.user_id,
        user_name=record.user_name,
        question=record.question,
        answer=record.answer,
        status=record.status,
        refused_by=record.refused_by,
        tool_count=tool_count,
        total_tokens=record.total_tokens,
        latency_ms=record.latency_ms,
        created_at=record.created_at,
    )


def _parse_json(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _tool_view(call: ToolCallEntry) -> ToolCallView:
    arguments = _parse_json(call.arguments_json, {})
    return ToolCallView(
        round=call.round,
        tool_name=call.tool_name,
        arguments=arguments if isinstance(arguments, dict) else {},
        result_summary=call.result_summary,
        ok=call.ok,
        error=call.error,
        latency_ms=call.latency_ms,
    )


def _stats_value(value: UsageStats) -> StatsValue:
    return StatsValue(count=value.count, tokens=value.tokens, cost_cny=value.cost_cny)


@router.get("", response_model=ConversationLogList)
def list_logs(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    platform: str | None = None,
    user_id: str | None = None,
    has_tool_call: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ConversationLogList:
    repo = _repo(request)
    records, total = repo.list_paged(
        offset=(page - 1) * size,
        limit=size,
        platform=platform,
        user_id=user_id,
        has_tool_call=has_tool_call,
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
    )
    items = [_item(record, len(repo.tool_calls_of(int(record.id or 0)))) for record in records]
    return ConversationLogList(items=items, total=total, page=page, size=size)


@router.get("/stats", response_model=ConversationStats)
def log_stats(request: Request) -> ConversationStats:
    repo = _repo(request)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return ConversationStats(
        today=_stats_value(repo.stats(start=today)),
        total=_stats_value(repo.stats()),
    )


@router.get("/{conversation_id}", response_model=ConversationLogView)
def get_log(request: Request, conversation_id: int) -> ConversationLogView:
    repo = _repo(request)
    record = repo.find_by_id(conversation_id)
    if record is None:
        raise NotFoundError("对话日志不存在", conversation_id=conversation_id)
    calls = repo.tool_calls_of(conversation_id)
    raw_citations = _parse_json(record.citations_json, [])
    citations = [CitationView.model_validate(item) for item in raw_citations]
    return ConversationLogView(
        **_item(record, len(calls)).model_dump(),
        session_key=record.session_key,
        citations=citations,
        tool_calls=[_tool_view(call) for call in calls],
        usage=UsageView(
            model=record.model,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            total_tokens=record.total_tokens,
            cost_cny=record.cost_cny,
        ),
    )
