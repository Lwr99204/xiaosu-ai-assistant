"""Token 估算与成本折算单测（TDD §4.3 / FR-5-10）。

优先级是「供应商回的真实 usage 永远第一」，本模块只在拿不到 usage 时估算。
其中「全 0 也算缺失」是实测得来的：流式请求漏带 stream_options.include_usage
时上游会回一个全 0 的 usage 帧，当真值记进库会让后台成本永远显示 0.00。
"""

from __future__ import annotations

import pytest

from app.llm.base import ChatMessage, TokenUsage, ToolCall
from app.llm.tokenizer import (
    ASCII_CHARS_PER_TOKEN,
    FALLBACK_PRICING,
    PRICING,
    cost_cny,
    estimate_messages_tokens,
    estimate_tokens,
    price_of,
    usage_or_estimate,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("abc", 1),
        ("abcd", 1),
        ("abcde", 2),
        ("小苏", 2),
        ("小苏abcd", 3),
    ],
)
def test_estimate_tokens_counts_cjk_as_one(text: str, expected: int) -> None:
    """中文按 1 字 1 token 估，其余按 4 字符 1 token 向上取整。"""
    assert estimate_tokens(text) == expected


def test_estimate_tokens_accepts_none() -> None:
    """assistant 消息在工具调用轮 content 为 None，估算不能因此抛异常。"""
    assert estimate_tokens(None) == 0
    assert ASCII_CHARS_PER_TOKEN == 4


def test_estimate_messages_tokens_adds_per_message_overhead() -> None:
    """每条消息另加 4：role 与分隔符本身也占 token，不加会系统性偏低。"""
    messages = (
        ChatMessage(role="user", content="小苏"),
        ChatMessage(role="assistant", content="abcd"),
    )

    assert estimate_messages_tokens(messages) == 2 + 4 + 1 + 4


def test_estimate_messages_tokens_counts_tool_calls() -> None:
    messages = (
        ChatMessage(
            role="assistant",
            tool_calls=(ToolCall(id="call_1", name="calculator", arguments="{}"),),
        ),
    )
    expected = 4 + estimate_tokens("calculator") + estimate_tokens("{}")

    assert estimate_messages_tokens(messages) == expected


def test_price_of_falls_back_for_unknown_model() -> None:
    assert price_of("qwen3.8-max") == PRICING["qwen3.8-max"]
    assert price_of("no-such-model") == FALLBACK_PRICING


def test_cost_cny_charges_input_and_output_separately() -> None:
    input_price, output_price = PRICING["qwen3.8-max"]
    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=1000,
        total_tokens=2000,
        model="qwen3.8-max",
    )

    assert cost_cny(usage) == pytest.approx(input_price + output_price)


def test_usage_or_estimate_prefers_provider_usage() -> None:
    usage = usage_or_estimate(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        model="qwen3.8-max",
    )

    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (10, 5, 15)
    assert usage.estimated is False
    assert usage.model == "qwen3.8-max"


def test_usage_or_estimate_reads_object_with_model_dump() -> None:
    """openai SDK 回的是 CompletionUsage 对象，靠 model_dump() 取值。"""

    class _FakeUsage:
        def model_dump(self) -> dict[str, int]:
            return {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    usage = usage_or_estimate(_FakeUsage(), model="m")

    assert usage.total_tokens == 3
    assert usage.estimated is False


def test_usage_or_estimate_treats_all_zero_usage_as_missing() -> None:
    usage = usage_or_estimate(
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        model="m",
        messages=(ChatMessage(role="user", content="小苏"),),
        completion_text="abcd",
    )

    assert usage.estimated is True
    assert usage.prompt_tokens == 2 + 4
    assert usage.completion_tokens == 1
    assert usage.total_tokens == 7


def test_usage_or_estimate_estimates_when_usage_is_absent() -> None:
    usage = usage_or_estimate(
        None,
        model="m",
        messages=(ChatMessage(role="user", content="小苏你好"),),
        completion_text="你好，我是小苏",
    )

    assert usage.estimated is True
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens == estimate_tokens("你好，我是小苏")


def test_usage_or_estimate_fills_missing_total() -> None:
    usage = usage_or_estimate({"prompt_tokens": 3, "completion_tokens": 4}, model="m")

    assert usage.total_tokens == 7
