# downloader.py
import os
import re
import time
import logging
import asyncio
import aiohttp
import yt_dlp
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import DOWNLOAD_DIR, USER_AGENT, PROTECTED_DOMAINS, COOKIES_PATH, SETTINGS
from utils import (
    clean_media_title, clean_title, sanitize_filename, check_disk_space_guard, cleanup_task_artifacts,
    trim_video_file, compress_video, apply_watermark, filter_audio_tracks, apply_resolution_downscale
)
logger = logging.getLogger(__name__)

PER_TASK_SEGMENT_CONCURRENCY = 4
CANCELLED_TASKS = set()
LIVE_TASKS = {}


def set_live_task(task_id, **data):
    if task_id is None:
        return
    entry = LIVE_TASKS.setdefault(str(task_id), {})
    entry.update(data)


def clear_live_task(task_id):
    if task_id is not None:
        LIVE_TASKS.pop(str(task_id), None)


class DashboardTracker:
    def __init__(self, msg, cur_idx, total_items, item_name, start_time, task_id=None):
        self.msg = msg
        self.cur_idx = cur_idx
        self.total = total_items
        self.name = item_name
        self.task_id = task_id
        self.batch_start = start_time
        self.upload_start = time.time()
        self.last_update = 0

    async def callback(self, cur, tot):
        if self.task_id is not None and str(self.task_id) in CANCELLED_TASKS:
            raise asyncio.CancelledError()
        now = time.time()
        if now - self.last_update < 3 and cur != tot:
            return
        self.last_update = now
        pct = min(100.0, (cur * 100 / tot)) if tot else 0.0
        fill = max(0, min(10, int(pct // 10)))
        bar = "█" * fill + "░" * (10 - fill)
        current_mb = cur / 1048576
        total_mb = tot / 1048576 if tot else 0.0
        elapsed = max(0.001, now - self.upload_start)
        speed = current_mb / elapsed
        eta_sec = int((total_mb - current_mb) / speed) if speed > 0 else 0
        eta = time.strftime("%M:%S", time.gmtime(max(0, eta_sec)))
        set_live_task(
            self.task_id,
            title=self.name,
            percent=pct,
            current_mb=current_mb,
            total_mb=total_mb,
            speed_mb=speed,
            eta=eta,
            operation="Telegram upload",
        )
        text = (
            f"🎬 **{self.name[:55]}**\n\n"
            f"⬆️ **UPLOADING**\n"
            f"`{bar}` **{pct:.0f}%**\n\n"
            f"💾 Data: {current_mb:.0f}/{total_mb:.0f} MB\n"
            f"⚡ {speed:.1f} MB/s • ETA {eta}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_task_{self.task_id}")
        ]])
        try:
            await self.msg.edit_text(text, reply_markup=markup)
        except Exception:
            pass

async def fetch_segment_with_backoff(session, index, url, sem, max_retries=3):
    async with sem:
        for attempt in range(max_retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        return index, data
                    elif resp.status in [429, 500, 502, 503, 504]:
                        await asyncio.sleep(2 ** attempt)
            except Exception:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return index, None

def clean_full_video_title(title, source_url="", idx=None):
    if not title:
        return ""

    title = str(title).strip()

    # Remove common website/domain suffixes.
    title = re.sub(
        r"\s*[|\-–—]\s*(?:https?://)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\s*$",
        "",
        title,
        flags=re.I,
    ).strip()

    # Never use a raw URL or domain as the video title.
    low = title.lower()
    source_low = str(source_url or "").strip().rstrip("/").lower()

    if source_low and low.rstrip("/") == source_low:
        return ""

    if re.match(r"^(https?://|www\.)", low):
        return ""

    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(?:/.*)?$", low):
        return ""

    # Keep the full actual title. Only filesystem-unsafe characters are cleaned.
    # Do not remove brackets/parentheses and do not append the item index.
    return sanitize_filename(title).strip()


def get_preset_settings(preset):
    preset = str(preset or "custom").lower().strip()

    presets = {
        "mobile": {
            "resolution": "480p",
            "audio": False,
            "compress": True,
        },
        "720p": {
            "resolution": "720p",
            "audio": False,
            "compress": False,
        },
        "1080p": {
            "resolution": "1080p",
            "audio": False,
            "compress": False,
        },
        "audio": {
            "resolution": "original",
            "audio": True,
            "compress": False,
        },
        "custom": {
            "resolution": SETTINGS.get("target_resolution", "original"),
            "audio": False,
            "compress": SETTINGS.get("compress", False),
        },
    }

    return presets.get(preset, presets["custom"])


async def download_single_item(url, idx, s_msg, custom_name="", trim_info="", is_audio_mode=False, audio_bitrate="192", preset="custom", task_id=None):
    f_file = None

    preset_cfg = get_preset_settings(preset)
    # Preset overrides only the task-specific download mode.
    # Custom keeps the existing global settings.
    effective_audio_mode = bool(is_audio_mode or preset_cfg["audio"])
    effective_resolution = preset_cfg["resolution"]
    effective_compress = bool(preset_cfg["compress"])
    limit_rate = SETTINGS["speed_limit"]
    title = custom_name.strip() if custom_name else ""
    
    if "playmogo" in url.lower():
            
        try:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=20)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                page_title = ""
                og_tag = soup.find("meta", attrs={"property": "og:title"})
                if og_tag and og_tag.get("content"):
                    page_title = og_tag.get("content", "").strip()

                if not page_title and soup.title:
                    page_title = soup.title.get_text(" ", strip=True)

                if not page_title:
                    h1 = soup.find("h1")
                    if h1:
                        page_title = h1.get_text(" ", strip=True)

                download_link = ""
                for a in soup.find_all("a"):
                    href = a.get("href", "")
                    text = a.get_text(strip=True)
                    if "High quality" in text or "/download/" in href:
                        download_link = urljoin(url, href)
                        break
                        
                if download_link:
                    headers = {
                        "Referer": url,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    }
                    dl_response = cffi_requests.get(download_link, headers=headers, impersonate="chrome", stream=True, timeout=60)
                    if dl_response.status_code == 200:
                        output_filename = os.path.join(DOWNLOAD_DIR, f"video_{idx}_{int(time.time())}.mp4")
                        with open(output_filename, 'wb') as f:
                            for chunk in dl_response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                        f_file = output_filename
        except Exception as e:
            logger.exception("Task %s Playmogo download failed", task_id or idx)
            
        if f_file and os.path.exists(f_file):
            title = clean_full_video_title(
                custom_name or page_title,
                url,
                idx,
            ) or f"Video {idx}"

    task_token = f"task_{task_id or 0}_{idx}_{time.time_ns()}"

    check_disk_space_guard()

    try:
        if not (f_file and os.path.exists(f_file)) and any(
            d in url.lower() for d in PROTECTED_DOMAINS
        ):
            e_url = url
            if "/d/" in url:
                e_url = url.replace("/d/", "/e/")
            m_id = re.search(r"(?:luluvdo|luluvid)\.com/(?:e/|d/)?([a-zA-Z0-9]+)", url)
            if m_id:
                e_url = f"https://luluvdo.com/e/{m_id.group(1)}"
            try:
                await s_msg.edit_text(
                    f"🎬 **{safe_title[:55]}**\n\n"
                    "🌐 **CONNECTING**\n"
                    "Browser session તૈયાર થઈ રહી છે...\n"
                    "⏳ Stream શોધી રહ્યા છીએ..."
                )
            except Exception:
                pass

            v_url = None
            r_title = ""
            cookies = {}
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(user_agent=USER_AGENT)
                    page = await context.new_page()
                    
                    async def run_browser_tasks():
                        nonlocal v_url, r_title, cookies
                        async def handle_route(route):
                            nonlocal v_url
                            req_url = route.request.url
                            if any(k in req_url.lower() for k in [".m3u8", "master.m3u8", "playlist.m3u8"]) and not v_url:
                                if not any(b in req_url.lower() for b in ["google", "analytics", "preview", "thumb"]):
                                    v_url = req_url
                            await route.continue_()

                        await page.route("**/*", handle_route)
                        await page.goto(e_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(4)
                        raw_t = await page.title()
                        og_t = ""
                        try:
                            og_t = await page.locator("meta[property=\"og:title\"]").get_attribute("content") or ""
                        except Exception:
                            pass
                        r_title = clean_media_title(og_t or raw_t)
                        
                        for frame in page.frames:
                            try:
                                stream_url = await frame.evaluate("""() => {
                                    if (typeof player !== "undefined" && player.getConfig) {
                                        let cfg = player.getConfig();
                                        if (cfg && cfg.sources && cfg.sources.length > 0) return cfg.sources[0].file;
                                    }
                                    if (typeof sources !== "undefined" && sources.length > 0) return sources[0].file;
                                    return null;
                                }""")
                                if stream_url and ".m3u8" in stream_url:
                                    v_url = stream_url
                                    break
                            except Exception:
                                pass

                        raw_cookies = await context.cookies()
                        cookies = {c["name"]: c["value"] for c in raw_cookies}

                    await asyncio.wait_for(run_browser_tasks(), timeout=40.0)
                    await browser.close()
            except Exception as e:
                logger.exception("Task %s Playwright processing failed", task_id or idx)

            if not v_url:
                await s_msg.edit_text(f"❌ **[{idx}]** વિડિયો સ્ટ્રીમ URL એક્સટ્રેક્ટ થઈ શકી નથી.")
                return None, None

            if not title:
                title = r_title or f"Video {idx}"
            safe_title = clean_full_video_title(title, url, idx) or f"Video {idx}"
            final_out = os.path.join(DOWNLOAD_DIR, f"{safe_title}_{task_token}.mp4")
            if effective_audio_mode:
                final_out = os.path.join(DOWNLOAD_DIR, f"{safe_title}_{task_token}.mp3")

            headers = {
                "User-Agent": USER_AGENT,
                "Referer": e_url
            }

            connector = aiohttp.TCPConnector(
                limit=150,
                limit_per_host=40,
                ttl_dns_cache=300,
                enable_cleanup_closed=True
            )

            async with aiohttp.ClientSession(headers=headers, cookies=cookies, connector=connector) as session:
                try:
                    async with session.get(v_url, timeout=aiohttp.ClientTimeout(total=15)) as master_resp:
                        if master_resp.status != 200:
                            raise Exception("Master playlist fetch failed")
                        master_content = await master_resp.text()
                except Exception as e:
                    await s_msg.edit_text(f"❌ **[{idx}]** માસ્ટર પ્લેલિસ્ટ એરર: {str(e)[:50]}")
                    return None, None

                lines = master_content.splitlines()
                sub_playlist_url = None
                for line in lines:
                    if ".m3u8" in line and not line.startswith("#"):
                        sub_playlist_url = line
                        break

                if sub_playlist_url:
                    if not sub_playlist_url.startswith("http"):
                        parsed_url = urlparse(v_url)
                        base_path = parsed_url.path.rsplit("/", 1)[0]
                        sub_playlist_url = f"{parsed_url.scheme}://{parsed_url.netloc}{base_path}/{sub_playlist_url}"
                    try:
                        async with session.get(sub_playlist_url, timeout=aiohttp.ClientTimeout(total=15)) as sub_resp:
                            playlist_content = await sub_resp.text()
                    except Exception:
                        playlist_content = master_content
                        sub_playlist_url = v_url
                else:
                    playlist_content = master_content
                    sub_playlist_url = v_url

                playlist_lines = playlist_content.splitlines()
                segment_urls = []
                for line in playlist_lines:
                    if line and not line.startswith("#"):
                        if not line.startswith("http"):
                            parsed_sub = urlparse(sub_playlist_url)
                            base_sub_path = parsed_sub.path.rsplit("/", 1)[0]
                            segment_url = f"{parsed_sub.scheme}://{parsed_sub.netloc}{base_sub_path}/{line}"
                        else:
                            segment_url = line
                        segment_urls.append(segment_url)

                if not segment_urls:
                    await s_msg.edit_text(f"❌ **[{idx}]** કોઈ વિડિયો સેગમેન્ટ્સ મળ્યા નથી.")
                    return None, None

                total_segs = len(segment_urls)
                cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 કેન્સલ કરો", callback_data=f"cancel_task_{task_id}")]])

                if effective_audio_mode:
                    ffmpeg_cmd = [
                        "ffmpeg", "-y", "-f", "mpegts", "-i", "pipe:0",
                        "-vn", "-c:a", "libmp3lame", "-b:a", f"{audio_bitrate}k", final_out
                    ]
                else:
                    ffmpeg_cmd = [
                        "ffmpeg", "-y", "-f", "mpegts", "-i", "pipe:0",
                        "-c", "copy", "-map_metadata", "-1", "-metadata", f"title={safe_title}",
                        "-movflags", "+faststart", final_out
                    ]

                proc = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )

                sem = asyncio.Semaphore(PER_TASK_SEGMENT_CONCURRENCY)
                tasks = [asyncio.create_task(fetch_segment_with_backoff(session, i, u, sem)) for i, u in enumerate(segment_urls)]
                buffer = {}
                next_index = 0
                total_bytes_processed = 0
                start_time = time.time()
                last_msg_time = time.time()
                pipeline_failed = False

                for completed_task in asyncio.as_completed(tasks):
                    if task_id is not None and str(task_id) in CANCELLED_TASKS:
                        pipeline_failed = True
                        break

                    s_i, s_data = await completed_task
                    if s_data is None:
                        pipeline_failed = True
                        break
                    buffer[s_i] = s_data

                    while next_index in buffer:
                        chunk = buffer.pop(next_index)
                        if chunk and proc.stdin:
                            try:
                                proc.stdin.write(chunk)
                                await proc.stdin.drain()
                                total_bytes_processed += len(chunk)
                            except (BrokenPipeError, ConnectionResetError):
                                pipeline_failed = True
                                break

                        next_index += 1
                        now = time.time()
                        if now - last_msg_time >= 3 or next_index == total_segs:
                            last_msg_time = now
                            pct = min(100.0, (next_index / total_segs) * 100)
                            fill = int(pct // 10)
                            prog_bar = "▰" * fill + "▱" * (10 - fill)
                            elapsed = now - start_time
                            current_mb = total_bytes_processed / (1024 * 1024)
                            speed_mb = (current_mb / elapsed) if elapsed > 0 else 0
                            eta_sec = int((elapsed / next_index) * (total_segs - next_index)) if next_index > 0 else 0
                            eta_str = f"{eta_sec // 60:02d}:{eta_sec % 60:02d}"

                            set_live_task(
                                task_id,
                                title=safe_title,
                                percent=pct,
                                current_mb=current_mb,
                                total_mb=None,
                                speed_mb=speed_mb,
                                eta=eta_str,
                                operation="Live merge / download",
                            )

                            ui_card = (
                                f"🎬 **{safe_title[:55]}**\n\n"
                                f"⬇️ **DOWNLOADING**\n"
                                f"`{prog_bar}` **{pct:.1f}%**\n"
                                f"⚡ {speed_mb:.2f} MB/s • ⏳ ETA {eta_str}\n\n"
                                f"📦 Segments: {next_index}/{total_segs}"
                            )
                            try:
                                await s_msg.edit_text(ui_card, reply_markup=cancel_markup)
                            except Exception:
                                pass

                    if pipeline_failed:
                        break

                if proc.stdin and not proc.stdin.is_closing():
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

                _, stderr_data = await proc.communicate()

                if pipeline_failed or proc.returncode != 0 or not os.path.exists(final_out) or os.path.getsize(final_out) < 1000:
                    if os.path.exists(final_out):
                        os.remove(final_out)
                    raise Exception(f"FFmpeg pipeline failed: {stderr_data.decode('utf-8', errors='ignore')[-200:] if stderr_data else 'Unknown'}")

                f_file = final_out
        else:
            ydl_rate = None
            if limit_rate != "0":
                mult = 1048576 if "M" in limit_rate.upper() else 1024
                ydl_rate = int(re.sub(r"[^\d]", "", limit_rate)) * mult

            out_tmpl = os.path.join(DOWNLOAD_DIR, f"%(title)s_{task_token}.%(ext)s")
            def ydl_progress_hook(progress):
                if task_id is not None and str(task_id) in CANCELLED_TASKS:
                    raise yt_dlp.utils.DownloadError("Task cancelled by user")

            ydl_opts = {
                "ratelimit": ydl_rate,
                "socket_timeout": 30,
                "quiet": True,
                "progress_hooks": [ydl_progress_hook],
            }
            if os.path.exists(COOKIES_PATH):
                ydl_opts["cookiefile"] = COOKIES_PATH

            if effective_audio_mode:
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "outtmpl": out_tmpl,
                    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": audio_bitrate}]
                })
            else:
                ydl_opts.update({
                    "format": "bestvideo+bestaudio/best",
                    "outtmpl": out_tmpl,
                    "merge_output_format": "mp4"
                })

            def run_ydl():
                with yt_dlp.YoutubeDL(ydl_opts) as y:
                    inf = y.extract_info(url, download=True)
                    fn = y.prepare_filename(inf)
                    ext = ".mp3" if effective_audio_mode else ".mp4"
                    b, _ = os.path.splitext(fn)
                    res_f = b + ext
                    r_t = inf.get("title", f"Video {idx}")
                    return res_f if os.path.exists(res_f) else fn, r_t

            f_file, r_t = await asyncio.get_event_loop().run_in_executor(None, run_ydl)
            if not title:
                title = clean_full_video_title(r_t, url, idx)
            if not title:
                title = f"Video {idx}"

        if f_file and os.path.exists(f_file) and not effective_audio_mode:
            if trim_info:
                f_file = await trim_video_file(f_file, trim_info)
            if effective_compress:
                f_file = await compress_video(f_file)
            f_file = await apply_watermark(f_file)
            f_file = await filter_audio_tracks(f_file)
            f_file = await apply_resolution_downscale(f_file, effective_resolution)

        return f_file, title
    finally:
        cleanup_task_artifacts(task_token)
