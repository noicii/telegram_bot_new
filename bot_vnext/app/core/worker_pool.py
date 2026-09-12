"""Bounded async worker pool used by download and upload stages."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class WorkerPool(Generic[T]):
    def __init__(self, name: str, workers: int, handler: Callable[[T], Awaitable[None]]):
        self.name = name
        self.workers = max(1, int(workers))
        self.handler = handler
        self.queue: asyncio.Queue[T] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._tasks = [asyncio.create_task(self._worker(i), name=f"{self.name}-{i+1}") for i in range(self.workers)]

    async def submit(self, item: T) -> None:
        if not self._started or self._stopping:
            raise RuntimeError(f"{self.name} pool is not accepting work")
        await self.queue.put(item)

    async def _worker(self, index: int) -> None:
        while True:
            item = await self.queue.get()
            try:
                await self.handler(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s worker %s failed", self.name, index + 1)
            finally:
                self.queue.task_done()

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    @property
    def pending(self) -> int:
        return self.queue.qsize()
