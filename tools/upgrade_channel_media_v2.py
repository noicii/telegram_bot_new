from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
UPLOAD = ROOT / "bot_vnext" / "app" / "uploader" / "engine.py"

s = UPLOAD.read_text()
if "import json\n" not in s:
    s = s.replace("import time\n", "import time\nimport json\nimport tempfile\n", 1)
needle = '''        task.check_cancelled()\n        total = path.stat().st_size\n        progress_state = {"last": 0.0, "started": time.monotonic()}'''
replacement = '''        task.check_cancelled()\n        total = path.stat().st_size\n        media = await self._probe_media(path)\n        if duration is None: duration = media.get("duration")\n        if width is None: width = media.get("width")\n        if height is None: height = media.get("height")\n        generated_thumb = None\n        if thumbnail is None and (mode or "video").lower() not in {"document", "file", "doc", "audio", "music"}:\n            generated_thumb = await self._make_thumbnail(path, task)\n            thumbnail = generated_thumb\n        progress_state = {"last": 0.0, "started": time.monotonic()}'''
if needle not in s:
    raise RuntimeError("upload setup anchor not found")
s = s.replace(needle, replacement, 1)
needle2 = '''        finally:\n            if progress_tasks:\n                await asyncio.gather(*progress_tasks, return_exceptions=True)'''
replacement2 = '''        finally:\n            if progress_tasks:\n                await asyncio.gather(*progress_tasks, return_exceptions=True)\n            if generated_thumb:\n                try: Path(generated_thumb).unlink(missing_ok=True)\n                except OSError: pass'''
if needle2 in s and "Path(generated_thumb).unlink" not in s:
    s = s.replace(needle2, replacement2, 1)
helpers = '''    async def _probe_media(self, path: Path) -> dict:\n        import shutil\n        binary = shutil.which("ffprobe")\n        if not binary: return {}\n        proc = await asyncio.create_subprocess_exec(binary, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)\n        out, _ = await proc.communicate()\n        if proc.returncode != 0: return {}\n        try:\n            data = json.loads(out.decode("utf-8", "ignore")); stream = (data.get("streams") or [{}])[0]; fmt = data.get("format") or {}\n            duration = int(float(fmt.get("duration") or 0))\n            return {"duration": duration or None, "width": stream.get("width"), "height": stream.get("height")}\n        except Exception: return {}\n\n    async def _make_thumbnail(self, path: Path, task: TaskContext) -> str | None:\n        import shutil\n        binary = shutil.which("ffmpeg")\n        if not binary: return None\n        thumb = Path(tempfile.gettempdir()) / f"botv2_thumb_{task.task_id}.jpg"\n        proc = await asyncio.create_subprocess_exec(binary, "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(thumb), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)\n        task.register_process(proc)\n        try:\n            await proc.wait()\n            if proc.returncode == 0 and thumb.is_file() and thumb.stat().st_size > 0: return str(thumb)\n            thumb.unlink(missing_ok=True)\n            proc = await asyncio.create_subprocess_exec(binary, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.1", "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(thumb), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)\n            task.register_process(proc); await proc.wait()\n            if proc.returncode == 0 and thumb.is_file() and thumb.stat().st_size > 0: return str(thumb)\n            thumb.unlink(missing_ok=True); return None\n        finally: task.unregister_process(proc)\n\n'''
if "async def _probe_media" not in s:
    s = s.replace("    async def _send(\n", helpers + "    async def _send(\n", 1)
UPLOAD.write_text(s)

m = MAIN.read_text()
if "import re\n" not in m: m = m.replace("import logging\n", "import logging\nimport re\n", 1)
if "DEFAULT_CHANNEL_ID" not in m.split("from crawler", 1)[0]:
    m = m.replace('from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH', 'from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH, DEFAULT_CHANNEL_ID', 1)
if '"upload_chat_id": DEFAULT_CHANNEL_ID' not in m:
    m = m.replace('"resolution": item.get("resolution"), "cookiefile":', '"resolution": item.get("resolution"), "upload_chat_id": DEFAULT_CHANNEL_ID, "cookiefile":', 1)
if "def _clean_media_title" not in m:
    helper = '''    @staticmethod\n    def _clean_media_title(title, resolution=None):\n        text = str(title or "Video")\n        text = re.sub(r"(?i)https?://\\S+", " ", text)\n        text = re.sub(r"(?i)\\b(?:happy2hub(?:\\s*[._-]?\\s*eu)?|upfiles(?:\\.com)?|send(?:\\.now)?|luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\\b", " ", text)\n        text = re.sub(r"(?i)\\bunknown\\s+episode\\b", " ", text)\n        text = re.sub(r"(?i)\\b(?:240|360|480|540|576|720|1080|1440|2160)p\\b", " ", text)\n        text = re.sub(r"\\s{2,}", " ", text).strip(" -._")\n        return (f"{text} - {resolution}" if resolution and text else text or "Video")[:220]\n\n'''
    m = m.replace("    def _series_name(self, items):", helper + "    def _series_name(self, items):", 1)
start = m.find("    async def enqueue_selected")
end = m.find("    async def ", start + 10)
if start >= 0:
    if end < 0: end = len(m)
    block = m[start:end].replace('item.get("title")', 'self._clean_media_title(item.get("title"), item.get("resolution"))')
    m = m[:start] + block + m[end:]
MAIN.write_text(m)
print("CHANNEL MEDIA + THUMBNAIL UPGRADE APPLIED")
print("FFprobe: duration + dimensions | FFmpeg: automatic JPEG thumbnail")
print("Clean Telegram captions/titles + channel destination")
