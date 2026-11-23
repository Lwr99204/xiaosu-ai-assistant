"""手写 SQL Repository（TDD §4.7）。

每个方法自开短连接（with closing(connect(...))），用完即还：WAL 模式下
读写互不阻塞，短连接规避了跨线程复用同一连接的隐患。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from app.db.engine import connect, now_iso
from app.db.entities import ConversationRecord, QAPair, ToolCallEntry

logger = logging.getLogger(__name__)

#: tool_calls.result_summary 截断上限（TDD §4.7：防日志爆炸）。
RESULT_SUMMARY_MAX = 500

_INSERT_CONVERSATION = """
INSERT INTO conversations (
  request_id, session_key, platform, user_id, user_name, question, answer,
  citations_json, refused_by, status, error_code, model,
  prompt_tokens, completion_tokens, total_tokens, cost_cny, latency_ms, created_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_INSERT_TOOL_CALL = """
INSERT INTO tool_calls (
  conversation_id, round, tool_name, arguments_json, result_summary,
  ok, error, latency_ms, created_at
) VALUES (?,?,?,?,?,?,?,?,?)
"""


class ConversationRepo:
    """conversations + tool_calls 两张表的读写。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def insert(self, record: ConversationRecord) -> int:
        """落一轮问答，返回自增 id（tool_calls 外键要用）。"""
        with closing(connect(self._db_path)) as conn, conn:
            cursor = conn.execute(
                _INSERT_CONVERSATION,
                (
                    record.request_id,
                    record.session_key,
                    record.platform,
                    record.user_id,
                    record.user_name,
                    record.question,
                    record.answer,
                    record.citations_json,
                    record.refused_by,
                    record.status,
                    record.error_code,
                    record.model,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.cost_cny,
                    record.latency_ms,
                    record.created_at,
                ),
            )
            return int(cursor.lastrowid or 0)

    def insert_tool_calls(self, conversation_id: int, calls: Sequence[ToolCallEntry]) -> None:
        """批量落工具调用留痕。result_summary 超长时截断（FR-3-09）。"""
        if not calls:
            return
        created = now_iso()
        rows = [
            (
                conversation_id,
                call.round,
                call.tool_name,
                call.arguments_json,
                call.result_summary[:RESULT_SUMMARY_MAX],
                int(call.ok),
                call.error,
                call.latency_ms,
                created,
            )
            for call in calls
        ]
        with closing(connect(self._db_path)) as conn, conn:
            conn.executemany(_INSERT_TOOL_CALL, rows)

    def recent_pairs(self, session_key: str, max_turns: int) -> list[QAPair]:
        """读会话最近 max_turns 轮问答，按时间正序返回（FR-2-06）。

        只取 answer 非空的行：落库失败或被中断的半截记录不该进上下文。
        """
        if max_turns <= 0:
            return []
        sql = (
            "SELECT question, answer FROM conversations "
            "WHERE session_key = ? AND answer IS NOT NULL AND answer != '' "
            "ORDER BY id DESC LIMIT ?"
        )
        with closing(connect(self._db_path)) as conn:
            rows = conn.execute(sql, (session_key, max_turns)).fetchall()
        return [QAPair(question=row["question"], answer=row["answer"]) for row in reversed(rows)]

    def tool_calls_of(self, conversation_id: int) -> list[ToolCallEntry]:
        """按对话 id 读工具链，后台「工具调用过程」展示用（FR-5-04）。"""
        sql = (
            "SELECT round, tool_name, arguments_json, result_summary, ok, error, latency_ms "
            "FROM tool_calls WHERE conversation_id = ? ORDER BY id"
        )
        with closing(connect(self._db_path)) as conn:
            rows = conn.execute(sql, (conversation_id,)).fetchall()
        return [
            ToolCallEntry(
                round=row["round"],
                tool_name=row["tool_name"],
                arguments_json=row["arguments_json"],
                result_summary=row["result_summary"] or "",
                ok=bool(row["ok"]),
                error=row["error"],
                latency_ms=row["latency_ms"] or 0,
            )
            for row in rows
        ]


class MessageRepo:
    """processed_messages 表：IM 幂等去重的持久层（FR-4-11）。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def try_claim(self, message_id: str, platform: str) -> bool:
        """抢占消息处理权。首次见到返回 True；重复推送返回 False。

        INSERT OR IGNORE 依赖 message_id 主键唯一性，天然原子，
        不需要先 SELECT 再 INSERT 的竞态窗口。
        """
        sql = "INSERT OR IGNORE INTO processed_messages (message_id, platform, created_at) VALUES (?,?,?)"
        with closing(connect(self._db_path)) as conn, conn:
            cursor = conn.execute(sql, (message_id, platform, now_iso()))
            return cursor.rowcount == 1
