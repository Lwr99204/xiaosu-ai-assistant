"""Web 调试问答 SSE 接口；与企业微信共用 AgentService。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.events import AgentEvent
from app.core.ids import new_request_id
from app.services.agent_service import AgentService, AskContext

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="web-default", min_length=1, max_length=120)
    user_id: str = Field(default="web-user", min_length=1, max_length=120)


def _frame(event: AgentEvent) -> dict[str, object]:
    payload: dict[str, object] = {"type": event.kind}
    if event.text:
        payload["content"] = event.text
    if event.tool_call is not None:
        payload["tool_call"] = asdict(event.tool_call)
    if event.citations:
        payload["citations"] = list(event.citations)
    if event.usage is not None:
        payload["usage"] = asdict(event.usage)
    if event.reply is not None:
        reply = asdict(event.reply)
        reply["tool_calls"] = [asdict(call) for call in event.reply.tool_calls]
        payload["reply"] = reply
    return payload


async def _stream(service: AgentService, payload: ChatRequest) -> AsyncIterator[str]:
    context = AskContext(
        session_key=f"web:{payload.session_id}:{payload.user_id}",
        platform="web",
        user_id=payload.user_id,
        request_id=new_request_id(),
    )
    async for event in service.ask(payload.question, context):
        yield f"data: {json.dumps(_frame(event), ensure_ascii=False, default=str)}\n\n"


@router.post("/stream")
async def chat_stream(request: Request, payload: ChatRequest) -> StreamingResponse:
    service: AgentService = request.app.state.agent_service
    return StreamingResponse(
        _stream(service, payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
