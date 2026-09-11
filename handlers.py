# handlers.py
import os
import re
import asyncio
import time
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from config import DEFAULT_CHANNEL_ID, ADMIN_IDS, COOKIES_PATH, STATS, CAPTION_PATH, DOWNLOAD_DIR, SETTINGS, save_settings, save_stats
from database import get_pending_tasks_db, clear_pending_tasks_db, add_task_db, update_task_status_db, get_next_queued_task_db, get_queue_counts_db, get_task_db, reset_processing_tasks_db, cancel_pending_tasks_db, get_queue_tasks_db, update_task_status_message_db, cancel_tasks_by_status_message_db, clear_crawl_items_db, add_crawl_item_db, get_crawl_items_db, get_crawl_item_db, toggle_crawl_item_db, select_all_crawl_items_db, clear_selected_crawl_items_db, get_selected_crawl_items_db
from utils import is_authorized, get_main_keyboard, extract_subtitles_from_video, split_large_file, generate_screenshots_collage, generate_sample_clip, generate_auto_thumbnail, async_get_video_metadata
from crawler import resolve_blog_links, crawl_blog_episodes, parse_input_lines
from downloader import download_single_item, DashboardTracker, CANCELLED_TASKS


def build_crawl_keyboard(items):
    buttons = []

    # Group items by episode while preserving crawler order.
    episodes = {}
    for item in items:
        episode = item.get("episode") or "Unknown Episode"
        episodes.setdefault(episode, []).append(item)

    for episode, episode_items in episodes.items():
        buttons.append([
            InlineKeyboardButton(
                f"🎬 {episode}",
                callback_data="crawl_noop",
            )
        ])

        for item in episode_items:
            icon = "✅" if item["selected"] else "⬜"
            source = item.get("source") or "Unknown"
            resolution = item.get("resolution") or "Unknown"

            label = f"{icon} {source} • {resolution}"

            # Telegram inline button text should stay reasonably short.
            if len(label) > 60:
                label = label[:57] + "..."

            buttons.append([
                InlineKeyboardButton(
                    label,
                    callback_data=f"crawl_toggle_{item['id']}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "☑️ Select All",
            callback_data="crawl_select_all",
        ),
        InlineKeyboardButton(
            "❌ Clear",
            callback_data="crawl_clear_all",
        ),
    ])

    buttons.append([
        InlineKeyboardButton(
            "🚀 Download Selected",
            callback_data="crawl_download_selected",
        )
    ])

    return InlineKeyboardMarkup(buttons)


async def enqueue_items(items, status_message, client, is_audio=False, audio_bitrate="192"):
    if not items:
        return []

    status_chat_id = status_message.chat.id
    status_message_id = status_message.id
    task_ids = []

    for item in items:
        if isinstance(item, dict):
            url = item.get("url", "")
            chat_id = item.get("chat_id", DEFAULT_CHANNEL_ID)
            custom_name = item.get("custom_name", "")
        else:
            url = item[0] if len(item) > 0 else ""
            chat_id = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID
            custom_name = item[2] if len(item) > 2 else ""

        if not url:
            continue

        task_id = add_task_db(
            url,
            chat_id,
            custom_name,
            status_chat_id=status_chat_id,
            status_message_id=status_message_id,
        )
        task_ids.append(task_id)

    return task_ids


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
        
        pending = get_pending_tasks_db()
        if pending:
            preview_items = []
            for t in pending[:3]:
                if isinstance(t, dict):
                    task_url = t.get("url", "")
                else:
                    task_url = t[1] if len(t) > 1 else ""

                if task_url:
                    preview_items.append(f"• `{task_url[:35]}...`")

            task_preview = "\n".join(preview_items)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 જૂના ટાસ્ક ચાલુ કરો", callback_data="resume_old_tasks"),
                 InlineKeyboardButton("🗑 ક્યૂ સાફ કરો", callback_data="clear_old_tasks")]
            ])
            await m.reply_text(
                f"⚠️ **બોટ રીસ્ટાર્ટ થયો છે! ક્યૂમાં અધૂરા ટાસ્ક મળ્યા છે:**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **બાકી ટાસ્ક:** `{len(pending)}`\n"
                f"{task_preview}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"તમે શું કરવા માંગો છો?",
                reply_markup=kb
            )
        else:
            text = (
                "🤖 **ઓલ-ઇન-વન વિડિયો ડાઉનલોડર કંટ્રોલ પેનલ**\n\n"
                "✨ ક્યૂ ખાલી છે. તમે નવી લિંક અથવા `.txt` ફાઇલ મોકલી શકો છો.\n\n"
                "નીચેના બટન્સથી ફીચર્સ **ON / OFF** કરો 👇"
            )
            await m.reply_text(text, reply_markup=get_main_keyboard())

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
                    s_msg.id,
                )

        await s_msg.edit_text(
            f"⚡ **જૂના {len(items)} ટાસ્ક Queue માં પાછા મૂકાયા છે.**\n\n"
            "⏳ FIFO Queue મુજબ processing ફરી શરૂ થશે...",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ટાસ્ક કેન્સલ કરો", callback_data="cancel_process")]
            ]),
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

        mk = InlineKeyboardMarkup([
            [InlineKeyboardButton("ટાસ્ક કેન્સલ કરો", callback_data="cancel_process")]
        ])

        s = await m.reply_text(
            f"{len(items)} લિંક્સ મળી. Queue માં ઉમેરાઈ રહી છે...",
            reply_markup=mk,
        )

        task_ids = await enqueue_items(items, s, c)

        await s.edit_text(
            f"📥 **{len(task_ids)} ટાસ્ક Queue માં ઉમેરાયા.**\n\n"
            "⏳ FIFO Queue મુજબ processing શરૂ થશે...",
            reply_markup=mk,
        )

    @app.on_message(filters.command("crawl") & filters.private)
    async def _cmd_crawl(c, m):
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return

        urls_found = re.findall(r"https?://[^\s<>]+", m.text or "")

        if not urls_found and m.reply_to_message and m.reply_to_message.text:
            urls_found = re.findall(
                r"https?://[^\s<>]+",
                m.reply_to_message.text,
            )

        if not urls_found:
            return await m.reply_text(
                "⚠️ કૃપા કરીને `/crawl <બ્લોગ_લિંક>` આ રીતે લિંક મોકલો."
            )

        blog_url = urls_found[0].strip("[]()<>\"\\x27")

        status_msg = await m.reply_text(
            f"🔍 બ્લોગ ક્રોલ થઈ રહ્યો છે: `{blog_url}`..."
        )

        episodes = crawl_blog_episodes(blog_url)

        if not episodes:
            return await status_msg.edit_text(
                "❌ આ બ્લોગમાંથી કોઈ Episode/LuluStream લિંક મળી નથી."
            )

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

        lines = [
            "🎬 **Crawl Complete**",
            "",
            f"📦 કુલ Episodes: **{len(items)}**",
            "",
            "👇 Download માટે Episodes select કરો.",
        ]

        for index, item in enumerate(items, 1):
            lines.append(f"**{index}. {item['title']}**")

        await status_msg.edit_text("\n".join(lines), reply_markup=build_crawl_keyboard(items))

    @app.on_callback_query(filters.regex(r"^crawl_.*"))
    async def _handle_crawl_callback(client, callback_query):
        if not is_authorized(callback_query.from_user.id, ADMIN_IDS):
            return await callback_query.answer("❌ Unauthorized", show_alert=True)

        data = callback_query.data
        chat_id = callback_query.message.chat.id

        if data == "crawl_noop":
            return await callback_query.answer("📂 Episode options નીચે છે.", show_alert=False)

        if data == "crawl_select_all":
            select_all_crawl_items_db(chat_id)
            items = get_crawl_items_db(chat_id)
            await callback_query.answer("☑️ બધા options select થયા.", show_alert=False)
            await callback_query.message.edit_reply_markup(
                build_crawl_keyboard(items)
            )
            return

        if data == "crawl_clear_all":
            clear_selected_crawl_items_db(chat_id)
            items = get_crawl_items_db(chat_id)
            await callback_query.answer("❌ Selection clear થઈ ગઈ.", show_alert=False)
            await callback_query.message.edit_reply_markup(
                build_crawl_keyboard(items)
            )
            return

        if data == "crawl_download_selected":
            selected_items = get_selected_crawl_items_db(chat_id)

            if not selected_items:
                return await callback_query.answer(
                    "⚠️ પહેલા ઓછામાં ઓછું એક option select કરો.",
                    show_alert=True,
                )

            await callback_query.answer(
                f"🚀 {len(selected_items)} options queueમાં ઉમેરાઈ રહ્યા છે...",
                show_alert=False,
            )

            await callback_query.message.edit_text(
                f"🚀 **{len(selected_items)} options selected.**\n\n"
                "⏳ Queue માં ઉમેરાઈ રહ્યા છે..."
            )

            queue_items = [
                {
                    "url": item["url"],
                    "chat_id": chat_id,
                    "custom_name": item["title"],
                    "source": item.get("source", "Unknown"),
                    "resolution": item.get("resolution", "Unknown"),
                    "source_url": item.get("source_url", item["url"]),
                }
                for item in selected_items
            ]

            status_msg = callback_query.message
            task_ids = await enqueue_items(queue_items, status_msg, client)

            await status_msg.edit_text(
                f"📥 **{len(task_ids)} crawl options Queue માં ઉમેરાયા.**\n\n"
                "⏳ FIFO Queue મુજબ processing શરૂ થશે...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ટાસ્ક કેન્સલ કરો", callback_data="cancel_process")]
                ]),
            )
            return

        if data.startswith("crawl_toggle_"):
            try:
                item_id = int(data.rsplit("_", 1)[1])
            except ValueError:
                return await callback_query.answer(
                    "❌ Invalid item",
                    show_alert=True,
                )

            item = get_crawl_item_db(item_id, chat_id)
            if not item:
                return await callback_query.answer(
                    "❌ Item not found",
                    show_alert=True,
                )

            toggle_crawl_item_db(item_id, chat_id)

            items = get_crawl_items_db(chat_id)
            selected_count = sum(1 for current_item in items if current_item["selected"])

            await callback_query.answer(
                f"Selection: {selected_count}/{len(items)}",
                show_alert=False,
            )

            await callback_query.message.edit_text(
                "🎬 **Crawl Complete**\n\n"
                f"📦 Total options: **{len(items)}**\n"
                f"✅ Selected: **{selected_count}**\n\n"
                "👇 દરેક Episodeમાંથી Source/Resolution પસંદ કરો.",
                reply_markup=build_crawl_keyboard(items),
            )
            return

    @app.on_callback_query(
        filters.regex(
            "^(toggle_|cycle_|set_|status|btn_status|clear_cache|btn_clearcache|cancel).*"
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

        # Status
        if data in ("status", "btn_status"):
            await callback_query.answer(
                "📊 Bot is running smoothly and active!",
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

        # Resolution cycle
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

        # Cancel only tasks belonging to this status-message batch.
        if data in ("cancel", "cancel_process"):
            status_chat_id = callback_query.message.chat.id
            status_message_id = callback_query.message.id

            cancelled_ids = cancel_tasks_by_status_message_db(
                status_chat_id,
                status_message_id,
            )

            for task_id in cancelled_ids:
                CANCELLED_TASKS.add(str(task_id))

            if cancelled_ids:
                await callback_query.answer(
                    f"🛑 {len(cancelled_ids)} task(s) cancelled.",
                    show_alert=True,
                )
            else:
                await callback_query.answer(
                    "ℹ️ આ batch માં cancel કરવા માટે કોઈ pending/processing task નથી.",
                    show_alert=True,
                )
            return

        # Unknown callback
        await callback_query.answer(
            f"⚙️ Action received: {data}",
            show_alert=False,
        )

async def finalize_queue_file(client, status_message, file_path, title, chat_id, task_id):
    from config import CUSTOM_THUMB_PATH, CAPTION_PATH, STATS, save_stats

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

        all_channels = [chat_id] + [
            ec for ec in SETTINGS.get("extra_channels", [])
            if ec != chat_id
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
                "progress": tracker.callback if target_chat == chat_id else None,
            }

            if th and os.path.exists(th):
                v_kwargs["thumb"] = th

            while True:
                try:
                    await client.send_video(**v_kwargs)
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)

        try:
            if os.path.exists(part_path):
                os.remove(part_path)
        except OSError:
            pass

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass

    return True

async def process_queue_task(client, task, status_message):
    task_id = task["id"]

    item = {
        "url": task.get("url", ""),
        "chat_id": task.get("chat_id", DEFAULT_CHANNEL_ID),
        "custom_name": task.get("custom_name") or "",
    }

    try:
        if status_message is None:
            status_message = await client.send_message(
                task.get("status_chat_id") or task.get("chat_id") or DEFAULT_CHANNEL_ID,
                f"📥 Task #{task_id} processing શરૂ...",
            )
            update_task_status_message_db(
                task_id,
                status_message.chat.id,
                status_message.id,
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
            return None, None, result_chat_id, 0, False

        await finalize_queue_file(
            client,
            status_message,
            file_path,
            title,
            result_chat_id,
            task_id,
        )

        STATS["total_downloaded_items"] += 1
        STATS["total_downloaded_mb"] += size_mb
        save_stats()

        update_task_status_db(task_id, "completed")

        try:
            await status_message.edit_text(
                f"✅ **Task #{task_id} completed successfully.**\n\n"
                f"🎬 `{title}`\n"
                f"💾 `{size_mb:.2f} MB`"
            )
        except Exception as status_exc:
            print(
                f"⚠️ Could not update completion status for task {task_id}: {status_exc}",
                flush=True,
            )

        return None, title, result_chat_id, size_mb, True

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            f"⚠️ Queue task {task_id} processing error: {exc}",
            flush=True,
        )
        current_task = get_task_db(task_id)
        if not current_task or current_task.get("status") != "cancelled":
            update_task_status_db(task_id, "failed")
        return None, None, item["chat_id"], 0, False



async def process_single_url_safe(client, message, item, total_count, idx, user_settings, is_audio=False, audio_bitrate="192", task_id=None):
    from config import MAX_RETRIES, TASK_TIMEOUT
    try:
        if isinstance(item, dict):
            u = item.get("url", "")
            ch = item.get("chat_id", DEFAULT_CHANNEL_ID)
            c_name = item.get("custom_name", "")
            trim_t = item.get("trim_info", "")
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
                        task_id=task_id,
                    ),
                    timeout=TASK_TIMEOUT
                )
                if f and os.path.exists(f):
                    sz = os.path.getsize(f) / 1048576
                    return f, t, ch, sz, True
            except Exception as e:
                print(f"⚠️ Task attempt {att} error: {e}", flush=True)
        return None, None, ch, 0, False
    except Exception as e:
        print(f"⚠️ Process single URL error: {e}", flush=True)

        if isinstance(item, dict):
            error_channel = item.get("chat_id", DEFAULT_CHANNEL_ID)
        else:
            error_channel = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID

        return None, None, error_channel, 0, False
