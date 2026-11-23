"""IM 归一化、会话键、幂等去重与回复格式化的单元测试。

对应 TDD §4.6.1（企微帧解析）/ §4.6.2（session_key 与去重）/ §4.6.4（回复模板）。
企微 SDK 没有公开文档化 body 里的会话字段，parse_message 用多候选键名做防御性
解析，这里的用例把每个候选分支都钉住，真机校准后改字段不会静默改行为。
"""

from __future__ import annotations

import pytest

from app.agent.events import AgentReply
from app.im.base import UnifiedMessage
from app.im.formatter import (
    DEGRADED_TEXTS,
    degraded_answer_text,
    format_citations,
    format_final_reply,
    split_reply,
)
from app.im.session import MessageDeduper, build_session_key, session_key_of
from app.im.wecom import parse_message, strip_mentions


def text_frame(content: str, **body_extra: object) -> dict[str, object]:
    """构造一个企微文本帧。body_extra 用来覆盖/追加会话字段。"""
    body: dict[str, object] = {
        "msgtype": "text",
        "text": {"content": content},
        "from": {"userid": "u001", "name": "张三"},
        "msgid": "msg_77",
    }
    body.update(body_extra)
    return {"headers": {"req_id": "req_1"}, "body": body}


# ── session_key ───────────────────────────────────────────────────


def test_build_session_key_is_three_part_tuple() -> None:
    """题目 7.3：platform:conversation_id:user_id 三元组。"""
    assert build_session_key("wecom", "chat_9", "u001") == "wecom:chat_9:u001"


def test_same_group_different_users_get_different_keys() -> None:
    first = build_session_key("wecom", "chat_9", "u001")
    second = build_session_key("wecom", "chat_9", "u002")
    assert first != second


def test_session_key_of_reads_unified_message() -> None:
    msg = UnifiedMessage(
        platform="wecom",
        message_id="m1",
        conversation_id="chat_9",
        conversation_type="group",
        user_id="u001",
        user_name="张三",
        text="年假几天",
        is_at_bot=True,
    )
    assert session_key_of(msg) == "wecom:chat_9:u001"


# ── 幂等去重 ──────────────────────────────────────────────────────


def test_first_sighting_is_not_duplicate() -> None:
    deduper = MessageDeduper()
    assert deduper.is_duplicate("m1", "wecom") is False
    assert deduper.is_duplicate("m1", "wecom") is True


def test_same_message_id_on_different_platforms_is_independent() -> None:
    deduper = MessageDeduper()
    assert deduper.is_duplicate("m1", "wecom") is False
    assert deduper.is_duplicate("m1", "web") is False


def test_memory_layer_evicts_oldest_entry() -> None:
    """内存层只是短窗口挡板，逐出后由 SQLite 层兜住最终语义。"""
    deduper = MessageDeduper(memory_limit=2)
    assert deduper.is_duplicate("m1", "wecom") is False
    assert deduper.is_duplicate("m2", "wecom") is False
    assert deduper.is_duplicate("m3", "wecom") is False
    assert deduper.is_duplicate("m1", "wecom") is False
    assert deduper.is_duplicate("m3", "wecom") is True


class ClaimRepo:
    """MessageRepo.try_claim 的替身。allow=False 表示已被别的进程占用。"""

    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.calls: list[tuple[str, str]] = []

    def try_claim(self, message_id: str, platform: str) -> bool:
        self.calls.append((message_id, platform))
        return self.allow


def test_db_layer_blocks_replay_after_restart() -> None:
    repo = ClaimRepo(allow=False)
    deduper = MessageDeduper(repo=repo)

    assert deduper.is_duplicate("m1", "wecom") is True
    assert repo.calls == [("m1", "wecom")]
    # 已判定为重复后走内存层，不再重复打库
    assert deduper.is_duplicate("m1", "wecom") is True
    assert len(repo.calls) == 1


def test_db_layer_allows_first_claim() -> None:
    deduper = MessageDeduper(repo=ClaimRepo(allow=True))
    assert deduper.is_duplicate("m1", "wecom") is False
    assert deduper.is_duplicate("m1", "wecom") is True


# ── @ 提及剔除 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@小苏 查一下年假", "查一下年假"),
        ("@小苏 @张三 查一下年假", "查一下年假"),
        ("@小苏\n查一下年假", "查一下年假"),
        ("查一下年假", "查一下年假"),
        ("@小苏", ""),
        ("  @小苏   年假几天  ", "年假几天"),
    ],
)
def test_strip_mentions(raw: str, expected: str) -> None:
    assert strip_mentions(raw) == expected


# ── 企微帧归一化 ──────────────────────────────────────────────────


def test_parse_group_message_strips_mention_and_marks_at_bot() -> None:
    msg = parse_message(text_frame("@小苏 张三上周考勤", chattype="group", chatid="chat_9"))

    assert msg is not None
    assert msg.platform == "wecom"
    assert msg.conversation_type == "group"
    assert msg.is_at_bot is True
    assert msg.text == "张三上周考勤"
    assert msg.user_id == "u001"
    assert msg.user_name == "张三"
    assert msg.conversation_id == "chat_9"
    assert msg.message_id == "msg_77"
    assert msg.raw["headers"] == {"req_id": "req_1"}


def test_parse_chatroom_alias_is_treated_as_group() -> None:
    msg = parse_message(text_frame("@小苏 你好", chattype="chatroom", chatid="chat_9"))

    assert msg is not None
    assert msg.conversation_type == "group"
    assert msg.is_at_bot is True


def test_parse_p2p_message_derives_conversation_id() -> None:
    msg = parse_message(text_frame("年假几天", chattype="single"))

    assert msg is not None
    assert msg.conversation_type == "p2p"
    assert msg.is_at_bot is False
    assert msg.conversation_id == "p2p_u001"
    assert msg.text == "年假几天"


def test_parse_p2p_message_keeps_at_prefix() -> None:
    """单聊里 @ 是正文的一部分，不能当提及剥掉。"""
    msg = parse_message(text_frame("@小苏 年假几天", chattype="single"))

    assert msg is not None
    assert msg.text == "@小苏 年假几天"


def test_parse_falls_back_to_alternate_field_names() -> None:
    """SDK 字段命名不确定，备选键名必须同样能解析出来。"""
    frame = {
        "headers": {"req_id": "req_9"},
        "body": {
            "msgtype": "text",
            "text": {"content": "年假几天"},
            "from": {"user_id": "u002", "nickname": "李四"},
            "chat_type": "group",
            "chat_id": "chat_8",
            "message_id": "msg_88",
        },
    }
    msg = parse_message(frame)

    assert msg is not None
    assert msg.user_id == "u002"
    assert msg.user_name == "李四"
    assert msg.conversation_id == "chat_8"
    assert msg.message_id == "msg_88"
    assert msg.conversation_type == "group"


def test_parse_uses_from_userid_when_from_block_missing() -> None:
    msg = parse_message(
        {
            "headers": {},
            "body": {
                "text": {"content": "年假几天"},
                "from_userid": "u003",
                "chattype": "single",
            },
        }
    )

    assert msg is not None
    assert msg.user_id == "u003"
    assert msg.conversation_id == "p2p_u003"


def test_parse_message_id_falls_back_to_req_id_then_generated() -> None:
    with_req_id = parse_message(text_frame("年假几天", msgid=None))
    assert with_req_id is not None
    assert with_req_id.message_id == "req_1"

    frame = {"body": {"text": {"content": "年假几天"}, "from": {"userid": "u001"}}}
    generated = parse_message(frame)
    assert generated is not None
    assert generated.message_id.startswith("msg_")


@pytest.mark.parametrize(
    "frame",
    [
        {},
        {"body": "not-a-dict"},
        {"body": {}},
        {"body": {"text": "not-a-dict"}},
        {"body": {"text": {}}},
        {"body": {"text": {"content": "   "}, "from": {"userid": "u001"}}},
        {"body": {"text": {"content": 42}, "from": {"userid": "u001"}}},
        {"body": {"text": {"content": "年假几天"}, "from": {"userid": ""}}},
        {"body": {"text": {"content": "年假几天"}}},
    ],
)
def test_parse_returns_none_for_unusable_frames(frame: dict[str, object]) -> None:
    """非文本帧或定位不到发送者时丢弃：没有 user_id 就拼不出 session_key。"""
    assert parse_message(frame) is None


# ── 回复格式化 ────────────────────────────────────────────────────


def test_format_citations_renders_numbered_entries() -> None:
    rendered = format_citations(
        [
            {
                "filename": "员工手册.md",
                "heading_path": ["假期制度", "年假"],
                "snippet": "满 1 年 10 天",
            }
        ]
    )

    assert rendered.split("\n") == [
        "---",
        "📎 **来源**",
        "1. 员工手册.md › 假期制度 > 年假",
        "   > 满 1 年 10 天",
    ]


def test_format_citations_handles_missing_fields_and_long_snippet() -> None:
    rendered = format_citations([{"snippet": "长" * 200}, {"heading_path": "单一标题"}])
    lines = rendered.split("\n")

    assert lines[2] == "1. 未知来源"
    assert lines[3].endswith("…")
    assert len(lines[3]) < 100
    assert "4. " not in rendered


def test_format_final_reply_appends_citations_and_footer() -> None:
    reply = AgentReply(
        answer="年假满 1 年 10 天。",
        status="ok",
        citations=({"filename": "员工手册.md", "heading_path": ["假期制度"]},),
        latency_ms=1500.0,
    )
    rendered = format_final_reply(reply)

    assert rendered.startswith("年假满 1 年 10 天。\n\n---\n📎 **来源**")
    assert rendered.endswith("tokens")
    assert "⏱ 耗时 1.5s" in rendered or "⏱ 耗时" in rendered


def test_format_final_reply_without_citations_skips_source_block() -> None:
    reply = AgentReply(answer="上周订单 7 笔。", status="ok", latency_ms=800.0)
    rendered = format_final_reply(reply)

    assert "📎" not in rendered
    assert rendered.startswith("上周订单 7 笔。")


def test_format_final_reply_answer_override_wins() -> None:
    """降级分级话术要盖掉编排器的通用兜底文本。"""
    reply = AgentReply(answer="服务暂时不可用", status="degraded", refused_by="llm:LLMAuthError")

    assert format_final_reply(reply, answer_override="检查 API Key").startswith("检查 API Key")


@pytest.mark.parametrize(
    ("status", "refused_by", "expected"),
    [
        ("ok", None, None),
        ("degraded", None, None),
        ("degraded", "llm:LLMAuthError", DEGRADED_TEXTS["llm:LLMAuthError"]),
        ("degraded", "llm:LLMTimeoutError", DEGRADED_TEXTS["llm:LLMTimeoutError"]),
        ("degraded", "tool_rounds_exceeded:5", None),
        ("refused", "privacy:salary", None),
    ],
)
def test_degraded_answer_text(status: str, refused_by: str | None, expected: str | None) -> None:
    reply = AgentReply(answer="兜底", status=status, refused_by=refused_by)  # type: ignore[arg-type]
    assert degraded_answer_text(reply) == expected


def test_auth_error_text_points_at_api_key() -> None:
    """验收题 Q10 的标准答案必须直指 API Key 配置。"""
    assert "API Key" in DEGRADED_TEXTS["llm:LLMAuthError"]


# ── 超长分段 ──────────────────────────────────────────────────────


def test_split_reply_keeps_short_text_intact() -> None:
    assert split_reply("短回复", 4000) == ["短回复"]


def test_split_reply_respects_limit_and_preserves_content() -> None:
    citations = [
        {"filename": f"文档{i}.md", "heading_path": ["章节"], "snippet": "摘要" * 10}
        for i in range(6)
    ]
    reply = AgentReply(answer="答案正文。" * 10, status="ok", citations=tuple(citations))
    text = format_final_reply(reply)

    segments = split_reply(text, 120)

    assert len(segments) > 1
    assert all(len(segment) <= 120 for segment in segments)
    for index in range(1, 7):
        assert f"{index}. 文档{index - 1}.md" in "\n\n".join(segments)


def test_split_reply_hard_cuts_single_oversized_block() -> None:
    segments = split_reply("字" * 250, 100)

    assert len(segments) == 3
    assert all(len(segment) <= 100 for segment in segments)
