"""Generic bounded async worker pool used by V2 download/upload queues."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    task_id: int | str
    payload: Any


class WorkerPool:
    def __init__(self, name: str, workers: int, handler: Callable[[QueueItem], Awaitable[None]]):
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self.name = name
        self.worker_count = workers
        self.handler = handler
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._workers = [
            asyncio.create_task(self._worker_loop(i), name=f"{self.name}-{i + 1}")
            for i in range(self.worker_count)
        ]

    async def put(self, item: QueueItem) -> None:
        if not self._started or self._stopping:
            raise RuntimeError(f"{self.name} is not accepting work")
        await self.queue.put(item)

    def qsize(self) -> int:
        return self.queue.qsize()

    def active_workers(self) -> int:
        return sum(not worker.done() for worker in self._workers)

    async def join(self) -> None:
        await self.queue.join()

    async def _worker_loop(self, index: int) -> None:
        worker_name = f"{self.name}-{index + 1}"
        while True:
            item = await self.queue.get()
            try:
                await self.handler(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s failed while processing task %s", worker_name, item.task_id)
            finally:
                self.queue.task_done()

    async def stop(self, cancel_pending: bool = False) -> None:
        self._stopping = True
        if cancel_pending:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                except asyncio.QueueEmpty:
                    break
        workers = list(self._workers)
        self._workers.clear()
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._started = False
