"""AgentService 集成测试（CURRENT_PLAN M3：会话持久化 + 多轮上下文）。

只验证服务层在编排器之外新增的三件事：读历史拼进 prompt、透传事件流、
done 之后把整轮问答与工具链落库。编排器自身行为已由 test_agent_with_mock_llm
与 test_agent_degrade 覆盖，这里不重复。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from app.agent.events import AgentEvent
from app.agent.orchestrator import build_orchestrator
from app.config import Settings
from app.db.engine import connect
from app.db.entities import ConversationRecord
from app.db.repositories import ConversationRepo
from app.llm.base import ChatProvider
from app.llm.mock import MockLLMProvider, auth_error, text_reply, tool_reply
from app.services.agent_service import AgentService, AskContext
from app.tools import build_tool_registry

pytestmark = pytest.mark.allow_hosts("127.0.0.1")

SESSION_KEY = "wecom:chat_1:user_1"

BuildService = Callable[[ChatProvider], AgentService]


@pytest.fixture()
def build_service(mock_settings: Settings) -> BuildService:
    """按给定 provider 组装 AgentService。知识库检索器留空，M4 才接真向量库。"""

    def _build(provider: ChatProvider) -> AgentService:
        registry = build_tool_registry(mock_settings)
        orchestrator = build_orchestrator(mock_settings, provider, registry)
        return AgentService(settings=mock_settings, orchestrator=orchestrator)

    return _build


def make_context(session_key: str = SESSION_KEY) -> AskContext:
    """IM runner 会组装的上下文，测试里写死同一份便于断言落库字段。"""
    return AskContext(
        session_key=session_key,
        platform="wecom",
        user_id="user_1",
        user_name="张三",
        request_id="req_test",
    )


async def ask(service: AgentService, question: str, context: AskContext) -> list[AgentEvent]:
    """把事件流抽干。落库发生在 done 事件那一刻，必须消费完整条流。"""
    return [event async for event in service.ask(question, context)]


def fetch_rows(db_path: Path, sql: str) -> list[dict[str, object]]:
    """直接查库，绕开 Repository，确保断言的是真正落盘的内容。"""
    with closing(connect(db_path)) as conn:
        return [dict(row) for row in conn.execute(sql).fetchall()]


async def test_ask_persists_conversation_row(
    build_service: BuildService, mock_settings: Settings
) -> None:
    """一轮问答落一行，字段与 reply 一致（后台全量对话日志的数据源，FR-5-01）。"""
    service = build_service(MockLLMProvider([text_reply("你好，我是小苏。")]))

    events = await ask(service, "你好", make_context())
    reply = events[-1].reply
    assert reply is not None

    rows = fetch_rows(mock_settings.sqlite_path, "SELECT * FROM conversations")
    assert len(rows) == 1
    row = rows[0]
    assert row["question"] == "你好"
    assert row["answer"] == "你好，我是小苏。"
    assert row["status"] == "ok"
    assert row["session_key"] == SESSION_KEY
    assert row["platform"] == "wecom"
    assert row["user_name"] == "张三"
    assert row["request_id"] == "req_test"
    assert row["model"] == "mock-llm"
    assert row["total_tokens"] == reply.usage.total_tokens
    assert row["latency_ms"] >= 0


async def test_second_turn_sees_previous_qa_in_prompt(build_service: BuildService) -> None:
    """多轮上下文来自库而非进程内存（FR-2-04/06）：第二次请求的 messages 里
    必须出现上一轮的用户问句与小苏的回答。"""
    provider = MockLLMProvider([text_reply("第一个回答"), text_reply("第二个回答")])
    service = build_service(provider)
    context = make_context()

    await ask(service, "第一个问题", context)
    await ask(service, "第二个问题", context)

    first_turn = [(item.role, item.content) for item in provider.calls[0]["messages"]]
    second_turn = [(item.role, item.content) for item in provider.calls[1]["messages"]]
    # 第一轮库里还没有任何问答对，prompt 只有系统提示词 + 当前问句
    assert [role for role, _ in first_turn] == ["system", "user"]
    assert first_turn[-1] == ("user", "第一个问题")
    assert ("user", "第一个问题") in second_turn
    assert ("assistant", "第一个回答") in second_turn
    assert second_turn[-1] == ("user", "第二个问题")


async def test_history_is_isolated_between_sessions(build_service: BuildService) -> None:
    """同群不同人不共享上下文（FR-4-04）。"""
    provider = MockLLMProvider([text_reply("a"), text_reply("b")])
    service = build_service(provider)

    await ask(service, "张三的问题", make_context("wecom:chat_1:user_1"))
    await ask(service, "李四的问题", make_context("wecom:chat_1:user_2"))

    second_turn = [(item.role, item.content) for item in provider.calls[1]["messages"]]
    assert ("user", "张三的问题") not in second_turn
    assert second_turn[-1] == ("user", "李四的问题")


async def test_history_respects_max_turns(
    build_service: BuildService, mock_settings: Settings
) -> None:
    """历史窗口有上限，超出只取最近 N 轮，避免 prompt 无限膨胀（FR-2-06）。"""
    service = build_service(MockLLMProvider())
    repo = ConversationRepo(mock_settings.sqlite_path)
    limit = mock_settings.agent_history_max_turns
    for index in range(limit + 5):
        repo.insert(
            ConversationRecord(
                request_id=f"req_{index}",
                session_key=SESSION_KEY,
                platform="wecom",
                user_id="user_1",
                question=f"q{index}",
                answer=f"a{index}",
                created_at="2026-11-24T00:00:00+00:00",
            )
        )

    messages = service.history(SESSION_KEY)
    assert len(messages) == limit * 2
    assert messages[0].content == "q5"
    assert messages[-1].content == f"a{limit + 4}"


async def test_tool_calls_are_persisted(
    build_service: BuildService, mock_settings: Settings
) -> None:
    """工具链留痕与对话同批落库，后台能重现「模型调了什么、结果如何」。"""
    provider = MockLLMProvider(
        [tool_reply(("get_current_time", {})), text_reply("现在是工作时间。")]
    )
    service = build_service(provider)

    await ask(service, "现在几点", make_context())

    conversations = fetch_rows(mock_settings.sqlite_path, "SELECT id FROM conversations")
    calls = fetch_rows(mock_settings.sqlite_path, "SELECT * FROM tool_calls")
    assert len(calls) == 1
    assert calls[0]["conversation_id"] == conversations[0]["id"]
    assert calls[0]["tool_name"] == "get_current_time"
    assert calls[0]["round"] == 1
    assert calls[0]["ok"] == 1
    assert calls[0]["result_summary"]


async def test_degraded_reply_records_refused_by(
    build_service: BuildService, mock_settings: Settings
) -> None:
    """上游鉴权失败也要留痕（Q10 的可观测面：后台能筛出所有降级轮次）。"""
    service = build_service(MockLLMProvider([auth_error()]))

    await ask(service, "年假有几天", make_context())

    rows = fetch_rows(mock_settings.sqlite_path, "SELECT * FROM conversations")
    assert rows[0]["status"] == "degraded"
    assert rows[0]["refused_by"] == "llm:LLMAuthError"


async def test_persist_failure_does_not_break_the_stream(
    build_service: BuildService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落库失败只记日志：答案已经推给用户了，此时抛异常只会把小故障放大。"""

    def boom(self: ConversationRepo, record: ConversationRecord) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ConversationRepo, "insert", boom)
    service = build_service(MockLLMProvider([text_reply("回答照常给出")]))

    events = await ask(service, "你好", make_context())

    assert events[-1].kind == "done"
    assert events[-1].reply is not None
    assert events[-1].reply.answer == "回答照常给出"
