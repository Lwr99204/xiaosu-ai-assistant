"""Agent 降级路径集成测试（验收题 Q10 与 usage 兜底）。

编排器的硬契约：任何上游故障都不外抛、都以 done 收尾、都给友好话术。这里用
MockLLMProvider 的异常脚本项，外加两个自制桩提供者：
- _NoUsageLLM：模拟不回 usage 的端点（Ollama / 部分网关），逼出本地估算兜底；
- _FragmentingLLM：把一次工具调用的 arguments 拆成多帧且不下发 id，
  验证按 index 累加拼装与 call_{n} 兜底（qwen 流式帧的真实形态）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import pytest

from app.agent.prompts import SYSTEM_PROMPT_VERSION
from app.agent.refusal import LLM_FAILURE_REPLY, TOOL_ROUNDS_EXCEEDED_REPLY
from app.llm.base import ChatMessage, LLMResult, StreamChunk, StreamToolCallDelta
from app.llm.mock import (
    MockLLMProvider,
    auth_error,
    text_reply,
    timeout_error,
    tool_reply,
)
from app.llm.tokenizer import estimate_tokens

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


class _NoUsageLLM:
    """按脚本返回但 usage 恒为 None 的提供者。"""

    model = "mock-llm"

    def __init__(self, script: Sequence[LLMResult]) -> None:
        self._script = list(script)
        self._cursor = 0

    async def chat(self, messages, *, tools=None, temperature=None) -> LLMResult:
        raise NotImplementedError

    def stream(self, messages, *, tools=None, temperature=None) -> AsyncIterator[StreamChunk]:
        result = self._script[self._cursor]
        self._cursor += 1
        return self._chunks(result)

    async def _chunks(self, result: LLMResult) -> AsyncIterator[StreamChunk]:
        for index, call in enumerate(result.message.tool_calls):
            yield StreamChunk(
                tool_call_deltas=(
                    StreamToolCallDelta(
                        index=index,
                        id=call.id,
                        name=call.name,
                        arguments_delta=call.arguments,
                    ),
                )
            )
        if result.message.content:
            yield StreamChunk(delta=result.message.content)
        yield StreamChunk(finish_reason=result.finish_reason, usage=None)


class _FragmentingLLM:
    """第一轮把 calculator 的 arguments 拆成两帧且不带 id，第二轮给终答。"""

    model = "mock-llm"

    def __init__(self) -> None:
        self.round = 0
        self.seen_messages: list[tuple[ChatMessage, ...]] = []

    async def chat(self, messages, *, tools=None, temperature=None) -> LLMResult:
        raise NotImplementedError

    def stream(self, messages, *, tools=None, temperature=None) -> AsyncIterator[StreamChunk]:
        self.seen_messages.append(tuple(messages))
        self.round += 1
        return self._first_round() if self.round == 1 else self._second_round()

    async def _first_round(self) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            tool_call_deltas=(
                StreamToolCallDelta(
                    index=0, id=None, name="calculator", arguments_delta='{"expression"'
                ),
            )
        )
        yield StreamChunk(
            tool_call_deltas=(StreamToolCallDelta(index=0, arguments_delta=': "1+1"}'),)
        )
        yield StreamChunk(finish_reason="tool_calls", usage=None)

    async def _second_round(self) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="1+1 等于 2。")
        yield StreamChunk(finish_reason="stop", usage=None)


async def test_auth_error_degrades_to_friendly_reply(ask_agent):
    """Q10 主场景：无效 Key → degraded + 友好话术，绝不外抛。"""
    llm = MockLLMProvider([auth_error()])
    reply, events = await ask_agent(llm, "员工每年有几天年假？")

    assert reply.status == "degraded"
    assert reply.answer == LLM_FAILURE_REPLY
    assert reply.refused_by == "llm:LLMAuthError"
    assert reply.prompt_version == SYSTEM_PROMPT_VERSION
    kinds = [event.kind for event in events]
    assert "error" in kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1


async def test_timeout_error_degrades_to_friendly_reply(ask_agent):
    llm = MockLLMProvider([timeout_error()])
    reply, events = await ask_agent(llm, "现在几点？")

    assert reply.status == "degraded"
    assert reply.answer == LLM_FAILURE_REPLY
    assert reply.refused_by == "llm:LLMTimeoutError"
    assert [event.kind for event in events][-1] == "done"


async def test_failure_in_second_round_keeps_first_round_records(ask_agent):
    """第二轮上游挂掉时，第一轮已执行的工具留痕不能丢（后台日志完整性）。"""
    llm = MockLLMProvider([tool_reply(("get_current_time", {})), timeout_error()])
    reply, _ = await ask_agent(llm, "现在几点？")

    assert reply.status == "degraded"
    assert reply.answer == LLM_FAILURE_REPLY
    assert [(record.round, record.tool_name, record.ok) for record in reply.tool_calls] == [
        (1, "get_current_time", True)
    ]


async def test_script_exhausted_mid_loop_degrades(ask_agent):
    """脚本耗尽（编排层多绕一圈）也要走降级，不能静默返回空答案。"""
    llm = MockLLMProvider([tool_reply(("get_current_time", {}))])
    reply, _ = await ask_agent(llm, "现在几点？")

    assert reply.status == "degraded"
    assert reply.answer == LLM_FAILURE_REPLY
    assert reply.refused_by == "llm:ScriptExhausted"
    assert len(reply.tool_calls) == 1


async def test_tool_rounds_exceeded_gives_split_hint(ask_agent):
    """模型在工具间打转 → 轮数耗尽，给「拆分后重新提问」话术。"""
    llm = MockLLMProvider([tool_reply(("get_current_time", {}))] * 3)
    reply, _ = await ask_agent(llm, "现在几点？", max_tool_rounds=3)

    assert reply.status == "degraded"
    assert reply.answer == TOOL_ROUNDS_EXCEEDED_REPLY
    assert reply.refused_by == "tool_rounds_exceeded:3"
    assert len(reply.tool_calls) == 3


async def test_missing_usage_falls_back_to_local_estimate(ask_agent):
    """端点不回 usage → 本地估算兜底，estimated=True，prompt 含 system 提示词。"""
    llm = _NoUsageLLM([text_reply("这是最终回答文本。")])
    reply, _ = await ask_agent(llm, "随便说点什么")

    assert reply.usage.estimated is True
    assert reply.usage.prompt_tokens > 0
    assert reply.usage.completion_tokens == estimate_tokens("这是最终回答文本。")
    assert reply.usage.total_tokens == reply.usage.prompt_tokens + reply.usage.completion_tokens


async def test_usage_accumulates_across_tool_rounds(ask_agent):
    """多轮各自计费，_merge_usage 必须逐字段累加（FR-5-10 账单口径）。"""
    single = _NoUsageLLM([text_reply("回答")])
    reply_single, _ = await ask_agent(single, "问题")

    multi = _NoUsageLLM(
        [
            tool_reply(("get_current_time", {})),
            tool_reply(("get_current_time", {})),
            text_reply("回答"),
        ]
    )
    reply_multi, _ = await ask_agent(multi, "问题")

    assert reply_multi.usage.estimated is True
    assert reply_multi.usage.prompt_tokens > reply_single.usage.prompt_tokens
    assert reply_multi.usage.completion_tokens > 0


async def test_fragmented_tool_call_reassembled_with_id_fallback(ask_agent):
    """arguments 分片按 index 拼回；上游没给 id 时兜底 call_1 并配对回灌。"""
    llm = _FragmentingLLM()
    reply, _ = await ask_agent(llm, "1+1 等于几？")

    assert reply.status == "ok"
    assert reply.answer == "1+1 等于 2。"
    record = reply.tool_calls[0]
    assert record.tool_name == "calculator"
    assert json.loads(record.arguments) == {"expression": "1+1"}
    assert record.ok is True

    second_round_messages = llm.seen_messages[1]
    tool_message = second_round_messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call_1"
    assert json.loads(tool_message.content)["result"] == 2
