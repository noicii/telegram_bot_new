"""End-to-end download -> upload orchestration for Bot V2."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.queue.worker_pool import QueueItem
from app.queue.download_manager import DownloadManager
from app.queue.upload_manager import UploadManager
from app.storage.database import Database

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, client, output_dir: str | Path, database: Database | None = None,
                 on_progress: Callable[..., Awaitable[None] | None] | None = None,
                 on_complete: Callable[[str], Awaitable[None]] | None = None,
                 on_failed: Callable[[str, Exception], Awaitable[None]] | None = None) -> None:
        self.db = database or Database()
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_failed = on_failed
        self.upload = UploadManager(client, workers=4, on_progress=self._upload_progress, on_complete=self._upload_complete, on_failed=self._upload_failed)
        self.download = DownloadManager(output_dir, workers=2, on_progress=self._download_progress, on_complete=self._download_complete, on_failed=self._download_failed)
        self._started = False

    async def start(self) -> None:
        if self._started: return
        await self.db.init()
        await self.db.recover_after_crash()
        await self.upload.start()
        await self.download.start()
        self._started = True
        await self._recover_queue()

    async def _recover_queue(self) -> None:
        for row in await self.db.get_queued_tasks():
            try: await self._dispatch_row(row)
            except Exception: logger.exception("failed to recover task %s", row.get("id"))

    async def _dispatch_row(self, row: dict[str, Any]) -> None:
        task_id = str(row["id"])
        payload = {
            "url": row.get("url"), "filename": Path(row.get("file_path") or f"{task_id}.mp4").name,
            "file_path": row.get("file_path"), "chat_id": row.get("chat_id"), "message_id": row.get("message_id"),
            "caption": row.get("caption"), "thumbnail": row.get("thumbnail"), "mode": row.get("mode") or "video",
            "title": row.get("title"), "metadata": row.get("metadata") or {},
        }
        if str(row.get("task_type") or "download") == "upload": await self.upload.submit(task_id, payload)
        else: await self.download.submit(task_id, payload)

    async def submit(self, task_id: int | str, payload: dict[str, Any]) -> None:
        await self.db.create_task(str(task_id), payload.get("task_type", "download"), **{k: v for k, v in payload.items() if k != "task_type"})
        row = await self.db.get_task(str(task_id))
        if not row: raise RuntimeError(f"Task {task_id} was not persisted")
        await self._dispatch_row(row)

    async def retry(self, task_id: int | str) -> bool:
        task_id = str(task_id)
        task = await self.db.get_task(task_id)
        if not task or task.get("status") not in {"failed", "cancelled"}: return False
        if not await self.db.reset_for_retry(task_id): return False
        await self._dispatch_row(await self.db.get_task(task_id))
        return True

    async def cancel_download(self, task_id: int | str) -> bool:
        cancelled = await self.download.cancel(task_id)
        if cancelled: await self.db.mark_cancelled(str(task_id))
        return cancelled

    async def cancel_upload(self, task_id: int | str) -> bool:
        cancelled = await self.upload.cancel(task_id)
        if cancelled: await self.db.mark_cancelled(str(task_id))
        return cancelled

    async def cancel(self, task_id: int | str) -> bool:
        return (await self.cancel_download(task_id)) or (await self.cancel_upload(task_id))

    async def _forward_progress(self, kind, args):
        task_id = str(args[0]) if args else ""
        percent = args[1] if len(args) > 1 else 0
        current = args[2] if len(args) > 2 else 0
        total = args[3] if len(args) > 3 else 0
        speed = args[4] if len(args) > 4 else 0
        eta = args[5] if len(args) > 5 else 0
        try:
            await self.db.update_progress(task_id, progress=percent or 0, speed=speed or 0, eta=eta or 0)
        except Exception:
            logger.debug("progress persistence failed for %s", task_id, exc_info=True)
        if self.on_progress:
            result = self.on_progress(kind, task_id, percent, current, total, speed, eta)
            if asyncio.iscoroutine(result): await result

    async def _download_progress(self, *args): await self._forward_progress("download", args)
    async def _upload_progress(self, *args): await self._forward_progress("upload", args)

    async def _download_complete(self, item: QueueItem, path: Path):
        task_id = str(item.task_id)
        await self.db.update_task(task_id, status="uploading", file_path=str(path), progress=0)
        payload = dict(item.payload); payload["file_path"] = str(path); payload["task_type"] = "upload"
        await self.upload.submit(task_id, payload)

    async def _retry_or_fail(self, task_id: str, error: Exception, redispatch: bool = True):
        allowed = await self.db.increment_retry(task_id)
        if allowed and redispatch:
            try:
                row = await self.db.get_task(task_id)
                if row: await self._dispatch_row(row)
                return
            except Exception:
                logger.exception("retry dispatch failed for %s", task_id)
        if not allowed: await self.db.mark_failed(task_id, str(error))
        else: await self.db.mark_failed(task_id, f"Retry dispatch failed: {error}")

    async def _download_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self._retry_or_fail(task_id, error)
        if self.on_failed: await self.on_failed(task_id, error)

    async def _upload_complete(self, item: QueueItem):
        task_id = str(item.task_id)
        await self.db.mark_completed(task_id)
        if self.on_complete: await self.on_complete(task_id)

    async def _upload_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self._retry_or_fail(task_id, error)
        if self.on_failed: await self.on_failed(task_id, error)

    async def stop(self) -> None:
        if not self._started: return
        await self.download.stop()
        await self.upload.stop()
        self._started = False
