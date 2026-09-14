"""Optimized Browser HLS downloader.

The browser is used for discovery/authentication of the real HLS stream. Once a
signed media playlist is found, its media objects are fetched concurrently via
the browser context request client, which shares the browser cookie jar. The
bodies are written directly to temporary files to keep RAM bounded, then a
local HLS playlist is handed to FFmpeg for remuxing.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app.core.task import TaskCancelled, TaskContext

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SEGMENT_CONCURRENCY = 16
DISCOVERY_TIMEOUT_MS = 45_000
REQUEST_TIMEOUT_MS = 60_000
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024


class BrowserHLSError(Exception):
    pass


class BrowserHLSDownloader:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str, output: Path, task: TaskContext, progress=None) -> None:
        if not shutil.which("ffmpeg"):
            raise BrowserHLSError("ffmpeg is not installed")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserHLSError("Playwright is not installed") from exc

        work = self.output_dir / f".browser_hls_{task.task_id}"
        work.mkdir(parents=True, exist_ok=True)
        task.register_temp(work)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            task.metadata["browser"] = browser
            try:
                context, page, playlist_url, playlist_body = await self._discover(
                    browser, url, task, progress
                )
                task.metadata["stream_url"] = playlist_url
                task.metadata["referer"] = page.url
                task.metadata["headers"] = {
                    "User-Agent": await page.evaluate("navigator.userAgent"),
                    "Referer": page.url,
                }

                api = context.request
                media_url, media_body = await self._resolve_media_playlist(
                    api, playlist_url, playlist_body, page.url, task
                )
                task.metadata["stream_url"] = media_url

                await self._download_media_playlist(
                    api,
                    media_url,
                    media_body,
                    page.url,
                    context,
                    work,
                    output,
                    task,
                    progress,
                )
            finally:
                task.metadata.pop("browser", None)
                task.metadata.pop("browser_page", None)
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _discover(self, browser, url, task, progress):
        context = await browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        task.metadata["browser_page"] = page
        captured: list[tuple[str, str]] = []
        seen: set[str] = set()

        async def capture(response):
            low = response.url.lower()
            ct = (response.headers.get("content-type") or "").lower()
            if response.status != 200 or (".m3u8" not in low and "mpegurl" not in ct):
                return
            if response.url in seen:
                return
            try:
                body = await response.text()
            except Exception:
                return
            if "#EXTM3U" not in body or len(body.encode()) > MAX_PLAYLIST_BYTES:
                return
            seen.add(response.url)
            captured.append((response.url, body))
            logger.info("browser-hls playlist captured task=%s host=%s url=%s", task.task_id, urlparse(response.url).hostname, response.url[:300])

        page.on("response", lambda response: asyncio.create_task(capture(response)))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=DISCOVERY_TIMEOUT_MS)
        except Exception as exc:
            logger.info("browser-hls page.goto warning task=%s: %s", task.task_id, exc)

        await self._trigger_playback(page, task)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not captured:
            task.check_cancelled()
            await self._report(progress, 1.0, 0, None, 0.0, {"browser_hls_stage": "discovering"})
            await asyncio.sleep(0.5)

        if not captured:
            # Some players expose the stream only in player configuration/HTML.
            for frame in page.frames:
                task.check_cancelled()
                try:
                    found = await frame.evaluate(
                        """() => {
                            const out=[];
                            try {
                                if (typeof jwplayer === 'function') {
                                    const p=jwplayer(); const c=p&&p.getConfig?p.getConfig():null;
                                    for (const s of ((c&&c.sources)||[])) if (s&&s.file) out.push(s.file);
                                }
                            } catch(e) {}
                            try { for (const v of document.querySelectorAll('video,source')) if (v.src) out.push(v.src); } catch(e) {}
                            try {
                                const html=document.documentElement.innerHTML||'';
                                const m=html.match(/https?:[^\\\"'\\s<>]+\\.m3u8(?:\\?[^\\\"'\\s<>]*)?/gi)||[];
                                out.push(...m);
                            } catch(e) {}
                            return out;
                        }"""
                    )
                    for item in found or []:
                        if isinstance(item, str) and ".m3u8" in item and item not in seen:
                            try:
                                response = await context.request.get(item, timeout=REQUEST_TIMEOUT_MS, fail_on_status_code=False)
                                body = await response.text()
                                if response.status == 200 and "#EXTM3U" in body:
                                    captured.append((item, body)); seen.add(item)
                            except Exception:
                                pass
                except Exception:
                    pass

        if not captured:
            await context.close()
            raise BrowserHLSError("Browser could not discover a successful HLS playlist")
        return context, page, captured[0][0], captured[0][1]

    async def _trigger_playback(self, page, task):
        for frame in page.frames:
            task.check_cancelled()
            try:
                videos = frame.locator("video")
                for i in range(min(await videos.count(), 3)):
                    try:
                        await videos.nth(i).evaluate(
                            "v => { v.muted=true; v.autoplay=true; const p=v.play(); if(p) p.catch(()=>{}); }"
                        )
                    except Exception:
                        pass
                for selector in (
                    ".jwplayer",
                    ".jw-display-icon-container",
                    "button[aria-label*='play' i]",
                    "[class*='play' i]",
                ):
                    try:
                        loc = frame.locator(selector)
                        if await loc.count():
                            await loc.first.click(timeout=2000, force=True)
                            break
                    except Exception:
                        pass
            except Exception:
                pass

    async def _resolve_media_playlist(self, api, base_url, body, referer, task):
        if "#EXT-X-STREAM-INF" not in body:
            return base_url, body
        variants: list[tuple[int, str]] = []
        pending = 0
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                m = re.search(r"(?:BANDWIDTH|AVERAGE-BANDWIDTH)=(\d+)", line)
                pending = int(m.group(1)) if m else 0
            elif pending and line and not line.startswith("#"):
                variants.append((pending, urljoin(base_url, line)))
                pending = 0
        variants.sort(reverse=True)
        headers = {"User-Agent": DEFAULT_UA, "Referer": referer, "Accept": "*/*"}
        for _, variant in variants or [(0, base_url)]:
            task.check_cancelled()
            try:
                response = await api.get(variant, headers=headers, timeout=REQUEST_TIMEOUT_MS, fail_on_status_code=False)
                text = await response.text()
                if response.status == 200 and "#EXTM3U" in text and self._has_media_segments(text):
                    return variant, text
            except Exception as exc:
                logger.debug("browser-hls variant failed task=%s url=%s: %s", task.task_id, variant, exc)
        raise BrowserHLSError("Could not resolve a usable HLS media playlist")

    @staticmethod
    def _has_media_segments(body: str) -> bool:
        return any(line.strip() and not line.strip().startswith("#") for line in body.splitlines())

    async def _download_media_playlist(self, api, media_url, body, referer, context, work, output, task, progress):
        lines = body.splitlines()
        entries: list[tuple[int, str]] = []
        key_urls: dict[str, str] = {}
        init_urls: dict[str, str] = {}
        segment_urls: list[tuple[int, str]] = []
        current_index = 0

        for line in lines:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                if raw.startswith("#EXT-X-KEY:"):
                    m = re.search(r'URI="([^"]+)"', raw)
                    if m:
                        key_urls[m.group(1)] = urljoin(media_url, m.group(1))
                elif raw.startswith("#EXT-X-MAP:"):
                    m = re.search(r'URI="([^"]+)"', raw)
                    if m:
                        init_urls[m.group(1)] = urljoin(media_url, m.group(1))
                continue
            segment_urls.append((current_index, urljoin(media_url, raw)))
            current_index += 1

        if not segment_urls:
            raise BrowserHLSError("Media playlist contains no downloadable segments")

        # Preserve the original playlist semantics while replacing remote media
        # URIs with local files. This handles TS and HLS fMP4 without holding all
        # segment bodies in RAM.
        local_names: dict[str, str] = {}
        for raw, absolute in {**key_urls, **init_urls}.items():
            local_names[absolute] = f"asset_{len(local_names):04d}.bin"
        for _, absolute in segment_urls:
            local_names.setdefault(absolute, f"seg_{len([x for x in local_names if x.startswith(media_url.rsplit('/',1)[0])]):06d}.bin")

        all_urls = list(local_names.keys())
        sem = asyncio.Semaphore(SEGMENT_CONCURRENCY)
        completed = 0
        total_bytes = 0
        retries = 0
        started = time.monotonic()
        lock = asyncio.Lock()
        failures: list[str] = []

        await self._report(progress, 0.0, 0, None, 0.0, {"hls_completed": 0, "hls_total": len(segment_urls), "hls_retries": 0, "browser_hls": True})

        async def fetch_asset(absolute: str):
            nonlocal completed, total_bytes, retries
            local = work / local_names[absolute]
            task.register_temp(local)
            async with sem:
                last_exc = None
                for attempt in range(3):
                    task.check_cancelled()
                    try:
                        response = await api.get(
                            absolute,
                            headers={"User-Agent": DEFAULT_UA, "Referer": referer, "Accept": "*/*"},
                            timeout=REQUEST_TIMEOUT_MS,
                            fail_on_status_code=False,
                        )
                        if response.status < 200 or response.status >= 300:
                            raise BrowserHLSError(f"HTTP {response.status}")
                        data = await response.body()
                        if not data:
                            raise BrowserHLSError("empty response")
                        local.write_bytes(data)
                        async with lock:
                            total_bytes += len(data)
                            if absolute in {u for _, u in segment_urls}:
                                completed += 1
                            elapsed = max(time.monotonic() - started, 0.001)
                            await self._report(progress, completed * 100 / len(segment_urls), total_bytes, None, total_bytes / elapsed, {"hls_completed": completed, "hls_total": len(segment_urls), "hls_retries": retries, "browser_hls": True})
                        return
                    except TaskCancelled:
                        raise
                    except Exception as exc:
                        last_exc = exc
                        if attempt < 2:
                            retries += 1
                            await asyncio.sleep(min(2 ** attempt, 4))
                failures.append(f"{absolute}: {last_exc}")

        await asyncio.gather(*(fetch_asset(u) for u in all_urls))
        if failures:
            raise BrowserHLSError("HLS media fetch failed: " + " | ".join(failures[:3]))

        # Rewrite the original media playlist to local file paths.
        rewritten: list[str] = []
        for line in lines:
            raw = line.strip()
            if raw.startswith("#EXT-X-KEY:") or raw.startswith("#EXT-X-MAP:"):
                def repl(match):
                    absolute = urljoin(media_url, match.group(1))
                    return f'URI="{local_names.get(absolute, match.group(1))}"'
                rewritten.append(re.sub(r'URI="([^"]+)"', repl, raw))
            elif raw and not raw.startswith("#"):
                absolute = urljoin(media_url, raw)
                rewritten.append(local_names.get(absolute, raw))
            else:
                rewritten.append(line)
        local_playlist = work / "playlist.m3u8"
        local_playlist.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        task.register_temp(local_playlist)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,crypto,data",
            "-allowed_extensions", "ALL",
            "-i", str(local_playlist), "-c", "copy", "-movflags", "+faststart", str(output),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        task.register_process(proc)
        try:
            while proc.returncode is None:
                task.check_cancelled()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    if output.exists():
                        await self._report(progress, 100.0, output.stat().st_size, output.stat().st_size, None, {"browser_hls": True, "stage": "muxing"})
            stderr = await proc.stderr.read()
            if proc.returncode != 0:
                raise BrowserHLSError(stderr.decode(errors="ignore")[-2000:] or f"ffmpeg exited with code {proc.returncode}")
        finally:
            task.unregister_process(proc)

        if not output.is_file() or output.stat().st_size <= 0:
            raise BrowserHLSError("FFmpeg produced no valid output file")

    @staticmethod
    async def _report(callback, percent, current, total, speed, details=None):
        if callback:
            result = callback(percent, current, total, speed, details)
            if asyncio.iscoroutine(result):
                await result
