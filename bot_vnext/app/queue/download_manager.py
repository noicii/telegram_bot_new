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

    def __init__(
        self,
        output_dir: str | Path,
        workers: int = 2,
        retries: int = 2,
        on_progress: Callable[..., Awaitable[None] | None] | None = None,
        on_complete: Callable[[QueueItem, Path], Awaitable[None]] | None = None,
        on_failed: Callable[[QueueItem, Exception], Awaitable[None]] | None = None,
    ):
        self.engine = HybridDownloader(output_dir, retries=retries)
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_failed = on_failed
        self.pool = WorkerPool("download", workers, self._handle)
        self.contexts: dict[str, TaskContext] = {}

    async def start(self) -> None:
        await self.pool.start()

    async def submit(self, task_id: int | str, payload: dict) -> None:
        key = str(task_id)
        if key in self.contexts:
            raise RuntimeError(f"Download task {task_id} is already active")
        self.contexts[key] = TaskContext(task_id)
        await self.pool.put(QueueItem(task_id, payload))

    async def cancel(self, task_id: int | str) -> bool:
        ctx = self.contexts.get(str(task_id))
        if not ctx:
            return False
        await ctx.cancel()
        return True

    async def _handle(self, item: QueueItem) -> None:
        key = str(item.task_id)
        ctx = self.contexts[key]
        payload = item.payload

        async def report(*args):
            if self.on_progress:
                result = self.on_progress(item.task_id, *args)
                if asyncio.iscoroutine(result):
                    await result

        try:
            ctx.metadata.update(payload.get("metadata") or {})
            result = await self.engine.download(
                payload["url"],
                ctx,
                payload["filename"],
                progress=report,
            )
            ctx.check_cancelled()
            if self.on_complete:
                await self.on_complete(item, result)
        except TaskCancelled:
            logger.info("download task %s cancelled", item.task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("download task %s failed", item.task_id)
            if self.on_failed:
                await self.on_failed(item, exc)
        finally:
            await ctx.cleanup()
            self.contexts.pop(key, None)

    async def stop(self) -> None:
        for ctx in list(self.contexts.values()):
            await ctx.cancel()
        await self.pool.stop(cancel_pending=True)
        self.contexts.clear()
