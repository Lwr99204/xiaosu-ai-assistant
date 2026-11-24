"""单 worker 文档索引队列，限制 embedding 并发并提供优雅排空。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

IndexJob = Callable[[], Awaitable[None]]


class IndexTaskQueue:
    """进程内 FIFO 队列；所有文档索引在唯一 worker 中串行执行。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[IndexJob] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    def submit(self, job: IndexJob) -> None:
        self._ensure_worker()
        self._queue.put_nowait(job)

    async def drain(self) -> None:
        """等待队列清空并停止空闲 worker；后续 submit 会按需重启。"""
        await self._queue.join()
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="document-index-worker")

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await job()
            except Exception:
                # 单个任务已由业务层维护 failed/回滚状态；这里兜底保证 worker 不退出。
                logger.exception("未处理的文档索引任务异常")
            finally:
                self._queue.task_done()
