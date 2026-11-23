"""AgentService：IM 与 Web API 共用的唯一 Agent 入口（CURRENT_PLAN M3）。

职责边界
--------
- 读会话历史并传给编排器（编排器无状态，TDD §4.5.3）；
- 透传 AgentEvent 流：IM runner 与 SSE 端点消费同一套事件；
- done 事件到达后把整轮问答与工具链落库（后台全量对话日志的数据源，FR-5-01）。

会话隔离：session_key 由入口层按 platform:conversation_id:user_id 生成，
本层只认 key 不认平台（FR-4-04 / FR-4-12）。

落库失败不影响回复：此时答案已流式推给用户，抛异常只会把「日志缺一行」
升级成「用户看到报错」，因此捕获后记 error 日志了事。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agent.events import AgentEvent, AgentReply
from app.agent.orchestrator import Orchestrator
from app.config import Settings
from app.db.engine import init_db, now_iso
from app.db.entities import ConversationRecord, ToolCallEntry
from app.db.repositories import ConversationRepo
from app.llm.base import ChatMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AskContext:
    """入口层生成的调用上下文。IM runner 与 Web API 各自组装。"""

    session_key: str
    platform: str
    user_id: str
    user_name: str = ""
    request_id: str = ""


class AgentService:
    """一次 ask = 读历史 → 编排器流式回答 → 透传事件 → 落库。"""

    def __init__(self, *, settings: Settings, orchestrator: Orchestrator) -> None:
        self._settings = settings
        self._orchestrator = orchestrator
        init_db(settings.sqlite_path)
        self._conversations = ConversationRepo(settings.sqlite_path)

    def history(self, session_key: str) -> list[ChatMessage]:
        """把库里的问答对转成 ChatMessage 列表（FR-2-04/06）。"""
        messages: list[ChatMessage] = []
        for pair in self._conversations.recent_pairs(
            session_key, self._settings.agent_history_max_turns
        ):
            messages.append(ChatMessage(role="user", content=pair.question))
            messages.append(ChatMessage(role="assistant", content=pair.answer))
        return messages

    async def ask(self, question: str, context: AskContext) -> AsyncIterator[AgentEvent]:
        """流式回答并落库。调用方负责消费完整个流（done 事件后才完成持久化）。"""
        history = self.history(context.session_key)
        async for event in self._orchestrator.ask(question, history=history):
            if event.kind == "done" and event.reply is not None:
                self._persist(question, context, event.reply)
            yield event

    def _persist(self, question: str, context: AskContext, reply: AgentReply) -> None:
        try:
            conversation_id = self._conversations.insert(self._to_record(question, context, reply))
            self._conversations.insert_tool_calls(
                conversation_id,
                [
                    ToolCallEntry(
                        round=call.round,
                        tool_name=call.tool_name,
                        arguments_json=call.arguments,
                        result_summary=call.result_summary,
                        ok=call.ok,
                        error=call.error,
                        latency_ms=int(call.latency_ms),
                    )
                    for call in reply.tool_calls
                ],
            )
        except Exception:
            logger.exception("对话落库失败（回复不受影响）request_id=%s", context.request_id)

    def _to_record(
        self, question: str, context: AskContext, reply: AgentReply
    ) -> ConversationRecord:
        return ConversationRecord(
            request_id=context.request_id,
            session_key=context.session_key,
            platform=context.platform,
            user_id=context.user_id,
            user_name=context.user_name,
            question=question,
            answer=reply.answer,
            citations_json=(
                json.dumps(list(reply.citations), ensure_ascii=False) if reply.citations else None
            ),
            refused_by=reply.refused_by,
            # ok / degraded / refused 三态与 AgentReply.status 一致。TDD §4.7 注释
            # 写的是 ok/degraded/error，refused 是它的细化：主动拒答是正常业务
            # 结果，后台按 refused_by 非空筛出，不与系统故障混在一起。
            status=reply.status,
            model=reply.usage.model or self._settings.llm_model,
            prompt_tokens=reply.usage.prompt_tokens,
            completion_tokens=reply.usage.completion_tokens,
            total_tokens=reply.usage.total_tokens,
            # 成本口径（单价表）在 M6 后台展示层接入；先落 0 保证列非空。
            cost_cny=0.0,
            latency_ms=int(reply.latency_ms),
            created_at=now_iso(),
        )
