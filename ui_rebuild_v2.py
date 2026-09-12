from pathlib import Path

ROOT = Path(__file__).resolve().parent


def between(text, start, end, replacement, label):
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"FAILED: {label} start not found")
    b = text.find(end, a + len(start))
    if b < 0:
        raise SystemExit(f"FAILED: {label} end not found")
    return text[:a] + replacement + text[b:]


def replace_exact(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"FAILED: {label} matches={count}")
    return text.replace(old, new, 1)

# ---------------- handlers.py ----------------
p = ROOT / "handlers.py"
s = p.read_text(encoding="utf-8")

summary = '''def build_batch_summary_text(batch_id):
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

    return "\\n".join([
        "📊 **QUEUE STATUS**",
        "",
        f"⬇️ Downloads: {downloading}/2",
        f"⬆️ Uploads: {uploading}/4",
        "",
        f"⏳ Queue: {pending}",
        f"✅ Done: {completed}",
        f"❌ Failed: {failed}",
    ])


'''
s = between(s, "def _dashboard_progress(pct):", "def build_crawl_keyboard(items, page=1):", summary, "dashboard+summary")

crawl_keyboard = '''def _crawl_series_name(items):
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

'''
s = between(s, "def build_crawl_keyboard(items, page=1):", "async def enqueue_items", crawl_keyboard, "crawl keyboard")

crawl_command = '''    @app.on_message(filters.command("crawl") & filters.private)
    async def _cmd_crawl(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return
        urls_found = re.findall(r"https?://[^\\s<>]+", m.text or "")
        if not urls_found and m.reply_to_message and m.reply_to_message.text:
            urls_found = re.findall(r"https?://[^\\s<>]+", m.reply_to_message.text)
        if not urls_found:
            return await m.reply_text("⚠️ કૃપા કરીને `/crawl <બ્લોગ_લિંક>` આ રીતે લિંક મોકલો.")

        blog_url = urls_found[0].strip("[]()<>\\\"\\x27")
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
            f"🎬 **{_crawl_series_name(items)}**\\n"
            f"📦 **{len(items)} Options**",
            reply_markup=build_crawl_keyboard(items, 1),
        )

'''
s = between(s, '    @app.on_message(filters.command("crawl") & filters.private)', '    @app.on_callback_query(filters.regex(r"^crawl_.*"))', crawl_command, "crawl command")

crawl_callback = '''    @app.on_callback_query(filters.regex(r"^crawl_.*"))
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
                await status_msg.edit_text(f"🚀 **{len(selected_items)} options selected**\\n\\n⏳ Adding to queue...")
                task_ids = await enqueue_items(queue_items, status_msg, client)
                await status_msg.edit_text(f"📥 **{len(task_ids)} tasks added to queue**\\n\\n⏳ Processing will start automatically.")
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

'''
s = between(s, '    @app.on_callback_query(filters.regex(r"^crawl_.*"))', '    @app.on_callback_query(\n        filters.regex(', crawl_callback, "crawl callback")

# Remove the separate DOWNLOAD COMPLETE transition. The upload progress card replaces it immediately.
old = '''        try:
            await status_message.edit_text(
                f"✅ **DOWNLOAD COMPLETE**\\n\\n"
                f"🎬 `{title}`\\n"
                f"💾 `{size_mb:.2f} MB`\\n"
                "📤 Uploading to Channel...\\n"
                "⏳ Starting upload...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "❌ Cancel Upload",
                        callback_data=f"cancel_task_{task_id}",
                    )
                ]]),
            )
        except Exception:
            pass

'''
s = replace_exact(s, old, "", "download complete transition")

old_done = '''                f"🎬 `{title}`\\n"
                f"💾 `{size_mb:.2f} MB`\\n"
                "📢 Sent to Channel",'''
new_done = '''                f"🎬 **{title[:55]}**\\n\\n"
                "✅ **UPLOAD COMPLETE**\\n"
                "📢 Sent to Channel",'''
s = replace_exact(s, old_done, new_done, "completion card")
p.write_text(s, encoding="utf-8")

# ---------------- downloader.py ----------------
p = ROOT / "downloader.py"
s = p.read_text(encoding="utf-8")
tracker = '''class DashboardTracker:
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
            f"🎬 **{self.name[:55]}**\\n\\n"
            f"⬆️ **UPLOADING**\\n"
            f"`{bar}` **{pct:.0f}%**\\n\\n"
            f"💾 Data: {current_mb:.0f}/{total_mb:.0f} MB\\n"
            f"⚡ {speed:.1f} MB/s • ETA {eta}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_task_{self.task_id}")
        ]])
        try:
            await self.msg.edit_text(text, reply_markup=markup)
        except Exception:
            pass

'''
s = between(s, "class DashboardTracker:", "async def fetch_segment_with_backoff", tracker, "upload tracker")
p.write_text(s, encoding="utf-8")

print("UI rebuild v2 applied successfully.")
