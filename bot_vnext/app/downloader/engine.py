"""V2 hybrid downloader engine with robust HLS/browser extraction."""
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
from app.downloader.browser_hls import BrowserHLSDownloader, BrowserHLSError

logger = logging.getLogger(__name__)
ProgressCallback = Callable[..., Awaitable[None] | None]
METHODS = {"auto", "hls-multi", "yt-dlp", "browser", "ffmpeg", "aria2c", "direct"}


class DownloadError(Exception):
    pass


class HybridDownloader:
    def __init__(self, output_dir: str | Path, retries: int = 2):
        self.output_dir = Path(output_dir)
        self.retries = max(0, int(retries))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str, task: TaskContext, filename: str, progress: ProgressCallback | None = None) -> Path:
        task.check_cancelled()
        output = self.output_dir / filename
        task.register_temp(output)
        if urlparse(url).scheme.lower() not in {"http", "https"}:
            raise DownloadError("Unsupported URL scheme")
        requested = str(task.metadata.get("download_method") or "auto").lower()
        if requested not in METHODS:
            requested = "auto"
        plan = self._build_plan(url, requested)
        logger.info("task=%s download_method=%s plan=%s", task.task_id, requested, plan)
        fallback_enabled = requested in {"auto", "hls-multi"}
        errors: list[str] = []
        for engine in plan:
            task.check_cancelled()
            for attempt in range(self.retries + 1):
                try:
                    if output.exists(): output.unlink(missing_ok=True)
                    logger.info("task=%s engine=%s attempt=%s/%s", task.task_id, engine, attempt + 1, self.retries + 1)
                    await self._report(progress, 0.0, 0, None, 0.0)
                    if engine == "hls-multi":
                        await self._hls_multi(url, output, task, progress)
                    elif engine == "aria2c":
                        await self._aria2c(url, output, task, progress)
                    elif engine == "direct":
                        await self._direct(url, output, task, progress)
                    elif engine == "ffmpeg":
                        await self._ffmpeg(task.metadata.get("stream_url") or url, output, task, progress, headers=task.metadata.get("headers") or None)
                    elif engine == "yt-dlp":
                        await self._ytdlp(task.metadata.get("stream_url") or url, output, task, progress)
                    elif engine == "browser":
                        await self._browser(url, output, task, progress)
                    else:
                        raise DownloadError(f"Unknown engine: {engine}")
                    task.check_cancelled()
                    if output.exists() and output.stat().st_size > 0:
                        task.temp_paths.discard(output)
                        await self._report(progress, 100.0, output.stat().st_size, output.stat().st_size, None)
                        return output
                    raise DownloadError("Engine returned no media file")
                except TaskCancelled:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors.append(f"{engine} attempt {attempt + 1}: {exc}")
                    logger.warning("task=%s %s attempt=%s failed: %s", task.task_id, engine, attempt + 1, exc)
                    if attempt < self.retries:
                        await asyncio.sleep(min(2.0 * (attempt + 1), 5.0))
                        continue
                    if not fallback_enabled:
                        raise DownloadError(f"Selected method '{engine}' failed after {self.retries + 1} attempts: {exc}") from exc
                    await asyncio.sleep(min(2.0 * (attempt + 1), 5.0))
                    break
        raise DownloadError("All download methods failed: " + " | ".join(errors[-8:]))

    @staticmethod
    def _build_plan(url: str, requested: str = "auto") -> list[str]:
        if requested == "hls-multi":
            return ["hls-multi", "ffmpeg", "browser", "yt-dlp", "direct"]
        if requested != "auto":
            return [requested]
        lower = url.lower()
        if ".m3u8" in lower or ".mpd" in lower:
            return ["hls-multi", "ffmpeg", "browser", "yt-dlp", "direct"]
        if any(ext in lower for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v")):
            return ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        return ["yt-dlp", "hls-multi", "browser", "ffmpeg", "aria2c", "direct"]

    @staticmethod
    def _browser_candidates(url: str) -> list[str]:
        candidates = [url]
        m = re.search(r"(?:luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\.com/(?:d|e)/([A-Za-z0-9]+)", url, re.I)
        if m:
            video_id = m.group(1)
            candidates.extend([f"https://luluvdo.com/e/{video_id}", f"https://lulustream.com/e/{video_id}", f"https://luluvdoo.com/e/{video_id}"])
        return list(dict.fromkeys(candidates))

    async def _hls_multi(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        """Download HLS with 16 concurrent workers while keeping segment bytes on disk, not RAM."""
        import aiohttp
        if not shutil.which("ffmpeg"):
            raise DownloadError("ffmpeg is not installed")
        headers = dict(task.metadata.get("headers") or {})
        headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36")
        if task.metadata.get("referer"):
            headers.setdefault("Referer", str(task.metadata["referer"]))
        cookies = dict(task.metadata.get("cookies") or {})
        stream_url = url if ".m3u8" in url.lower() else await self._discover_hls(url, task, headers, progress)
        if not stream_url:
            raise DownloadError("HLS stream URL could not be discovered")
        task.metadata["stream_url"] = stream_url
        task.metadata["headers"] = headers
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
        connector = aiohttp.TCPConnector(limit=150, limit_per_host=40, ttl_dns_cache=300, enable_cleanup_closed=True)
        segment_dir = self.output_dir / f".hls-{task.task_id}"
        segment_dir.mkdir(parents=True, exist_ok=True)
        task.register_temp(segment_dir)
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=cookies, timeout=timeout, connector=connector) as session:
                playlist = await self._fetch_text(session, stream_url)
                if "#EXT-X-STREAM-INF" in playlist:
                    stream_url = self._best_variant(stream_url, playlist)
                    task.metadata["stream_url"] = stream_url
                    playlist = await self._fetch_text(session, stream_url)
                if "#EXT-X-KEY:" in playlist:
                    logger.info("task=%s encrypted HLS detected; using FFmpeg", task.task_id)
                    await self._ffmpeg(stream_url, output, task, progress, headers=headers)
                    return
                segments = [urljoin(stream_url, line.strip()) for line in playlist.splitlines() if line.strip() and not line.strip().startswith("#")]
                if not segments:
                    raise DownloadError("No HLS segments found")
                sem = asyncio.Semaphore(16)
                started = time.monotonic()
                completed = 0
                total_bytes = 0
                retry_count = 0
                progress_lock = asyncio.Lock()
                await self._report(progress, 0.0, 0, None, 0.0, {"hls_completed": 0, "hls_total": len(segments), "hls_retries": 0})

                async def fetch(index: int, segment_url: str):
                    nonlocal completed, total_bytes, retry_count
                    path = segment_dir / f"{index:08d}.seg"
                    async with sem:
                        last_exc = None
                        for attempt in range(3):
                            task.check_cancelled()
                            try:
                                tmp = path.with_suffix(".part")
                                tmp.unlink(missing_ok=True)
                                async with session.get(segment_url) as response:
                                    response.raise_for_status()
                                    size = 0
                                    with tmp.open("wb") as fh:
                                        async for chunk in response.content.iter_chunked(1024 * 1024):
                                            task.check_cancelled()
                                            fh.write(chunk)
                                            size += len(chunk)
                                if size <= 0:
                                    raise DownloadError("Empty HLS segment")
                                tmp.replace(path)
                                async with progress_lock:
                                    completed += 1
                                    total_bytes += size
                                    elapsed = max(time.monotonic() - started, 0.001)
                                    await self._report(progress, completed * 100 / len(segments), total_bytes, None, total_bytes / elapsed, {"hls_completed": completed, "hls_total": len(segments), "hls_retries": retry_count})
                                return
                            except TaskCancelled:
                                raise
                            except Exception as exc:
                                last_exc = exc
                                path.with_suffix(".part").unlink(missing_ok=True)
                                if attempt < 2:
                                    async with progress_lock:
                                        retry_count += 1
                                    await asyncio.sleep(2 ** attempt)
                        raise DownloadError(f"HLS segment {index + 1} failed: {last_exc}")

                workers = [asyncio.create_task(fetch(i, segment)) for i, segment in enumerate(segments)]
                try:
                    await asyncio.gather(*workers)
                except Exception:
                    for worker in workers:
                        worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
                    raise

            concat_file = segment_dir / "segments.ffconcat"
            with concat_file.open("w", encoding="utf-8") as fh:
                fh.write("ffconcat version 1.0\n")
                for index in range(len(segments)):
                    fh.write(f"file '{(segment_dir / f'{index:08d}.seg').as_posix().replace(chr(39), chr(39)+chr(39))}'\n")
            proc = await asyncio.create_subprocess_exec("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(output), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            task.register_process(proc)
            try:
                _, stderr_data = await proc.communicate()
                if proc.returncode != 0:
                    raise DownloadError(stderr_data.decode(errors="ignore")[-1500:] or f"ffmpeg exited with code {proc.returncode}")
            finally:
                task.unregister_process(proc)
        finally:
            shutil.rmtree(segment_dir, ignore_errors=True)
            task.temp_paths.discard(segment_dir)

    async def _discover_hls(self, url: str, task: TaskContext, headers: dict, progress=None) -> str | None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise DownloadError("Playwright is not installed") from exc
        captured: list[str] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            task.metadata["browser"] = browser
            try:
                for candidate in self._browser_candidates(url):
                    task.check_cancelled(); captured.clear()
                    context = await browser.new_context(user_agent=headers.get("User-Agent"), extra_http_headers={k: v for k, v in headers.items() if k.lower() != "user-agent"})
                    page = await context.new_page(); task.metadata["browser_page"] = page
                    async def on_response(response):
                        low = response.url.lower()
                        if ".m3u8" in low and not any(x in low for x in ("google", "analytics", "preview", "thumb")) and response.url not in captured:
                            captured.append(response.url)
                    page.on("response", on_response)
                    try:
                        await page.goto(candidate, wait_until="domcontentloaded", timeout=30000)
                        await self._report(progress, 1.0, 0, None, 0.0)
                        for tick in range(25):
                            task.check_cancelled()
                            if captured: break
                            await asyncio.sleep(1)
                            if tick % 5 == 0: await self._report(progress, 1.0, 0, None, 0.0)
                        for frame in page.frames:
                            try:
                                found = await frame.evaluate("""() => { const out=[]; try { if(typeof jwplayer==='function'){const p=jwplayer();const c=p&&p.getConfig?p.getConfig():null;for(const x of (c&&c.sources)||[]) if(x&&x.file) out.push(x.file);} } catch(e) {} try { for(const v of document.querySelectorAll('video,source')) if(v.src) out.push(v.src); } catch(e) {} return out; }""")
                                for item in found or []:
                                    if isinstance(item, str) and ".m3u8" in item and item not in captured: captured.append(item)
                            except Exception:
                                pass
                        raw_cookies = await context.cookies()
                        if raw_cookies: task.metadata["cookies"] = {c["name"]: c["value"] for c in raw_cookies}
                    finally:
                        await context.close(); task.metadata.pop("browser_page", None)
                    if captured: return captured[0]
                return None
            finally:
                task.metadata.pop("browser_page", None); task.metadata.pop("browser", None); await browser.close()

    @staticmethod
    async def _fetch_text(session, url: str) -> str:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()

    @staticmethod
    def _best_variant(base_url: str, playlist: str) -> str:
        lines = [line.strip() for line in playlist.splitlines()]
        variants: list[tuple[int, str]] = []; pending = 0
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF:"):
                match = re.search(r"(?:RESOLUTION|BANDWIDTH)=(?:\d+x)?(\d+)", line); pending = int(match.group(1)) if match else 0
            elif pending and line and not line.startswith("#"):
                variants.append((pending, urljoin(base_url, line))); pending = 0
        return max(variants, key=lambda x: x[0])[1] if variants else base_url

    async def _direct(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        import aiohttp
        tmp = output.with_suffix(output.suffix + ".part"); task.register_temp(tmp)
        headers = task.metadata.get("headers") or {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=30, sock_read=60), headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status(); content_type = (response.headers.get("Content-Type") or "").lower(); final_url = str(response.url).lower()
                if content_type.startswith(("text/html", "text/plain", "application/json")): raise DownloadError(f"Direct response is not media ({content_type or 'unknown'})")
                if not any(x in final_url for x in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".m3u8", ".mpd")) and not content_type.startswith(("video/", "audio/", "application/octet-stream")): raise DownloadError(f"Direct response is not recognized media ({content_type or 'unknown'})")
                total = int(response.headers.get("Content-Length") or 0) or None; done = 0
                with tmp.open("wb") as fh:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        task.check_cancelled(); fh.write(chunk); done += len(chunk)
                        await self._report(progress, done * 100 / total if total else None, done, total, None)
        tmp.replace(output); task.temp_paths.discard(tmp)

    async def _aria2c(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        binary = shutil.which("aria2c")
        if not binary: raise DownloadError("aria2c is not installed")
        proc = await asyncio.create_subprocess_exec(binary, "--allow-overwrite=true", "--auto-file-renaming=false", "--summary-interval=1", "--console-log-level=warn", "--dir", str(output.parent), "--out", output.name, url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        task.register_process(proc)
        try:
            while True:
                task.check_cancelled(); line = await proc.stdout.readline()
                if not line: break
                if progress and output.exists(): await self._report(progress, None, output.stat().st_size, None, None)
            rc = await proc.wait()
            if rc != 0: raise DownloadError(f"aria2c exited with code {rc}")
        finally: task.unregister_process(proc)

    async def _ffmpeg(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None, headers: dict | None = None) -> None:
        binary = shutil.which("ffmpeg")
        if not binary: raise DownloadError("ffmpeg is not installed")
        command = [binary, "-hide_banner", "-loglevel", "error", "-y"]
        if headers:
            header_text = "".join(f"{k}: {v}\r\n" for k, v in headers.items()); command += ["-headers", header_text]
        command += ["-i", url, "-c", "copy", str(output)]
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE); task.register_process(proc)
        try:
            while proc.returncode is None:
                task.check_cancelled()
                try: await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    if progress and output.exists(): await self._report(progress, None, output.stat().st_size, None, None)
            err = await proc.stderr.read()
            if proc.returncode != 0: raise DownloadError(err.decode(errors="ignore")[-1000:] or f"ffmpeg exited with code {proc.returncode}")
        finally: task.unregister_process(proc)

    async def _ytdlp(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        binary = shutil.which("yt-dlp") or sys.executable
        command = [binary, "-m", "yt_dlp"] if binary == sys.executable else [binary]
        if task.metadata.get("cookiefile"): command += ["--cookies", str(task.metadata["cookiefile"])]
        command += ["--newline", "--no-part", "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", str(output), url]
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT); task.register_process(proc)
        try:
            while True:
                task.check_cancelled(); line = await proc.stdout.readline()
                if not line: break
                if progress and output.exists(): await self._report(progress, None, output.stat().st_size, None, None)
            rc = await proc.wait()
            if rc != 0: raise DownloadError(f"yt-dlp exited with code {rc}")
        finally: task.unregister_process(proc)

    async def _browser(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        downloader = BrowserHLSDownloader(self.output_dir)
        try:
            await downloader.download(url, output, task, progress)
        except BrowserHLSError as exc:
            raise DownloadError(str(exc)) from exc

    @staticmethod
    async def _report(callback, percent, current, total, speed, details=None):
        if callback:
            result = callback(percent, current, total, speed, details)
            if asyncio.iscoroutine(result): await result
