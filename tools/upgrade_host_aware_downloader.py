from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"
s = ENGINE.read_text()

anchor = '        requested = str(task.metadata.get("download_method") or "auto").lower()\n'
insert = '        url = self._normalize_source_url(url, task)\n\n'
if insert not in s:
    if anchor not in s:
        raise SystemExit("anchor: requested method not found")
    s = s.replace(anchor, insert + anchor, 1)

marker = '    @staticmethod\n    def _build_plan(url: str, requested: str = "auto") -> list[str]:\n'
helpers = r'''    @staticmethod
    def _host_kind(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if "pixeldrain" in host:
            return "pixeldrain"
        if host in {"dood.to", "dood.la", "dood.pm", "dood.sh", "dood.ws", "dood.one", "dood.watch"} or re.search(r"(^|\.)(doodstream|dood)\.", host):
            return "dood"
        if re.search(r"(^|\.)(luluvid|luluvdo|luluvdoo|luluvideo|lulustream)\.com$", host):
            return "lulu"
        return "generic"

    @classmethod
    def _normalize_source_url(cls, url: str, task: TaskContext) -> str:
        kind = cls._host_kind(url)
        if kind == "pixeldrain":
            match = re.search(r"pixeldrain\.com/(?:u|file)/([A-Za-z0-9_-]+)", url, re.I)
            if match:
                file_id = match.group(1)
                task.metadata["source_host"] = "pixeldrain"
                task.metadata["pixeldrain_file_id"] = file_id
                return f"https://pixeldrain.com/api/file/{file_id}?download"
        task.metadata["source_host"] = kind
        return url

'''
if helpers not in s:
    if marker not in s:
        raise SystemExit("anchor: build plan not found")
    s = s.replace(marker, helpers + marker, 1)

old = '''    @staticmethod
    def _build_plan(url: str, requested: str = "auto") -> list[str]:
        if requested != "auto": return [requested]
        lower = url.lower()
        if ".m3u8" in lower or ".mpd" in lower: return ["hls-multi", "ffmpeg", "browser", "yt-dlp", "direct"]
        if any(ext in lower for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v")): return ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        return ["yt-dlp", "hls-multi", "browser", "ffmpeg", "aria2c", "direct"]
'''
new = '''    @classmethod
    def _build_plan(cls, url: str, requested: str = "auto") -> list[str]:
        if requested != "auto":
            return [requested]
        kind = cls._host_kind(url)
        lower = url.lower()
        if kind == "pixeldrain":
            return ["aria2c", "direct", "yt-dlp"]
        if kind in {"dood", "lulu"}:
            return ["yt-dlp", "hls-multi", "browser", "ffmpeg", "direct"]
        if ".m3u8" in lower or ".mpd" in lower:
            return ["hls-multi", "ffmpeg", "browser", "yt-dlp", "direct"]
        if any(ext in lower for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts")):
            return ["aria2c", "direct", "ffmpeg", "yt-dlp", "browser"]
        return ["yt-dlp", "hls-multi", "browser", "ffmpeg", "aria2c", "direct"]
'''
if old not in s:
    raise SystemExit("planner block not found")
s = s.replace(old, new, 1)

old = '''        candidates = [url]
        m = re.search(r"(?:luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\\.com/(?:d|e)/([A-Za-z0-9]+)", url, re.I)
        if m:
            video_id = m.group(1)
            candidates.extend([
                f"https://luluvdo.com/e/{video_id}",
                f"https://lulustream.com/e/{video_id}",
                f"https://luluvdoo.com/e/{video_id}",
            ])
        return list(dict.fromkeys(candidates))
'''
new = '''        candidates = [url]
        lulu = re.search(r"(?:luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\\.com/(?:d|e)/([A-Za-z0-9]+)", url, re.I)
        if lulu:
            video_id = lulu.group(1)
            candidates.extend([
                f"https://luluvdo.com/e/{video_id}",
                f"https://luluvid.com/e/{video_id}",
                f"https://lulustream.com/e/{video_id}",
                f"https://luluvdoo.com/e/{video_id}",
                f"https://luluvideo.com/e/{video_id}",
            ])
        dood = re.search(r"(?:doodstream|dood)\\.(?:com|to|la|pm|sh|ws|one|watch)/[de]/([A-Za-z0-9]+)", url, re.I)
        if dood:
            video_id = dood.group(1)
            candidates.extend([
                f"https://dood.to/e/{video_id}",
                f"https://dood.la/e/{video_id}",
                f"https://dood.pm/e/{video_id}",
                f"https://dood.sh/e/{video_id}",
                f"https://dood.watch/e/{video_id}",
            ])
        return list(dict.fromkeys(candidates))
'''
if old not in s:
    raise SystemExit("browser candidate block not found")
s = s.replace(old, new, 1)

s = re.sub(r'asyncio\.Semaphore\(\d+\)', 'asyncio.Semaphore(16)', s, count=1)
needle = '            # Encrypted HLS must be handled by FFmpeg because the key metadata is part of the playlist.\n'
extra = '''            # Advanced HLS playlists are safest through native FFmpeg.\n            if "#EXT-X-MAP:" in playlist or "#EXT-X-BYTERANGE:" in playlist or "#EXT-X-DISCONTINUITY" in playlist:\n                logger.info("task=%s advanced HLS playlist detected; using native FFmpeg", task.task_id)\n                await self._ffmpeg(stream_url, output, task, progress, headers=headers)\n                return\n\n'''
if extra not in s:
    if needle not in s:
        raise SystemExit("HLS encryption anchor not found")
    s = s.replace(needle, extra + needle, 1)

s = s.replace('aiohttp.TCPConnector(limit=150, limit_per_host=40', 'aiohttp.TCPConnector(limit=250, limit_per_host=80', 1)
s = s.replace('async with session.get(segment_url) as response:\n                                response.raise_for_status()\n                                data = await response.read()', 'async with session.get(segment_url) as response:\n                                if response.status in (401, 403, 404, 410):\n                                    raise DownloadError(f"HLS segment HTTP {response.status}")\n                                response.raise_for_status()\n                                data = await response.read()\n                                if not data:\n                                    raise DownloadError("Empty HLS segment response")', 1)
s = s.replace('timeout=30000', 'timeout=45000', 1)
s = s.replace('for tick in range(25):', 'for tick in range(35):', 1)
s = s.replace('if ".m3u8" in low and not any', 'if (".m3u8" in low or ".mpd" in low) and not any', 1)

# Patch yt-dlp command regardless of previous local tuning.
pattern = r'        command \+= \["--newline",.*?str\(output\), url\]\n'
replacement = '''        command += [
            "--newline", "--no-part",
            "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "5",
            "--socket-timeout", "30", "--concurrent-fragments", "16",
            "--force-overwrites", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", str(output), url,
        ]
        headers = task.metadata.get("headers") or {}
        if headers.get("User-Agent"):
            command[1:1] = ["--user-agent", str(headers["User-Agent"])]
        if headers.get("Referer"):
            command[1:1] = ["--referer", str(headers["Referer"])]
'''
s2, n = re.subn(pattern, replacement, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit("yt-dlp command block not found; no changes written")
s = s2

ENGINE.write_text(s)
print("HOST-AWARE ROBUST DOWNLOADER UPGRADE APPLIED")
print("Pixeldrain: documented API file endpoint + aria2c/direct fallback")
print("Dood/Lulu: yt-dlp -> HLS Multi -> browser -> ffmpeg -> direct")
print("HLS: 16 segments/task | 250 total / 80 per-host")
print("HLS: fMP4/byterange/discontinuity -> native FFmpeg")
print("yt-dlp: 10 retries + 10 fragment retries + 16 concurrent fragments")
print("Browser discovery: 45s + 35s player wait + alternate host routes")
