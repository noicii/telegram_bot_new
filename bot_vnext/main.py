"""Telegram entrypoint for Bot V2 with a complete owner-only command center."""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent
if str(V2_ROOT) not in sys.path: sys.path.insert(0, str(V2_ROOT))
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH
from crawler import crawl_blog_episodes
from utils import sanitize_filename
from app.pipeline import Pipeline
from app.downloader.method_store import get_method, set_method, get_default_method, set_default_method, next_method, method_label, METHODS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("bot_vnext")
PAGE_SIZE = 8

COMMANDS = [("start", "🚀 Start Bot V2"), ("status", "📊 Live download/upload progress"), ("queue", "📋 Running & queued tasks"), ("cancel", "🛑 Cancel a download/upload task"), ("retry", "🔁 Retry a failed task"), ("clear", "🧹 Clear completed/failed/cancelled tasks"), ("crawl", "🔎 Crawl URL & select episodes"), ("settings", "⚙️ Bot settings & controls"), ("method", "🎯 Choose download method"), ("health", "🩺 Check bot & engine health")]


def owner_only(message) -> bool:
    return bool(OWNER_ID and message.from_user and message.from_user.id == OWNER_ID)


def callback_owner(query) -> bool:
    return bool(OWNER_ID and query.from_user and query.from_user.id == OWNER_ID)


class V2Bot:
    def __init__(self, client: Client):
        self.client = client
        self.pipeline: Pipeline | None = None
        self.sessions: dict[int, dict] = {}
        self.progress_cache: dict[str, tuple] = {}
        self.dashboard_messages: dict[int, int] = {}

    async def start(self):
        self.pipeline = Pipeline(self.client, DOWNLOAD_DIR, on_progress=self.on_progress, on_complete=self.on_complete, on_failed=self.on_failed)
        await self.pipeline.start()
        try: await self.client.set_bot_commands([BotCommand(c, d) for c, d in COMMANDS])
        except Exception: logger.exception("could not set bot command menu")
        logger.info("V2 pipeline started: downloads=2 uploads=4")

    async def stop(self):
        if self.pipeline:
            await self.pipeline.stop(); self.pipeline = None

    async def start_cmd(self, client, message):
        if not owner_only(message): return
        await message.reply_text("🎬 **BOT V2 READY**\n\n🔎 `/crawl <URL>` — crawl & select episodes\n📊 `/status` — live status\n📋 `/queue` — task list\n🎯 `/method` — choose download method\n⚙️ `/settings` — all controls\n🩺 `/health` — system health\n\n⚡ Downloads: **2** simultaneous\n⚡ Uploads: **4** simultaneous\n🛑 Download/upload cancellation is independent.")

    async def status_cmd(self, client, message):
        if owner_only(message): await self.send_status(message)

    async def send_status(self, message):
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is not running."); return
        counts = await self.pipeline.db.counts()
        d, u = counts.get("download", {}), counts.get("upload", {})
        active_d, active_u = self.pipeline.download.active_workers(), self.pipeline.upload.active_workers()
        queued_d, queued_u = d.get("queued", 0), u.get("queued", 0)
        done = d.get("completed", 0) + u.get("completed", 0)
        failed = d.get("failed", 0) + u.get("failed", 0)
        cancelled = d.get("cancelled", 0) + u.get("cancelled", 0)
        default_method = await get_default_method()
        active_rows = await self.pipeline.db.get_tasks(statuses=("queued", "downloading", "uploading"), limit=25)
        method_counts = {}
        for row in active_rows:
            method = (row.get("metadata") or {}).get("download_method") or "auto"
            method_counts[method] = method_counts.get(method, 0) + 1
        method_line = f"🎯 **Selected method:** {method_label(default_method)}"
        if method_counts:
            method_line += "\n📌 **Active task methods:** " + " • ".join(f"{method_label(k)} ×{v}" for k, v in method_counts.items())
        text = ("📊 **V2 QUEUE STATUS**\n\n" + method_line + "\n\n" + f"⬇️ Downloads: **{active_d}/2** active • {queued_d} queued\n" + f"⬆️ Uploads: **{active_u}/4** active • {queued_u} queued\n\n" + f"⏳ Download pending: {queued_d}\n⏳ Upload pending: {queued_u}\n✅ Done: {done}\n❌ Failed: {failed}\n🛑 Cancelled: {cancelled}")
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="v2:status")], [InlineKeyboardButton("🎯 Change Method", callback_data="v2:methodmenu"), InlineKeyboardButton("📋 Queue", callback_data="v2:queue")], [InlineKeyboardButton("⚙️ Settings", callback_data="v2:settings")]]))

    async def queue_cmd(self, client, message):
        if owner_only(message): await self.send_queue(message)

    async def send_queue(self, message):
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline."); return
        rows = await self.pipeline.db.get_tasks(limit=25)
        active = [r for r in rows if r.get("status") in {"queued", "downloading", "uploading"}]
        if not active:
            await message.reply_text("📋 **QUEUE EMPTY**\n\nNo queued or running tasks.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Status", callback_data="v2:status")]])); return
        lines = ["📋 **V2 QUEUE**", ""]
        buttons = []
        for i, row in enumerate(active[:15], 1):
            kind = "⬇️" if row.get("task_type") == "download" else "⬆️"
            status = str(row.get("status", "?")).upper()
            title = str(row.get("title") or row.get("url") or row.get("id"))[:42]
            progress = float(row.get("progress") or 0)
            method = (row.get("metadata") or {}).get("download_method") or "auto"
            lines.append(f"{kind} **{i}. {status}** • {progress:.0f}% • {method_label(method)}\n`{title}`\n`{row.get('id')}`")
            buttons.append([InlineKeyboardButton(f"🛑 Cancel {i}", callback_data=f"v2:cancel:{row.get('id')}")])
        buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="v2:queue"), InlineKeyboardButton("🎯 Method", callback_data="v2:methodmenu")])
        buttons.append([InlineKeyboardButton("🧹 Clear Done", callback_data="v2:clear")])
        await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

    async def cancel_cmd(self, client, message):
        if not owner_only(message): return
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.reply_text("Usage: `/cancel TASK_ID`\n\nThis cancels whichever stage is active: download or upload."); return
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline."); return
        ok = await self.pipeline.cancel(parts[-1].strip())
        await message.reply_text("🛑 Task cancelled independently." if ok else "⚠️ Task is not currently active.")

    async def retry_cmd(self, client, message):
        if not owner_only(message): return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.reply_text("Usage: `/retry TASK_ID`\n\nDownloads retry with the current `/method` selection and get a fresh 3-attempt limit."); return
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline."); return
        selected = await get_default_method()
        ok = await self.pipeline.retry(parts[1].strip(), method=selected)
        await message.reply_text(f"🔁 Task re-queued with **{method_label(selected)}** (max 3 attempts)." if ok else "⚠️ Retry is available only for failed/cancelled tasks.")

    async def clear_cmd(self, client, message):
        if not owner_only(message): return
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline."); return
        count = await self.pipeline.db.clear_finished()
        await message.reply_text(f"🧹 Cleared **{count}** completed/failed/cancelled task(s).")

    async def crawl_cmd(self, client, message):
        if not owner_only(message): return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.reply_text("Usage: `/crawl https://example.com/episode-page`"); return
        await self.crawl_url(message, parts[1].strip())

    async def text_url(self, client, message):
        if not owner_only(message): return
        text = (message.text or "").strip()
        if not text or text.startswith("/"): return
        if text.startswith(("http://", "https://")): await self.crawl_url(message, text)
        else: await message.reply_text("🔗 Send a valid http/https URL or use `/crawl <URL>`.")

    async def crawl_url(self, message, url: str):
        wait = await message.reply_text("🔎 Crawling… please wait")
        try: items = await asyncio.to_thread(crawl_blog_episodes, url)
        except Exception as exc:
            logger.exception("crawl failed"); await wait.edit_text(f"❌ Crawl failed\n`{str(exc)[:700]}`"); return
        if not items:
            await wait.edit_text("❌ No downloadable episode links found."); return
        series = self._series_name(items)
        saved_method = await get_method(items[0].get("url") or url)
        session = {"items": items, "page": 0, "selected": set(), "series": series, "source_url": url, "message_id": wait.id, "method": saved_method}
        self.sessions[message.from_user.id] = session
        await self.render_selection(wait, session)

    def _series_name(self, items):
        title = str(items[0].get("title") or "Series")
        episode, resolution = str(items[0].get("episode") or ""), str(items[0].get("resolution") or "")
        for part in (f" - {episode}", f" - {resolution}"):
            if part and part in title: title = title.split(part, 1)[0]
        return title.strip(" -") or "Series"

    def _button_label(self, item, index, selected):
        mark = "☑️" if index in selected else "⬜"
        return f"{mark} {item.get('episode') or 'Episode ?'} • {item.get('resolution') or 'Unknown'} • {urlparse(item.get('url') or '').netloc or 'Unknown'}"

    async def render_selection(self, message, session):
        items, page, selected = session["items"], session["page"], session["selected"]
        total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        start, end = page * PAGE_SIZE, min(len(items), page * PAGE_SIZE + PAGE_SIZE)
        lines = [f"🎬 **{session['series']}**", f"📦 **{len(items)} Options**", ""] + [self._button_label(items[i], i, selected) for i in range(start, end)] + ["", f"📄 Page {page + 1}/{total_pages} • ☑️ {len(selected)}/{len(items)}", f"🎯 **Download method: {method_label(session.get('method', 'auto'))}**"]
        buttons = [[InlineKeyboardButton(self._button_label(items[i], i, selected), callback_data=f"v2:t:{i}")] for i in range(start, end)]
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("◀️ Previous", callback_data="v2:p:-1"))
        if page + 1 < total_pages: nav.append(InlineKeyboardButton("Next ▶️", callback_data="v2:p:1"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton("☑️ Select All", callback_data="v2:all"), InlineKeyboardButton("❌ Clear", callback_data="v2:selclear")])
        buttons.append([InlineKeyboardButton(f"🎯 Change: {method_label(session.get('method', 'auto'))}", callback_data="v2:method")])
        buttons.append([InlineKeyboardButton("🚀 Download Selected", callback_data="v2:download")])
        buttons.append([InlineKeyboardButton("❌ Cancel Selection", callback_data="v2:close")])
        await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

    async def method_cmd(self, client, message):
        if not owner_only(message): return
        await self.send_method_menu(message)

    async def send_method_menu(self, message):
        current = await get_default_method()
        buttons = [[InlineKeyboardButton(("✅ " if method == current else "") + method_label(method), callback_data=f"v2:m:{method}")] for method in METHODS]
        await message.reply_text(f"🎯 **DOWNLOAD METHOD**\n\nCurrent default: **{method_label(current)}**\n\nTap a method. It becomes the default for new crawls and is remembered. During a crawl you can also change it with the **Change** button.", reply_markup=InlineKeyboardMarkup(buttons))

    async def settings_cmd(self, client, message):
        if owner_only(message): await self.send_settings(message)

    async def send_settings(self, message):
        method = await get_default_method()
        await message.reply_text("⚙️ **BOT V2 SETTINGS**\n\n" + f"🎯 Download method: **{method_label(method)}**\n" + "⬇️ Download workers: **2**\n⬆️ Upload workers: **4**\n🛑 Independent cancellation: **ON**\n💾 Persistent SQLite queue: **ON**\n🖼️ Auto thumbnail: configured\n🍪 Cookies: auto-detected\n\nChoose an action below:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎯 Download Method", callback_data="v2:methodmenu")], [InlineKeyboardButton("📊 Queue Status", callback_data="v2:status"), InlineKeyboardButton("📋 Queue", callback_data="v2:queue")], [InlineKeyboardButton("🩺 Health Check", callback_data="v2:health")], [InlineKeyboardButton("🧹 Clear Finished", callback_data="v2:clear")]]))

    async def health_cmd(self, client, message):
        if owner_only(message): await self.send_health(message)

    async def send_health(self, message):
        checks = [("Pipeline", bool(self.pipeline and self.pipeline._started)), ("SQLite DB", Path(ROOT / "bot_vnext.db").exists()), ("FFmpeg", shutil.which("ffmpeg") is not None), ("aria2c", shutil.which("aria2c") is not None)]
        chromium = Path.home() / ".cache" / "ms-playwright"
        checks.append(("Playwright cache", chromium.exists() and any(chromium.glob("chromium*"))))
        checks.append(("Download workers", bool(self.pipeline and len(self.pipeline.download.running_tasks) <= 2)))
        checks.append(("Upload workers", bool(self.pipeline and len(self.pipeline.upload.running_tasks) <= 4)))
        lines = ["🩺 **V2 HEALTH CHECK**", ""] + [f"{'✅' if ok else '❌'} {name}" for name, ok in checks]
        if self.pipeline: lines += ["", f"⬇️ Active downloads: {self.pipeline.download.active_workers()}/2", f"⬆️ Active uploads: {self.pipeline.upload.active_workers()}/4"]
        await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Recheck", callback_data="v2:health")]]))

    async def callback(self, client, query):
        if not callback_owner(query):
            await query.answer("Not allowed", show_alert=True); return
        data = query.data or ""
        if data == "v2:status": await query.answer(); await self.send_status(query.message); return
        if data == "v2:queue": await query.answer(); await self.send_queue(query.message); return
        if data == "v2:settings": await query.answer(); await self.send_settings(query.message); return
        if data in {"v2:health", "v2:healthmenu"}: await query.answer(); await self.send_health(query.message); return
        if data == "v2:methodmenu" and not self.sessions.get(query.from_user.id): await query.answer(); await self.send_method_menu(query.message); return
        if data.startswith("v2:m:"):
            method = data.rsplit(":", 1)[1]
            if method not in METHODS:
                await query.answer("Invalid method", show_alert=True); return
            await set_default_method(method); await query.answer(f"Default: {method_label(method)}"); await self.send_method_menu(query.message); return
        if data == "v2:clear":
            if self.pipeline:
                count = await self.pipeline.db.clear_finished(); await query.answer(f"Cleared {count} task(s)"); await self.send_queue(query.message)
            else: await query.answer("Pipeline offline", show_alert=True)
            return
        if data.startswith("v2:cancel:"):
            task_id = data.split(":", 2)[2]; ok = bool(self.pipeline and await self.pipeline.cancel(task_id)); await query.answer("Cancelled" if ok else "Not active", show_alert=not ok); await self.send_queue(query.message); return
        session = self.sessions.get(query.from_user.id)
        if not session:
            await query.answer("Selection expired. Send /crawl again.", show_alert=True); return
        if data == "v2:method":
            session["method"] = next_method(session.get("method", "auto")); await query.answer(f"Method: {method_label(session['method'])}"); await self.render_selection(query.message, session); return
        if data.startswith("v2:t:"):
            index = int(data.rsplit(":", 1)[1]); session["selected"].discard(index) if index in session["selected"] else session["selected"].add(index); await query.answer(); await self.render_selection(query.message, session); return
        if data == "v2:all": session["selected"] = set(range(len(session["items"]))); await query.answer("All selected"); await self.render_selection(query.message, session); return
        if data == "v2:selclear": session["selected"].clear(); await query.answer("Selection cleared"); await self.render_selection(query.message, session); return
        if data.startswith("v2:p:"):
            delta = int(data.rsplit(":", 1)[1]); total_pages = max(1, (len(session["items"]) + PAGE_SIZE - 1) // PAGE_SIZE); session["page"] = max(0, min(total_pages - 1, session["page"] + delta)); await query.answer(); await self.render_selection(query.message, session); return
        if data == "v2:close": self.sessions.pop(query.from_user.id, None); await query.answer("Selection closed"); await query.message.edit_text("❌ Selection cancelled."); return
        if data == "v2:download":
            selected = sorted(session["selected"])
            if not selected: await query.answer("Select at least one item", show_alert=True); return
            await query.answer("Queued"); await self.enqueue_selected(query.message, session, selected); return

    async def enqueue_selected(self, message, session, selected):
        if not self.pipeline: await message.edit_text("❌ V2 pipeline is offline."); return
        selected_method = session.get("method", await get_default_method())
        dashboard = await message.edit_text(f"🚀 **Queued {len(selected)} task(s)**\n🎬 {session['series']}\n🎯 Method: {method_label(selected_method)}\n\n⬇️ Downloads: 2\n⬆️ Uploads: 4\n\n⏳ Starting download…")
        self.dashboard_messages[message.chat.id] = dashboard.id
        for index in selected:
            item = session["items"][index]; task_id = uuid.uuid4().hex
            filename = sanitize_filename(item.get("title") or f"{task_id}.mp4")
            if not Path(filename).suffix: filename += ".mp4"
            await set_method(item.get("url") or "", selected_method)
            metadata = {"source_url": item.get("source_url"), "provider": item.get("source"), "resolution": item.get("resolution"), "cookiefile": str(COOKIES_PATH) if COOKIES_PATH.is_file() else None, "download_method": selected_method, "headers": {"Referer": item.get("source_url")} if item.get("source_url") else {}}
            metadata = {k: v for k, v in metadata.items() if v}
            payload = {"task_type": "download", "url": item["url"], "filename": filename, "chat_id": message.chat.id, "caption": item.get("title") or session["series"], "thumbnail": str(THUMB_PATH) if THUMB_PATH.is_file() else None, "mode": "video", "title": item.get("title"), "provider": item.get("source"), "resolution": item.get("resolution"), "metadata": metadata}
            try: await self.pipeline.submit(task_id, payload)
            except Exception: logger.exception("queue submit failed for %s", task_id)
        self.sessions.pop(message.chat.id, None)

    async def _render_live_dashboard(self, chat_id: int):
        message_id = self.dashboard_messages.get(chat_id)
        if not message_id or not self.pipeline: return
        rows = await self.pipeline.db.get_tasks(statuses=("queued", "downloading", "uploading"), limit=25)
        counts = await self.pipeline.db.counts()
        d, u = counts.get("download", {}), counts.get("upload", {})
        lines = ["📊 **LIVE DOWNLOAD / UPLOAD**", "", f"⬇️ Downloads: **{self.pipeline.download.active_workers()}/2 active** • {d.get('queued', 0)} queued", f"⬆️ Uploads: **{self.pipeline.upload.active_workers()}/4 active** • {u.get('queued', 0)} queued", ""]
        if not rows:
            lines += ["✅ No active tasks.", f"Completed: {d.get('completed',0) + u.get('completed',0)} • Failed: {d.get('failed',0) + u.get('failed',0)} • Cancelled: {d.get('cancelled',0) + u.get('cancelled',0)}"]
        else:
            for row in rows[:10]:
                kind = "⬇️" if row.get("task_type") == "download" else "⬆️"
                status = str(row.get("status") or "queued").upper()
                pct = float(row.get("progress") or 0)
                method = (row.get("metadata") or {}).get("download_method") or "auto"
                title = str(row.get("title") or row.get("url") or row.get("id"))[:45]
                lines.append(f"{kind} **{status}** • **{pct:.1f}%** • {method_label(method)}")
                lines.append(f"🎬 {title}")
                if row.get("speed"): lines.append(f"⚡ {float(row.get('speed') or 0)/1048576:.2f} MB/s")
                lines.append("")
        try:
            msg = await self.client.get_messages(chat_id, message_id)
            if msg and msg.text:
                await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="v2:status"), InlineKeyboardButton("📋 Queue", callback_data="v2:queue")]]))
        except Exception:
            pass

    async def on_progress(self, kind, task_id, percent=None, current=None, total=None, speed=None, eta=None):
        task_id = str(task_id); now = asyncio.get_running_loop().time(); last = self.progress_cache.get(task_id)
        if last and now - last[0] < 2.0 and (percent is None or last[1] == percent): return
        self.progress_cache[task_id] = (now, percent)
        try:
            row = await self.pipeline.db.get_task(task_id) if self.pipeline else None
            if row and row.get("chat_id"):
                await self._render_live_dashboard(int(row["chat_id"]))
        except Exception:
            logger.debug("live dashboard update failed", exc_info=True)

    async def on_complete(self, task_id):
        self.progress_cache.pop(str(task_id), None)
        try:
            row = await self.pipeline.db.get_task(str(task_id)) if self.pipeline else None
            if row and row.get("chat_id"): await self._render_live_dashboard(int(row["chat_id"]))
        except Exception: pass

    async def on_failed(self, task_id, error):
        self.progress_cache.pop(str(task_id), None); logger.error("task %s failed: %s", task_id, error)
        try:
            row = await self.pipeline.db.get_task(str(task_id)) if self.pipeline else None
            if row and row.get("chat_id"): await self._render_live_dashboard(int(row["chat_id"]))
        except Exception: pass


async def run():
    if not BOT_TOKEN or not API_ID or not API_HASH or not OWNER_ID: raise RuntimeError("API_ID, API_HASH, BOT_TOKEN and OWNER_ID must be configured")
    app = Client("telegram_bot_v2", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=str(ROOT / "data"))
    bot = V2Bot(app)
    app.add_handler(MessageHandler(bot.start_cmd, filters.command("start")))
    app.add_handler(MessageHandler(bot.status_cmd, filters.command("status")))
    app.add_handler(MessageHandler(bot.queue_cmd, filters.command("queue")))
    app.add_handler(MessageHandler(bot.cancel_cmd, filters.command("cancel")))
    app.add_handler(MessageHandler(bot.retry_cmd, filters.command("retry")))
    app.add_handler(MessageHandler(bot.clear_cmd, filters.command("clear")))
    app.add_handler(MessageHandler(bot.crawl_cmd, filters.command("crawl")))
    app.add_handler(MessageHandler(bot.settings_cmd, filters.command("settings")))
    app.add_handler(MessageHandler(bot.method_cmd, filters.command("method")))
    app.add_handler(MessageHandler(bot.health_cmd, filters.command("health")))
    app.add_handler(MessageHandler(bot.text_url, filters.text & ~filters.command([c for c, _ in COMMANDS])))
    app.add_handler(CallbackQueryHandler(bot.callback))
    async with app:
        await bot.start()
        try: await asyncio.Event().wait()
        finally: await bot.stop()


if __name__ == "__main__": asyncio.run(run())
