from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
UPLOAD = ROOT / "bot_vnext" / "app" / "uploader" / "engine.py"

# ------------------------- uploader: reliable Telegram media metadata -------------------------
s = UPLOAD.read_text()

# Extend imports for media inspection / thumbnail generation.
s = s.replace(
    "import time\nfrom pathlib import Path\n",
    "import time\nimport json\nimport tempfile\nfrom pathlib import Path\n"
    if "import json" not in s else "import time\nimport json\nimport tempfile\nfrom pathlib import Path\n",
    1,
)

# Replace the upload setup block with automatic ffprobe + thumbnail preparation.
needle = '''        task.check_cancelled()\n        total = path.stat().st_size\n        progress_state = {"last": 0.0, "started": time.monotonic()}'''
replacement = '''        task.check_cancelled()\n        total = path.stat().st_size\n\n        # Always prepare real video metadata and a Telegram-compatible thumbnail.\n        media = await self._probe_media(path)\n        if duration is None:\n            duration = media.get("duration")\n        if width is None:\n            width = media.get("width")\n        if height is None:\n            height = media.get("height")\n        generated_thumb = None\n        if thumbnail is None and normalized_mode := (mode or "video").lower() in {"video", "movie"}:\n            generated_thumb = await self._make_thumbnail(path, task)\n            thumbnail = generated_thumb\n\n        progress_state = {"last": 0.0, "started": time.monotonic()}'''
# The walrus expression above is intentionally avoided by a cleaner second replacement.
replacement = '''        task.check_cancelled()\n        total = path.stat().st_size\n\n        # Always prepare real video metadata and a Telegram-compatible thumbnail.\n        media = await self._probe_media(path)\n        if duration is None:\n            duration = media.get("duration")\n        if width is None:\n            width = media.get("width")\n        if height is None:\n            height = media.get("height")\n        generated_thumb = None\n        if thumbnail is None and (mode or "video").lower() not in {"document", "file", "doc", "audio", "music"}:\n            generated_thumb = await self._make_thumbnail(path, task)\n            thumbnail = generated_thumb\n\n        progress_state = {"last": 0.0, "started": time.monotonic()}'''
if needle not in s:
    raise RuntimeError("upload setup anchor not found")
s = s.replace(needle, replacement, 1)

# Ensure generated thumbnail is removed after the upload attempt.
needle2 = '''        finally:\n            if progress_tasks:\n                await asyncio.gather(*progress_tasks, return_exceptions=True)'''
replacement2 = '''        finally:\n            if progress_tasks:\n                await asyncio.gather(*progress_tasks, return_exceptions=True)\n            if generated_thumb:\n                try:\n                    Path(generated_thumb).unlink(missing_ok=True)\n                except OSError:\n                    pass'''
if needle2 not in s:
    raise RuntimeError("upload finally anchor not found")
s = s.replace(needle2, replacement2, 1)

# Add helpers before _send.
anchor = '''    async def _send(\n'''
helpers = '''    async def _probe_media(self, path: Path) -> dict:\n        """Read duration and dimensions with ffprobe without failing the upload."""\n        import shutil\n        binary = shutil.which("ffprobe")\n        if not binary:\n            return {}\n        proc = await asyncio.create_subprocess_exec(\n            binary, "-v", "error", "-select_streams", "v:0",\n            "-show_entries", "stream=width,height:format=duration",\n            "-of", "json", str(path),\n            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,\n        )\n        out, _ = await proc.communicate()\n        if proc.returncode != 0:\n            return {}\n        try:\n            data = json.loads(out.decode("utf-8", "ignore"))\n            stream = (data.get("streams") or [{}])[0]\n            fmt = data.get("format") or {}\n            duration = int(float(fmt.get("duration") or 0))\n            return {"duration": duration or None, "width": stream.get("width"), "height": stream.get("height")}\n        except Exception:\n            return {}\n\n    async def _make_thumbnail(self, path: Path, task: TaskContext) -> str | None:\n        """Generate a small JPEG from the video so Telegram can display a thumbnail."""\n        import shutil\n        binary = shutil.which("ffmpeg")\n        if not binary:\n            return None\n        thumb = Path(tempfile.gettempdir()) / f"botv2_thumb_{task.task_id}.jpg"\n        proc = await asyncio.create_subprocess_exec(\n            binary, "-hide_banner", "-loglevel", "error", "-y",\n            "-ss", "3", "-i", str(path), "-frames:v", "1",\n            "-vf", "scale=640:-2", "-q:v", "3", str(thumb),\n            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,\n        )\n        task.register_process(proc)\n        try:\n            await proc.wait()\n            if proc.returncode == 0 and thumb.is_file() and thumb.stat().st_size > 0:\n                return str(thumb)\n            # Very short videos may not have a frame at 3 seconds. Retry at 0.1s.\n            thumb.unlink(missing_ok=True)\n            proc = await asyncio.create_subprocess_exec(\n                binary, "-hide_banner", "-loglevel", "error", "-y",\n                "-ss", "0.1", "-i", str(path), "-frames:v", "1",\n                "-vf", "scale=640:-2", "-q:v", "3", str(thumb),\n                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,\n            )\n            task.register_process(proc)\n            await proc.wait()\n            if proc.returncode == 0 and thumb.is_file() and thumb.stat().st_size > 0:\n                return str(thumb)\n            thumb.unlink(missing_ok=True)\n            return None\n        finally:\n            task.unregister_process(proc)\n\n'''
if "async def _probe_media" not in s:
    if anchor not in s:
        raise RuntimeError("_send anchor not found")
    s = s.replace(anchor, helpers + anchor, 1)

UPLOAD.write_text(s)

# ------------------------- main: clean caption/title + channel metadata -------------------------
m = MAIN.read_text()
if "DEFAULT_CHANNEL_ID" not in m.split("from crawler", 1)[0]:
    old_import = 'from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH'
    new_import = 'from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH, DEFAULT_CHANNEL_ID'
    m = m.replace(old_import, new_import, 1)

# Persist the channel destination in each task payload.
if '"upload_chat_id": DEFAULT_CHANNEL_ID' not in m:
    m = m.replace(
        '"resolution": item.get("resolution"), "cookiefile":',
        '"resolution": item.get("resolution"), "upload_chat_id": DEFAULT_CHANNEL_ID, "cookiefile":',
        1,
    )

# Use the cleaned title for Telegram caption/title, not the crawler's raw provider string.
if "def _clean_media_title" not in m:
    helper = '''    @staticmethod\n    def _clean_media_title(title, resolution=None):\n        text = str(title or "Video")\n        text = re.sub(r"(?i)https?://\\S+", " ", text)\n        text = re.sub(r"(?i)\\b(?:happy2hub(?:\\s*[._-]?\\s*eu)?|upfiles(?:\\.com)?|send(?:\\.now)?|luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\\b", " ", text)\n        text = re.sub(r"(?i)\\bunknown\\s+episode\\b", " ", text)\n        text = re.sub(r"(?i)\\b(?:240|360|480|540|576|720|1080|1440|2160)p\\b", " ", text)\n        text = re.sub(r"\\s*[-–—]+\\s*[-–—]+\\s*", " - ", text)\n        text = re.sub(r"\\s{2,}", " ", text).strip(" -._")\n        return (f"{text} - {resolution}" if resolution and text else text or "Video")[:220]\n\n'''
    anchor = "    def _series_name(self, items):"
    if anchor in m:
        m = m.replace(anchor, helper + anchor, 1)

# Clean title references in enqueue_selected block only.
start = m.find("    async def enqueue_selected")
end = m.find("    async def ", start + 10)
if start >= 0:
    if end < 0:
        end = len(m)
    block = m[start:end]
    block = block.replace('item.get("title")', 'self._clean_media_title(item.get("title"), item.get("resolution"))')
    m = m[:start] + block + m[end:]

MAIN.write_text(m)
print("CHANNEL MEDIA + THUMBNAIL UPGRADE APPLIED")
print("FFprobe: duration + dimensions")
print("FFmpeg: automatic JPEG thumbnail")
print("Channel upload: configured destination")
print("Captions/titles: cleaned provider/domain/resolution noise")
