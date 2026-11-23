"""耗时统计（TDD §3 `core/timing.py`，服务于 §4.2 的关键埋点）。

为什么单独抽一层而不是到处写 `t0 = time.perf_counter()`：
1. 用 `perf_counter` 而非 `time.time()`——后者受系统时钟调整影响，可能算出负耗时。
2. 埋点格式统一，日志才能被 grep/聚合（M8 后台要展示工具调用耗时，FR-3-09）。
3. 上下文管理器保证**异常路径也会记录耗时**，而手写 `t1 - t0` 在抛异常时直接丢失。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Timer:
    """一次性计时器。`elapsed_ms` 在 `stop()` 后可读，重复 stop 返回首次结果。"""

    started_at: float
    elapsed_ms: float | None = None

    def stop(self) -> float:
        if self.elapsed_ms is None:
            self.elapsed_ms = (time.perf_counter() - self.started_at) * 1000.0
        return self.elapsed_ms

    @property
    def ms(self) -> float:
        """已用毫秒；未 stop 时返回**实时**值，便于长任务中途打点。"""
        if self.elapsed_ms is not None:
            return self.elapsed_ms
        return (time.perf_counter() - self.started_at) * 1000.0


def start_timer() -> Timer:
    return Timer(started_at=time.perf_counter())


@contextmanager
def timed() -> Iterator[Timer]:
    """`with timed() as t: ...` → 退出时 `t.elapsed_ms` 已填好（异常也会填）。"""
    timer = start_timer()
    try:
        yield timer
    finally:
        timer.stop()


@contextmanager
def log_duration(
    logger: logging.Logger,
    operation: str,
    *,
    level: int = logging.INFO,
    threshold_ms: float = 0.0,
    **fields: object,
) -> Iterator[Timer]:
    """计时并在退出时打一行结构化日志。

    `threshold_ms > 0` 时只记录慢于阈值的调用——高频埋点（如每次工具调用）
    全量打日志会淹没 logs/app.log，只留慢的才有排查价值。
    """
    with timed() as timer:
        try:
            yield timer
        finally:
            elapsed = timer.elapsed_ms or 0.0
            extra = "".join(f" {k}={v}" for k, v in fields.items())
            if elapsed >= threshold_ms:
                logger.log(level, "%s 耗时 %.1fms%s", operation, elapsed, extra)


def format_duration(ms: float) -> str:
    """人类可读耗时，用于 IM 回复与后台展示（避免满屏 12345.678ms）。"""
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.2f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60)}s"
