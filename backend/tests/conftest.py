"""pytest 共享 fixture。

Agent 相关的集成测试有两个共同前提：配置必须隔离（不读仓库 .env、不打真实 API），
以及编排器的消费方式完全一致（收集全部事件、取最后一个 done）。把这两件事收在
这里，测试文件就只剩下脚本编排与断言。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.agent.events import AgentEvent, AgentReply
from app.agent.orchestrator import Orchestrator
from app.config import REPO_ROOT, Settings
from app.llm.base import ChatMessage, ChatProvider
from app.tools import build_tool_registry

MOCK_DATA_DIR = REPO_ROOT / "data" / "mock"

#: 「上周」在 data/mock 订单数据里对应的自然周区间，与 get_current_time 返回的
#: last_week 口径一致（周一为一周之首，闭区间）。
LAST_WEEK = {"start_date": "2026-11-16", "end_date": "2026-11-22"}

#: 检索器桩的固定命中。真向量库要到 M4 才有，这里先打通 Agent 侧的引用契约：
#: 编号、filename、heading_path、char_start/char_end 都是 citations 视图的必填项。
KNOWLEDGE_HITS: tuple[dict[str, Any], ...] = (
    {
        "filename": "员工手册.md",
        "heading_path": ["假期制度", "年假"],
        "snippet": "员工入职满 1 年可享受 5 天带薪年假。",
        "char_start": 120,
        "char_end": 148,
        "score": 0.83,
    },
    {
        "filename": "新人入职指南.md",
        "heading_path": ["报到第一天"],
        "snippet": "报到当天需完成工牌领取、系统账号开通与部门介绍。",
        "char_start": 0,
        "char_end": 26,
        "score": 0.71,
    },
)


@pytest.fixture()
def mock_settings(tmp_path: Path) -> Settings:
    """隔离配置：不读 .env、假 Key、mock provider、日志与存储落临时目录。

    🔴 假 Key 刻意写短：pre-commit 的密钥扫描会拦「密钥名=值 且值 ≥20 字符」
    与 sk- 开头 16 位以上的模式，测试里放一个像真 Key 的串会白白触发门禁。
    """
    return Settings(
        _env_file=None,
        llm_provider="mock",
        llm_api_key="sk-test-1",
        log_dir=tmp_path / "logs",
        storage_dir=tmp_path / "storage",
        mock_data_dir=MOCK_DATA_DIR,
    )


@pytest.fixture()
def knowledge_hits() -> tuple[dict[str, Any], ...]:
    """检索器桩的固定命中，供测试写死引用断言。"""
    return KNOWLEDGE_HITS


async def _stub_search(query: str, top_k: int) -> Sequence[Mapping[str, Any]]:
    return KNOWLEDGE_HITS[:top_k]


AskAgent = Callable[..., Awaitable[tuple[AgentReply, list[AgentEvent]]]]


@pytest.fixture()
def ask_agent(mock_settings: Settings) -> AskAgent:
    """跑一轮 Agent 问答，返回 (最终 reply, 全部事件)。

    收尾那几条断言是编排器的硬契约，放在 fixture 里让每个测试自动继承：
    IM 适配层靠 done 事件发 finish=True，SSE 靠它关流，漏发或发两次都会让
    客户端一直转圈（验收题 Q10 的反面）。
    """

    async def _run(
        provider: ChatProvider,
        question: str,
        *,
        history: Sequence[ChatMessage] = (),
        knowledge: bool = False,
        max_tool_rounds: int | None = None,
    ) -> tuple[AgentReply, list[AgentEvent]]:
        registry = build_tool_registry(
            mock_settings,
            knowledge_search=_stub_search if knowledge else None,
        )
        orchestrator = Orchestrator(
            llm=provider,
            registry=registry,
            max_tool_rounds=max_tool_rounds or mock_settings.agent_max_tool_rounds,
            history_max_turns=mock_settings.agent_history_max_turns,
        )
        events = [event async for event in orchestrator.ask(question, history=history)]

        assert events, "编排器至少要吐出一个事件"
        assert events[-1].kind == "done", "编排器必须以 done 事件收尾"
        assert sum(1 for event in events if event.kind == "done") == 1, "done 事件必须恰好一个"
        assert events[-1].reply is not None
        return events[-1].reply, events

    return _run
