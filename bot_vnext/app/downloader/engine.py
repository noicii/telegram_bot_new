"""V2 hybrid downloader engine with explicit or automatic method selection."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

from app.core.task import TaskCancelled, TaskContext

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float | None, int | None, int | None, float | None], Awaitable[None] | None]
METHODS = {"auto", "hls-multi", "yt-dlp", "browser", "ffmpeg", "aria2c", "direct"}


class DownloadError(Exception):
    """All expected downloader-engine failures are normalized to this type."""


class HybridDownloader:
    """Run one explicitly selected engine, or the automatic fallback chain."""

    def __init__(self, output_dir: str | Path, retries: int = 2):
        self.output_dir = Path(output_dir)
        self.retries = max(0, int(retries))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str, task: TaskContext, filename: str, progress: ProgressCallback | None = None) -> Path:
        task.check_cancelled()
        output = self.output_dir / filename
        task.register_temp(output)
        scheme = urlparse(url).scheme.lower()
        if scheme not in {"http", "https"}:
            raise DownloadError("Unsupported URL scheme")

        requested = str(task.metadata.get("download_method") or "auto").lower()
        if requested not in METHODS:
            requested = "auto"
        plan = self._build_plan(url, requested)
        logger.info("task=%s download_method=%s plan=%s", task.task_id, requested, plan)

        errors: list[str] = []
        for engine in plan:
            task.check_cancelled()
            for attempt in range(self.retries + 1):
                task.check_cancelled()
                try:
                    if output.exists():
                        output.unlink(missing_ok=True)
                    logger.info("task=%s engine=%s attempt=%s/%s", task.task_id, engine, attempt + 1, self.retries + 1)
                    if engine == "hls-multi":
                        await self._hls_multi(url, output, task, progress)
                    elif engine == "aria2c":
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
                    errors.append(f"{engine} attempt {attempt + 1}: {exc}")
                    logger.warning("task=%s %s attempt=%s failed: %s", task.task_id, engine, attempt + 1, exc)
                    if requested != "auto":
                        if attempt < self.retries:
                            await asyncio.sleep(min(2.0 * (attempt + 1), 5.0))
                            continue
                        raise DownloadError(f"Selected method '{engine}' failed after {self.retries + 1} attempts: {exc}") from exc
                    await asyncio.sleep(min(2.0 * (attempt + 1), 5.0))

        raise DownloadError("All download methods failed: " + " | ".join(errors[-8:]))

    @staticmethod
    def _build_plan(url: str, requested: str = "auto") -> list[str]:
        if requested != "auto":
            return [requested]
        lower = url.lower()
        if ".m3u8" in lower or ".mpd" in lower:
            return ["hls-multi", "ffmpeg", "browser", "yt-dlp", "direct"]
        if any(ext in lower for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v")):
            return ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        return ["yt-dlp", "hls-multi", "browser", "ffmpeg", "aria2c", "direct"]

    async def _hls_multi(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        """Download HLS segments concurrently (4 at a time), then mux with FFmpeg."""
        import aiohttp

        binary = shutil.which("ffmpeg")
        if not binary:
            raise DownloadError("ffmpeg is not installed")

        stream_url = url
        headers = dict(task.metadata.get("headers") or {})
        headers.setdefault("User-Agent", "Mozilla/5.0")
        if task.metadata.get("referer"):
            headers.setdefault("Referer", str(task.metadata["referer"]))
        cookies = task.metadata.get("cookies") or {}

        if ".m3u8" not in url.lower():
            stream_url = await self._discover_hls(url, task, headers)
        if not stream_url:
            raise DownloadError("HLS stream URL could not be discovered")

        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
        connector = aiohttp.TCPConnector(limit=150, limit_per_host=40, ttl_dns_cache=300, enable_cleanup_closed=True)
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout, connector=connector) as session:
            playlist = await self._fetch_text(session, stream_url)
            if "#EXT-X-STREAM-INF" in playlist:
                variant = self._best_variant(stream_url, playlist)
                playlist = await self._fetch_text(session, variant)
                stream_url = variant

            segments = []
            for line in playlist.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    segments.append(urljoin(stream_url, line))
            if not segments:
                raise DownloadError("No HLS segments found")

            sem = asyncio.Semaphore(4)
            started = time.monotonic()
            total_bytes = 0
            completed = 0
            results: list[bytes | None] = [None] * len(segments)

            async def fetch(index: int, segment_url: str):
                nonlocal total_bytes, completed
                async with sem:
                    last_exc = None
                    for attempt in range(3):
                        task.check_cancelled()
                        try:
                            async with session.get(segment_url) as response:
                                response.raise_for_status()
                                data = await response.read()
                                results[index] = data
                                total_bytes += len(data)
                                completed += 1
                                elapsed = max(time.monotonic() - started, 0.001)
                                await self._report(progress, completed * 100 / len(segments), total_bytes, None, total_bytes / elapsed)
                                return
                        except TaskCancelled:
                            raise
                        except Exception as exc:
                            last_exc = exc
                            if attempt < 2:
                                await asyncio.sleep(2 ** attempt)
                    raise DownloadError(f"HLS segment {index + 1} failed: {last_exc}")

            workers = [asyncio.create_task(fetch(i, u)) for i, u in enumerate(segments)]
            try:
                await asyncio.gather(*workers)
            except Exception:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise

            proc = await asyncio.create_subprocess_exec(
                binary, "-hide_banner", "-loglevel", "error", "-y", "-f", "mpegts", "-i", "pipe:0",
                "-c", "copy", "-movflags", "+faststart", str(output),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            task.register_process(proc)
            try:
                for data in results:
                    task.check_cancelled()
                    if not data:
                        raise DownloadError("Empty HLS segment")
                    try:
                        proc.stdin.write(data)
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError) as exc:
                        raise DownloadError(f"FFmpeg HLS pipe failed: {exc}") from exc
                proc.stdin.close()
                _, stderr_data = await proc.communicate()
                if proc.returncode != 0:
                    raise DownloadError(stderr_data.decode(errors="ignore")[-1000:] or f"ffmpeg exited with code {proc.returncode}")
            finally:
                task.unregister_process(proc)

    async def _discover_hls(self, url: str, task: TaskContext, headers: dict) -> str | None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise DownloadError("Playwright is not installed") from exc
        captured: list[str] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            task.metadata["browser"] = browser
            try:
                context = await browser.new_context(user_agent=headers.get("User-Agent"), extra_http_headers={k: v for k, v in headers.items() if k.lower() != "user-agent"})
                page = await context.new_page()
                task.metadata["browser_page"] = page

                async def on_response(response):
                    low = response.url.lower()
                    if ".m3u8" in low and not any(x in low for x in ("google", "analytics", "preview", "thumb")):
                        if response.url not in captured:
                            captured.append(response.url)

                page.on("response", on_response)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(30):
                    task.check_cancelled()
                    if captured:
                        break
                    await asyncio.sleep(1)
                raw_cookies = await context.cookies()
                if raw_cookies and not task.metadata.get("cookies"):
                    task.metadata["cookies"] = {c["name"]: c["value"] for c in raw_cookies}
                return captured[0] if captured else None
            finally:
                task.metadata.pop("browser_page", None)
                task.metadata.pop("browser", None)
                await browser.close()

    @staticmethod
    async def _fetch_text(session, url: str) -> str:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

    @staticmethod
    def _best_variant(base_url: str, playlist: str) -> str:
        lines = [line.strip() for line in playlist.splitlines()]
        variants: list[tuple[int, str]] = []
        pending = 0
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                match = re.search(r"(?:RESOLUTION|BANDWIDTH)=(?:\d+x)?(\d+)", line)
                pending = int(match.group(1)) if match else 0
            elif pending and line and not line.startswith("#"):
                variants.append((pending, urljoin(base_url, line)))
                pending = 0
        return max(variants, key=lambda x: x[0])[1] if variants else base_url

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
        proc = await asyncio.create_subprocess_exec(binary, "--allow-overwrite=true", "--auto-file-renaming=false", "--summary-interval=1", "--console-log-level=warn", "--dir", str(output.parent), "--out", output.name, url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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
        proc = await asyncio.create_subprocess_exec(binary, "-hide_banner", "-loglevel", "error", "-y", "-i", url, "-c", "copy", str(output), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
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
        command = [binary, "-m", "yt_dlp"] if binary == sys.executable else [binary]
        if task.metadata.get("cookiefile"):
            command += ["--cookies", str(task.metadata["cookiefile"])]
        command += ["--newline", "--no-part", "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(output), url]
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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
        stream = await self._discover_hls(url, task, {"User-Agent": "Mozilla/5.0"})
        if not stream:
            raise DownloadError("Browser could not discover media stream")
        await self._ffmpeg(stream, output, task, progress)

    @staticmethod
    async def _report(callback, percent, current, total, speed):
        if callback:
            result = callback(percent, current, total, speed)
            if asyncio.iscoroutine(result):
                await result
