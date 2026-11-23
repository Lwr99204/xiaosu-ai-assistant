"""IM 适配层统一抽象（TDD §4.6.1）。

新增平台 = 实现 IMAdapter 这一个 Protocol；AgentService 与 IMRunner 完全
不感知平台差异（FR-4-12 / NFR-08）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

Platform = Literal["wecom", "feishu", "web"]

ConversationType = Literal["group", "p2p"]


@dataclass(frozen=True, slots=True)
class UnifiedMessage:
    """各平台 adapter 归一化后的统一消息视图。"""

    platform: Platform
    message_id: str  # 幂等去重用（FR-4-11）
    conversation_id: str  # 群 ID 或单聊 ID
    conversation_type: ConversationType
    user_id: str
    user_name: str
    text: str  # 已剔除 @ 提及的纯净 query（FR-4-06）
    is_at_bot: bool
    raw: dict[str, Any] = field(default_factory=dict)  # 原始帧，回复时透传 headers


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    """连接健康状态（FR-4-15）。/api/health 与后台 IM 状态卡片读取它。"""

    enabled: bool
    connected: bool
    last_heartbeat: datetime | None = None
    detail: str = ""


#: adapter.start() 收到消息后的回调形状（IMRunner.handle）。
MessageHandler = Callable[[UnifiedMessage], Awaitable[None]]


@runtime_checkable
class IMAdapter(Protocol):
    """平台适配器最小契约。

    stream_reply 承载 TDD §4.6.3 方案 A（原生流式）：content 为**累计全文**，
    同一 stream_id 原地刷新，finish=True 收尾。send_reply 是一次性整条消息，
    供兜底话术与超长分段使用。
    """

    platform: str

    async def start(self, on_message: MessageHandler) -> None: ...

    async def stop(self) -> None: ...

    async def send_reply(self, msg: UnifiedMessage, content: str) -> None: ...

    async def send_fallback(self, msg: UnifiedMessage, reason: str) -> None: ...

    async def stream_reply(
        self, msg: UnifiedMessage, stream_id: str, content: str, finish: bool
    ) -> None: ...

    def health(self) -> AdapterHealth: ...
