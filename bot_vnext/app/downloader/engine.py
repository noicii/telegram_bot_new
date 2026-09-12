"""V2 hybrid downloader engine.

The engine is intentionally provider-agnostic. It executes multiple download
strategies with isolated process ownership so cancellation can stop the exact
operation without touching other queue workers.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from app.core.task import TaskCancelled, TaskContext

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float | None, int | None, int | None, float | None], Awaitable[None] | None]


class DownloadError(Exception):
    """All expected downloader-engine failures are normalized to this type."""


class HybridDownloader:
    """Try the most appropriate available engine, then fall back safely."""

    def __init__(self, output_dir: str | Path, retries: int = 2):
        self.output_dir = Path(output_dir)
        self.retries = max(0, int(retries))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def download(
        self,
        url: str,
        task: TaskContext,
        filename: str,
        progress: ProgressCallback | None = None,
    ) -> Path:
        task.check_cancelled()
        output = self.output_dir / filename
        task.register_temp(output)

        scheme = urlparse(url).scheme.lower()
        if scheme not in {"http", "https"}:
            raise DownloadError("Unsupported URL scheme")

        plan = self._build_plan(url)
        errors: list[str] = []
        for engine in plan:
            task.check_cancelled()
            for attempt in range(self.retries + 1):
                task.check_cancelled()
                try:
                    if output.exists():
                        output.unlink(missing_ok=True)
                    logger.info("task=%s engine=%s attempt=%s", task.task_id, engine, attempt + 1)
                    if engine == "aria2c":
                        await self._aria2c(url, output, task, progress)
                    elif engine == "direct":
                        await self._direct(url, output, task, progress)
                    elif engine == "ffmpeg":
                        await self._ffmpeg(url, output, task, progress)
                    elif engine == "yt-dlp":
                        await self._ytdlp(url, output, task, progress)
                    elif engine == "browser":
                        await self._browser(url, output, task, progress)
                    else:
                        raise DownloadError(f"Unknown engine: {engine}")
                    task.check_cancelled()
                    if output.exists() and output.stat().st_size > 0:
                        task.temp_paths.discard(output)
                        return output
                    raise DownloadError("Engine returned no media file")
                except TaskCancelled:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors.append(f"{engine}: {exc}")
                    logger.warning("task=%s %s failed: %s", task.task_id, engine, exc)
                    await asyncio.sleep(min(2.0 * (attempt + 1), 5.0))

        raise DownloadError("All download methods failed: " + " | ".join(errors[-8:]))

    @staticmethod
    def _build_plan(url: str) -> list[str]:
        lower = url.lower()
        if ".m3u8" in lower or ".mpd" in lower:
            return ["ffmpeg", "browser", "yt-dlp", "direct"]
        if any(ext in lower for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v")):
            return ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        return ["yt-dlp", "browser", "ffmpeg", "aria2c", "direct"]

    async def _direct(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        import aiohttp

        tmp = output.with_suffix(output.suffix + ".part")
        task.register_temp(tmp)
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
        headers = task.metadata.get("headers") or {}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                content_type = (response.headers.get("Content-Type") or "").lower()
                final_url = str(response.url).lower()
                if content_type.startswith(("text/html", "text/plain", "application/json")):
                    raise DownloadError(f"Direct response is not media ({content_type or 'unknown'})")
                if not any(token in final_url for token in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".m3u8", ".mpd")) and not content_type.startswith(("video/", "audio/", "application/octet-stream")):
                    raise DownloadError(f"Direct response is not recognized media ({content_type or 'unknown'})")
                total = int(response.headers.get("Content-Length") or 0) or None
                done = 0
                with tmp.open("wb") as fh:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        task.check_cancelled()
                        fh.write(chunk)
                        done += len(chunk)
                        await self._report(progress, done * 100 / total if total else None, done, total, None)
        tmp.replace(output)
        task.temp_paths.discard(tmp)

    async def _aria2c(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        binary = shutil.which("aria2c")
        if not binary:
            raise DownloadError("aria2c is not installed")
        proc = await asyncio.create_subprocess_exec(
            binary, "--allow-overwrite=true", "--auto-file-renaming=false",
            "--summary-interval=1", "--console-log-level=warn",
            "--dir", str(output.parent), "--out", output.name, url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        task.register_process(proc)
        try:
            while True:
                task.check_cancelled()
                line = await proc.stdout.readline()
                if not line:
                    break
                if progress and output.exists():
                    await self._report(progress, None, output.stat().st_size, None, None)
            rc = await proc.wait()
            if rc != 0:
                raise DownloadError(f"aria2c exited with code {rc}")
        finally:
            task.unregister_process(proc)

    async def _ffmpeg(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        binary = shutil.which("ffmpeg")
        if not binary:
            raise DownloadError("ffmpeg is not installed")
        proc = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error", "-y", "-i", url,
            "-c", "copy", str(output),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        task.register_process(proc)
        try:
            while proc.returncode is None:
                task.check_cancelled()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    if progress and output.exists():
                        await self._report(progress, None, output.stat().st_size, None, None)
            err = await proc.stderr.read()
            if proc.returncode != 0:
                raise DownloadError(err.decode(errors="ignore")[-1000:] or f"ffmpeg exited with code {proc.returncode}")
        finally:
            task.unregister_process(proc)

    async def _ytdlp(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        binary = shutil.which("yt-dlp") or sys.executable
        if binary == sys.executable:
            command = [binary, "-m", "yt_dlp"]
        else:
            command = [binary]
        if task.metadata.get("cookiefile"):
            command += ["--cookies", str(task.metadata["cookiefile"])]
        command += [
            "--newline", "--no-part", "-f", "bv*+ba/b",
            "--merge-output-format", "mp4", "-o", str(output), url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        task.register_process(proc)
        try:
            while True:
                task.check_cancelled()
                line = await proc.stdout.readline()
                if not line:
                    break
                if progress and output.exists():
                    await self._report(progress, None, output.stat().st_size, None, None)
            rc = await proc.wait()
            if rc != 0:
                raise DownloadError(f"yt-dlp exited with code {rc}")
        finally:
            task.unregister_process(proc)

    async def _browser(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise DownloadError("Playwright is not installed") from exc
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            task.metadata["browser"] = browser
            try:
                page = await browser.new_page()
                task.metadata["browser_page"] = page
                captured: list[str] = []

                async def on_response(response):
                    u = response.url
                    if ".m3u8" in u or ".mpd" in u:
                        captured.append(u)

                page.on("response", on_response)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(30):
                    task.check_cancelled()
                    if captured:
                        break
                    await asyncio.sleep(1)
                if not captured:
                    raise DownloadError("Browser could not discover a media stream")
                await self._ffmpeg(captured[0], output, task, progress)
            finally:
                await browser.close()
                task.metadata.pop("browser_page", None)
                task.metadata.pop("browser", None)

    @staticmethod
    async def _report(callback, percent, current, total, speed):
        if callback:
            result = callback(percent, current, total, speed)
            if asyncio.iscoroutine(result):
                await result
