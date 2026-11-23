"""session_key 与幂等去重（TDD §4.6.2 / FR-4-04 / FR-4-11）。

session_key 三元组 platform:conversation_id:user_id 是题目 7.3 的明确考点：
同群两人问同一句话上下文不能串，同一人跨群也要隔离。
"""

from __future__ import annotations

from collections import OrderedDict

from app.db.repositories import MessageRepo
from app.im.base import UnifiedMessage


def build_session_key(platform: str, conversation_id: str, user_id: str) -> str:
    """三元组拼接。user_id 必须进 key，否则同群两人共享上下文（TDD §4.6.2）。"""
    return f"{platform}:{conversation_id}:{user_id}"


def session_key_of(msg: UnifiedMessage) -> str:
    """从统一消息直接取 key。Web 后台入口用 build_session_key 自行拼装。"""
    return build_session_key(msg.platform, msg.conversation_id, msg.user_id)


class MessageDeduper:
    """双层幂等：内存 LRU 挡短窗口重推，SQLite 挡进程重启后的重放。

    IM 网关可能在秒级内重推同一条消息（ACK 丢失时的标准行为），每次都打
    SQLite 太浪费；但只靠内存层，进程重启后重放的消息会被当成新消息再答
    一遍。两层各司其职，DB 层保证 at-most-once 的最终语义。
    """

    def __init__(self, *, repo: MessageRepo | None = None, memory_limit: int = 1024) -> None:
        self._repo = repo
        self._memory_limit = max(1, memory_limit)
        self._seen: OrderedDict[str, bool] = OrderedDict()

    def is_duplicate(self, message_id: str, platform: str) -> bool:
        """首次见到返回 False 并占用；重复返回 True。"""
        key = f"{platform}:{message_id}"
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        if self._repo is not None and not self._repo.try_claim(message_id, platform):
            self._remember(key)
            return True
        self._remember(key)
        return False

    def _remember(self, key: str) -> None:
        self._seen[key] = True
        while len(self._seen) > self._memory_limit:
            self._seen.popitem(last=False)
