"""Four-worker independent Telegram upload queue for Bot V2."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.core.task import TaskCancelled, TaskContext
from app.queue.worker_pool import QueueItem, WorkerPool
from app.storage.database import Database
from app.uploader.engine import UploadEngine
from config import DESTINATION_CHAT_ID

logger = logging.getLogger(__name__)


class UploadManager:
    """Own exactly four independent upload workers by default."""

    def __init__(
        self,
        client,
        *,
        database: Database | None = None,
        workers: int = 4,
        retries: int = 3,
        on_progress: Callable[..., Awaitable[None] | None] | None = None,
        on_complete: Callable[[QueueItem], Awaitable[None]] | None = None,
        on_failed: Callable[[QueueItem, Exception], Awaitable[None]] | None = None,
        delete_after_upload: bool = True,
    ):
        self.engine = UploadEngine(client, retries=retries)
        self.database = database
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_failed = on_failed
        self.delete_after_upload = delete_after_upload
        self.pool = WorkerPool("upload", workers, self._handle)
        self.contexts: dict[str, TaskContext] = {}
        self.running_tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    async def start(self) -> None:
        await self.pool.start()

    async def submit(self, task_id: int | str, payload: dict) -> None:
        key = str(task_id)
        if key in self.contexts:
            raise RuntimeError(f"Upload task {task_id} is already active")
        self._cancelled.discard(key)
        self.contexts[key] = TaskContext(task_id)
        await self.pool.put(QueueItem(task_id, payload))

    async def cancel(self, task_id: int | str) -> bool:
        key = str(task_id)
        ctx = self.contexts.get(key)
        if not ctx:
            self._cancelled.add(key)
            if self.database:
                await self.database.mark_cancelled(task_id)
            return False

        self._cancelled.add(key)
        await ctx.cancel()
        running = self.running_tasks.get(key)
        if running and not running.done() and running is not asyncio.current_task():
            running.cancel()

        if self.database:
            await self.database.mark_cancelled(task_id)
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

        file_path = Path(payload["file_path"])
        destination = DESTINATION_CHAT_ID
        try:
            if key in self._cancelled:
                raise TaskCancelled(f"Upload task {item.task_id} cancelled before start")

            ctx.metadata.update(payload.get("metadata") or {})
            if self.database:
                await self.database.mark_uploading(item.task_id)

            logger.info(
                "upload START task=%s file=%s size=%.1fMiB destination=%s mode=%s",
                item.task_id,
                file_path,
                file_path.stat().st_size / 1024 / 1024 if file_path.is_file() else 0,
                destination,
                payload.get("mode", "video"),
            )

            result = await self.engine.upload(
                ctx,
                file_path,
                chat_id=destination,
                caption=payload.get("caption"),
                thumbnail=payload.get("thumbnail"),
                mode=payload.get("mode", "video"),
                title=payload.get("title"),
                duration=payload.get("duration"),
                width=payload.get("width"),
                height=payload.get("height"),
                supports_streaming=payload.get("supports_streaming", True),
                progress=report,
                reply_to_message_id=payload.get("reply_to_message_id"),
            )
            ctx.check_cancelled()

            message_id = getattr(result, "id", None)
            logger.info(
                "upload SUCCESS task=%s destination=%s message_id=%s file=%s",
                item.task_id,
                destination,
                message_id,
                file_path,
            )

            if self.database:
                await self.database.mark_completed(item.task_id)

            if self.delete_after_upload:
                self._delete_file(file_path)

            if self.on_complete:
                await self.on_complete(item)

        except TaskCancelled:
            logger.info("upload CANCELLED task=%s destination=%s", item.task_id, destination)
            self._delete_file(file_path)
        except asyncio.CancelledError:
            logger.info("upload asyncio-CANCELLED task=%s destination=%s", item.task_id, destination)
            self._delete_file(file_path)
            raise
        except Exception as exc:
            logger.exception(
                "upload FAILED task=%s destination=%s file=%s error=%s",
                item.task_id,
                destination,
                file_path,
                exc,
            )
            if self.database:
                await self.database.mark_failed(item.task_id, str(exc))
            if self.on_failed:
                await self.on_failed(item, exc)
        finally:
            self.running_tasks.pop(key, None)
            await ctx.cleanup()
            self.contexts.pop(key, None)
            self._cancelled.discard(key)

    @staticmethod
    def _delete_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete uploaded file: %s", path)

    def queue_size(self) -> int:
        return self.pool.qsize()

    def active_workers(self) -> int:
        """Return the number of currently processing upload tasks."""
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
