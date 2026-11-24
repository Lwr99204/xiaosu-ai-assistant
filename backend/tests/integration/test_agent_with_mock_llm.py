"""Agent 编排器 × MockLLM × 真实工具注册的集成测试。

覆盖招聘笔试验收题在 Agent 层可离线验证的部分：Q1（知识库引用）、Q4（员工查询）、
Q5（订单 + 计算器两步编排）、Q6（时间工具）、Q7（多轮历史理解「他」）、Q8（隐私拒答）。
全部走 MockLLMProvider 脚本 + pytest-socket 禁网，不打真实 API。
"""

from __future__ import annotations

import json

import pytest

from app.agent.orchestrator import build_orchestrator
from app.agent.refusal import NOT_FOUND_REPLY, PRIVACY_REFUSAL, threshold_refusal
from app.llm.base import ChatMessage
from app.llm.mock import MockLLMProvider, text_reply, tool_reply
from app.tools import build_tool_registry

pytestmark = pytest.mark.allow_hosts("127.0.0.1")

#: 「上周」在 data/mock 订单数据里对应的自然周区间（周一为首，闭区间），
#: 与 get_current_time 返回的 last_week 口径一致。
LAST_WEEK = {"start_date": "2026-11-16", "end_date": "2026-11-22"}


def _streamed_text(events) -> str:
    return "".join(event.text for event in events if event.kind == "delta")


async def test_q4_employee_lookup(ask_agent):
    """Q4「员工 001 是哪个部门的？」→ 调 query_employee，答案不要求引用。"""
    llm = MockLLMProvider(
        [
            tool_reply(("query_employee", {"emp_id": "001"})),
            text_reply("员工 001 是张三，属于技术部。"),
        ]
    )
    reply, events = await ask_agent(llm, "员工 001 是哪个部门的？")

    assert reply.status == "ok"
    assert [(record.round, record.tool_name) for record in reply.tool_calls] == [
        (1, "query_employee")
    ]
    payload = json.loads(reply.tool_calls[0].result_summary)
    assert payload["ok"] is True
    assert payload["employee"]["name"] == "张三"
    assert payload["employee"]["dept"] == "技术部"
    # CURRENT_PLAN 修正 1：非知识库答案不因缺引用被拒
    assert reply.citations == ()
    assert _streamed_text(events) == reply.answer


async def test_q5_orders_then_calculator_two_rounds(ask_agent):
    """Q5「上周一共多少订单？」→ query_orders 返回明细，calculator 自己算。"""
    llm = MockLLMProvider(
        [
            tool_reply(("query_orders", LAST_WEEK)),
            tool_reply(("calculator", {"expression": "15600+3000+98000+4200+600"})),
            text_reply("上周共 7 笔订单，已支付金额合计 121400 元。"),
        ]
    )
    reply, _ = await ask_agent(llm, "上周一共多少订单？")

    assert reply.status == "ok"
    # CURRENT_PLAN 修正 3：验收题必须出现 query_orders → calculator 两轮编排
    assert [(record.round, record.tool_name) for record in reply.tool_calls] == [
        (1, "query_orders"),
        (2, "calculator"),
    ]
    orders_payload = json.loads(reply.tool_calls[0].result_summary)
    assert orders_payload["ok"] is True
    assert len(orders_payload["orders"]) == 7
    assert "summary" not in orders_payload, "query_orders 不得替模型预算汇总"
    calc_payload = json.loads(reply.tool_calls[1].result_summary)
    assert calc_payload["ok"] is True
    assert calc_payload["result"] == 121400


async def test_q6_current_time_tool(ask_agent):
    """Q6「现在几点？」→ get_current_time。只断言结构与时区，不断言具体时刻。"""
    llm = MockLLMProvider(
        [
            tool_reply(("get_current_time", {})),
            text_reply("现在是北京时间下午 3 点。"),
        ]
    )
    reply, _ = await ask_agent(llm, "现在几点？")

    assert reply.status == "ok"
    assert [record.tool_name for record in reply.tool_calls] == ["get_current_time"]
    payload = json.loads(reply.tool_calls[0].result_summary)
    assert payload["ok"] is True
    assert payload["timezone"] == "Asia/Shanghai"
    assert {"datetime", "date", "time", "weekday", "this_week", "last_week"} <= set(payload)
    assert set(payload["last_week"]) == {"start", "end"}


async def test_q7_followup_question_uses_history(ask_agent):
    """Q7「他上周来上班几天？」→ 历史进模型，「他」由模型解析为员工 001。"""
    history = (
        ChatMessage(role="user", content="员工 001 是哪个部门的？"),
        ChatMessage(role="assistant", content="员工 001 是张三，属于技术部。"),
    )
    llm = MockLLMProvider(
        [
            tool_reply(
                (
                    "query_attendance",
                    {"emp_id": "001", "start_date": "2026-11-16", "end_date": "2026-11-20"},
                )
            ),
            text_reply("张三上周到岗 5 天。"),
        ]
    )
    reply, _ = await ask_agent(llm, "他上周来上班几天？", history=history)

    assert reply.status == "ok"
    record = reply.tool_calls[0]
    assert record.tool_name == "query_attendance"
    payload = json.loads(record.result_summary)
    assert payload["ok"] is True
    assert len(payload["records"]) == 5
    assert {item["date"] for item in payload["records"]} == {
        "2026-11-16",
        "2026-11-17",
        "2026-11-18",
        "2026-11-19",
        "2026-11-20",
    }
    # 历史必须真的进了模型上下文，否则「他」无从解析
    sent = llm.calls[0]["messages"]
    assert [message.role for message in sent] == ["system", "user", "assistant", "user"]
    assert sent[1].content == "员工 001 是哪个部门的？"
    assert sent[2].content == "员工 001 是张三，属于技术部。"
    assert sent[-1].content == "他上周来上班几天？"


async def test_q8_privacy_refusal_never_reaches_llm(ask_agent):
    """Q8「CEO 的家庭住址」→ L1 拦截，不进模型、不调工具。"""
    llm = MockLLMProvider([text_reply("CEO 住在某小区")])
    reply, events = await ask_agent(llm, "我们公司 CEO 的家庭住址是？")

    assert reply.status == "refused"
    assert reply.answer == PRIVACY_REFUSAL
    assert reply.refused_by == "sensitive:家庭住址"
    assert reply.tool_calls == ()
    assert [event.kind for event in events] == ["delta", "done"]
    assert llm.calls == [], "隐私问题必须在进模型之前被拦截"
    assert llm.remaining == 1


async def test_q1_knowledge_answer_carries_citations(ask_agent):
    """Q1「每年几天年假」→ 知识库命中，答案带 [1]，citations 可定位原文。"""
    llm = MockLLMProvider(
        [
            tool_reply(("search_knowledge_base", {"query": "年假天数"})),
            text_reply("入职满 1 年可享受 5 天带薪年假 [1]。"),
        ]
    )
    reply, events = await ask_agent(llm, "员工每年有几天年假？", knowledge=True)

    assert reply.status == "ok"
    assert reply.knowledge_used is True
    assert len(reply.citations) == 1
    citation = reply.citations[0]
    assert citation["ref_no"] == 1
    assert citation["filename"] == "员工手册.md"
    assert citation["heading_path"] == ["假期制度", "年假"]
    citations_events = [event for event in events if event.kind == "citations"]
    assert len(citations_events) == 1
    assert list(citations_events[0].citations) == list(reply.citations)


async def test_knowledge_answer_without_citation_is_retracted(ask_agent):
    """L3：用了知识库却不标 [n] → 撤回答案换成 NOT_FOUND_REPLY。"""
    llm = MockLLMProvider(
        [
            tool_reply(("search_knowledge_base", {"query": "年假天数"})),
            text_reply("入职满 1 年可享受 5 天带薪年假。"),
        ]
    )
    reply, events = await ask_agent(llm, "员工每年有几天年假？", knowledge=True)

    assert reply.status == "refused"
    assert reply.answer == NOT_FOUND_REPLY
    assert reply.refused_by is not None
    assert reply.refused_by.startswith("citation:")
    assert reply.citations == ()
    # delta 已把错误答案流出去了，适配层必须以 done.reply.answer 替换（events.py 契约）
    assert _streamed_text(events) != reply.answer


async def test_out_of_range_citation_is_retracted(ask_agent):
    """L3：编造 [99] 越界引用 → 撤回答案，原因串带越界编号。"""
    llm = MockLLMProvider(
        [
            tool_reply(("search_knowledge_base", {"query": "年假天数"})),
            text_reply("年假 5 天 [99]。"),
        ]
    )
    reply, _ = await ask_agent(llm, "员工每年有几天年假？", knowledge=True)

    assert reply.status == "refused"
    assert reply.answer == NOT_FOUND_REPLY
    assert "99" in (reply.refused_by or "")
    assert reply.citations == ()


async def test_long_history_trimmed_keeping_first_turn(ask_agent):
    """超长历史裁剪：保住开头两条身份线索 + 最近 9 轮，工具消息不带入。"""
    history: list[ChatMessage] = [
        ChatMessage(role="user", content="我是技术部的张三"),
        ChatMessage(role="assistant", content="你好，张三"),
    ]
    for index in range(30):
        history.append(ChatMessage(role="user", content=f"闲聊 {index}"))
        history.append(ChatMessage(role="assistant", content=f"回复 {index}"))
    history.append(
        ChatMessage(role="tool", content="过期工具输出", tool_call_id="call_x", name="calculator")
    )
    llm = MockLLMProvider([text_reply("好的，请讲。")])

    reply, _ = await ask_agent(llm, "我们部门有几个人？", history=history)

    assert reply.status == "ok"
    sent = llm.calls[0]["messages"]
    assert sent[0].role == "system"
    assert sent[1].content == "我是技术部的张三"
    assert sent[2].content == "你好，张三"
    assert sent[-1].content == "我们部门有几个人？"
    assert all(message.role in ("system", "user", "assistant") for message in sent)
    # system + 保住的前 2 条 + 最近 (10*2-2) 条 + 本轮提问
    assert len(sent) == 1 + 2 + 18 + 1


async def test_build_orchestrator_wires_settings(mock_settings):
    """build_orchestrator 按配置接线，轮数与历史上限取自 Settings。"""
    llm = MockLLMProvider([text_reply("你好")])
    registry = build_tool_registry(mock_settings)
    orchestrator = build_orchestrator(mock_settings, llm, registry)

    assert orchestrator._max_tool_rounds == mock_settings.agent_max_tool_rounds
    assert orchestrator._history_max_turns == mock_settings.agent_history_max_turns


async def test_knowledge_tool_reports_unavailable_before_index(ask_agent):
    """M4 之前知识库工具如实报告「未建索引」，不伪装成查过但没有。"""
    llm = MockLLMProvider(
        [
            tool_reply(("search_knowledge_base", {"query": "年假"})),
            text_reply("我不确定。"),
        ]
    )
    reply, _ = await ask_agent(llm, "年假几天？", knowledge=False)

    record = reply.tool_calls[0]
    payload = json.loads(record.result_summary)
    assert payload["ok"] is False
    assert "尚未建立文档索引" in payload["error"]
    # 工具算「用过知识库」，终答又没带引用 → L3 撤回
    assert reply.status == "refused"
    assert reply.answer == NOT_FOUND_REPLY


async def test_empty_successful_retrieval_refuses_without_second_llm_call(mock_settings):
    """L1：检索正常执行但阈值过滤为空，直接拒答且不再调用模型。"""

    async def empty_search(_query: str, _top_k: int):
        return []

    registry = build_tool_registry(mock_settings, knowledge_search=empty_search)
    llm = MockLLMProvider(
        [
            tool_reply(("search_knowledge_base", {"query": "2030 年销售目标"})),
            text_reply("这条不应被调用。"),
        ]
    )
    orchestrator = build_orchestrator(mock_settings, llm, registry)
    events = [event async for event in orchestrator.ask("2030 年销售目标是多少？")]
    reply = next(event.reply for event in events if event.kind == "done")
    assert reply is not None
    assert reply.status == "refused"
    assert reply.refused_by == "threshold"
    assert reply.answer == threshold_refusal("2030 年销售目标")
    assert len(llm.calls) == 1
    assert llm.remaining == 1
