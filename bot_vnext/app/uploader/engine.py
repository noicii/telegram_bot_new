"""Telegram upload engine for Bot V2.

Uploads are deliberately independent from downloads. The caller owns the
asyncio task, so cancelling one upload does not affect the other upload
workers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Any

from pyrogram import Client
from pyrogram.errors import FloodWait

from app.core.task import TaskCancelled, TaskContext

ProgressCallback = Callable[[float, int, int, float, int], Awaitable[None] | None]


class UploadError(Exception):
    """Expected upload failure."""


class UploadEngine:
    def __init__(self, client: Client, *, retries: int = 3):
        self.client = client
        self.retries = max(0, int(retries))

    async def upload(
        self,
        task: TaskContext,
        file_path: str | Path,
        *,
        chat_id: int | str,
        caption: str | None = None,
        thumbnail: str | Path | None = None,
        mode: str = "video",
        title: str | None = None,
        duration: int | None = None,
        width: int | None = None,
        height: int | None = None,
        supports_streaming: bool = True,
        progress: ProgressCallback | None = None,
        reply_to_message_id: int | None = None,
    ) -> Any:
        path = Path(file_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise UploadError(f"Upload file not found or empty: {path}")

        task.check_cancelled()
        total = path.stat().st_size
        progress_state = {"last": 0.0, "started": time.monotonic()}
        progress_tasks: set[asyncio.Task] = set()
        loop = asyncio.get_running_loop()

        async def report(current: int, force: bool = False):
            now = time.monotonic()
            elapsed = max(now - progress_state["started"], 0.001)
            if not force and now - progress_state["last"] < 0.75:
                return
            progress_state["last"] = now
            speed = current / elapsed
            percent = (current * 100.0 / total) if total else 0.0
            remaining = max(total - current, 0)
            eta = int(remaining / speed) if speed > 0 else 0
            if progress:
                result = progress(percent, current, total, speed, eta)
                if asyncio.iscoroutine(result):
                    await result

        def pyrogram_progress(current: int, total_bytes: int, *_args):
            if task.is_cancelled() or loop.is_closed():
                return
            current_total = total_bytes or total

            def schedule():
                if task.is_cancelled() or loop.is_closed():
                    return
                pending = loop.create_task(report(current, force=current >= current_total))
                progress_tasks.add(pending)
                pending.add_done_callback(progress_tasks.discard)

            loop.call_soon_threadsafe(schedule)

        try:
            for attempt in range(self.retries + 1):
                task.check_cancelled()
                try:
                    result = await self._send(
                        task,
                        path,
                        chat_id=chat_id,
                        caption=caption,
                        thumbnail=thumbnail,
                        mode=mode,
                        title=title,
                        duration=duration,
                        width=width,
                        height=height,
                        supports_streaming=supports_streaming,
                        progress=pyrogram_progress,
                        reply_to_message_id=reply_to_message_id,
                    )
                    task.check_cancelled()
                    await report(total, force=True)
                    return result
                except TaskCancelled:
                    raise
                except asyncio.CancelledError:
                    raise
                except FloodWait as exc:
                    if attempt >= self.retries:
                        raise UploadError(f"Telegram FloodWait: {exc.value}s") from exc
                    await asyncio.sleep(exc.value)
                except Exception as exc:
                    if attempt >= self.retries:
                        raise UploadError(str(exc)) from exc
                    await asyncio.sleep(min(2 ** attempt, 8))
        finally:
            if progress_tasks:
                await asyncio.gather(*progress_tasks, return_exceptions=True)

    async def _send(
        self,
        task: TaskContext,
        path: Path,
        *,
        chat_id,
        caption,
        thumbnail,
        mode,
        title,
        duration,
        width,
        height,
        supports_streaming,
        progress,
        reply_to_message_id,
    ):
        task.check_cancelled()
        common = {
            "chat_id": chat_id,
            "caption": caption,
            "progress": progress,
        }
        if reply_to_message_id is not None:
            common["reply_to_message_id"] = reply_to_message_id

        if thumbnail:
            thumb = Path(thumbnail)
            if thumb.is_file():
                common["thumb"] = str(thumb)

        normalized = (mode or "video").lower()
        if normalized in {"document", "file", "doc"}:
            return await self.client.send_document(document=str(path), **common)

        if normalized in {"audio", "music"}:
            if title:
                common["title"] = title
            if duration:
                common["duration"] = duration
            return await self.client.send_audio(audio=str(path), **common)

        if duration:
            common["duration"] = duration
        if width:
            common["width"] = width
        if height:
            common["height"] = height
        common["supports_streaming"] = bool(supports_streaming)

        return await self.client.send_video(video=str(path), **common)
