"""企业微信智能机器人适配器（TDD §4.6.1 / ADR-17 / CURRENT_PLAN M3）。

平台事实（Gate 0 已真机实测，见 backend/dev_tools/wecom_spike.py）
------------------------------------------------------------------
- SDK 是出向 WebSocket 长连接，企业内网无需公网入口；
- 事件由 pyee AsyncIOEventEmitter 派发：connected / authenticated / error /
  message.text（body.msgtype == "text" 时携带整帧）；
- 回复用 reply_stream(frame, stream_id, content, finish)：同一 stream_id 原地
  刷新，content 是**累计全文**，finish=True 收尾；
- 群聊只推送 @ 了机器人的消息，因此收到群聊帧即可视为 is_at_bot=True。

🔴 SDK 没有公开文档化 body 里的会话字段（chatid / from / chattype），本模块用
多候选键名做防御性解析，并在首帧把**键名**（不含任何值）记一次日志供真机校准
（见 _log_frame_keys）。任何级别都不打印消息正文、response_url 或凭证。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.core.ids import new_id
from app.im.base import AdapterHealth, ConversationType, MessageHandler, UnifiedMessage
from app.im.sdk_logger import SafeSdkLogger

logger = logging.getLogger(__name__)

#: 群聊开头的 @ 提及，如「@小苏 查一下张三的年假」（FR-4-06）。
#: 锚点要容忍提及前的空白：部分客户端把 @ 渲染成「 @小苏 」，写成 ^@ 会漏剥，
#: 导致交给 Agent 的 query 里混着「@小苏」前缀（FR-4-06 的「纯净 query」要求）。
_MENTION_PREFIX = re.compile(r"^\s*(?:@\S+\s*)+")

#: SDK 帧里可能表示群聊的取值。
_GROUP_CHAT_TYPES = frozenset({"group", "chatroom"})


def strip_mentions(text: str) -> str:
    """剔除开头的 @ 提及，留下纯净 query 交给 Agent。"""
    return _MENTION_PREFIX.sub("", text).strip()


def _first_text(*candidates: Any) -> str:
    """返回第一个非空字符串候选；全空返回空串。"""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _is_group(body: dict[str, Any]) -> bool:
    """按 chattype 判定会话类型；字段缺失时按单聊处理。"""
    chat_type = _first_text(body.get("chattype"), body.get("chat_type")).lower()
    return chat_type in _GROUP_CHAT_TYPES


def parse_message(frame: dict[str, Any]) -> UnifiedMessage | None:
    """把 SDK 文本帧归一化为 UnifiedMessage；非文本帧或无法定位发送者时返回 None。

    发送者缺失时宁可丢弃：没有 user_id 就拼不出 session_key，继续处理会把不同
    人的历史混进同一会话（FR-4-04），比不回复更糟。
    """
    body = frame.get("body")
    if not isinstance(body, dict):
        return None
    text_field = body.get("text")
    if not isinstance(text_field, dict):
        return None
    raw_text = text_field.get("content")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    sender = body.get("from") if isinstance(body.get("from"), dict) else {}
    user_id = _first_text(sender.get("userid"), sender.get("user_id"), body.get("from_userid"))
    if not user_id:
        return None

    group = _is_group(body)
    chat_type: ConversationType = "group" if group else "p2p"
    headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
    message_id = _first_text(
        body.get("msgid"), body.get("message_id"), headers.get("req_id")
    ) or new_id("msg")
    conversation_id = _first_text(body.get("chatid"), body.get("chat_id"))
    if not conversation_id:
        conversation_id = f"p2p_{user_id}"

    return UnifiedMessage(
        platform="wecom",
        message_id=message_id,
        conversation_id=conversation_id,
        conversation_type=chat_type,
        user_id=user_id,
        user_name=_first_text(sender.get("name"), sender.get("nickname")),
        text=strip_mentions(raw_text) if group else raw_text.strip(),
        is_at_bot=group,
        raw=frame,
    )


class WeComAdapter:
    """企业微信长连接适配器：收帧归一化 + 原生流式回写。

    start() 会一直挂到连接关闭；业务处理放在独立 task 里，避免慢查询阻塞
    SDK 的事件循环导致心跳丢失。
    """

    platform = "wecom"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._connected = False
        self._last_heartbeat: datetime | None = None
        self._frame_keys_logged = False
        # 消息处理任务的强引用集合：不持有引用时任务可能被 GC 提前回收，
        # 表现为「回复到一半就没了」（RUF006）。
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self, on_message: MessageHandler) -> None:
        """建立长连接并注册事件回调。"""
        try:
            from aibot import WSClient, WSClientOptions
        except ImportError as exc:  # pragma: no cover - 依赖缺失才触发
            raise RuntimeError("缺少企业微信 SDK，请在 backend 目录执行 uv sync --frozen") from exc

        bot_id = self._settings.wecom_bot_id
        secret = self._settings.wecom_bot_secret
        if bot_id is None or secret is None:
            raise RuntimeError("WECOM_ENABLED=true 但缺少 WECOM_BOT_ID / WECOM_BOT_SECRET")

        client = WSClient(
            WSClientOptions(
                bot_id=bot_id.get_secret_value(),
                secret=secret.get_secret_value(),
                ws_url=self._settings.wecom_ws_url,
                logger=SafeSdkLogger(),
            )
        )
        self._client = client
        self._register_events(client, on_message)
        logger.info("企业微信长连接启动中（凭证不打印）")
        await client.connect()

    async def stop(self) -> None:
        """断开长连接。SDK 的 disconnect 是同步方法。"""
        client = self._client
        if client is None:
            return
        self._client = None
        self._connected = False
        client.disconnect()
        logger.info("企业微信长连接已关闭")

    async def send_reply(self, msg: UnifiedMessage, content: str) -> None:
        """一次性整条消息（超长分段的后续段落走这里）。"""
        client = self._require_client()
        await client.reply_stream(msg.raw, new_id("reply"), content, True)

    async def stream_reply(
        self, msg: UnifiedMessage, stream_id: str, content: str, finish: bool
    ) -> None:
        """原生流式回写。content 为累计全文，同一 stream_id 原地刷新。"""
        client = self._require_client()
        await client.reply_stream(msg.raw, stream_id, content, finish)

    async def send_fallback(self, msg: UnifiedMessage, reason: str) -> None:
        """兜底话术。企微没有独立的提示消息类型，直接用整条回复发送。"""
        await self.send_reply(msg, reason)

    def health(self) -> AdapterHealth:
        """连接健康状态，供 /api/health 与后台 IM 卡片读取（FR-4-15）。"""
        return AdapterHealth(
            enabled=self._settings.wecom_enabled,
            connected=self._connected,
            last_heartbeat=self._last_heartbeat,
            detail="" if self._connected else "未连接或未鉴权",
        )

    def _register_events(self, client: Any, on_message: MessageHandler) -> None:
        """注册 SDK 事件。回调里只做轻量归一化，业务丢给独立 task。"""

        @client.on("connected")
        def _on_connected() -> None:
            logger.info("企业微信 WebSocket 已连接，等待鉴权")

        @client.on("authenticated")
        def _on_authenticated() -> None:
            self._connected = True
            self._last_heartbeat = datetime.now(UTC)
            logger.info("企业微信鉴权成功，机器人开始接收消息")

        @client.on("error")
        def _on_error(error: object) -> None:
            # 只记异常类型：SDK 的异常文本可能带鉴权帧内容（NFR-11）。
            self._connected = False
            logger.error("企业微信连接错误 type=%s", type(error).__name__)

        @client.on("message.text")
        async def _on_text(frame: dict[str, Any]) -> None:
            self._last_heartbeat = datetime.now(UTC)
            self._log_frame_keys(frame)
            msg = parse_message(frame)
            if msg is None:
                logger.warning("丢弃无法解析的企微帧（正文不落日志）")
                return
            task = asyncio.create_task(self._deliver(on_message, msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _deliver(self, on_message: MessageHandler, msg: UnifiedMessage) -> None:
        """在独立 task 中执行业务处理，异常就地记日志不外泄到 SDK。"""
        try:
            await on_message(msg)
        except Exception:
            logger.exception("IM 消息处理异常 message_id=%s", msg.message_id)

    def _log_frame_keys(self, frame: dict[str, Any]) -> None:
        """首帧只记**键名**用于校准字段解析，值一律不记（FR-6-08）。"""
        if self._frame_keys_logged:
            return
        self._frame_keys_logged = True
        body = frame.get("body")
        body_keys = sorted(body) if isinstance(body, dict) else []
        logger.info("企微首帧字段校准 frame_keys=%s body_keys=%s", sorted(frame), body_keys)

    def _require_client(self) -> Any:
        """未启动就回复属调用顺序错误，直接抛出让 runner 走兜底话术。"""
        if self._client is None:
            raise RuntimeError("企业微信 adapter 未启动，无法回复")
        return self._client
