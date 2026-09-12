from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
ENGINE = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"
UPLOAD = ROOT / "bot_vnext" / "app" / "uploader" / "engine.py"

# ---------------- HLS / direct downloader: aggressive but bounded ----------------
e = ENGINE.read_text()
e = re.sub(r"asyncio\.Semaphore\(\d+\)", "asyncio.Semaphore(16)", e, count=1)
e = re.sub(r"aiohttp\.TCPConnector\(limit=\d+, limit_per_host=\d+", "aiohttp.TCPConnector(limit=200, limit_per_host=60", e, count=1)
# aria2c: 16 connections per download.
e = e.replace('"-x", "8", "-s", "8", "-k", "1M"', '"-x", "16", "-s", "16", "-k", "1M"', 1)
e = e.replace('"-x", "8", "-s", "8"', '"-x", "16", "-s", "16"', 1)
# yt-dlp fragment concurrency.
e = e.replace('"-N", "8", "-f",', '"-N", "16", "-f",', 1)
e = e.replace('"-N", "8"', '"-N", "16"', 1)
ENGINE.write_text(e)

# ---------------- Upload engine: clean metadata + reliable generated thumb ----------------
u = UPLOAD.read_text()
if "import re" not in u.split("from pyrogram", 1)[0]:
    u = u.replace("import logging\n", "import logging\nimport re\n", 1)

# Sanitize the final Telegram caption/title at the last possible point so raw crawler
# provider strings cannot leak into channel posts.
if "def _clean_channel_text" not in u:
    helper = '''    @staticmethod\n    def _clean_channel_text(text: str | None) -> str | None:\n        if not text:\n            return text\n        text = str(text)\n        text = re.sub(r"(?i)https?://\\S+", " ", text)\n        text = re.sub(r"(?i)\\b(?:happy2hub(?:\\s*[._-]?\\s*eu)?|upfiles(?:\\.com)?|send(?:\\.now)?|luluvdo|luluvid|luluvdoo|luluvideo|lulustream|unknown\\s+episode)\\b", " ", text)\n        text = re.sub(r"(?i)\\b(?:240|360|480|540|576|720|1080|1440|2160)p\\b", " ", text)\n        text = re.sub(r"\\s*[-–—|]+\\s*", " - ", text)\n        text = re.sub(r"\\s{2,}", " ", text).strip(" -._")\n        return text[:220] or "Video"\n\n'''
    anchor = "    async def _send("
    if anchor not in u:
        raise RuntimeError("Upload _send anchor not found")
    u = u.replace(anchor, helper + anchor, 1)

# Clean caption/title immediately before Telegram API call.
needle = '''        common = {\n            "chat_id": chat_id,\n            "caption": caption,\n            "progress": progress,\n        }'''
replacement = '''        caption = self._clean_channel_text(caption)\n        title = self._clean_channel_text(title)\n        common = {\n            "chat_id": chat_id,\n            "caption": caption,\n            "progress": progress,\n        }'''
if needle in u:
    u = u.replace(needle, replacement, 1)
UPLOAD.write_text(u)

# ---------------- Main: allow multiple Telegram media transmissions ----------------
m = MAIN.read_text()
# Pyrogram's concurrent transmission setting improves aggregate upload throughput while
# the bot's own UploadManager still limits active uploads to four.
if "max_concurrent_transmissions" not in m:
    patterns = [
        (r'(Client\([^\n]*BOT_TOKEN[^\n]*)(\))', r'\1, max_concurrent_transmissions=8\2'),
        (r'(Client\([^\n]*bot_token[^\n]*)(\))', r'\1, max_concurrent_transmissions=8\2'),
    ]
    for pattern, repl in patterns:
        new_m, n = re.subn(pattern, repl, m, count=1)
        if n:
            m = new_m
            break

MAIN.write_text(m)
print("FINAL AGGRESSIVE MEDIA TUNING APPLIED")
print("HLS concurrency: 16 segments/task")
print("Network: 200 total / 60 per-host")
print("aria2c: 16 connections | yt-dlp: 16 fragments")
print("Uploads: Pyrogram max_concurrent_transmissions=8")
print("Channel captions: provider/domain metadata stripped")
