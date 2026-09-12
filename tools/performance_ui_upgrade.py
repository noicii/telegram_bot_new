from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
ENGINE = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"


def replace_method(src: str, name: str, new_body: str) -> str:
    pattern = rf"(?ms)^    (?:async )?def {re.escape(name)}\(.*?(?=^    (?:async )?def |\Z)"
    match = re.search(pattern, src)
    if not match:
        raise RuntimeError(f"method not found: {name}")
    return src[:match.start()] + new_body.rstrip() + "\n\n" + src[match.end():]


# ------------------------- downloader engine -------------------------
engine = ENGINE.read_text()
engine = engine.replace(
    "ProgressCallback = Callable[[float | None, int | None, int | None, float | None], Awaitable[None] | None]",
    "ProgressCallback = Callable[[float | None, int | None, int | None, float | None, float | None], Awaitable[None] | None]",
)
engine = re.sub(r"sem = asyncio\.Semaphore\(\d+\)", "sem = asyncio.Semaphore(12)", engine, count=1)
engine = engine.replace(
    "connector = aiohttp.TCPConnector(limit=150, limit_per_host=40, ttl_dns_cache=300, enable_cleanup_closed=True)",
    "connector = aiohttp.TCPConnector(limit=300, limit_per_host=80, ttl_dns_cache=600, enable_cleanup_closed=True)",
)
old_report = "await self._report(progress, completed * 100 / len(segments), total_bytes, None, total_bytes / elapsed)"
new_report = "rate = total_bytes / elapsed\n                                eta = ((len(segments) - completed) * elapsed / completed) if completed else None\n                                await self._report(progress, completed * 100 / len(segments), completed, len(segments), rate, eta)"
if old_report in engine:
    engine = engine.replace(old_report, new_report, 1)
else:
    engine = re.sub(
        r"await self\._report\(progress, completed \* 100 / len\(segments\), total_bytes, None, total_bytes / elapsed\)",
        new_report,
        engine,
        count=1,
    )
engine = engine.replace(
    "async def _report(self, callback, percent, current, total, speed):",
    "async def _report(self, callback, percent, current, total, speed, eta=None):",
)
engine = engine.replace(
    "callback(percent, current, total, speed)",
    "callback(percent, current, total, speed, eta)",
)
ENGINE.write_text(engine)


# ------------------------- main / live dashboard -------------------------
main = MAIN.read_text()
if "import re\n" not in main:
    main = main.replace("import logging\n", "import logging\nimport re\n", 1)

needle = "        self.dashboard_messages: dict[int, int] = {}"
if needle in main and "self.dashboard_locks" not in main:
    main = main.replace(needle, needle + "\n        self.dashboard_locks: dict[int, asyncio.Lock] = {}", 1)

helper = '''    @staticmethod
    def _clean_media_title(title, resolution=None):
        text = str(title or "Video")
        text = re.sub(r"(?i)https?://\\S+", " ", text)
        text = re.sub(r"(?i)\\b(?:happy2hub(?:\\s*[._-]?\\s*eu)?|upfiles(?:\\.com)?|send(?:\\.now)?|luluvdo|luluvid|luluvdoo|luluvideo|lulustream)\\b", " ", text)
        text = re.sub(r"(?i)\\bunknown\\s+episode\\b", " ", text)
        text = re.sub(r"(?i)\\b(?:240|360|480|540|576|720|1080|1440|2160)p\\b", " ", text)
        text = re.sub(r"[|]+", " ", text)
        text = re.sub(r"\\s*[-–—]+\\s*[-–—]+\\s*", " - ", text)
        text = re.sub(r"\\s{2,}", " ", text).strip(" -._")
        if resolution:
            text = f"{text} - {resolution}" if text else str(resolution)
        return text[:220] or "Video"
'''
if "def _clean_media_title" not in main:
    anchor = "    def _series_name(self, items):"
    if anchor not in main:
        raise RuntimeError("_series_name anchor not found")
    main = main.replace(anchor, helper + "\n" + anchor, 1)

m = re.search(r"(?ms)^    async def enqueue_selected\(.*?(?=^    (?:async )?def |\Z)", main)
if m:
    block = m.group(0)
    block = block.replace("item.get(\"title\")", "self._clean_media_title(item.get(\"title\"), item.get(\"resolution\"))")
    main = main[:m.start()] + block + main[m.end():]

new_dashboard = '''    async def _render_live_dashboard(self, chat_id):
        if not self.pipeline:
            return
        message_id = self.dashboard_messages.get(chat_id)
        if not message_id:
            return
        lock = self.dashboard_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            try:
                rows = await self.pipeline.db.get_tasks(statuses=("queued", "downloading", "uploading"), limit=25)
                rows.sort(key=lambda r: str(r.get("created_at") or ""))
                counts = await self.pipeline.db.counts()
                d = counts.get("download", {})
                u = counts.get("upload", {})
                active_d = self.pipeline.download.active_workers()
                active_u = self.pipeline.upload.active_workers()
                lines = ["📊 **LIVE DOWNLOAD / UPLOAD**", "", f"⬇️ Downloads: **{active_d}/2** active • {d.get('queued', 0)} queued", f"⬆️ Uploads: **{active_u}/4** active • {u.get('queued', 0)} queued", ""]
                if not rows:
                    lines.append("✅ No active tasks.")
                for row in rows[:8]:
                    task_id = str(row.get("id"))
                    kind = str(row.get("task_type") or "download")
                    cache = self.progress_cache.get(task_id, ())
                    percent = float(cache[1] if len(cache) > 1 and cache[1] is not None else row.get("progress") or 0)
                    current = cache[2] if len(cache) > 2 else None
                    total = cache[3] if len(cache) > 3 else None
                    speed = cache[4] if len(cache) > 4 else row.get("speed")
                    eta = cache[5] if len(cache) > 5 else row.get("eta")
                    title = self._clean_media_title(row.get("title") or row.get("url") or "Video", row.get("resolution"))
                    method = (row.get("metadata") or {}).get("download_method") or "auto"
                    filled = max(0, min(20, int(percent / 5)))
                    bar = "[" + "█" * filled + "░" * (20 - filled) + "]"
                    icon = "⬆️" if kind == "upload" else "⬇️"
                    label = "UPLOADING" if kind == "upload" else "DOWNLOADING"
                    method_text = f" • {method_label(method)}" if kind == "download" else ""
                    lines.append(f"{icon} **{label} • {percent:.1f}%**{method_text}")
                    lines.append(f"🎬 {title}")
                    lines.append(f"{bar} **{percent:.1f}%**")
                    if kind == "download" and current is not None and total:
                        lines.append(f"📦 Segments: **{int(current)}/{int(total)}**")
                    if speed:
                        lines.append(f"⚡ **{float(speed) / 1024 / 1024:.2f} MB/s**")
                    if eta:
                        lines.append(f"⏱️ ETA: **{max(0, int(float(eta)))}s**")
                    lines.append("")
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="v2:live_refresh"), InlineKeyboardButton("📋 Queue", callback_data="v2:queue")]])
                await self.client.edit_message_text(chat_id, message_id, "\\n".join(lines).strip(), reply_markup=markup)
            except Exception as exc:
                logger.debug("live dashboard refresh failed: %s", exc)
'''
if "async def _render_live_dashboard" in main:
    main = replace_method(main, "_render_live_dashboard", new_dashboard)

new_progress = '''    async def on_progress(self, kind, task_id, percent, current, total, speed, eta=None):
        now = asyncio.get_running_loop().time()
        previous = self.progress_cache.get(str(task_id))
        if previous and now - float(previous[0]) < 1.25:
            return
        self.progress_cache[str(task_id)] = (now, percent or 0, current, total, speed or 0, eta or 0)
        for chat_id in list(self.dashboard_messages):
            try:
                await self._render_live_dashboard(chat_id)
            except Exception:
                logger.debug("progress dashboard update failed", exc_info=True)
'''
if "async def on_progress" in main:
    main = replace_method(main, "on_progress", new_progress)

if 'data == "v2:live_refresh"' not in main:
    marker = '        if data == "v2:status":'
    if marker in main:
        main = main.replace(marker, '        if data == "v2:live_refresh":\n            await callback_query.answer("Refreshing…")\n            await self._render_live_dashboard(callback_query.message.chat.id)\n            return\n' + marker, 1)

MAIN.write_text(main)
print("V2 PERFORMANCE + CLEAN LIVE UI APPLIED")
print("HLS concurrency: 12 segments/task | max 24 across 2 download workers")
print("HLS telemetry: segment count + speed + ETA")
print("Upload UI: no fake segment counters")
print("Titles: provider/domain/resolution noise cleaned")
