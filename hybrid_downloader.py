# hybrid_downloader.py
"""Advanced multi-engine downloader.

Engines:
  1. aria2c (optional) for direct HTTP/HTTPS files
  2. aiohttp streaming fallback for direct files
  3. FFmpeg for HLS/DASH streams
  4. yt-dlp for supported video sites
  5. Playwright extraction for JS-generated media URLs

This module is intentionally self-contained so it can be tested before the
existing downloader is switched over to it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urljoin, urlparse

import aiohttp

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None] | None]

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".3gp"
}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}
STREAM_HINTS = (".m3u8", ".mpd", "manifest", "playlist")


@dataclass
class HybridResult:
    path: Optional[str]
    engine: Optional[str]
    title: str = ""
    error: Optional[str] = None
    attempts: list[str] = field(default_factory=list)


class HybridDownloadError(Exception):
    pass


class HybridDownloader:
    def __init__(
        self,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        cookies_path: str | None = None,
        timeout: int = 60,
        direct_connections: int = 8,
    ):
        self.user_agent = user_agent
        self.cookies_path = cookies_path
        self.timeout = timeout
        self.direct_connections = max(1, min(32, int(direct_connections)))

    # ----------------------------- public API -----------------------------
    async def download(
        self,
        url: str,
        output_path: str,
        *,
        referer: str = "",
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        audio_only: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        preferred_engine: str | None = None,
    ) -> HybridResult:
        if not url or not url.startswith(("http://", "https://")):
            return HybridResult(None, None, error="Invalid HTTP/HTTPS URL")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        base_headers = {"User-Agent": self.user_agent}
        if referer:
            base_headers["Referer"] = referer
        if headers:
            base_headers.update(headers)

        kind = await self.classify(url, base_headers, cookies or {})
        plan = self._build_plan(url, kind, preferred_engine)
        attempts: list[str] = []
        last_error = "download failed"

        for engine in plan:
            attempts.append(engine)
            tmp = output_path + f".hybrid-{engine}-{os.getpid()}-{time.time_ns()}"
            try:
                logger.info("HYBRID engine=%s kind=%s url=%s", engine, kind, url)
                title = ""
                if engine == "aria2c":
                    await self._aria2c(url, tmp, base_headers, cookies or {})
                elif engine == "direct":
                    await self._direct(url, tmp, base_headers, cookies or {}, progress_callback)
                elif engine == "ffmpeg":
                    await self._ffmpeg(url, tmp, base_headers, cookies or {}, audio_only)
                elif engine == "yt-dlp":
                    title = await self._yt_dlp(url, tmp, base_headers, audio_only, progress_callback)
                elif engine == "browser":
                    extracted = await self._browser_extract(url, base_headers, cookies or {})
                    if not extracted:
                        raise HybridDownloadError("Browser could not find a media URL")
                    media_url, browser_headers, browser_cookies, browser_title = extracted
                    title = browser_title
                    merged_headers = dict(base_headers)
                    merged_headers.update(browser_headers)
                    if self._is_stream_url(media_url):
                        await self._ffmpeg(media_url, tmp, merged_headers, browser_cookies, audio_only)
                    else:
                        await self._direct(media_url, tmp, merged_headers, browser_cookies, progress_callback)
                else:
                    raise HybridDownloadError(f"Unknown engine: {engine}")

                if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                    raise HybridDownloadError("Engine produced an empty file")
                os.replace(tmp, output_path)
                return HybridResult(output_path, engine, title=title, attempts=attempts)
            except asyncio.CancelledError:
                self._remove(tmp)
                raise
            except Exception as exc:
                last_error = str(exc)
                logger.warning("HYBRID engine %s failed: %s", engine, exc)
                self._remove(tmp)

        return HybridResult(None, None, error=last_error, attempts=attempts)

    async def classify(self, url: str, headers: Dict[str, str], cookies: Dict[str, str]) -> str:
        low = url.lower()
        if self._is_stream_url(url):
            return "stream"
        ext = os.path.splitext(urlparse(url).path)[1].lower()
        if ext in VIDEO_EXTENSIONS or ext in AUDIO_EXTENSIONS:
            return "direct"
        if any(x in low for x in STREAM_HINTS):
            return "stream"

        # A cheap HEAD/GET probe. Never make classification failure fatal.
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(headers=headers, cookies=cookies) as session:
                async with session.head(url, allow_redirects=True, timeout=timeout) as r:
                    ctype = (r.headers.get("Content-Type") or "").lower()
                    if "mpegurl" in ctype or "dash+xml" in ctype:
                        return "stream"
                    if ctype.startswith("video/") or ctype.startswith("audio/"):
                        return "direct"
        except Exception:
            pass
        return "site"

    # ------------------------------ routing -------------------------------
    def _build_plan(self, url: str, kind: str, preferred: str | None) -> list[str]:
        if preferred:
            candidates = [preferred]
        elif kind == "stream":
            candidates = ["ffmpeg", "browser", "yt-dlp"]
        elif kind == "direct":
            candidates = ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        else:
            candidates = ["yt-dlp", "browser", "ffmpeg", "direct"]

        # aria2c is optional; avoid wasting a fallback attempt when missing.
        if shutil.which("aria2c") is None:
            candidates = [x for x in candidates if x != "aria2c"]
        return list(dict.fromkeys(candidates))

    # --------------------------- direct engines --------------------------
    async def _aria2c(self, url: str, output: str, headers: Dict[str, str], cookies: Dict[str, str]):
        if shutil.which("aria2c") is None:
            raise HybridDownloadError("aria2c is not installed")
        cmd = [
            "aria2c", "--allow-overwrite=true", "--auto-file-renaming=false",
            "--max-connection-per-server=%d" % self.direct_connections,
            "--split=%d" % self.direct_connections,
            "--min-split-size=1M", "--max-tries=5", "--retry-wait=2",
            "--connect-timeout=15", "--timeout=%d" % self.timeout,
            "--dir", str(Path(output).parent), "--out", Path(output).name,
        ]
        for k, v in headers.items():
            cmd += ["--header", f"{k}: {v}"]
        if cookies:
            cookie_file = self._temporary_cookie_file(cookies)
            try:
                cmd += ["--load-cookies", cookie_file]
                await self._run_process(cmd + [url])
            finally:
                self._remove(cookie_file)
        else:
            await self._run_process(cmd + [url])

    async def _direct(self, url: str, output: str, headers: Dict[str, str], cookies: Dict[str, str], callback: Optional[ProgressCallback]):
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=self.timeout)
        connector = aiohttp.TCPConnector(limit=64, limit_per_host=16, ttl_dns_cache=300)
        async with aiohttp.ClientSession(headers=headers, cookies=cookies, connector=connector) as session:
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                if resp.status >= 400:
                    raise HybridDownloadError(f"HTTP {resp.status}")
                total = int(resp.headers.get("Content-Length") or 0)
                current = 0
                with open(output, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        current += len(chunk)
                        await self._progress(callback, current, total)

    # ------------------------------ FFmpeg --------------------------------
    async def _ffmpeg(self, url: str, output: str, headers: Dict[str, str], cookies: Dict[str, str], audio_only: bool):
        if shutil.which("ffmpeg") is None:
            raise HybridDownloadError("ffmpeg is not installed")

        header_blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        if header_blob:
            cmd += ["-headers", header_blob]
        cmd += ["-i", url]
        if cookies:
            cookie_blob = "; ".join(f"{k}={v}" for k, v in cookies.items())
            cmd += ["-cookies", cookie_blob]
        if audio_only:
            cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]
        else:
            cmd += ["-c", "copy", "-movflags", "+faststart"]
        cmd += [output]
        await self._run_process(cmd)

    # ------------------------------ yt-dlp --------------------------------
    async def _yt_dlp(self, url: str, output: str, headers: Dict[str, str], audio_only: bool, callback: Optional[ProgressCallback]) -> str:
        loop = asyncio.get_running_loop()
        title_holder = {"title": ""}

        def progress(data: Dict[str, Any]):
            status = data.get("status")
            if status == "finished":
                title_holder["title"] = data.get("info_dict", {}).get("title", "") or ""
            if status not in {"downloading", "finished"}:
                return
            current = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            if callback:
                fut = asyncio.run_coroutine_threadsafe(self._progress(callback, current, total), loop)
                try:
                    fut.result(timeout=5)
                except Exception:
                    pass

        opts: Dict[str, Any] = {
            "outtmpl": output,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": 8,
            "http_headers": headers,
            "merge_output_format": "mp4",
            "restrictfilenames": False,
        }
        if self.cookies_path and os.path.exists(self.cookies_path):
            opts["cookiefile"] = self.cookies_path
        if audio_only:
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            opts["format"] = "bestvideo*+bestaudio/best"

        def run():
            import yt_dlp
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        await asyncio.to_thread(run)
        # yt-dlp may append an extension/postprocessor suffix.
        if os.path.exists(output):
            return title_holder["title"]
        stem = os.path.splitext(output)[0]
        for candidate in Path(Path(output).parent).glob(Path(stem).name + ".*"):
            if candidate.is_file() and candidate.stat().st_size:
                os.replace(candidate, output)
                return title_holder["title"]
        raise HybridDownloadError("yt-dlp produced no output")

    # ----------------------------- browser --------------------------------
    async def _browser_extract(self, url: str, headers: Dict[str, str], cookies: Dict[str, str]):
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise HybridDownloadError(f"Playwright unavailable: {exc}")

        media_url = None
        title = ""
        captured_headers: Dict[str, str] = {}
        captured_cookies: Dict[str, str] = dict(cookies)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(user_agent=headers.get("User-Agent", self.user_agent), extra_http_headers=headers)
            page = await context.new_page()

            async def response_handler(response):
                nonlocal media_url
                u = response.url
                ct = (response.headers.get("content-type") or "").lower()
                if media_url:
                    return
                if ".m3u8" in u.lower() or ".mpd" in u.lower() or "mpegurl" in ct or "dash+xml" in ct:
                    media_url = u
                    captured_headers["Referer"] = url

            page.on("response", response_handler)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            title = await page.title()

            # Common HTML5/player sources.
            try:
                sources = await page.locator("video source").evaluate_all("els => els.map(e => e.src).filter(Boolean)")
                for src in sources:
                    if self._is_media_url(src):
                        media_url = src
                        break
            except Exception:
                pass

            # Give dynamically-created requests a little extra time.
            if not media_url:
                await page.wait_for_timeout(3000)
            for c in await context.cookies():
                captured_cookies[c["name"]] = c["value"]
            await browser.close()

        if not media_url:
            return None
        return media_url, captured_headers, captured_cookies, title

    # ------------------------------ helpers -------------------------------
    @staticmethod
    def _is_stream_url(url: str) -> bool:
        low = url.lower().split("?", 1)[0]
        return low.endswith((".m3u8", ".mpd")) or ".m3u8?" in url.lower() or ".mpd?" in url.lower()

    @staticmethod
    def _is_media_url(url: str) -> bool:
        low = url.lower().split("?", 1)[0]
        return HybridDownloader._is_stream_url(url) or any(low.endswith(x) for x in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)

    @staticmethod
    async def _progress(callback, current: int, total: int):
        if not callback:
            return
        result = callback(current, total)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    async def _run_process(cmd: list[str]):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or stdout).decode("utf-8", "replace")[-1500:]
            raise HybridDownloadError(err or f"process exited {proc.returncode}")

    @staticmethod
    def _temporary_cookie_file(cookies: Dict[str, str]) -> str:
        fd, path = tempfile.mkstemp(prefix="hybrid-cookies-", suffix=".txt")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            # aria2 accepts Netscape cookie format.
            for name, value in cookies.items():
                fh.write(f"\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        return path

    @staticmethod
    def _remove(path: str):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


_default_engine: HybridDownloader | None = None


def get_hybrid_downloader(**kwargs) -> HybridDownloader:
    global _default_engine
    if _default_engine is None:
        _default_engine = HybridDownloader(**kwargs)
    return _default_engine


async def hybrid_download(url: str, output_path: str, **kwargs) -> HybridResult:
    """Convenience wrapper used by the future downloader integration."""
    return await get_hybrid_downloader().download(url, output_path, **kwargs)
