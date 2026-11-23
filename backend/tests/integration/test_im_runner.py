"""IM 适配层集成测试（TDD §4.6.2 / §4.6.3 / §4.6.4）。

覆盖 runner 的四条硬契约：
1. 过滤层（空文本、群聊非 @、白名单、幂等去重）不消耗 Agent；
2. 流式回写是**累计全文**，同一 stream_id 原地刷新，finish=True 恰好一次；
3. 终局用 done.reply 替换已发内容（L3 引用撤回与降级分级话术都靠这条）；
4. 超时不取消任务、内部异常也要给用户一句带 request_id 的话。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

import pytest

from app.agent.events import AgentEvent, AgentReply, ToolCallRecord
from app.agent.orchestrator import Orchestrator
from app.agent.refusal import NOT_FOUND_REPLY
from app.config import Settings
from app.im.base import AdapterHealth, ConversationType, MessageHandler, Platform, UnifiedMessage
from app.im.formatter import DEGRADED_TEXTS, format_final_reply
from app.im.runner import FALLBACK_INTERNAL, FALLBACK_TIMEOUT, IMRunner
from app.im.session import MessageDeduper
from app.llm.mock import MockLLMProvider, auth_error
from app.services.agent_service import AgentService, AskContext
from app.tools import build_tool_registry

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


@dataclass(frozen=True, slots=True)
class StreamCall:
    """一次 stream_reply 调用的快照。"""

    stream_id: str
    content: str
    finish: bool


class FakeAdapter:
    """记录全部回写调用的替身适配器（形状对齐 IMAdapter Protocol）。"""

    platform = "wecom"

    def __init__(self) -> None:
        self.stream_calls: list[StreamCall] = []
        self.replies: list[str] = []
        self.fallbacks: list[str] = []

    async def start(self, on_message: MessageHandler) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        return None

    async def send_reply(self, msg: UnifiedMessage, content: str) -> None:
        self.replies.append(content)

    async def send_fallback(self, msg: UnifiedMessage, reason: str) -> None:
        self.fallbacks.append(reason)

    async def stream_reply(
        self, msg: UnifiedMessage, stream_id: str, content: str, finish: bool
    ) -> None:
        self.stream_calls.append(StreamCall(stream_id, content, finish))

    def health(self) -> AdapterHealth:
        return AdapterHealth(enabled=True, connected=True)

    @property
    def final_text(self) -> str:
        """收尾帧内容。顺带断言 finish=True 恰好一次（多发或漏发都会让客户端转圈）。"""
        finished = [call for call in self.stream_calls if call.finish]
        assert len(finished) == 1, f"finish=True 必须恰好一次，实际 {len(finished)} 次"
        return finished[0].content


class ScriptedService:
    """按脚本吐 AgentEvent 的假 AgentService。

    delay_s 用来构造「回复超时」场景；error 用来构造内部异常兜底场景。
    """

    def __init__(
        self,
        events: Sequence[AgentEvent] = (),
        *,
        delay_s: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._events = list(events)
        self._delay_s = delay_s
        self._error = error
        self.questions: list[str] = []
        self.contexts: list[AskContext] = []

    async def ask(self, question: str, context: AskContext) -> AsyncIterator[AgentEvent]:
        self.questions.append(question)
        self.contexts.append(context)
        for event in self._events:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield event
        if self._error is not None:
            raise self._error


def build_reply(
    answer: str,
    *,
    status: str = "ok",
    citations: Sequence[dict[str, object]] = (),
    tool_calls: Sequence[ToolCallRecord] = (),
    refused_by: str | None = None,
    latency_ms: float = 1200.0,
) -> AgentReply:
    """构造终局 reply。status 用 str 传参，运行时由 dataclass 原样接收。"""
    return AgentReply(
        answer=answer,
        status=status,  # type: ignore[arg-type]
        citations=tuple(citations),
        tool_calls=tuple(tool_calls),
        refused_by=refused_by,
        latency_ms=latency_ms,
    )


def done_event(reply: AgentReply) -> AgentEvent:
    return AgentEvent(kind="done", reply=reply)


def build_message(
    text: str = "查一下张三上周的考勤",
    *,
    platform: Platform = "wecom",
    message_id: str = "msg_1",
    conversation_id: str = "chat_1",
    conversation_type: ConversationType = "group",
    user_id: str = "u001",
    is_at_bot: bool = True,
) -> UnifiedMessage:
    return UnifiedMessage(
        platform=platform,
        message_id=message_id,
        conversation_id=conversation_id,
        conversation_type=conversation_type,
        user_id=user_id,
        user_name="张三",
        text=text,
        is_at_bot=is_at_bot,
        raw={"headers": {"req_id": message_id}},
    )


def build_runner(
    settings: Settings,
    service: object,
    adapter: FakeAdapter,
    deduper: MessageDeduper | None = None,
) -> IMRunner:
    return IMRunner(
        settings=settings,
        service=service,  # type: ignore[arg-type]
        adapter=adapter,
        deduper=deduper or MessageDeduper(),
    )


@pytest.fixture()
def im_settings(mock_settings: Settings) -> Settings:
    """把攒批粒度调到 4 字，测试里才能观察到多次流式刷新。"""
    return mock_settings.model_copy(update={"im_stream_chunk_chars": 4})


# ── 过滤层：不该消耗 Agent 的四种消息 ──────────────────────────────


async def test_blank_text_is_ignored(im_settings: Settings) -> None:
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("不该出现"))])

    await build_runner(im_settings, service, adapter).handle(build_message(text="   "))

    assert service.questions == []
    assert adapter.stream_calls == []
    assert adapter.fallbacks == []


async def test_group_message_without_mention_is_ignored(im_settings: Settings) -> None:
    """群聊里没 @ 机器人的消息直接丢弃（FR-4-06）。"""
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("不该出现"))])

    await build_runner(im_settings, service, adapter).handle(build_message(is_at_bot=False))

    assert service.questions == []
    assert adapter.stream_calls == []


async def test_whitelist_blocks_unknown_wecom_user(im_settings: Settings) -> None:
    settings = im_settings.model_copy(update={"wecom_allowed_user_ids": ["u999"]})
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("不该出现"))])
    runner = build_runner(settings, service, adapter)

    await runner.handle(build_message(user_id="u001"))
    await runner.handle(build_message(user_id="u999", message_id="msg_2"))

    assert service.questions == ["查一下张三上周的考勤"]
    assert len(service.contexts) == 1
    assert service.contexts[0].user_id == "u999"


async def test_web_platform_bypasses_wecom_whitelist(im_settings: Settings) -> None:
    """白名单只管企微；Web 后台走自己的登录鉴权（FR-4-10）。"""
    settings = im_settings.model_copy(update={"wecom_allowed_user_ids": ["u999"]})
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("Web 回答"))])

    await build_runner(settings, service, adapter).handle(
        build_message(platform="web", user_id="admin", conversation_type="p2p", is_at_bot=False)
    )

    assert service.questions == ["查一下张三上周的考勤"]
    assert service.contexts[0].platform == "web"


async def test_duplicate_message_answered_once(im_settings: Settings) -> None:
    """IM 网关秒级重推同一条消息时只答一次（FR-4-11）。"""
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("只答一次"))])
    runner = build_runner(im_settings, service, adapter, MessageDeduper())

    await runner.handle(build_message(message_id="dup-1"))
    await runner.handle(build_message(message_id="dup-1"))

    assert len(service.questions) == 1


async def test_session_key_is_three_part_tuple(im_settings: Settings) -> None:
    """题目 7.3 考点：session_key 必须含 user_id，同群两人上下文不串。"""
    adapter = FakeAdapter()
    service = ScriptedService([done_event(build_reply("好"))])
    runner = build_runner(im_settings, service, adapter)

    await runner.handle(build_message(user_id="u001", message_id="m1"))
    await runner.handle(build_message(user_id="u002", message_id="m2"))

    assert [context.session_key for context in service.contexts] == [
        "wecom:chat_1:u001",
        "wecom:chat_1:u002",
    ]


# ── 流式回写与终局替换语义 ────────────────────────────────────────


async def test_stream_replies_are_cumulative_with_single_finish(im_settings: Settings) -> None:
    reply = build_reply("张三上周出勤 5 天。")
    events = [
        AgentEvent(kind="delta", text="张三上周"),
        AgentEvent(kind="delta", text="出勤 5 天。"),
        done_event(reply),
    ]
    adapter = FakeAdapter()

    await build_runner(im_settings, ScriptedService(events), adapter).handle(build_message())

    lengths = [len(call.content) for call in adapter.stream_calls]
    assert lengths == sorted(lengths), "content 是累计全文，长度必须单调不减"
    assert len({call.stream_id for call in adapter.stream_calls}) == 1, "同一轮必须复用 stream_id"
    assert adapter.final_text == format_final_reply(reply)
    assert "⏱ 耗时" in adapter.final_text


async def test_tool_call_progress_is_flushed_then_replaced(im_settings: Settings) -> None:
    """工具调用要立刻让用户看到进度；终局帧换成权威答案，进度提示不残留。"""
    record = ToolCallRecord(
        round=1,
        tool_name="search_attendance",
        arguments="{}",
        result_summary="5 条记录",
        ok=True,
        latency_ms=12.0,
    )
    events = [
        AgentEvent(kind="tool_call", tool_call=record),
        done_event(build_reply("张三上周出勤 5 天。")),
    ]
    adapter = FakeAdapter()

    await build_runner(im_settings, ScriptedService(events), adapter).handle(build_message())

    progress = [call for call in adapter.stream_calls if "🔍 正在调用工具" in call.content]
    assert progress, "工具调用进度必须立刻刷给用户"
    assert "search_attendance" in progress[0].content
    assert "🔍" not in adapter.final_text
    assert adapter.final_text.startswith("张三上周出勤 5 天。")


async def test_final_reply_replaces_fabricated_citation_answer(im_settings: Settings) -> None:
    """L3 引用后校验撤回答案：已推出去的 delta 必须被终局文本整体替换。"""
    events = [
        AgentEvent(kind="delta", text="根据员工手册[9]，年假有 30 天。"),
        done_event(build_reply(NOT_FOUND_REPLY, status="refused", refused_by="citation_check")),
    ]
    adapter = FakeAdapter()

    await build_runner(im_settings, ScriptedService(events), adapter).handle(build_message())

    assert any("30 天" in call.content for call in adapter.stream_calls)
    assert NOT_FOUND_REPLY in adapter.final_text
    assert "30 天" not in adapter.final_text
    assert "[9]" not in adapter.final_text


async def test_long_reply_splits_at_citation_boundary(im_settings: Settings) -> None:
    """超过企微 4000 字上限时在引用条目边界分段，后续段落走 send_reply。"""
    settings = im_settings.model_copy(update={"wecom_max_reply_chars": 60})
    citations = [{"filename": "员工手册.md", "heading_path": ["假期制度"], "snippet": "年假说明"}]
    reply = build_reply("年假满 1 年 10 天，满 5 年 15 天。", citations=citations)
    adapter = FakeAdapter()

    await build_runner(settings, ScriptedService([done_event(reply)]), adapter).handle(
        build_message()
    )

    segments = [adapter.final_text, *adapter.replies]
    assert len(segments) > 1, "超长回复必须分段"
    assert all(len(segment) <= 60 for segment in segments)
    joined = "\n\n".join(segments)
    assert "📎 **来源**" in joined
    assert "1. 员工手册.md › 假期制度" in joined


# ── 兜底路径 ──────────────────────────────────────────────────────


async def test_auth_error_degrades_to_api_key_hint(im_settings: Settings) -> None:
    """Q10 主场景端到端：无效 Key → 「请联系管理员检查 API Key 配置」。"""
    orchestrator = Orchestrator(
        llm=MockLLMProvider([auth_error()]),
        registry=build_tool_registry(im_settings),
        max_tool_rounds=im_settings.agent_max_tool_rounds,
        history_max_turns=im_settings.agent_history_max_turns,
    )
    service = AgentService(settings=im_settings, orchestrator=orchestrator)
    adapter = FakeAdapter()

    await build_runner(im_settings, service, adapter).handle(
        build_message(text="员工每年有几天年假？")
    )

    assert DEGRADED_TEXTS["llm:LLMAuthError"] in adapter.final_text
    assert "请联系管理员检查 API Key 配置" in adapter.final_text
    assert adapter.fallbacks == []


async def test_timeout_sends_notice_and_task_still_finishes(im_settings: Settings) -> None:
    """硬超时先发提示但**不取消任务**；任务跑完的收尾帧覆盖掉提示。"""
    settings = im_settings.model_copy(update={"im_reply_timeout_s": 0.2})
    events = [
        AgentEvent(kind="delta", text="慢答案"),
        done_event(build_reply("这是最终答案。")),
    ]
    adapter = FakeAdapter()

    await build_runner(settings, ScriptedService(events, delay_s=0.3), adapter).handle(
        build_message()
    )
    assert adapter.fallbacks == [FALLBACK_TIMEOUT]

    await asyncio.sleep(1.0)

    assert "这是最终答案。" in adapter.final_text
    assert FALLBACK_TIMEOUT not in adapter.final_text


async def test_internal_error_fallback_carries_request_id(im_settings: Settings) -> None:
    """内部异常也要给用户一句话，并带上可检索的日志编号。"""
    adapter = FakeAdapter()
    service = ScriptedService(error=RuntimeError("boom"))

    await build_runner(im_settings, service, adapter).handle(build_message())

    assert len(adapter.fallbacks) == 1
    notice = adapter.fallbacks[0]
    assert notice.startswith("🙇")
    assert "编号 " in notice
    assert "{request_id}" not in notice
    assert notice != FALLBACK_INTERNAL
    assert adapter.stream_calls == []
