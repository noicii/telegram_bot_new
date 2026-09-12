# handlers.py
from pathlib import Path
import os
import re
import asyncio
import time
import logging
import psutil
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from config import DEFAULT_CHANNEL_ID, ADMIN_IDS, COOKIES_PATH, STATS, CAPTION_PATH, DOWNLOAD_DIR, SETTINGS, save_settings, save_stats, MAX_CONCURRENT_DOWNLOADS
from database import get_pending_tasks_db, get_pending_only_tasks_db, clear_pending_tasks_db, add_task_db, update_task_status_db, update_task_status_message_db, get_next_queued_task_db, get_queue_counts_db, get_task_db, reset_processing_tasks_db, cancel_pending_tasks_db, cancel_task_db, clear_summary_history_db, retry_failed_task_db, retry_all_failed_tasks_db, move_task_next_db, update_task_title_db, get_queue_tasks_db, cancel_tasks_by_status_message_db, clear_crawl_items_db, add_crawl_item_db, get_crawl_items_db, get_crawl_item_db, toggle_crawl_item_db, select_all_crawl_items_db, clear_selected_crawl_items_db, get_selected_crawl_items_db, get_batch_tasks_db, get_batch_summary_db, set_batch_status_message_db, get_batch_status_message_db
from utils import is_authorized, get_main_keyboard, extract_subtitles_from_video, split_large_file, generate_screenshots_collage, generate_sample_clip, generate_auto_thumbnail, async_get_video_metadata
from crawler import resolve_blog_links, crawl_blog_episodes, parse_input_lines
from downloader import download_single_item, DashboardTracker, CANCELLED_TASKS, LIVE_TASKS, set_live_task, clear_live_task

CRAWL_PAGE_SIZE = 8
logger = logging.getLogger(__name__)

MAX_CONCURRENT_UPLOADS = 4
UPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
UPLOAD_TASKS = {}

def register_upload_task(task_id, task):
    UPLOAD_TASKS[int(task_id)] = task

def get_upload_task(task_id):
    task = UPLOAD_TASKS.get(int(task_id))
    return task if task and not task.done() else None

def cancel_upload_task(task_id):
    task = get_upload_task(task_id)
    if task:
        task.cancel()
        return True
    return False

def cancel_all_upload_tasks():
    tasks = list(UPLOAD_TASKS.values())
    for task in tasks:
        if task and not task.done():
            task.cancel()
    return len(tasks)


def build_batch_summary_text(batch_id):
    tasks = get_batch_tasks_db(batch_id) or []
    downloading = 0
    uploading = 0
    pending = 0
    completed = 0
    failed = 0

    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status == "pending":
            pending += 1
        elif status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        elif status == "processing":
            live = LIVE_TASKS.get(str(task.get("id")), {})
            if "upload" in str(live.get("operation") or "").lower():
                uploading += 1
            else:
                downloading += 1

    return "\n".join([
        "📊 **QUEUE STATUS**",
        "",
        f"⬇️ Downloads: {downloading}/2",
        f"⬆️ Uploads: {uploading}/4",
        "",
        f"⏳ Queue: {pending}",
        f"✅ Done: {completed}",
        f"❌ Failed: {failed}",
    ])


def _crawl_series_name(items):
    for item in items:
        raw = str(item.get("title") or "").strip()
        if raw:
            parts = raw.split(" - ")
            if len(parts) > 1:
                return parts[0].strip()
            return raw
    return "Crawled Series"


def build_crawl_keyboard(items, page=1):
    total_pages = max(1, (len(items) + CRAWL_PAGE_SIZE - 1) // CRAWL_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * CRAWL_PAGE_SIZE
    buttons = []

    for item in items[start:start + CRAWL_PAGE_SIZE]:
        icon = "✅" if item.get("selected") else "⬜"
        episode = str(item.get("episode") or "Episode ?").strip()
        source = str(item.get("source") or "Unknown").strip()
        resolution = str(item.get("resolution") or "").strip()
        if resolution and resolution.lower() not in ("unknown", "original", "none", "auto"):
            if not resolution.lower().endswith("p"):
                resolution += "p"
            label = f"{episode} • {resolution} • {source}"
        else:
            label = f"{episode} • {source}"
        buttons.append([InlineKeyboardButton(
            f"{icon} {label}",
            callback_data=f"crawl_toggle_{item['id']}_{page}",
        )])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"crawl_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="crawl_noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"crawl_page_{page + 1}"))
    buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("☑️ Select All", callback_data=f"crawl_select_all_{page}"),
        InlineKeyboardButton("❌ Clear", callback_data=f"crawl_clear_all_{page}"),
    ])
    buttons.append([InlineKeyboardButton("🚀 Download Selected", callback_data="crawl_download_selected")])
    return InlineKeyboardMarkup(buttons)

async def enqueue_items(items, status_message, client, is_audio=False, audio_bitrate="192", preset=None):
    if not items:
        return []

    if preset is None:
        preset = SETTINGS.get("download_preset", "custom")

    status_chat_id = status_message.chat.id
    task_ids = []

    # One batch represents one user submission containing multiple videos.
    batch_total = len(items)
    batch_id = (
        f"batch_{status_chat_id}_{status_message.id}_{time.time_ns()}"
        if batch_total > 1
        else None
    )

    for item in items:
        if isinstance(item, dict):
            url = item.get("url", "")
            chat_id = item.get("chat_id", DEFAULT_CHANNEL_ID)
            custom_name = item.get("custom_name", "")
            title = item.get("title", "")
        else:
            url = item[0] if len(item) > 0 else ""
            chat_id = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID
            custom_name = item[2] if len(item) > 2 else ""
            title = item[3] if len(item) > 3 else ""

        if not url:
            continue

        task_id = add_task_db(
            url,
            chat_id,
            custom_name,
            status_chat_id=status_chat_id,
            status_message_id=None,
            preset=preset,
            title=title or None,
            batch_id=batch_id,
            batch_total=batch_total,
        )
        task_ids.append(task_id)

    # Create the shared summary only after all batch tasks exist.
    if batch_id and task_ids:
        summary_message = await status_message.reply_text(
            build_batch_summary_text(batch_id)
        )
        set_batch_status_message_db(
            batch_id,
            summary_message.chat.id,
            summary_message.id,
        )

    return task_ids
def get_disk_cleanup_files():
    """Return downloadable media/temp files inside downloads/."""
    files = []
    if not os.path.isdir(DOWNLOAD_DIR):
        return files

    for root, _dirs, filenames in os.walk(DOWNLOAD_DIR):
        for filename in filenames:
            path = os.path.join(root, filename)
            ext = os.path.splitext(filename)[1].lower()
            is_generated_thumb = filename.startswith("t_") and ext in {".jpg", ".jpeg", ".png"}
            if ext in DISK_CLEAN_VIDEO_EXTENSIONS or ext in DISK_CLEAN_TEMP_EXTENSIONS or is_generated_thumb:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                files.append((path, size, ext in DISK_CLEAN_TEMP_EXTENSIONS))
    return files

def format_bytes(size):
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024


def _display_task_name(task):
    return (
        (task.get("custom_name") or "").strip()
        or (task.get("title") or "").strip()
        or "Unknown video"
    )


def _task_source(task):
    try:
        from urllib.parse import urlparse
        host = urlparse(task.get("url") or "").netloc.lower()
        return host.replace("www.", "") or "Unknown"
    except Exception:
        return "Unknown"


def _active_task_line(task, number=1):
    task_id = str(task.get("id"))
    live = LIVE_TASKS.get(task_id, {})
    title = live.get("title") or _display_task_name(task)
    pct = live.get("percent")
    if pct is None:
        progress = "Starting..."
    else:
        pct = float(pct)
        fill = max(0, min(10, int(pct // 10)))
        progress = f"{('▰' * fill) + ('▱' * (10 - fill))} {pct:.1f}%"
    current = live.get("current_mb")
    total = live.get("total_mb")
    size = f"{current:.1f} MB" if current is not None else "Calculating..."
    if total:
        size += f" / {total:.1f} MB"
    speed = live.get("speed_mb")
    speed_text = f"{speed:.2f} MB/s" if speed is not None else "—"
    eta = live.get("eta") or "—"
    operation = live.get("operation") or "Downloading"
    return (
        f"{number}️⃣ **#{task_id} — {title[:45]}**\n"
        f"   📺 {SETTINGS.get('target_resolution', 'original')} • MP4 • {_task_source(task)}\n"
        f"   📊 `{progress}`\n"
        f"   💾 {size} • ⚡ {speed_text} • ⏳ ETA {eta}\n"
        f"   🔧 {operation}"
    )


def build_status_text():
    counts = get_queue_counts_db()
    tasks = get_queue_tasks_db()
    processing_tasks = [task for task in tasks if task.get("status") == "processing"]
    cpu = psutil.cpu_percent(interval=0.2)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(DOWNLOAD_DIR))
    memory_used_gb = memory.used / (1024 ** 3)
    memory_total_gb = memory.total / (1024 ** 3)
    disk_used_gb = disk.used / (1024 ** 3)
    disk_total_gb = disk.total / (1024 ** 3)
    disk_free_gb = disk.free / (1024 ** 3)

    lines = [
        "🤖 **BOT STATUS**",
        "",
        "🟢 **Bot:** Online / Running",
        f"🖥️ **CPU:** {cpu:.1f}%",
        f"🧠 **RAM:** {memory_used_gb:.2f} / {memory_total_gb:.2f} GB ({memory.percent:.1f}%)",
        f"💽 **Disk:** {disk_used_gb:.2f} / {disk_total_gb:.2f} GB",
        f"🟢 **Free Disk:** {disk_free_gb:.2f} GB",
        "",
        "📊 **QUEUE SUMMARY**",
        f"⏳ Pending: **{counts.get('pending', 0)}**",
        f"🚀 Processing: **{counts.get('processing', 0)}/{MAX_CONCURRENT_DOWNLOADS if 'MAX_CONCURRENT_DOWNLOADS' in globals() else 2}**",
        f"✅ Completed: **{counts.get('completed', 0)}**",
        f"❌ Failed: **{counts.get('failed', 0)}**",
        f"🚫 Cancelled: **{counts.get('cancelled', 0)}**",
        "",
        "🚀 **PROCESSING / ACTIVE**",
    ]
    if processing_tasks:
        for number, task in enumerate(processing_tasks, 1):
            lines.append(_active_task_line(task, number))
            lines.append("")
    else:
        lines.append("— હાલ કોઈ video processingમાં નથી.")
    lines.extend([
        f"⏳ **PENDING QUEUE: {counts.get('pending', 0)} video(s)**",
        "📌 Full pending list માટે `/pending` ચલાવો.",
    ])
    return "\n".join(lines)


def build_pending_text():
    tasks = get_pending_only_tasks_db()
    if not tasks:
        return "⏳ **PENDING QUEUE**\n\n✅ હાલ કોઈ video pending નથી."
    lines = [
        "⏳ **PENDING QUEUE**",
        "",
        f"📦 Total pending: **{len(tasks)}**",
        "",
    ]
    for n, task in enumerate(tasks, 1):
        lines.extend([
            f"**{n}. #{task.get('id')} — {_display_task_name(task)[:55]}**",
            f"   🌐 {_task_source(task)} • 📺 {SETTINGS.get('target_resolution', 'original')} • MP4",
            "",
        ])
    return "\n".join(lines).rstrip()

def register_handlers(app):
    @app.on_message(filters.command("setcookie") & filters.private)
    async def _cmd_set_cookie(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return
        if not m.reply_to_message or not m.reply_to_message.document:
            return await m.reply_text("⚠️ કૃપા કરીને તમારા `cookies.txt` ફાઇલને reply કરીને `/setcookie` લખો.")
        await m.reply_to_message.download(file_name=COOKIES_PATH)
        await m.reply_text("✅ **કૂકીઝ ફાઇલ સફળતાપૂર્વક અપડેટ થઈ ગઈ છે!**")

    @app.on_message(filters.command("stats"))
    async def _stats_cmd(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return
        tot_gb = STATS["total_downloaded_mb"] / 1024
        txt = (
            "📊 **બોટ વપરાશ એનાલિટિક્સ (Statistics)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **કુલ સફળ ડાઉનલોડ્સ:** `{STATS['total_downloaded_items']}`\n"
            f"❌ **કુલ ફેલ લિંક્સ:** `{STATS['failed_items']}`\n"
            f"💾 **પ્રોસેસ થયેલ કુલ ડેટા:** `{tot_gb:.2f} GB` (`{STATS['total_downloaded_mb']:.1f} MB`)\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await m.reply_text(txt)

    @app.on_message(filters.command(["start", "help"]))
    async def _start(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Bot Restart", callback_data="confirm_restart")]])
        await m.reply_text(
            "🤖 **Bot Control**\n\n"
            "બોટ restart કરવા માટે નીચેનું button દબાવો.\n"
            "⚠️ Restart આપમેળે નહીં થાય.",
            reply_markup=kb,
        )

    @app.on_message(filters.command("status"))
    async def _status_cmd(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return

        try:
            await m.reply_text(
                build_status_text(),
                reply_markup=get_main_keyboard(),
            )
        except Exception as exc:
            await m.reply_text(f"❌ Status error: {exc}")

    @app.on_message(filters.command("pending"))
    async def _pending_cmd(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return

        tasks = get_pending_only_tasks_db()
        if not tasks:
            await m.reply_text(
                "⏳  **PENDING QUEUE**\n\n"
                "✅  હાલ કોઈ video pending નથી."
            )
            return

        buttons = []
        for task in tasks[:30]:
            task_id = task.get("id")
            name = _display_task_name(task)

            buttons.append([
                InlineKeyboardButton(
                    f"⏫  #{task_id} • {name[:38]}",
                    callback_data=f"move_task_next_{task_id}",
                )
            ])

        await m.reply_text(
            build_pending_text()
            + "\n\n👇 Queue માં આગળ મોકલવા માટે task પસંદ કરો:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    @app.on_message(filters.command("diskclean"))
    async def _diskclean_cmd(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return

        counts = get_queue_counts_db()
        pending = int(counts.get("pending", 0))
        processing = int(counts.get("processing", 0))
        if pending or processing:
            await m.reply_text(
                "⚠️ **Disk Cleanup અટકાવ્યું**\n\n"
                f"📥 Pending: {pending}\n"
                f"⚙️ Processing: {processing}\n\n"
                "હાલ queue active છે, એટલે કોઈ file delete નહીં કરું.\n"
                "બધા videos send થયા પછી ફરી `/diskclean` ચલાવો."
            )
            return

        files = get_disk_cleanup_files()
        if not files:
            await m.reply_text(
                "🧹 **Disk Cleanup**\n\n"
                "✅ Downloads folder માં delete કરવા માટે કોઈ video અથવા temporary file નથી."
            )
            return

        video_count = sum(1 for _path, _size, is_temp in files if not is_temp)
        temp_count = sum(1 for _path, _size, is_temp in files if is_temp)
        total_size = sum(size for _path, size, _is_temp in files)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Clean Disk", callback_data="diskclean_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="diskclean_cancel"),
            ]
        ])
        await m.reply_text(
            "🧹 **Disk Cleanup**\n\n"
            f"🎬 Video files: {video_count}\n"
            f"🗑️ Temporary files: {temp_count}\n"
            f"💾 Space to free: {format_bytes(total_size)}\n\n"
            "🟢 Queue હાલમાં idle છે.\n"
            "⚠️ Confirm કર્યા પછી downloads/ માંની આ files delete થશે.",
            reply_markup=kb,
        )

    @app.on_callback_query(filters.regex("^resume_old_tasks$"))
    async def _cb_resume(c, q):
        pending = get_pending_tasks_db()
        if not pending:
            return await q.answer("કોઈ જૂના ટાસ્ક બાકી નથી!", show_alert=True)
        
        task_ids = [t.get("id") for t in pending]
        items = [
            {
                "url": t.get("url", ""),
                "chat_id": t.get("chat_id", DEFAULT_CHANNEL_ID),
                "custom_name": t.get("custom_name") or "",
            }
            for t in pending
        ]
        await q.answer(f"જૂના {len(items)} ટાસ્ક ફરી શરૂ થયા છે!", show_alert=True)
        s_msg = await q.message.edit_text(f"⚡ **જૂના {len(items)} ટાસ્કનું ડાઉનલોડિંગ ફરી શરૂ થાય છે...**")
        
        from queue_worker import start_queue_worker

        for task in pending:
            task_id = task.get("id")
            if task_id:
                update_task_status_db(task_id, "pending")
                update_task_status_message_db(
                    task_id,
                    s_msg.chat.id,
                    None,
                )

        await s_msg.edit_text(
            f"⚡ **જૂના {len(items)} ટાસ્ક Queue માં પાછા મૂકાયા છે.**\n\n"
            "⏳ FIFO Queue મુજબ processing ફરી શરૂ થશે...",
        )

        start_queue_worker(c)

    @app.on_callback_query(filters.regex("^clear_old_tasks$"))
    async def _cb_clear_queue(c, q):
        clear_pending_tasks_db()
        await q.answer("ક્યૂ સાફ થઈ ગઈ!", show_alert=True)
        await q.message.edit_text(
            "🗑 **જૂના ટાસ્ક રદ કરવામાં આવ્યા છે.**\n"
            "તમે હવે નવી લિંક્સ મોકલી શકો છો! 🚀",
            reply_markup=get_main_keyboard()
        )

    @app.on_message(filters.text & filters.private & ~filters.regex(r"^/"))
    async def _tx(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS) or m.text.startswith("/"):
            return
        items = parse_input_lines(m.text.splitlines())
        if not items:
            return await m.reply_text("કોઈ માન્ય લિંક નથી.")

        s = await m.reply_text(
            f"{len(items)} લિંક્સ મળી. Queue માં ઉમેરાઈ રહી છે...",
        )

        task_ids = await enqueue_items(items, s, c)

        await s.edit_text(
            f"📥 **{len(task_ids)} ટાસ્ક Queue માં ઉમેરાયા.**\n\n"
            "⏳ FIFO Queue મુજબ processing શરૂ થશે..."
        )

    @app.on_message(filters.command("crawl") & filters.private)
    async def _cmd_crawl(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return
        urls_found = re.findall(r"https?://[^\s<>]+", m.text or "")
        if not urls_found and m.reply_to_message and m.reply_to_message.text:
            urls_found = re.findall(r"https?://[^\s<>]+", m.reply_to_message.text)
        if not urls_found:
            return await m.reply_text("⚠️ કૃપા કરીને `/crawl <બ્લોગ_લિંક>` આ રીતે લિંક મોકલો.")

        blog_url = urls_found[0].strip("[]()<>\"\x27")
        status_msg = await m.reply_text("🔍 Crawling page... ⏳")
        episodes = crawl_blog_episodes(blog_url)
        if not episodes:
            return await status_msg.edit_text("❌ આ બ્લોગમાંથી કોઈ Episode option મળ્યો નથી.")

        clear_crawl_items_db(m.from_user.id)
        for episode in episodes:
            add_crawl_item_db(
                m.from_user.id,
                episode["title"],
                episode["episode"],
                episode["url"],
                episode.get("source", "Unknown"),
                episode.get("resolution", "Unknown"),
                episode.get("source_url", episode["url"]),
            )
        items = get_crawl_items_db(m.from_user.id)
        await status_msg.edit_text(
            f"🎬 **{_crawl_series_name(items)}**\n"
            f"📦 **{len(items)} Options**",
            reply_markup=build_crawl_keyboard(items, 1),
        )

    @app.on_callback_query(filters.regex(r"^crawl_.*"))
    async def _handle_crawl_callback(client, callback_query):
        if not is_authorized(callback_query.from_user.id, ADMIN_IDS):
            return await callback_query.answer("❌ Unauthorized", show_alert=True)
        data = callback_query.data
        chat_id = callback_query.message.chat.id
        items = get_crawl_items_db(chat_id)
        if not items:
            return await callback_query.answer("❌ Crawl options expired.", show_alert=True)

        total_pages = max(1, (len(items) + CRAWL_PAGE_SIZE - 1) // CRAWL_PAGE_SIZE)
        if data == "crawl_noop":
            return await callback_query.answer("📂 Episode options નીચે છે.")

        if data.startswith("crawl_page_"):
            try:
                page = int(data.rsplit("_", 1)[1])
            except ValueError:
                page = 1
            page = max(1, min(page, total_pages))
            await callback_query.answer()
            await callback_query.message.edit_reply_markup(build_crawl_keyboard(items, page))
            return

        if data.startswith("crawl_select_all_"):
            try:
                page = int(data.rsplit("_", 1)[1])
            except ValueError:
                page = 1
            page = max(1, min(page, total_pages))
            select_all_crawl_items_db(chat_id)
            items = get_crawl_items_db(chat_id)
            await callback_query.answer("☑️ All options selected.")
            await callback_query.message.edit_reply_markup(build_crawl_keyboard(items, page))
            return

        if data.startswith("crawl_clear_all_"):
            try:
                page = int(data.rsplit("_", 1)[1])
            except ValueError:
                page = 1
            page = max(1, min(page, total_pages))
            clear_selected_crawl_items_db(chat_id)
            items = get_crawl_items_db(chat_id)
            await callback_query.answer("❌ Selection cleared.")
            await callback_query.message.edit_reply_markup(build_crawl_keyboard(items, page))
            return

        if data == "crawl_download_selected":
            selected_items = get_selected_crawl_items_db(chat_id)
            if not selected_items:
                return await callback_query.answer("⚠️ પહેલા ઓછામાં ઓછું એક option select કરો.", show_alert=True)
            await callback_query.answer(f"🚀 {len(selected_items)} options queue થઈ રહ્યા છે...")
            status_msg = callback_query.message
            queue_items = [{
                "url": item["url"],
                "chat_id": chat_id,
                "custom_name": item["title"],
                "source": item.get("source", "Unknown"),
                "resolution": item.get("resolution", "Unknown"),
                "source_url": item.get("source_url", item["url"]),
            } for item in selected_items]
            try:
                await status_msg.edit_text(f"🚀 **{len(selected_items)} options selected**\n\n⏳ Adding to queue...")
                task_ids = await enqueue_items(queue_items, status_msg, client)
                await status_msg.edit_text(f"📥 **{len(task_ids)} tasks added to queue**\n\n⏳ Processing will start automatically.")
            except Exception:
                logger.exception("Failed to queue crawl selections")
                await status_msg.edit_text("❌ **Could not add selected options to queue.**")
            return

        if data.startswith("crawl_toggle_"):
            parts = data.split("_")
            try:
                item_id = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 1
            except (ValueError, IndexError):
                return await callback_query.answer("❌ Invalid option.", show_alert=True)
            item = get_crawl_item_db(item_id, chat_id)
            if not item:
                return await callback_query.answer("❌ Option not found.", show_alert=True)
            toggle_crawl_item_db(item_id, chat_id)
            items = get_crawl_items_db(chat_id)
            total_pages = max(1, (len(items) + CRAWL_PAGE_SIZE - 1) // CRAWL_PAGE_SIZE)
            page = max(1, min(page, total_pages))
            selected = sum(1 for current_item in items if current_item.get("selected"))
            await callback_query.answer(f"Selected: {selected}/{len(items)}")
            await callback_query.message.edit_reply_markup(build_crawl_keyboard(items, page))
            return

    @app.on_callback_query(
        filters.regex(
            r"^(toggle_|cycle_|set_|status|btn_status|clear_cache|btn_clearcache|cancel|confirm_restart|restart_bot|restart_cancel|diskclean_confirm|diskclean_cancel|clear_summary_history|clear_summary_confirm|clear_summary_cancel|retry_failed|retry_all|retry_task_\d+|retry_failed_close|cancel_task_\d+|move_task_next_\d+|btn_pending).*"
        )
    )
    async def _handle_all_settings_callbacks(client, callback_query):
        data = callback_query.data
        user_id = callback_query.from_user.id

        if not is_authorized(user_id, ADMIN_IDS):
            await callback_query.answer(
                "⚠️ You are not authorized!",
                show_alert=True,
            )
            return

        if data == "diskclean_confirm":
            counts = get_queue_counts_db()
            pending = int(counts.get("pending", 0))
            processing = int(counts.get("processing", 0))
            if pending or processing:
                await callback_query.answer(
                    "⚠️ Queue active છે. Cleanup કરવામાં આવ્યું નથી.",
                    show_alert=True,
                )
                return

            files = get_disk_cleanup_files()
            deleted = 0
            failed = 0
            freed = 0
            for path, size, _is_temp in files:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted += 1
                        freed += size
                except OSError:
                    failed += 1

            await callback_query.answer("🧹 Disk cleanup complete!", show_alert=True)
            await callback_query.message.edit_text(
                "✅ **Disk Cleanup Complete**\n\n"
                f"🎬/🗑️ Files deleted: {deleted}\n"
                f"💾 Space freed: {format_bytes(freed)}\n"
                f"⚠️ Could not delete: {failed}"
            )
            return

        if data == "diskclean_cancel":
            await callback_query.answer("Cleanup cancel થયું.")
            await callback_query.message.edit_text(
                "🧹 **Disk Cleanup**\n\n"
                "❌ Cleanup cancel થયું. કોઈ file delete થઈ નથી."
            )
            return

        if data == "restart_bot":
            await callback_query.answer("🔄 Bot restart થઈ રહ્યો છે...", show_alert=True)
            await callback_query.message.edit_text(
                "🔄 **Bot Restart થઈ રહ્યો છે...**\n\n"
                "થોડી ક્ષણમાં bot પાછો online થશે."
            )
            await asyncio.sleep(1)
            os._exit(0)

        if data == "btn_pending":
            await callback_query.answer()
            await callback_query.message.edit_text(
                build_pending_text(),
                reply_markup=get_main_keyboard(),
            )
            return

        # Status
        if data == "restart_cancel":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Bot Restart", callback_data="confirm_restart")]
            ])
            await callback_query.answer("Restart cancel થયું.")
            await callback_query.message.edit_text(
                "🤖 **Bot Control**\n\nRestart cancel થયું.",
                reply_markup=kb,
            )
            return
        if data == "confirm_restart":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ હા, Restart", callback_data="restart_bot"),
                 InlineKeyboardButton("❌ Cancel", callback_data="restart_cancel")]
            ])
            await callback_query.answer()
            await callback_query.message.edit_text(
                "⚠️ **Bot Restart કરવો છે?**",
                reply_markup=kb
            )
            return
        if data in ("status", "btn_status"):
            try:
                await callback_query.answer()
                await callback_query.message.edit_text(
                    build_status_text(),
                    reply_markup=get_main_keyboard(),
                )
            except Exception as exc:
                await callback_query.answer(
                    f"❌ Status error: {exc}",
                    show_alert=True,
                )
            return
        # Clear cache
        if data in ("clear_cache", "btn_clearcache"):
            await callback_query.answer(
                "🧹 Cache cleared successfully!",
                show_alert=True,
            )
            return

        # Audio track cycle
        if data == "toggle_audiotrack":
            modes = ["all", "first", "none"]
            current = SETTINGS.get("audio_track_mode", "all")

            try:
                index = modes.index(current)
            except ValueError:
                index = 0

            SETTINGS["audio_track_mode"] = modes[(index + 1) % len(modes)]
            save_settings()

            await callback_query.answer(
                f"🎵 Audio Track: {SETTINGS['audio_track_mode']}",
                show_alert=False,
            )

            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        # Download preset cycle
        if data == "cycle_preset":
            presets = ["custom", "mobile", "720p", "1080p", "audio"]
            current = str(SETTINGS.get("download_preset", "custom")).lower()
            try:
                index = presets.index(current)
            except ValueError:
                index = 0
            SETTINGS["download_preset"] = presets[(index + 1) % len(presets)]
            save_settings()
            await callback_query.answer(
                f"🎚 Preset: {SETTINGS['download_preset']}",
                show_alert=False,
            )
            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        # Resolution cycle
        if data == "toggle_queue_pause":
            SETTINGS["queue_paused"] = not bool(
                SETTINGS.get("queue_paused", False)
            )
            save_settings()

            if SETTINGS["queue_paused"]:
                message = "⏸ Queue paused\n\nRunning downloads continue, but new pending tasks will wait."
            else:
                message = "▶️ Queue resumed\n\nPending tasks will start automatically."

            await callback_query.answer(
                message,
                show_alert=True,
            )
            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        if data == "cycle_resolution":
            resolutions = ["original", "1080p", "720p", "480p"]
            current = SETTINGS.get("target_resolution", "original")

            try:
                index = resolutions.index(current)
            except ValueError:
                index = 0

            SETTINGS["target_resolution"] = resolutions[
                (index + 1) % len(resolutions)
            ]
            save_settings()

            await callback_query.answer(
                f"📺 Resolution: {SETTINGS['target_resolution']}",
                show_alert=False,
            )

            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        # Speed cycle
        if data == "cycle_speed":
            speeds = ["0", "1M", "2M", "4M", "8M"]
            current = str(SETTINGS.get("speed_limit", "0"))

            try:
                index = speeds.index(current)
            except ValueError:
                index = 0

            SETTINGS["speed_limit"] = speeds[(index + 1) % len(speeds)]
            save_settings()

            await callback_query.answer(
                f"⚡ Speed Limit: {SETTINGS['speed_limit']}",
                show_alert=False,
            )

            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        # Boolean settings
        if data.startswith("toggle_"):
            setting_map = {
                "auto_delete": "auto_delete",
                "compress": "compress",
                "sample": "sample_video",
                "screenshots": "screenshots",
                "subtitles": "extract_subtitles",
                "rename": "custom_rename",
            }

            setting_name = data[len("toggle_"):]

            if setting_name not in setting_map:
                await callback_query.answer(
                    f"⚙️ Unknown setting: {setting_name}",
                    show_alert=True,
                )
                return

            config_key = setting_map[setting_name]
            SETTINGS[config_key] = not bool(SETTINGS.get(config_key, False))
            save_settings()

            state = "ON" if SETTINGS[config_key] else "OFF"

            await callback_query.answer(
                f"✅ {setting_name.replace('_', ' ').title()}: {state}",
                show_alert=False,
            )

            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        if data.startswith("move_task_next_"):
            try:
                task_id = int(data.rsplit("_", 1)[1])
            except ValueError:
                await callback_query.answer(
                    "❌ Invalid task ID",
                    show_alert=True,
                )
                return

            task = get_task_db(task_id)
            if not task:
                await callback_query.answer(
                    "ℹ️ Task not found.",
                    show_alert=True,
                )
                return

            if task.get("status") != "pending":
                await callback_query.answer(
                    "ℹ️ Only pending tasks can be moved to next.",
                    show_alert=True,
                )
                return

            if not move_task_next_db(task_id):
                await callback_query.answer(
                    "⚠️ Could not move task to next.",
                    show_alert=True,
                )
                return

            await callback_query.answer(
                f"⏫ Task #{task_id} moved to next.",
                show_alert=True,
            )

            try:
                await callback_query.message.edit_reply_markup(
                    reply_markup=get_main_keyboard()
                )
            except Exception:
                pass
            return

        if data.startswith("cancel_task_"):
            try:
                task_id = int(data.rsplit("_", 1)[1])
            except ValueError:
                return await callback_query.answer("❌ Invalid task ID", show_alert=True)

            task = get_task_db(task_id)
            if not task or task.get("status") not in ("pending", "processing"):
                await callback_query.answer("ℹ️ આ task હવે active નથી.", show_alert=True)
                return

            if not cancel_task_db(task_id):
                await callback_query.answer("ℹ️ Task પહેલેથી પૂર્ણ/રદ થઈ ગઈ છે.", show_alert=True)
                return

            CANCELLED_TASKS.add(str(task_id))
            clear_live_task(task_id)

            await update_batch_summary_message(client, task)

            await callback_query.answer(f"🛑 Task #{task_id} cancelled.", show_alert=True)
            try:
                await callback_query.message.edit_text(
                    f"🚫 **Task #{task_id} Cancelled**\n\n"
                    f"🎬 `{_display_task_name(task)[:60]}`"
                )
            except Exception:
                pass
            return

        if data.startswith("retry_task_"):
            try:
                task_id = int(data.split("_")[-1])
            except (TypeError, ValueError):
                await callback_query.answer(
                    "❌ Invalid task ID",
                    show_alert=True,
                )
                return

            new_id = retry_failed_task_db(task_id)

            if new_id:
                await callback_query.answer(
                    f"🔄 Task #{task_id} added as #{new_id}",
                    show_alert=True,
                )
                return

            await callback_query.answer(
                "⚠️ Task is no longer failed/cancelled",
                show_alert=True,
            )
            return

        if data == "retry_failed_close":
            await callback_query.answer()
            await callback_query.message.edit_reply_markup(
                reply_markup=get_main_keyboard()
            )
            return

        if data == "retry_failed":
            new_ids = retry_all_failed_tasks_db()

            if new_ids:
                await callback_query.answer(
                    f"🔄 {len(new_ids)} task(s) added to queue",
                    show_alert=True,
                )
            else:
                await callback_query.answer(
                    "ℹ️ No failed/cancelled tasks to retry",
                    show_alert=True,
                )

            return

        if data == "retry_all":
            new_ids = retry_all_failed_tasks_db()

            if new_ids:
                await callback_query.answer(
                    f"🔄 Retrying {len(new_ids)} task(s)",
                    show_alert=True,
                )
            else:
                await callback_query.answer(
                    "ℹ️ No failed/cancelled tasks to retry",
                    show_alert=True,
                )

            return

        if data == "clear_summary_history":
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ હા, Clear History", callback_data="clear_summary_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="clear_summary_cancel"),
            ]])
            await callback_query.answer()
            await callback_query.message.edit_text(
                "⚠️ **Clear Queue Summary History?**\n\n"
                "Completed / Failed / Cancelled records delete થશે.\n"
                "⏳ Pending અને 🚀 Processing tasks safe રહેશે.",
                reply_markup=kb,
            )
            return

        if data == "clear_summary_confirm":
            deleted = clear_summary_history_db()
            await callback_query.answer(f"🗑 {deleted} history record(s) cleared.", show_alert=True)
            await callback_query.message.edit_text(
                build_status_text(),
                reply_markup=get_main_keyboard(),
            )
            return

        if data == "clear_summary_cancel":
            await callback_query.answer("Clear Summary cancel થયું.")
            await callback_query.message.edit_text(
                build_status_text(),
                reply_markup=get_main_keyboard(),
            )
            return

        # Unknown callback
        await callback_query.answer(
            f"⚙️ Action received: {data}",
            show_alert=False,
        )

async def finalize_queue_file(client, status_message, file_path, title, chat_id, task_id):
    from config import CUSTOM_THUMB_PATH, CAPTION_PATH, STATS, save_stats, MAX_RETRIES

    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError("Downloaded file not found")

    split_files = split_large_file(file_path)
    if not split_files:
        raise FileNotFoundError("No output file after splitting")

    for p_idx, part_path in enumerate(split_files, 1):
        part_label = f"{title} (Part {p_idx})" if len(split_files) > 1 else title
        tracker = DashboardTracker(
            status_message,
            1,
            1,
            part_label,
            time.time(),
            task_id=task_id,
        )

        dur, w, h = await async_get_video_metadata(part_path)

        try:
            dur = int(round(float(dur or 0)))
        except (TypeError, ValueError):
            dur = 0

        try:
            w = int(round(float(w or 0)))
        except (TypeError, ValueError):
            w = 0

        try:
            h = int(round(float(h or 0)))
        except (TypeError, ValueError):
            h = 0

        th = CUSTOM_THUMB_PATH if os.path.exists(CUSTOM_THUMB_PATH) else await generate_auto_thumbnail(
            part_path,
            os.path.join(DOWNLOAD_DIR, f"t_{task_id}_{p_idx}.jpg"),
        )

        if th and (not os.path.exists(th) or os.path.getsize(th) == 0):
            th = None

        primary_upload_chat = DEFAULT_CHANNEL_ID
        all_channels = [primary_upload_chat] + [
            ec for ec in SETTINGS.get("extra_channels", [])
            if ec != primary_upload_chat
        ]

        p_sz = os.path.getsize(part_path) / 1048576
        dur_m, dur_s = divmod(int(dur or 0), 60)
        dur_h, dur_m = divmod(dur_m, 60)
        dur_str = (
            f"{dur_h:02d}:{dur_m:02d}:{dur_s:02d}"
            if dur_h > 0
            else f"{dur_m:02d}:{dur_s:02d}"
        )

        if os.path.exists(CAPTION_PATH):
            try:
                with open(CAPTION_PATH, "r", encoding="utf-8") as cf:
                    caption_template = cf.read().strip()
            except Exception:
                caption_template = ""
        else:
            caption_template = ""

        if caption_template:
            c_cap = (
                caption_template
                .replace("{title}", part_label)
                .replace("{size}", f"{p_sz:.2f} MB")
                .replace("{duration}", dur_str)
            )
        else:
            c_cap = f"🎬 **{part_label}**\n⏱ `{dur_str}` | 💾 `{p_sz:.2f} MB`"

        for target_chat in all_channels:
            v_kwargs = {
                "chat_id": target_chat,
                "video": part_path,
                "file_name": f"{part_label}.mp4",
                "caption": c_cap,
                "duration": dur,
                "width": w,
                "height": h,
                "supports_streaming": True,
                "progress": tracker.callback if target_chat == primary_upload_chat else None,
            }

            if th and os.path.exists(th):
                v_kwargs["thumb"] = th

            upload_ok = False
            upload_error = None

            for upload_attempt in range(1, MAX_RETRIES + 1):
                try:
                    async with UPLOAD_SEMAPHORE:
                        await client.send_video(**v_kwargs)
                    upload_ok = True
                    break
                except FloodWait as fw:
                    wait_seconds = int(fw.value) + 1
                    print(
                        f"⚠️ Task {task_id} upload FloodWait on channel "
                        f"{target_chat}; waiting {wait_seconds}s "
                        f"(attempt {upload_attempt}/{MAX_RETRIES})",
                        flush=True,
                    )
                    await asyncio.sleep(wait_seconds)
                except Exception as exc:
                    upload_error = exc
                    print(
                        f"⚠️ Task {task_id} upload error on channel "
                        f"{target_chat}: {exc} "
                        f"(attempt {upload_attempt}/{MAX_RETRIES})",
                        flush=True,
                    )
                    if upload_attempt < MAX_RETRIES:
                        await asyncio.sleep(2 ** (upload_attempt - 1))

            if not upload_ok:
                raise RuntimeError(
                    f"Upload failed for task {task_id} "
                    f"to channel {target_chat}: {upload_error}"
                )

        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass

        # Remove only thumbnails generated for this upload. Never remove the
        # user's configured/custom thumbnail from assets/.
        if th and os.path.abspath(th) != os.path.abspath(CUSTOM_THUMB_PATH):
            try:
                if os.path.exists(th):
                    os.remove(th)
            except OSError:
                pass

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass

    return True


async def update_batch_summary_message(client, task):
    batch_id = task.get("batch_id")
    if not batch_id:
        return

    try:
        text = build_batch_summary_text(batch_id)
        message_info = get_batch_status_message_db(batch_id)

        if message_info:
            try:
                await client.edit_message_text(
                    message_info["batch_status_chat_id"],
                    message_info["batch_status_message_id"],
                    text,
                )
                return
            except Exception:
                logger.exception(
                    "Could not edit batch summary message for batch %s",
                    batch_id,
                )

        chat_id = task.get("status_chat_id") or task.get("chat_id")
        if not chat_id:
            return

        message = await client.send_message(
            chat_id,
            text,
        )

        set_batch_status_message_db(
            batch_id,
            message.chat.id,
            message.id,
        )

    except Exception:
        logger.exception(
            "Failed to update batch summary for batch %s",
            batch_id,
        )

async def process_queue_task(client, task, status_message):
    task_id = task["id"]
    item = {
        "url": task.get("url", ""),
        "chat_id": task.get("chat_id", DEFAULT_CHANNEL_ID),
        "custom_name": task.get("custom_name") or "",
        "preset": task.get("preset") or "custom",
    }
    file_path = None

    try:
        if status_message is None:
            status_message = await client.send_message(
                task.get("status_chat_id")
                or task.get("chat_id")
                or DEFAULT_CHANNEL_ID,
                f"📥 **Task #{task_id} starting...**\n\n"
                f"🎬 `{_display_task_name(task)[:60]}`\n"
                "⏳ Preparing download...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🛑 Cancel this video",
                        callback_data=f"cancel_task_{task_id}",
                    )
                ]]),
            )
            update_task_status_message_db(
                task_id,
                status_message.chat.id,
                status_message.id,
            )

        set_live_task(
            task_id,
            title=_display_task_name(task),
            percent=0,
            current_mb=0,
            speed_mb=0,
            eta="—",
            operation="Preparing download",
        )

        result = await process_single_url_safe(
            client,
            status_message,
            item,
            1,
            1,
            user_settings=None,
            task_id=task_id,
        )

        file_path, title, result_chat_id, size_mb, ok = result

        if not ok or not file_path or not os.path.exists(file_path):
            current_task = get_task_db(task_id)
            if not current_task or current_task.get("status") != "cancelled":
                update_task_status_db(task_id, "failed")
            clear_live_task(task_id)
            await update_batch_summary_message(client, task)
            return None, None, result_chat_id, 0, False

        # Download is complete. Count download statistics now.
        STATS["total_downloaded_items"] += 1
        STATS["total_downloaded_mb"] += size_mb
        save_stats()

        # Upload runs independently from the download worker.
        # The upload pipeline owns the final task status.
        upload_task = asyncio.create_task(
            _run_upload_pipeline(
                client,
                task,
                status_message,
                file_path,
                title,
                result_chat_id,
                size_mb,
                task_id,
            )
        )
        register_upload_task(task_id, upload_task)

        # Ownership of the file is transferred to the upload task.
        file_path = None

        return None, title, result_chat_id, size_mb, True

    except asyncio.CancelledError:
        clear_live_task(task_id)

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        raise

    except Exception as exc:
        logger.exception(
            "Queue task %s processing error",
            task_id,
        )

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        current_task = get_task_db(task_id)
        if not current_task or current_task.get("status") != "cancelled":
            update_task_status_db(task_id, "failed")

        clear_live_task(task_id)
        await update_batch_summary_message(client, task)

        return None, None, item["chat_id"], 0, False


async def _run_upload_pipeline(
    client,
    task,
    status_message,
    file_path,
    title,
    result_chat_id,
    size_mb,
    task_id,
):
    try:
        set_live_task(
            task_id,
            title=title,
            percent=0,
            current_mb=0,
            speed_mb=0,
            eta="—",
            operation="Uploading to Channel",
        )

        await finalize_queue_file(
            client,
            status_message,
            file_path,
            title,
            result_chat_id,
            task_id,
        )

        update_task_status_db(task_id, "completed")
        clear_live_task(task_id)
        await update_batch_summary_message(client, task)

        try:
            await status_message.edit_text(
                f"✅ **UPLOAD COMPLETE**\n\n"
                f"🎬 **{title[:55]}**\n\n"
                "✅ **UPLOAD COMPLETE**\n"
                "📢 Sent to Channel",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔄 Download Again",
                        callback_data=f"retry_task_{task_id}",
                    )
                ]]),
            )
        except Exception:
            pass

    except asyncio.CancelledError:
        current_task = get_task_db(task_id)

        if current_task and current_task.get("status") == "processing":
            update_task_status_db(task_id, "pending")

        clear_live_task(task_id)

        # Clean only this task's original file, split parts, and generated thumbnails.
        if file_path:
            try:
                original = Path(file_path)
                if original.exists():
                    original.unlink()
                for part in original.parent.glob(f"{original.stem}.part*{original.suffix}"):
                    if part.is_file():
                        part.unlink()
            except OSError:
                pass
        try:
            for thumb in Path(DOWNLOAD_DIR).glob(f"t_{task_id}_*.jpg"):
                if thumb.is_file():
                    thumb.unlink()
        except OSError:
            pass

        raise

    except Exception:
        logger.exception(
            "Upload pipeline failed for task %s",
            task_id,
        )

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        current_task = get_task_db(task_id)

        if not current_task or current_task.get("status") != "cancelled":
            update_task_status_db(task_id, "failed")

        clear_live_task(task_id)
        await update_batch_summary_message(client, task)

        try:
            await status_message.edit_text(
                f"❌ **UPLOAD FAILED**\n\n"
                f"🎬 `{title}`\n"
                "⚠️ Upload failed after retries.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔄 Retry",
                        callback_data=f"retry_task_{task_id}",
                    )
                ]]),
            )
        except Exception:
            pass

    finally:
        UPLOAD_TASKS.pop(int(task_id), None)

async def process_single_url_safe(client, message, item, total_count, idx, user_settings, is_audio=False, audio_bitrate="192", task_id=None):
    from config import MAX_RETRIES, TASK_TIMEOUT
    try:
        if isinstance(item, dict):
            u = item.get("url", "")
            ch = item.get("chat_id", DEFAULT_CHANNEL_ID)
            c_name = item.get("custom_name", "")
            trim_t = item.get("trim_info", "")
            preset = item.get("preset") or "custom"
        else:
            u = item[0] if len(item) > 0 else ""
            ch = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID
            c_name = item[2] if len(item) > 2 else ""
            trim_t = item[3] if len(item) > 3 else ""

        if not u:
            raise ValueError("URL is empty")

        for att in range(1, MAX_RETRIES + 1):
            if (
                (task_id is not None and str(task_id) in CANCELLED_TASKS)
                or (task_id is None and (str(idx) in CANCELLED_TASKS or "all" in CANCELLED_TASKS))
            ):
                break
            try:
                f, t = await asyncio.wait_for(
                    download_single_item(
                        u,
                        idx,
                        message,
                        custom_name=c_name,
                        trim_info=trim_t,
                        is_audio_mode=is_audio,
                        audio_bitrate=audio_bitrate,
                        preset=preset,
                        task_id=task_id,
                    ),
                    timeout=TASK_TIMEOUT
                )
                if f and os.path.exists(f):
                    if task_id is not None and t:
                        try:
                            update_task_title_db(task_id, t)
                        except Exception:
                            logger.exception(
                                "Task %s title database update failed",
                                task_id,
                            )
                    sz = os.path.getsize(f) / 1048576
                    return f, t, ch, sz, True
            except Exception as e:
                logger.exception("Task %s attempt %s failed", task_id or idx, att)
        return None, None, ch, 0, False
    except Exception as e:
        logger.exception("Task %s process_single_url failed", task_id or idx)

        if isinstance(item, dict):
            error_channel = item.get("chat_id", DEFAULT_CHANNEL_ID)
        else:
            error_channel = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID

        return None, None, error_channel, 0, False
