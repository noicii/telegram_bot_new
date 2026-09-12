from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = ROOT / "bot_vnext" / "main.py"
upload = ROOT / "bot_vnext" / "app" / "queue" / "upload_manager.py"
engine = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"

# Keep dashboard/progress chat separate from the destination channel.
s = main.read_text()
old_import = 'from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH'
new_import = 'from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH, DEFAULT_CHANNEL_ID'
if old_import in s and 'DEFAULT_CHANNEL_ID' not in s.split('from crawler', 1)[0]:
    s = s.replace(old_import, new_import, 1)

needle = '"resolution": item.get("resolution"), "cookiefile":'
if needle in s and '"upload_chat_id": DEFAULT_CHANNEL_ID' not in s:
    s = s.replace(needle, '"resolution": item.get("resolution"), "upload_chat_id": DEFAULT_CHANNEL_ID, "cookiefile":', 1)
main.write_text(s)

# Upload to the configured channel, while chat_id remains the owner's dashboard chat.
s = upload.read_text()
old = 'chat_id=payload["chat_id"],'
new = 'chat_id=(payload.get("metadata") or {}).get("upload_chat_id") or payload["chat_id"],'
if old in s:
    s = s.replace(old, new, 1)
upload.write_text(s)

# Aggressive but bounded network tuning: 8 HLS segments/task, 80 connections/host,
# up to 8 aria2c connections and 8 yt-dlp fragment workers.
e = engine.read_text()
e = e.replace('limit_per_host=40', 'limit_per_host=80', 1)
e = e.replace('asyncio.Semaphore(4)', 'asyncio.Semaphore(8)', 1)
e = e.replace('"--console-log-level=warn", "--dir"', '"--console-log-level=warn", "-x", "8", "-s", "8", "-k", "1M", "--dir"', 1)
e = e.replace('command += ["--newline", "--no-part", "-f",', 'command += ["--newline", "--no-part", "-N", "8", "-f",', 1)
# Make the progress callback compatible with segment ETA telemetry.
e = e.replace('ProgressCallback = Callable[[float | None, int | None, int | None, float | None], Awaitable[None] | None]', 'ProgressCallback = Callable[[float | None, int | None, int | None, float | None, int | None], Awaitable[None] | None]', 1)
e = e.replace('async def _report(callback, percent, current, total, speed):', 'async def _report(callback, percent, current, total, speed, eta=None):', 1)
e = e.replace('result = callback(percent, current, total, speed)', 'result = callback(percent, current, total, speed, eta)', 1)
# Segment progress + ETA.
old_report = 'await self._report(progress, completed * 100 / len(segments), total_bytes, None, total_bytes / elapsed)'
new_report = 'rate = total_bytes / elapsed\n                                eta = ((len(segments) - completed) * elapsed / completed) if completed else None\n                                await self._report(progress, completed * 100 / len(segments), completed, len(segments), rate, eta)'
if old_report in e:
    e = e.replace(old_report, new_report, 1)
engine.write_text(e)

print("AGGRESSIVE DOWNLOAD + CHANNEL UPLOAD APPLIED")
print("HLS concurrency: 8 segments/task | aria2c: 8 connections | yt-dlp: 8 fragments")
print("Upload destination: DEFAULT_CHANNEL_ID")
