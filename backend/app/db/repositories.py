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
from app.db.entities import ConversationRecord, DocumentRecord, QAPair, ToolCallEntry

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

_UPSERT_DOCUMENT = """
INSERT INTO documents (
  doc_id, filename, ext, byte_size, content_hash, status, chunk_count, version,
  error_message, storage_path, created_at, updated_at, indexed_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(doc_id) DO UPDATE SET
  filename=excluded.filename,
  ext=excluded.ext,
  byte_size=excluded.byte_size,
  content_hash=excluded.content_hash,
  status=excluded.status,
  chunk_count=excluded.chunk_count,
  version=excluded.version,
  error_message=excluded.error_message,
  storage_path=excluded.storage_path,
  created_at=excluded.created_at,
  updated_at=excluded.updated_at,
  indexed_at=excluded.indexed_at
"""


def _document_from_row(row: object) -> DocumentRecord:
    """sqlite3.Row 转内部实体，集中维护字段映射。"""
    return DocumentRecord(
        doc_id=row["doc_id"],  # type: ignore[index]
        filename=row["filename"],  # type: ignore[index]
        ext=row["ext"],  # type: ignore[index]
        byte_size=row["byte_size"],  # type: ignore[index]
        content_hash=row["content_hash"],  # type: ignore[index]
        status=row["status"],  # type: ignore[index]
        chunk_count=row["chunk_count"],  # type: ignore[index]
        version=row["version"],  # type: ignore[index]
        error_message=row["error_message"],  # type: ignore[index]
        storage_path=row["storage_path"],  # type: ignore[index]
        created_at=row["created_at"],  # type: ignore[index]
        updated_at=row["updated_at"],  # type: ignore[index]
        indexed_at=row["indexed_at"],  # type: ignore[index]
    )


class DocumentRepo:
    """documents 表 CRUD；业务层负责原文件和向量的一致性编排。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def find_by_doc_id(self, doc_id: str) -> DocumentRecord | None:
        with closing(connect(self._db_path)) as conn:
            row = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return _document_from_row(row) if row is not None else None

    def upsert(self, record: DocumentRecord) -> None:
        """新增或完整恢复一条记录；替换失败回滚时也走同一原子语句。"""
        values = (
            record.doc_id,
            record.filename,
            record.ext,
            record.byte_size,
            record.content_hash,
            record.status,
            record.chunk_count,
            record.version,
            record.error_message,
            record.storage_path,
            record.created_at,
            record.updated_at,
            record.indexed_at,
        )
        with closing(connect(self._db_path)) as conn, conn:
            conn.execute(_UPSERT_DOCUMENT, values)

    def list_paged(self, *, offset: int = 0, limit: int = 50) -> tuple[list[DocumentRecord], int]:
        """按最近更新时间倒序分页；返回当前页和总数。"""
        if offset < 0 or limit <= 0:
            raise ValueError("offset 必须 >= 0 且 limit 必须 > 0")
        with closing(connect(self._db_path)) as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC, doc_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_document_from_row(row) for row in rows], total

    def mark_indexed(self, doc_id: str, *, chunk_count: int, indexed_at: str | None = None) -> bool:
        timestamp = indexed_at or now_iso()
        sql = (
            "UPDATE documents SET status='indexed', chunk_count=?, error_message=NULL, "
            "indexed_at=?, updated_at=? WHERE doc_id=?"
        )
        with closing(connect(self._db_path)) as conn, conn:
            return conn.execute(sql, (chunk_count, timestamp, timestamp, doc_id)).rowcount == 1

    def mark_failed(self, doc_id: str, error_message: str) -> bool:
        timestamp = now_iso()
        sql = (
            "UPDATE documents SET status='failed', error_message=?, updated_at=? "
            "WHERE doc_id=?"
        )
        with closing(connect(self._db_path)) as conn, conn:
            return conn.execute(sql, (error_message, timestamp, doc_id)).rowcount == 1

    def delete(self, doc_id: str) -> bool:
        with closing(connect(self._db_path)) as conn, conn:
            return conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,)).rowcount == 1


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
