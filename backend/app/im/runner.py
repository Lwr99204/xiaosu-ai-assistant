"""IM 消息处理编排（TDD §4.6.2 / §4.6.3）。

runner 是平台无关的一层：过滤（群聊非 @、白名单、幂等去重）→ 绑定 request_id →
调 AgentService 消费事件流 → 攒批流式回写 → 终局用 done.reply 覆盖全文。

三个必须守住的语义
------------------
1. **超时不取消任务**（TDD §4.6.3 兜底话术表）：用 asyncio.wait 而不是
   wait_for。超时后先回一句「仍在后台处理」，任务继续跑完并落库；它最后那次
   finish=True 会原地覆盖掉这句提示，用户最终仍能看到完整答案。
2. **终局替换而非追加**：权威答案在 done.reply.answer。L3 引用后校验可能在
   所有 delta 都发完之后才撤回答案，所以收尾帧发的是重新格式化的全文。
3. **异常也要给用户一句话**：兜底发送本身可能失败，那时只记日志不再上抛，
   否则异常会窜回 SDK 的事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import Settings
from app.core.ids import bind_request_id, new_id, new_request_id, unbind_request_id
from app.im.base import IMAdapter, UnifiedMessage
from app.im.formatter import degraded_answer_text, format_final_reply, split_reply
from app.im.session import MessageDeduper, build_session_key
from app.services.agent_service import AgentService, AskContext

logger = logging.getLogger(__name__)

#: 硬超时兜底话术（FR-4-09）。任务不被取消，跑完后会覆盖这句提示。
FALLBACK_TIMEOUT = "⏳ 这次查询耗时较长，仍在后台处理，完成后会补发回复。"

#: 内部异常兜底话术。带 request_id 方便用户在后台日志里定位这一轮。
FALLBACK_INTERNAL = "🙇 我这边出了点问题，已记录日志（编号 {request_id}），请联系管理员。"

#: 攒批的时间上限：字数没攒够也要定期刷一次，避免用户长时间看不到动静。
_FLUSH_INTERVAL_S = 0.3


class IMRunner:
    """把统一消息喂给 Agent，并把事件流渲染成企微的原生流式回复。"""

    def __init__(
        self,
        *,
        settings: Settings,
        service: AgentService,
        adapter: IMAdapter,
        deduper: MessageDeduper,
    ) -> None:
        self._settings = settings
        self._service = service
        self._adapter = adapter
        self._deduper = deduper

    async def handle(self, msg: UnifiedMessage) -> None:
        """adapter 的 MessageHandler 入口。过滤 → 计时兜底 → 落回复。"""
        if not msg.text.strip():
            return
        if msg.conversation_type == "group" and not msg.is_at_bot:
            return
        if not self._allowed(msg):
            logger.warning("拒绝白名单外的调用 user_id=%s", msg.user_id)
            return
        if self._deduper.is_duplicate(msg.message_id, msg.platform):
            logger.info("重复消息已忽略 message_id=%s", msg.message_id)
            return

        request_id, token = bind_request_id(new_request_id())
        try:
            task = asyncio.create_task(self._answer(msg, request_id))
            finished, _pending = await asyncio.wait(
                {task}, timeout=self._settings.im_reply_timeout_s
            )
            if not finished:
                logger.warning("回复超时，先发兜底话术（任务继续跑完并落库）")
                await self._safe_fallback(msg, FALLBACK_TIMEOUT)
                return
            failure = task.exception()
            if failure is not None:
                raise failure
        except Exception:
            logger.exception("处理 IM 消息失败 request_id=%s", request_id)
            await self._safe_fallback(msg, FALLBACK_INTERNAL.format(request_id=request_id))
        finally:
            unbind_request_id(token)

    async def _answer(self, msg: UnifiedMessage, request_id: str) -> None:
        """消费 Agent 事件流并流式回写；异常交由 handle 统一兜底。"""
        stream_id = new_id("stream")
        context = AskContext(
            session_key=build_session_key(msg.platform, msg.conversation_id, msg.user_id),
            platform=msg.platform,
            user_id=msg.user_id,
            user_name=msg.user_name,
            request_id=request_id,
        )
        buffer = ""
        sent = 0
        reply = None
        last_flush = time.monotonic()

        async for event in self._service.ask(msg.text, context):
            force_flush = event.kind == "tool_call"
            if event.kind == "delta":
                buffer += event.text
            elif force_flush and event.tool_call is not None:
                # 工具调用要立刻让用户看到进度，不等攒批阈值（FR-4-05）。
                notice = f"🔍 正在调用工具 {event.tool_call.tool_name}…"
                buffer = f"{buffer}\n\n{notice}" if buffer else notice
            elif event.kind == "done":
                reply = event.reply
            else:
                continue

            if not force_flush and not self._should_flush(len(buffer) - sent, last_flush):
                continue
            await self._adapter.stream_reply(msg, stream_id, buffer, False)
            sent = len(buffer)
            last_flush = time.monotonic()

        if reply is None:
            raise RuntimeError("Agent 事件流结束却没有 done 事件")

        final_text = format_final_reply(reply, answer_override=degraded_answer_text(reply))
        segments = split_reply(final_text, self._settings.wecom_max_reply_chars)
        await self._adapter.stream_reply(msg, stream_id, segments[0], True)
        for extra in segments[1:]:
            await self._adapter.send_reply(msg, extra)

    def _should_flush(self, unsent: int, last_flush: float) -> bool:
        """攒批判定：够字数就发，字数不够但等太久也发。"""
        if unsent >= self._settings.im_stream_chunk_chars:
            return True
        return unsent > 0 and time.monotonic() - last_flush >= _FLUSH_INTERVAL_S

    def _allowed(self, msg: UnifiedMessage) -> bool:
        """白名单只管企微：Web 后台走自己的登录鉴权，不在这层拦（FR-4-10）。"""
        if msg.platform != "wecom":
            return True
        allowed = self._settings.wecom_allowed_user_ids
        return not allowed or msg.user_id in allowed

    async def _safe_fallback(self, msg: UnifiedMessage, text: str) -> None:
        """发兜底话术。发送失败只记日志，避免异常窜回 SDK 事件循环。"""
        try:
            await self._adapter.send_fallback(msg, text)
        except Exception:
            logger.exception("兜底话术发送失败 message_id=%s", msg.message_id)
