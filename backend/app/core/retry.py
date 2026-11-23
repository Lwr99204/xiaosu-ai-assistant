"""指数退避 + 抖动重试（TDD §4.9 降级链路 / FR-6-08）。

三条不可让步的约定：
1. **4xx 语义错误绝不重试**。`AppError.retryable` 是唯一判据：无效 Key 重试
   100 次也还是 401，只会白烧配额、拖长用户等待、并可能触发供应商风控。
2. **必须带抖动（jitter）**。固定退避在多副本部署下会造成「惊群」——所有实例
   在同一毫秒集体重试，把刚恢复的上游再次打挂。
3. **重试次数默认 2**（TDD §4.1 `llm_max_retries`），加上首次共 3 次尝试。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.errors import AppError

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 2
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 8.0

#: 除 AppError 外，这些标准库异常也视为可重试的网络/超时类故障
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    AppError,
    TimeoutError,
    ConnectionError,
)


def compute_backoff(
    attempt: int,
    *,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """第 `attempt` 次失败后的等待秒数（attempt 从 0 开始）。

    采用 **full jitter**：`uniform(0, min(max_delay, base * 2**attempt))`。
    `jitter=False` 时退化为纯指数序列（0.5 / 1.0 / 2.0 …），供单元测试断言。
    """
    ceiling = min(max_delay, base_delay * (2**attempt))
    if ceiling <= 0:
        return 0.0
    if not jitter:
        return ceiling
    return (rng or random).uniform(0.0, ceiling)


def is_retryable(exc: BaseException) -> bool:
    """判定异常是否值得重试。AppError 交给它自己声明，其余按类型归类。"""
    if isinstance(exc, AppError):
        return exc.retryable
    return isinstance(exc, (TimeoutError, ConnectionError))


async def retry_async[T](
    factory: Callable[[], Awaitable[T]],
    *,
    operation: str = "operation",
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
    rng: random.Random | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retryable_exceptions: tuple[type[BaseException], ...] = RETRYABLE_EXCEPTIONS,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """执行 `factory()`，失败则按指数退避重试；次数耗尽后抛出最后一次的异常。

    🔴 第一个参数是**无参工厂**（`lambda: client.chat(...)`）而不是协程对象。
    协程被 await 一次后即失效，直接传协程会让第二次重试抛
    `RuntimeError: cannot reuse already awaited coroutine`——这是生成重试代码时
    最常踩的坑，也是本函数刻意这样设计签名的原因。

    `sleep` 可注入，测试时换成收集延迟的假实现即可，无需真等。
    """
    attempt = 0
    while True:
        try:
            return await factory()
        except retryable_exceptions as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            delay = compute_backoff(
                attempt, base_delay=base_delay, max_delay=max_delay, jitter=jitter, rng=rng
            )
            attempt += 1
            logger.warning(
                "%s 第 %d/%d 次重试，%.2fs 后执行（原因：%s: %s）",
                operation,
                attempt,
                max_retries,
                delay,
                type(exc).__name__,
                exc,
            )
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            await sleep(delay)
