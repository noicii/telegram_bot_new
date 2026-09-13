"""Telegram upload engine for Bot V2.

Uploads are deliberately independent from downloads. Files larger than the
Telegram upload limit are automatically split into safe video parts before
uploading, so a large downloaded video does not fail at the upload stage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Awaitable, Callable, Any

from pyrogram import Client
from pyrogram.errors import FloodWait

from app.core.task import TaskCancelled, TaskContext

logger = logging.getLogger(__name__)
ProgressCallback = Callable[..., Awaitable[None] | None]

MAX_UPLOAD_BYTES = 2000 * 1024 * 1024
SPLIT_TARGET_BYTES = 1900 * 1024 * 1024


class UploadError(Exception):
    """Expected upload failure."""


class UploadEngine:
    def __init__(self, client: Client, *, retries: int = 3):
        self.client = client
        self.retries = max(0, int(retries))

    async def upload(self, task: TaskContext, file_path: str | Path, *, chat_id: int | str,
                     caption: str | None = None, thumbnail: str | Path | None = None,
                     mode: str = "video", title: str | None = None, duration: int | None = None,
                     width: int | None = None, height: int | None = None,
                     supports_streaming: bool = True, progress: ProgressCallback | None = None,
                     reply_to_message_id: int | None = None) -> Any:
        path = Path(file_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise UploadError(f"Upload file not found or empty: {path}")
        task.check_cancelled()
        original_size = path.stat().st_size

        if original_size > MAX_UPLOAD_BYTES:
            normalized = (mode or "video").lower()
            if normalized not in {"video", "document", "file", "doc"}:
                raise UploadError(f"File is larger than 2000 MiB and automatic splitting is only supported for video/document uploads: {path}")
            parts_dir = path.parent / f".{path.stem}_parts"
            parts = await self._split_large_video(task, path, parts_dir)
            try:
                uploaded = None
                completed_bytes = 0
                total_bytes = sum(p.stat().st_size for p in parts)
                part_count = len(parts)
                for index, part in enumerate(parts, 1):
                    task.check_cancelled()
                    part_caption = caption
                    if part_count > 1:
                        suffix = f"\n\n📦 Part {index}/{part_count}"
                        part_caption = f"{caption}{suffix}" if caption else suffix.lstrip()
                    part_size = part.stat().st_size
                    if part_size > MAX_UPLOAD_BYTES:
                        raise UploadError(f"Generated part is still larger than 2000 MiB ({part_size / 1024 / 1024:.1f} MiB): {part}")

                    async def part_progress(percent, current, _total, speed, eta, *, base=completed_bytes, psize=part_size, part_no=index, parts_total=part_count):
                        if progress:
                            overall_current = base + min(current, psize)
                            overall_percent = (overall_current * 100.0 / total_bytes) if total_bytes else percent
                            details = {
                                "part": part_no,
                                "parts": parts_total,
                                "part_current": min(current, psize),
                                "part_total": psize,
                            }
                            result = progress(overall_percent, overall_current, total_bytes, speed, eta, details)
                            if asyncio.iscoroutine(result):
                                await result

                    uploaded = await self._upload_single(task, part, chat_id=chat_id, caption=part_caption,
                        thumbnail=thumbnail, mode=mode, title=title, duration=duration, width=width,
                        height=height, supports_streaming=supports_streaming, progress=part_progress,
                        reply_to_message_id=reply_to_message_id)
                    completed_bytes += part_size
                if progress:
                    result = progress(100.0, total_bytes, total_bytes, 0.0, 0, {"part": part_count, "parts": part_count, "part_current": total_bytes and parts[-1].stat().st_size, "part_total": parts[-1].stat().st_size})
                    if asyncio.iscoroutine(result):
                        await result
                return uploaded
            finally:
                self._cleanup_parts(parts_dir, parts)

        return await self._upload_single(task, path, chat_id=chat_id, caption=caption, thumbnail=thumbnail,
            mode=mode, title=title, duration=duration, width=width, height=height,
            supports_streaming=supports_streaming, progress=progress, reply_to_message_id=reply_to_message_id)

    async def _upload_single(self, task: TaskContext, path: Path, *, chat_id: int | str,
                             caption: str | None, thumbnail: str | Path | None, mode: str,
                             title: str | None, duration: int | None, width: int | None,
                             height: int | None, supports_streaming: bool,
                             progress: ProgressCallback | None, reply_to_message_id: int | None) -> Any:
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
                    result = await self._send(task, path, chat_id=chat_id, caption=caption, thumbnail=thumbnail,
                        mode=mode, title=title, duration=duration, width=width, height=height,
                        supports_streaming=supports_streaming, progress=pyrogram_progress,
                        reply_to_message_id=reply_to_message_id)
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

    async def _send(self, task: TaskContext, path: Path, *, chat_id, caption, thumbnail, mode,
                    title, duration, width, height, supports_streaming, progress, reply_to_message_id):
        task.check_cancelled()
        common = {"chat_id": chat_id, "caption": caption, "progress": progress}
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
            if title: common["title"] = title
            if duration: common["duration"] = duration
            return await self.client.send_audio(audio=str(path), **common)
        if duration: common["duration"] = duration
        if width: common["width"] = width
        if height: common["height"] = height
        common["supports_streaming"] = bool(supports_streaming)
        return await self.client.send_video(video=str(path), **common)

    async def _split_large_video(self, task: TaskContext, source: Path, parts_dir: Path) -> list[Path]:
        """Split a large video into upload-safe MP4 parts using stream copy."""
        task.check_cancelled()
        await asyncio.to_thread(self._check_ffmpeg)
        parts_dir.mkdir(parents=True, exist_ok=True)
        duration = await asyncio.to_thread(self._probe_duration, source)
        if duration <= 0:
            raise UploadError(f"Could not determine video duration for splitting: {source}")
        size = source.stat().st_size
        target_seconds = max(30.0, duration * SPLIT_TARGET_BYTES / size * 0.88)
        output_pattern = str(parts_dir / "part_%03d.mp4")
        logger.info("splitting large upload file=%s size=%.1fMiB duration=%.1fs target_segment=%.1fs", source, size / 1024 / 1024, duration, target_seconds)
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0", "-c", "copy", "-f", "segment", "-segment_time", f"{target_seconds:.3f}", "-reset_timestamps", "1", "-movflags", "+faststart", output_pattern]
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-2000:]
            raise UploadError(f"FFmpeg could not split large video: {message}")
        parts = sorted(parts_dir.glob("part_*.mp4"))
        if not parts:
            raise UploadError(f"FFmpeg produced no upload parts for {source}")
        oversized = [p for p in parts if p.stat().st_size > MAX_UPLOAD_BYTES]
        if oversized:
            biggest = max(p.stat().st_size for p in oversized) / 1024 / 1024
            self._cleanup_parts(parts_dir, parts)
            raise UploadError(f"FFmpeg split produced an oversized part ({biggest:.1f} MiB). Source bitrate/keyframes prevent safe automatic splitting.")
        return parts

    @staticmethod
    def _check_ffmpeg() -> None:
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise UploadError("FFmpeg is required for automatic large-video splitting but is not installed") from exc

    @staticmethod
    def _probe_duration(path: Path) -> float:
        command = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout or "{}")
            return float((data.get("format") or {}).get("duration") or 0)
        except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
            raise UploadError(f"Could not probe video duration: {path}") from exc

    @staticmethod
    def _cleanup_parts(parts_dir: Path, parts: list[Path]) -> None:
        for part in parts:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete temporary upload part: %s", part)
        try:
            parts_dir.rmdir()
        except OSError:
            pass
