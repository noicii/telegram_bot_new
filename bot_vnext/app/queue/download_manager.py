"""Persistent-facing download queue coordinator for Bot V2."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.core.task import TaskCancelled, TaskContext
from app.queue.worker_pool import QueueItem, WorkerPool
from app.downloader.engine import HybridDownloader

logger = logging.getLogger(__name__)


class DownloadManager:
    """Own exactly N download workers and isolate every task's cancellation."""

    def __init__(self, output_dir: str | Path, workers: int = 2, retries: int = 2, database=None,
                 on_progress: Callable[..., Awaitable[None] | None] | None = None,
                 on_complete: Callable[[QueueItem, Path], Awaitable[None]] | None = None,
                 on_failed: Callable[[QueueItem, Exception], Awaitable[None]] | None = None):
        self.engine = HybridDownloader(output_dir, retries=retries)
        self.database = database
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_failed = on_failed
        self.pool = WorkerPool("download", workers, self._handle)
        self.contexts: dict[str, TaskContext] = {}
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    async def start(self) -> None:
        await self.pool.start()

    async def submit(self, task_id: int | str, payload: dict) -> None:
        key = str(task_id)
        if key in self.contexts:
            raise RuntimeError(f"Download task {task_id} is already active")
        self._cancelled.discard(key)
        self.contexts[key] = TaskContext(task_id)
        await self.pool.put(QueueItem(task_id, payload))

    async def cancel(self, task_id: int | str) -> bool:
        key = str(task_id)
        ctx = self.contexts.get(key)
        if not ctx:
            self._cancelled.add(key)
            return False
        self._cancelled.add(key)
        await ctx.cancel()
        running = self.running_tasks.get(key)
        if running and not running.done() and running is not asyncio.current_task():
            running.cancel()
        return True

    async def _handle(self, item: QueueItem) -> None:
        key = str(item.task_id)
        ctx = self.contexts[key]
        payload = item.payload
        current_task = asyncio.current_task()
        if current_task:
            self.running_tasks[key] = current_task

        async def report(*args):
            if self.on_progress:
                result = self.on_progress(item.task_id, *args)
                if asyncio.iscoroutine(result):
                    await result

        expected_path = self.engine.output_dir / payload["filename"]
        try:
            if key in self._cancelled:
                raise TaskCancelled(f"Download task {item.task_id} cancelled before start")

            ctx.metadata.update(payload.get("metadata") or {})
            if self.database:
                await self.database.update_task(key, status="downloading", file_path=str(expected_path))

            result = await self.engine.download(payload["url"], ctx, payload["filename"], progress=report)
            ctx.check_cancelled()
            if self.on_complete:
                await self.on_complete(item, result)
        except TaskCancelled:
            logger.info("download task %s cancelled", item.task_id)
            if self.database:
                await self.database.mark_cancelled(key)
        except asyncio.CancelledError:
            logger.info("download task %s asyncio-cancelled", item.task_id)
            if self.database and key in self._cancelled:
                await self.database.mark_cancelled(key)
            raise
        except Exception as exc:
            logger.exception("download task %s failed", item.task_id)
            if self.on_failed:
                await self.on_failed(item, exc)
        finally:
            self.running_tasks.pop(key, None)
            await ctx.cleanup()
            self.contexts.pop(key, None)
            self._cancelled.discard(key)

    def queue_size(self) -> int:
        return self.pool.qsize()

    def active_workers(self) -> int:
        return len(self.running_tasks)

    async def stop(self) -> None:
        for ctx in list(self.contexts.values()):
            await ctx.cancel()
        for task in list(self.running_tasks.values()):
            if not task.done():
                task.cancel()
        await self.pool.stop(cancel_pending=True)
        if self.running_tasks:
            await asyncio.gather(*self.running_tasks.values(), return_exceptions=True)
        self.running_tasks.clear()
        self.contexts.clear()
        self._cancelled.clear()
