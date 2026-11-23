"""真实 Key 冒烟：Q4/Q5/Q6/Q7 走真实模型端点 + Q10 降级实测。

前置条件：仓库根 .env 里 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 已恢复为真实值
（2026-11-23 换机替换后它们曾是占位模板，详见 docs/ai_journal.md [M2-5]）。

用法（仓库根目录）：
    backend/.venv/Scripts/python.exe backend/dev_tools/agent_smoke_real.py

只打印答案与工具轨迹，不打印任何密钥。全部通过输出 REAL-KEY SMOKE ALL PASS。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.events import AgentReply
from app.agent.orchestrator import build_orchestrator
from app.agent.refusal import LLM_FAILURE_REPLY
from app.config import Settings, get_settings
from app.llm.base import ChatMessage
from app.llm.factory import build_chat_provider
from app.tools import build_tool_registry


async def run_case(
    settings: Settings, question: str, history: Sequence[ChatMessage] = ()
) -> AgentReply:
    llm = build_chat_provider(settings)
    registry = build_tool_registry(settings)
    orchestrator = build_orchestrator(settings, llm, registry)
    events = [event async for event in orchestrator.ask(question, history=history)]
    reply = events[-1].reply
    assert reply is not None
    trace = [(r.round, r.tool_name, r.ok) for r in reply.tool_calls]
    print("Q:", question)
    print("  status:", reply.status)
    print("  tools :", trace)
    print("  usage :", reply.usage)
    print("  answer:", reply.answer[:300])
    print("-" * 60)
    return reply


def tool_names(reply: AgentReply) -> list[str]:
    return [record.tool_name for record in reply.tool_calls]


async def main() -> None:
    settings = get_settings()
    print("provider:", settings.llm_provider, "model:", settings.llm_model)
    if "your-" in settings.llm_model or "example.com" in settings.llm_base_url:
        print("ABORT: .env 的 LLM 配置仍是占位模板，请先恢复真实值（见 ai_journal [M2-5]）")
        raise SystemExit(2)

    r4 = await run_case(settings, "员工 001 是哪个部门的？")
    history = (
        ChatMessage(role="user", content="员工 001 是哪个部门的？"),
        ChatMessage(role="assistant", content=r4.answer),
    )
    r7 = await run_case(settings, "他上周来上班几天？", history=history)
    r6 = await run_case(settings, "现在几点？")
    r5 = await run_case(settings, "上周一共多少订单？已支付金额总计多少？")

    bad = settings.model_copy(
        update={
            "llm_api_key": "dummy-invalid-key-for-q10-test",
            "llm_timeout_s": 15.0,
            "llm_max_retries": 0,
        }
    )
    r10 = await run_case(bad, "现在几点？")

    results = {
        "Q4 query_employee": "query_employee" in tool_names(r4) and r4.status == "ok",
        "Q7 query_attendance": "query_attendance" in tool_names(r7) and r7.status == "ok",
        "Q6 get_current_time": "get_current_time" in tool_names(r6) and r6.status == "ok",
        "Q5 query_orders": "query_orders" in tool_names(r5) and r5.status == "ok",
        "Q5 calculator": "calculator" in tool_names(r5),
        "Q10 degraded": r10.status == "degraded" and r10.answer == LLM_FAILURE_REPLY,
    }
    for name, passed in results.items():
        print(("PASS " if passed else "FAIL ") + name)
    if not all(results.values()):
        raise SystemExit(1)
    print("REAL-KEY SMOKE ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
