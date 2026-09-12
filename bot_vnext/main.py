"""Telegram entrypoint for Bot V2.

The old bot remains untouched on its existing service. This entrypoint uses
V2's persistent pipeline with the existing crawler.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import API_HASH, API_ID, BOT_TOKEN, OWNER_ID, DOWNLOAD_DIR, THUMB_PATH, COOKIES_PATH
from crawler import crawl_blog_episodes
from utils import sanitize_filename
from app.pipeline import Pipeline
from app.downloader.method_store import get_method, set_method, next_method, method_label

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot_vnext")
PAGE_SIZE = 8


def owner_only(message) -> bool:
    return bool(OWNER_ID and message.from_user and message.from_user.id == OWNER_ID)


def callback_owner(query) -> bool:
    return bool(OWNER_ID and query.from_user and query.from_user.id == OWNER_ID)


class V2Bot:
    def __init__(self, client: Client):
        self.client = client
        self.pipeline: Pipeline | None = None
        self.sessions: dict[int, dict] = {}
        self.progress_messages: dict[str, object] = {}
        self.progress_cache: dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def start(self):
        self.pipeline = Pipeline(
            self.client,
            DOWNLOAD_DIR,
            on_progress=self.on_progress,
            on_complete=self.on_complete,
            on_failed=self.on_failed,
        )
        await self.pipeline.start()
        logger.info("V2 pipeline started: downloads=2 uploads=4")

    async def stop(self):
        if self.pipeline:
            await self.pipeline.stop()
            self.pipeline = None

    async def start_cmd(self, client, message):
        if not owner_only(message):
            return
        await message.reply_text(
            "🎬 **Bot V2 Ready**\n\n"
            "Send a crawl URL and I will show selectable episodes.\n\n"
            "⚡ Downloads: 2 simultaneous\n"
            "⚡ Uploads: 4 simultaneous\n"
            "🛑 Every task has independent cancel."
        )

    async def status_cmd(self, client, message):
        if not owner_only(message):
            return
        await self.send_status(message)

    async def send_status(self, message):
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is not running.")
            return
        counts = await self.pipeline.db.counts()
        d = counts.get("download", {})
        u = counts.get("upload", {})
        active_d = self.pipeline.download.active_workers()
        active_u = self.pipeline.upload.active_workers()
        queued_d = d.get("queued", 0)
        queued_u = u.get("queued", 0)
        text = (
            "📊 **V2 QUEUE STATUS**\n\n"
            f"⬇️ Downloads: {active_d}/2 active • {queued_d} queued\n"
            f"⬆️ Uploads: {active_u}/4 active • {queued_u} queued\n"
            f"⏳ Download pending: {queued_d}\n"
            f"⏳ Upload pending: {queued_u}\n"
            f"✅ Done: {d.get('completed', 0) + u.get('completed', 0)}\n"
            f"❌ Failed: {d.get('failed', 0) + u.get('failed', 0)}\n"
            f"🛑 Cancelled: {d.get('cancelled', 0) + u.get('cancelled', 0)}"
        )
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="v2:status")],
        ]))

    async def text_url(self, client, message):
        if not owner_only(message):
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return
        if not (text.startswith("http://") or text.startswith("https://")):
            await message.reply_text("🔗 Send a valid http/https URL.")
            return

        wait = await message.reply_text("🔎 Crawling… please wait")
        try:
            items = await asyncio.to_thread(crawl_blog_episodes, text)
        except Exception as exc:
            logger.exception("crawl failed")
            await wait.edit_text(f"❌ Crawl failed\n`{str(exc)[:700]}`")
            return

        if not items:
            await wait.edit_text("❌ No downloadable episode links found.")
            return

        series = self._series_name(items)
        saved_method = await get_method(items[0].get("url") or text)
        session = {
            "items": items,
            "page": 0,
            "selected": set(),
            "series": series,
            "source_url": text,
            "message_id": wait.id,
            "method": saved_method,
        }
        self.sessions[message.from_user.id] = session
        await self.render_selection(wait, session)

    def _series_name(self, items):
        title = str(items[0].get("title") or "Series")
        episode = str(items[0].get("episode") or "")
        resolution = str(items[0].get("resolution") or "")
        for part in (f" - {episode}", f" - {resolution}"):
            if part and part in title:
                title = title.split(part, 1)[0]
        return title.strip(" -") or "Series"

    def _button_label(self, item, index, selected):
        mark = "☑️" if index in selected else "⬜"
        episode = item.get("episode") or "Episode ?"
        resolution = item.get("resolution") or "Unknown"
        url = item.get("url") or ""
        from urllib.parse import urlparse
        site = urlparse(url).netloc or "Unknown"
        return f"{mark} {episode} • {resolution} • {site}"

    async def render_selection(self, message, session):
        items = session["items"]
        page = session["page"]
        total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        start = page * PAGE_SIZE
        end = min(len(items), start + PAGE_SIZE)
        selected = session["selected"]

        lines = [f"🎬 **{session['series']}**", f"📦 **{len(items)} Options**", ""]
        for i in range(start, end):
            item = items[i]
            lines.append(f"{i + 1}. {item.get('episode', 'Episode ?')} • {item.get('resolution', 'Unknown')}")
        lines += ["", f"📄 Page {page + 1}/{total_pages}", f"☑️ Selected: {len(selected)}/{len(items)}"]

        buttons = []
        for i in range(start, end):
            buttons.append([InlineKeyboardButton(
                self._button_label(items[i], i, selected),
                callback_data=f"v2:t:{i}",
            )])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Previous", callback_data="v2:p:-1"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data="v2:p:1"))
        if nav:
            buttons.append(nav)
        buttons.append([
            InlineKeyboardButton("☑️ Select All", callback_data="v2:all"),
            InlineKeyboardButton("❌ Clear", callback_data="v2:clear"),
        ])
        buttons.append([
            InlineKeyboardButton(
                f"⚙️ Method: {method_label(session.get('method', 'auto'))}",
                callback_data="v2:method",
            )
        ])
        buttons.append([InlineKeyboardButton("🚀 Download Selected", callback_data="v2:download")])
        buttons.append([InlineKeyboardButton("❌ Cancel Selection", callback_data="v2:close")])

        await message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def callback(self, client, query):
        if not callback_owner(query):
            await query.answer("Not allowed", show_alert=True)
            return
        session = self.sessions.get(query.from_user.id)
        data = query.data or ""
        if data == "v2:status":
            await query.answer()
            await self.send_status(query.message)
            return
        if not session:
            await query.answer("Selection expired. Send the URL again.", show_alert=True)
            return
        if data == "v2:method":
            session["method"] = next_method(session.get("method", "auto"))
            await query.answer(f"Method: {method_label(session['method'])}")
            await self.render_selection(query.message, session)
            return
        if data.startswith("v2:t:"):
            index = int(data.rsplit(":", 1)[1])
            if index in session["selected"]:
                session["selected"].remove(index)
            else:
                session["selected"].add(index)
            await query.answer()
            await self.render_selection(query.message, session)
            return
        if data == "v2:all":
            session["selected"] = set(range(len(session["items"])))
            await query.answer("All selected")
            await self.render_selection(query.message, session)
            return
        if data == "v2:clear":
            session["selected"].clear()
            await query.answer("Selection cleared")
            await self.render_selection(query.message, session)
            return
        if data.startswith("v2:p:"):
            delta = int(data.rsplit(":", 1)[1])
            total_pages = max(1, (len(session["items"]) + PAGE_SIZE - 1) // PAGE_SIZE)
            session["page"] = max(0, min(total_pages - 1, session["page"] + delta))
            await query.answer()
            await self.render_selection(query.message, session)
            return
        if data == "v2:close":
            self.sessions.pop(query.from_user.id, None)
            await query.answer("Selection closed")
            await query.message.edit_text("❌ Selection cancelled.")
            return
        if data == "v2:download":
            selected = sorted(session["selected"])
            if not selected:
                await query.answer("Select at least one item", show_alert=True)
                return
            await query.answer("Queued")
            await self.enqueue_selected(query.message, session, selected)
            return

    async def enqueue_selected(self, message, session, selected):
        if not self.pipeline:
            await message.edit_text("❌ V2 pipeline is offline.")
            return
        user_id = message.chat.id
        selected_method = session.get("method", "auto")
        status_lines = [
            f"🚀 **Queued {len(selected)} task(s)**",
            f"🎬 {session['series']}",
            f"⚙️ Method: {method_label(selected_method)}",
            "",
            "⬇️ Download limit: 2",
            "⬆️ Upload limit: 4",
            "",
            "Use /status for live queue status.",
        ]
        await message.edit_text("\n".join(status_lines), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Queue Status", callback_data="v2:status")],
        ]))

        for index in selected:
            item = session["items"][index]
            task_id = uuid.uuid4().hex
            filename = sanitize_filename(item.get("title") or f"{task_id}.mp4")
            if not Path(filename).suffix:
                filename += ".mp4"
            await set_method(item.get("url") or "", selected_method)
            metadata = {
                "source_url": item.get("source_url"),
                "provider": item.get("source"),
                "resolution": item.get("resolution"),
                "cookiefile": str(COOKIES_PATH) if COOKIES_PATH.is_file() else None,
                "download_method": selected_method,
            }
            metadata = {k: v for k, v in metadata.items() if v}
            payload = {
                "task_type": "download",
                "url": item["url"],
                "filename": filename,
                "chat_id": user_id,
                "caption": item.get("title") or session["series"],
                "thumbnail": str(THUMB_PATH) if THUMB_PATH.is_file() else None,
                "mode": "video",
                "title": item.get("title"),
                "provider": item.get("source"),
                "resolution": item.get("resolution"),
                "metadata": metadata,
            }
            try:
                await self.pipeline.submit(task_id, payload)
            except Exception:
                logger.exception("queue submit failed for %s", task_id)

        self.sessions.pop(message.chat.id, None)

    async def cancel_task(self, client, message):
        if not owner_only(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.reply_text("Usage: `/cancel TASK_ID`")
            return
        task_id = parts[1].strip()
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline.")
            return
        ok = await self.pipeline.cancel(task_id)
        await message.reply_text("🛑 Cancelled." if ok else "⚠️ Task is not currently active.")

    async def on_progress(self, kind, task_id, percent=None, current=None, total=None, speed=None):
        task_id = str(task_id)
        now = asyncio.get_running_loop().time()
        last = self.progress_cache.get(task_id)
        if last and now - last[0] < 2.0 and (percent is None or last[1] == percent):
            return
        self.progress_cache[task_id] = (now, percent)
        message = self.progress_messages.get(task_id)
        if not message:
            return
        p = "?" if percent is None else f"{percent:.1f}%"
        if speed:
            speed_text = self._human_size(speed) + "/s"
        else:
            speed_text = "—"
        try:
            await message.edit_text(f"{'⬇️' if kind == 'download' else '⬆️'} **{kind.title()}**\n\n`{p}`\n⚡ {speed_text}\n🆔 `{task_id}`")
        except Exception:
            pass

    async def on_complete(self, task_id):
        self.progress_messages.pop(str(task_id), None)
        self.progress_cache.pop(str(task_id), None)

    async def on_failed(self, task_id, error):
        self.progress_messages.pop(str(task_id), None)
        self.progress_cache.pop(str(task_id), None)
        logger.error("task %s failed: %s", task_id, error)

    @staticmethod
    def _human_size(value):
        value = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"


async def run():
    if not BOT_TOKEN or not API_ID or not API_HASH or not OWNER_ID:
        raise RuntimeError("API_ID, API_HASH, BOT_TOKEN and OWNER_ID must be configured")

    app = Client(
        "telegram_bot_v2",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir=str(ROOT / "data"),
    )
    bot = V2Bot(app)

    app.add_handler(MessageHandler(bot.start_cmd, filters.command("start")))
    app.add_handler(MessageHandler(bot.status_cmd, filters.command("status")))
    app.add_handler(MessageHandler(bot.cancel_task, filters.command("cancel")))
    app.add_handler(MessageHandler(bot.text_url, filters.text & ~filters.command(["start", "status", "cancel"])))
    app.add_handler(CallbackQueryHandler(bot.callback))

    async with app:
        await bot.start()
        try:
            await asyncio.Event().wait()
        finally:
            await bot.stop()


if __name__ == "__main__":
    asyncio.run(run())
