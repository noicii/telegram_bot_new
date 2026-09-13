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
from app.storage.cleanup import cleanup_download_artifacts_async, remove_artifact_async
from app.downloader.method_store import get_default_method

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 30


class Pipeline:
    def __init__(self, client, output_dir: str | Path, database: Database | None = None,
                 on_progress: Callable[..., Awaitable[None] | None] | None = None,
                 on_complete: Callable[[str], Awaitable[None]] | None = None,
                 on_failed: Callable[[str, Exception], Awaitable[None]] | None = None) -> None:
        self.db = database or Database()
        self.output_dir = Path(output_dir)
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_failed = on_failed
        self.upload = UploadManager(client, workers=4, database=self.db, on_progress=self._upload_progress, on_complete=self._upload_complete, on_failed=self._upload_failed)
        self.download = DownloadManager(output_dir, workers=2, database=self.db, on_progress=self._download_progress, on_complete=self._download_complete, on_failed=self._download_failed)
        self._started = False
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._started:
            return
        await cleanup_download_artifacts_async(self.output_dir, force=True)
        await self.db.init()
        await self.db.recover_after_crash()
        for path in await self.db.get_cleanup_paths():
            await remove_artifact_async(path)
        await cleanup_download_artifacts_async(self.output_dir, active_paths=await self.db.get_active_paths())
        await self.upload.start()
        await self.download.start()
        self._started = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="disk-cleanup")
        await self._recover_queue()

    async def _cleanup_loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                if not self._started:
                    break
                active = await self.db.get_active_paths()
                await cleanup_download_artifacts_async(self.output_dir, active_paths=active)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("periodic disk cleanup failed")

    async def _cleanup_now(self) -> None:
        try:
            await cleanup_download_artifacts_async(self.output_dir, active_paths=await self.db.get_active_paths())
        except Exception:
            logger.exception("disk cleanup failed")

    async def _recover_queue(self) -> None:
        for row in await self.db.get_queued_tasks():
            try:
                await self._dispatch_row(row)
            except Exception:
                logger.exception("failed to recover task %s", row.get("id"))

    async def _dispatch_row(self, row: dict[str, Any]) -> None:
        task_id = str(row["id"])
        payload = {
            "url": row.get("url"),
            "filename": Path(row.get("file_path") or f"{task_id}.mp4").name,
            "file_path": row.get("file_path"),
            "chat_id": row.get("chat_id"),
            "message_id": row.get("message_id"),
            "caption": row.get("caption"),
            "thumbnail": row.get("thumbnail"),
            "mode": row.get("mode") or "video",
            "title": row.get("title"),
            "metadata": row.get("metadata") or {},
        }
        if str(row.get("task_type") or "download") == "upload":
            await self.upload.submit(task_id, payload)
        else:
            await self.download.submit(task_id, payload)

    async def submit(self, task_id: int | str, payload: dict[str, Any]) -> None:
        await self.db.create_task(str(task_id), payload.get("task_type", "download"), **{k: v for k, v in payload.items() if k != "task_type"})
        row = await self.db.get_task(str(task_id))
        if not row:
            raise RuntimeError(f"Task {task_id} was not persisted")
        await self._dispatch_row(row)

    async def retry(self, task_id: int | str, method: str | None = None) -> bool:
        task_id = str(task_id)
        task = await self.db.get_task(task_id)
        if not task or task.get("status") not in {"failed", "cancelled"}:
            return False
        if task.get("task_type") == "download":
            selected = method or await get_default_method()
            metadata = dict(task.get("metadata") or {})
            metadata["download_method"] = selected
            await self.db.update_task(task_id, metadata=metadata, file_path=None)
        if not await self.db.reset_for_retry(task_id):
            return False
        row = await self.db.get_task(task_id)
        if not row:
            return False
        await self._dispatch_row(row)
        return True

    async def cancel_download(self, task_id: int | str) -> bool:
        cancelled = await self.download.cancel(task_id)
        if cancelled:
            await self.db.mark_cancelled(str(task_id))
            task = await self.db.get_task(str(task_id))
            if task and task.get("file_path"):
                await remove_artifact_async(task["file_path"])
            await self._cleanup_now()
        return cancelled

    async def cancel_upload(self, task_id: int | str) -> bool:
        cancelled = await self.upload.cancel(task_id)
        if cancelled:
            await self.db.mark_cancelled(str(task_id))
            task = await self.db.get_task(str(task_id))
            if task and task.get("file_path"):
                await remove_artifact_async(task["file_path"])
            await self._cleanup_now()
        return cancelled

    async def cancel(self, task_id: int | str) -> bool:
        return (await self.cancel_download(task_id)) or (await self.cancel_upload(task_id))

    async def _forward_progress(self, kind, args):
        task_id = str(args[0]) if args else ""
        percent = args[1] if len(args) > 1 else 0
        current = args[2] if len(args) > 2 else 0
        total = args[3] if len(args) > 3 else 0
        speed = args[4] if len(args) > 4 else 0
        eta = args[5] if len(args) > 5 and not isinstance(args[5], dict) else 0
        details = args[6] if len(args) > 6 and isinstance(args[6], dict) else (args[5] if len(args) > 5 and isinstance(args[5], dict) else None)
        try:
            await self.db.update_progress(task_id, progress=percent or 0, speed=speed or 0, eta=eta or 0)
        except Exception:
            logger.debug("progress persistence failed for %s", task_id, exc_info=True)
        if self.on_progress:
            result = self.on_progress(kind, task_id, percent, current, total, speed, eta, details)
            if asyncio.iscoroutine(result):
                await result

    async def _download_progress(self, *args):
        await self._forward_progress("download", args)

    async def _upload_progress(self, *args):
        await self._forward_progress("upload", args)

    async def _download_complete(self, item: QueueItem, path: Path):
        await self.db.update_task(str(item.task_id), status="uploading", file_path=str(path), progress=0)
        payload = dict(item.payload)
        payload["file_path"] = str(path)
        payload["task_type"] = "upload"
        await self.upload.submit(item.task_id, payload)

    async def _download_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self.db.mark_failed(task_id, str(error))
        await remove_artifact_async(item.payload.get("file_path") or (self.output_dir / item.payload.get("filename", f"{task_id}.mp4")))
        await self._cleanup_now()
        if self.on_failed:
            await self.on_failed(task_id, error)

    async def _upload_complete(self, item: QueueItem):
        task_id = str(item.task_id)
        task = await self.db.get_task(task_id)
        if task and task.get("file_path"):
            await remove_artifact_async(task["file_path"])
        await self.db.mark_completed(task_id)
        await self._cleanup_now()
        if self.on_complete:
            await self.on_complete(task_id)

    async def _upload_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self.db.mark_failed(task_id, str(error))
        if item.payload.get("file_path"):
            await remove_artifact_async(item.payload["file_path"])
        await self._cleanup_now()
        if self.on_failed:
            await self.on_failed(task_id, error)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
        self._cleanup_task = None
        await self.download.stop()
        await self.upload.stop()
        await self._cleanup_now()
