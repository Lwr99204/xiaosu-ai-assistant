"""retry_async 与 compute_backoff 单元测试（TDD §4.9 降级链路）。

核心约定：4xx 语义错误（LLMAuthError）绝不重试；退避必须带抖动；默认重试 2 次
共 3 次尝试。sleep 全部注入假实现，测试零等待。
"""

from __future__ import annotations

import random

import pytest

from app.core.errors import LLMAuthError, LLMTimeoutError
from app.core.retry import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_DELAY,
    DEFAULT_MAX_RETRIES,
    compute_backoff,
    is_retryable,
    retry_async,
)

pytestmark = pytest.mark.allow_hosts("127.0.0.1")


def _sleep_collector(delays: list[float]):
    """假 sleep：只记录延迟秒数，不真等。"""

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    return _sleep


def test_backoff_without_jitter_is_pure_exponential():
    assert [compute_backoff(attempt, jitter=False) for attempt in range(4)] == [0.5, 1.0, 2.0, 4.0]


def test_backoff_capped_at_max_delay():
    assert compute_backoff(20, jitter=False) == DEFAULT_MAX_DELAY


def test_backoff_with_jitter_stays_within_ceiling():
    rng = random.Random(42)
    for attempt in range(6):
        ceiling = min(DEFAULT_MAX_DELAY, DEFAULT_BASE_DELAY * 2**attempt)
        value = compute_backoff(attempt, rng=rng)
        assert 0.0 <= value <= ceiling


def test_is_retryable_classification():
    assert is_retryable(LLMTimeoutError("upstream timeout")) is True
    assert is_retryable(LLMAuthError("bad key")) is False
    assert is_retryable(TimeoutError()) is True
    assert is_retryable(ConnectionError()) is True
    assert is_retryable(ValueError("logic bug")) is False


async def test_succeeds_after_transient_failures():
    delays: list[float] = []
    attempts = 0

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise LLMTimeoutError("flaky")
        return "ok"

    result = await retry_async(factory, jitter=False, sleep=_sleep_collector(delays))
    assert result == "ok"
    assert attempts == 3
    assert delays == [DEFAULT_BASE_DELAY, DEFAULT_BASE_DELAY * 2]


async def test_gives_up_after_max_retries_and_raises_last_error():
    attempts = 0

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        raise LLMTimeoutError("always down")

    with pytest.raises(LLMTimeoutError):
        await retry_async(factory, jitter=False, sleep=_sleep_collector([]))
    assert attempts == DEFAULT_MAX_RETRIES + 1


async def test_auth_error_is_never_retried():
    delays: list[float] = []

    async def factory() -> str:
        raise LLMAuthError("invalid api key")

    with pytest.raises(LLMAuthError):
        await retry_async(factory, sleep=_sleep_collector(delays))
    assert delays == []


async def test_on_retry_callback_receives_attempt_exception_delay():
    seen: list[tuple[int, str, float]] = []
    attempts = 0

    async def factory() -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LLMTimeoutError("once")
        return 7

    result = await retry_async(
        factory,
        operation="llm.chat",
        jitter=False,
        sleep=_sleep_collector([]),
        on_retry=lambda attempt, exc, delay: seen.append((attempt, type(exc).__name__, delay)),
    )
    assert result == 7
    assert seen == [(1, "LLMTimeoutError", DEFAULT_BASE_DELAY)]


async def test_factory_rebuilds_coroutine_each_attempt():
    built = 0

    async def factory() -> int:
        nonlocal built
        built += 1
        if built < 3:
            raise ConnectionError("connection reset")
        return built

    assert await retry_async(factory, jitter=False, sleep=_sleep_collector([])) == 3
    assert built == 3
