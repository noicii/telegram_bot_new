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
from app.core.telegram_gate import TelegramFloodGate

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
        self.telegram_gate = TelegramFloodGate(upload_max=4)
        self._install_dashboard_hooks(client)
        try:
            client.sleep_threshold = 0
        except Exception:
            logger.debug("could not set Pyrogram sleep_threshold=0", exc_info=True)
        self.upload = UploadManager(client, workers=4, database=self.db, telegram_gate=self.telegram_gate, on_progress=self._upload_progress, on_complete=self._upload_complete, on_failed=self._upload_failed)
        self.download = DownloadManager(output_dir, workers=2, database=self.db, on_progress=self._download_progress, on_complete=self._download_complete, on_failed=self._download_failed)
        self._started = False
        self._cleanup_task: asyncio.Task | None = None

    def _install_dashboard_hooks(self, client) -> None:
        """Route only the persistent live-dashboard message through the gate.

        Pyrogram Message.edit_text() is a bound shortcut to Client.edit_message_text().
        The existing dashboard also refetched its own message before every edit.
        We cache the dashboard Message locally, remove that extra Telegram read,
        and gate only dashboard-shaped edits so selection/UI edits stay normal.
        """
        if getattr(client, "_v2_dashboard_gate_installed", False):
            return
        setattr(client, "_v2_dashboard_gate_installed", True)
        cache: dict[tuple[int | str, int], Any] = {}
        gate = self.telegram_gate
        original_send = client.send_message
        original_get = client.get_messages
        original_edit = client.edit_message_text

        def is_dashboard_text(value: Any) -> bool:
            return isinstance(value, str) and value.startswith("📊 **LIVE DOWNLOAD / UPLOAD**")

        async def send_message(*args, **kwargs):
            message = await original_send(*args, **kwargs)
            text = kwargs.get("text")
            if text is None and len(args) >= 2:
                text = args[1]
            if is_dashboard_text(text):
                chat_id = kwargs.get("chat_id", args[0] if args else None)
                if chat_id is not None and getattr(message, "id", None) is not None:
                    cache[(chat_id, message.id)] = message
            return message

        async def get_messages(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            message_ids = kwargs.get("message_ids", args[1] if len(args) >= 2 else None)
            if isinstance(message_ids, int):
                cached = cache.get((chat_id, message_ids))
                if cached is not None:
                    return cached
            return await original_get(*args, **kwargs)

        async def edit_message_text(*args, **kwargs):
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            message_id = kwargs.get("message_id", args[1] if len(args) >= 2 else None)
            text = kwargs.get("text", args[2] if len(args) >= 3 else None)
            if not is_dashboard_text(text):
                return await original_edit(*args, **kwargs)
            key = f"{chat_id}:{message_id}"
            async def operation():
                result = await original_edit(*args, **kwargs)
                if getattr(result, "id", None) is not None:
                    cache[(chat_id, result.id)] = result
                return result
            await gate.publish_dashboard(key, operation)
            return cache.get((chat_id, message_id))

        client.send_message = send_message
        client.get_messages = get_messages
        client.edit_message_text = edit_message_text

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
            async def render_latest() -> None:
                result = self.on_progress(kind, task_id, percent, current, total, speed, eta, details)
                if asyncio.iscoroutine(result):
                    await result
            row = await self.db.get_task(task_id)
            chat_id = int(row["chat_id"]) if row and row.get("chat_id") else None
            if chat_id is not None:
                await self.telegram_gate.publish_dashboard(str(chat_id), render_latest)

    async def _download_progress(self, *args):
        await self._forward_progress("download", args)

    async def _upload_progress(self, *args):
        await self._forward_progress("upload", args)

    async def _download_complete(self, item: QueueItem, path: Path):
        task_id = str(item.task_id)
        destination = item.payload.get("chat_id")
        logger.info(
            "download COMPLETE task=%s file=%s size=%.1fMiB; handing off to upload destination=%s",
            task_id,
            path,
            path.stat().st_size / 1024 / 1024 if path.is_file() else 0,
            destination,
        )
        await self.db.update_task(task_id, status="uploading", file_path=str(path), progress=0)
        payload = dict(item.payload)
        payload["file_path"] = str(path)
        payload["task_type"] = "upload"
        await self.upload.submit(item.task_id, payload)
        logger.info("upload QUEUED task=%s file=%s", task_id, path)

    async def _download_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self.db.mark_failed(task_id, str(error))
        await remove_artifact_async(item.payload.get("file_path") or (self.output_dir / item.payload.get("filename", f"{task_id}.mp4")))
        await self._cleanup_now()
        if self.on_failed:
            async def render_failed() -> None:
                await self.on_failed(task_id, error)
            chat_id = int(item.payload.get("chat_id")) if item.payload.get("chat_id") else None
            if chat_id is not None:
                await self.telegram_gate.publish_dashboard(str(chat_id), render_failed)

    async def _upload_complete(self, item: QueueItem):
        task_id = str(item.task_id)
        task = await self.db.get_task(task_id)
        if task and task.get("file_path"):
            await remove_artifact_async(task["file_path"])
        await self.db.mark_completed(task_id)
        await self._cleanup_now()
        if self.on_complete:
            async def render_complete() -> None:
                await self.on_complete(task_id)
            chat_id = int(task.get("chat_id")) if task and task.get("chat_id") else None
            if chat_id is not None:
                await self.telegram_gate.publish_dashboard(str(chat_id), render_complete)

    async def _upload_failed(self, item: QueueItem, error: Exception):
        task_id = str(item.task_id)
        await self.db.mark_failed(task_id, str(error))
        if item.payload.get("file_path"):
            await remove_artifact_async(item.payload["file_path"])
        await self._cleanup_now()
        if self.on_failed:
            async def render_failed() -> None:
                await self.on_failed(task_id, error)
            chat_id = int(item.payload.get("chat_id")) if item.payload.get("chat_id") else None
            if chat_id is not None:
                await self.telegram_gate.publish_dashboard(str(chat_id), render_failed)

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
        await self.telegram_gate.close()
        await self._cleanup_now()
