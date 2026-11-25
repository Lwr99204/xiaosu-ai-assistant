"""SQLite 会话存储集成测试（TDD §4.7）。

覆盖四件事：建表幂等、问答对读回的顺序与轮数上限、工具链留痕的截断，
以及 IM 幂等去重的持久层抢占语义。库文件全部落在 tmp_path，不碰仓库 storage/。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from app.db.engine import connect, init_db
from app.db.entities import ConversationRecord, DocumentRecord, ToolCallEntry
from app.db.repositories import RESULT_SUMMARY_MAX, ConversationRepo, DocumentRepo, MessageRepo

pytestmark = pytest.mark.allow_hosts("127.0.0.1")

#: schema.sql 建的四张表。缺任何一张都会让对应功能在运行期才炸。
EXPECTED_TABLES = ("documents", "conversations", "tool_calls", "processed_messages")

SESSION_KEY = "wecom:chat_1:user_1"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """临时库路径（已建表）。"""
    path = tmp_path / "storage" / "test.db"
    init_db(path)
    return path


def make_record(
    question: str, answer: str | None, *, session_key: str = SESSION_KEY
) -> ConversationRecord:
    """构造一行问答记录，只留必填项，其余走实体默认值。"""
    return ConversationRecord(
        request_id="req_test",
        session_key=session_key,
        platform="wecom",
        user_id="user_1",
        user_name="张三",
        question=question,
        answer=answer,
        created_at="2026-11-24T00:00:00+00:00",
    )


def make_document(
    doc_id: str, *, version: int = 1, updated_at: str = "2026-11-24T00:00:00+00:00"
) -> DocumentRecord:
    return DocumentRecord(
        doc_id=doc_id,
        filename=doc_id,
        ext="md",
        byte_size=12,
        content_hash=f"hash-{version}",
        status="pending",
        chunk_count=0,
        version=version,
        storage_path=f"storage/uploads/{doc_id}",
        created_at="2026-11-24T00:00:00+00:00",
        updated_at=updated_at,
    )


def test_init_db_creates_all_tables_idempotently(db_path: Path) -> None:
    """重复建表不报错（IF NOT EXISTS），且四张表都在。"""
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert set(EXPECTED_TABLES) <= {row["name"] for row in rows}


def test_connection_pragmas_enabled(db_path: Path) -> None:
    """WAL 让读写不互斥；foreign_keys 必须显式打开，否则级联删除是空话。"""
    with closing(connect(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1


def test_document_repo_crud_and_status_transitions(db_path: Path) -> None:
    repo = DocumentRepo(db_path)
    record = make_document("handbook.md")

    assert repo.find_by_doc_id(record.doc_id) is None
    repo.upsert(record)
    assert repo.find_by_doc_id(record.doc_id) == record

    assert repo.mark_indexed(record.doc_id, chunk_count=7, indexed_at="2026-11-24T01:00:00+00:00")
    indexed = repo.find_by_doc_id(record.doc_id)
    assert indexed is not None
    assert (indexed.status, indexed.chunk_count, indexed.error_message) == ("indexed", 7, None)

    assert repo.mark_failed(record.doc_id, "embedding down")
    failed = repo.find_by_doc_id(record.doc_id)
    assert failed is not None
    assert (failed.status, failed.error_message) == ("failed", "embedding down")

    assert repo.delete(record.doc_id) is True
    assert repo.delete(record.doc_id) is False
    assert repo.find_by_doc_id(record.doc_id) is None


def test_document_repo_upsert_restores_previous_record(db_path: Path) -> None:
    """替换失败时，业务层可用一次 upsert 原子恢复完整旧记录。"""
    repo = DocumentRepo(db_path)
    original = make_document("handbook.md")
    replacement = make_document("handbook.md", version=2)
    repo.upsert(original)
    repo.upsert(replacement)
    assert repo.find_by_doc_id("handbook.md") == replacement
    repo.upsert(original)
    assert repo.find_by_doc_id("handbook.md") == original


def test_document_repo_lists_newest_first_with_total(db_path: Path) -> None:
    repo = DocumentRepo(db_path)
    repo.upsert(make_document("old.md", updated_at="2026-11-24T00:00:00+00:00"))
    repo.upsert(make_document("new.md", updated_at="2026-11-24T02:00:00+00:00"))

    items, total = repo.list_paged(offset=0, limit=1)
    assert total == 2
    assert [item.doc_id for item in items] == ["new.md"]

    with pytest.raises(ValueError):
        repo.list_paged(limit=0)


def test_recent_pairs_keeps_chronological_order_and_limit(db_path: Path) -> None:
    """读回的是最近 N 轮，且按时间正序（拼进 prompt 的顺序不能反）。"""
    repo = ConversationRepo(db_path)
    for index in range(5):
        repo.insert(make_record(f"q{index}", f"a{index}"))

    pairs = repo.recent_pairs(SESSION_KEY, 3)
    assert [(pair.question, pair.answer) for pair in pairs] == [
        ("q2", "a2"),
        ("q3", "a3"),
        ("q4", "a4"),
    ]


def test_recent_pairs_skips_blank_answers_and_other_sessions(db_path: Path) -> None:
    """半截记录（answer 为空/NULL）与他人会话都不能进上下文（FR-4-04）。"""
    repo = ConversationRepo(db_path)
    repo.insert(make_record("q1", "a1"))
    repo.insert(make_record("q2", None))
    repo.insert(make_record("q3", ""))
    repo.insert(make_record("other", "x", session_key="wecom:chat_2:user_9"))

    assert [pair.question for pair in repo.recent_pairs(SESSION_KEY, 10)] == ["q1"]
    assert repo.recent_pairs(SESSION_KEY, 0) == []


def test_tool_call_result_summary_is_truncated(db_path: Path) -> None:
    """工具输出留痕截断到 500 字，防止一条大 JSON 把日志表撑爆（FR-3-09）。"""
    repo = ConversationRepo(db_path)
    conversation_id = repo.insert(make_record("q", "a"))
    repo.insert_tool_calls(
        conversation_id,
        [
            ToolCallEntry(
                round=1,
                tool_name="calculator",
                arguments_json='{"expression": "1+1"}',
                result_summary="x" * (RESULT_SUMMARY_MAX + 50),
                ok=True,
                latency_ms=12,
            )
        ],
    )

    calls = repo.tool_calls_of(conversation_id)
    assert len(calls) == 1
    assert len(calls[0].result_summary) == RESULT_SUMMARY_MAX
    assert calls[0].ok is True
    assert calls[0].latency_ms == 12


def test_insert_tool_calls_is_noop_when_empty(db_path: Path) -> None:
    """纯闲聊没有工具调用，不应写入任何行。"""
    repo = ConversationRepo(db_path)
    conversation_id = repo.insert(make_record("q", "a"))
    repo.insert_tool_calls(conversation_id, [])
    assert repo.tool_calls_of(conversation_id) == []


def test_tool_calls_follow_round_order(db_path: Path) -> None:
    """后台「工具调用过程」按轮次展示，读回顺序必须稳定（FR-5-04）。"""
    repo = ConversationRepo(db_path)
    conversation_id = repo.insert(make_record("q", "a"))
    repo.insert_tool_calls(
        conversation_id,
        [
            ToolCallEntry(
                round=index,
                tool_name=f"tool_{index}",
                arguments_json="{}",
                result_summary="ok",
                ok=True,
            )
            for index in (1, 2, 3)
        ],
    )
    assert [call.round for call in repo.tool_calls_of(conversation_id)] == [1, 2, 3]


def test_message_claim_succeeds_once(db_path: Path) -> None:
    """幂等去重的持久层：同一 message_id 只有第一次抢占成功（FR-4-11）。"""
    repo = MessageRepo(db_path)
    assert repo.try_claim("msg_1", "wecom") is True
    assert repo.try_claim("msg_1", "wecom") is False
    assert repo.try_claim("msg_2", "wecom") is True


def test_deleting_conversation_cascades_to_tool_calls(db_path: Path) -> None:
    """外键级联删除生效：清理旧对话不会留下孤儿工具记录。"""
    repo = ConversationRepo(db_path)
    conversation_id = repo.insert(make_record("q", "a"))
    repo.insert_tool_calls(
        conversation_id,
        [
            ToolCallEntry(
                round=1, tool_name="calculator", arguments_json="{}", result_summary="2", ok=True
            )
        ],
    )

    with closing(connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        remaining = conn.execute("SELECT COUNT(*) AS total FROM tool_calls").fetchone()["total"]

    assert remaining == 0


def test_conversation_admin_queries_filter_paginate_and_aggregate(db_path: Path) -> None:
    repo = ConversationRepo(db_path)
    first = make_record("知识库问题", "带引用回答")
    first.platform = "wecom"
    first.total_tokens = 120
    first.cost_cny = 0.12
    first_id = repo.insert(first)
    repo.insert_tool_calls(
        first_id,
        [ToolCallEntry(1, "search_knowledge_base", "{}", "命中员工手册", True)],
    )
    second = make_record("网页问题", "普通回答", session_key="web:s1:admin")
    second.platform = "web"
    second.user_id = "admin"
    second.total_tokens = 80
    second.cost_cny = 0.08
    second_id = repo.insert(second)

    items, total = repo.list_paged(platform="wecom", has_tool_call=True)
    assert total == 1
    assert [item.id for item in items] == [first_id]
    assert repo.find_by_id(second_id) is not None
    assert repo.find_by_id(9999) is None

    no_tools, no_tools_total = repo.list_paged(has_tool_call=False)
    assert no_tools_total == 1
    assert no_tools[0].id == second_id

    stats = repo.stats()
    assert (stats.count, stats.tokens, stats.cost_cny) == (2, 200, pytest.approx(0.2))
