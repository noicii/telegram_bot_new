# handlers.py
import os
import re
import asyncio
import time
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from config import DEFAULT_CHANNEL_ID, ADMIN_IDS, COOKIES_PATH, STATS, CAPTION_PATH, DOWNLOAD_DIR, SETTINGS, save_settings
from database import get_pending_tasks_db, clear_pending_tasks_db, add_task_db
from utils import is_authorized, get_main_keyboard, extract_subtitles_from_video, split_large_file, generate_screenshots_collage, generate_sample_clip, generate_auto_thumbnail, async_get_video_metadata
from crawler import resolve_blog_links, parse_input_lines
from downloader import download_single_item, DashboardTracker, CANCELLED_TASKS, download_semaphore

current_active_task = None

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
        
        items = [(t[1], DEFAULT_CHANNEL_ID, t[3] or "", "") for t in pending]
        await q.answer(f"જૂના {len(items)} ટાસ્ક ફરી શરૂ થયા છે!", show_alert=True)
        s_msg = await q.message.edit_text(f"⚡ **જૂના {len(items)} ટાસ્કનું ડાઉનલોડિંગ ફરી શરૂ થાય છે...**")
        
        global current_active_task
        current_active_task = asyncio.create_task(process_all_urls(items, s_msg, c))

    @app.on_callback_query(filters.regex("^clear_old_tasks$"))
    async def _cb_clear_queue(c, q):
        clear_pending_tasks_db()
        await q.answer("ક્યૂ સાફ થઈ ગઈ!", show_alert=True)
        await q.message.edit_text(
            "🗑 **જૂના ટાસ્ક રદ કરવામાં આવ્યા છે.**\n"
            "તમે હવે નવી લિંક્સ મોકલી શકો છો! 🚀",
            reply_markup=get_main_keyboard()
        )

    @app.on_message(filters.text & filters.private)
    async def _tx(c, m):
        global current_active_task
        if not is_authorized(m.from_user.id, ADMIN_IDS) or m.text.startswith("/"):
            return
        items = parse_input_lines(m.text.splitlines())
        if not items:
            return await m.reply_text("કોઈ માન્ય લિંક નથી.")
        mk = InlineKeyboardMarkup([[InlineKeyboardButton("ટાસ્ક કેન્સલ કરો", callback_data="cancel_process")]])
        CANCELLED_TASKS.clear()
        s = await m.reply_text(f"{len(items)} લિંક્સ મળી. પ્રોસેસિંગ શરૂ થાય છે...", reply_markup=mk)
        current_active_task = asyncio.create_task(process_all_urls(items, s, c))

    @app.on_message(filters.command("crawl") & filters.private)
    async def _cmd_crawl(c, m):
        global current_active_task
        if not is_authorized(m.from_user.id, ADMIN_IDS):
            return
        
        urls_found = re.findall(r"https?://[^\s<>]+", m.text)
        if not urls_found and m.reply_to_message and m.reply_to_message.text:
            urls_found = re.findall(r"https?://[^\s<>]+", m.reply_to_message.text)
            
        if not urls_found:
            return await m.reply_text("⚠️ કૃપા કરીને `/crawl <બ્લોગ_લિંક>` આ રીતે લિંક મોકલો.")
            
        blog_url = urls_found[0].strip("[]()<>\"\x27")
        await m.reply_text(f"🔍 બ્લોગ ક્રોલ થઈ રહ્યો છે: `{blog_url}`...")
        
        resolved_links = resolve_blog_links(blog_url)
        if not resolved_links:
            return await m.reply_text("❌ આ બ્લોગમાંથી કોઈ માન્ય વિડિયો લિંક મળી નથી.")
            
        items = []
        for r_url in resolved_links:
            items.append((r_url, DEFAULT_CHANNEL_ID, "", ""))
            
        mk = InlineKeyboardMarkup([[InlineKeyboardButton("ટાસ્ક કેન્સલ કરો", callback_data="cancel_process")]])
        CANCELLED_TASKS.clear()
        s = await m.reply_text(f"📦 કુલ {len(items)} વિડિયો લિંક્સ મળી છે. ડાઉનલોડિંગ શરૂ થાય છે...", reply_markup=mk)
        current_active_task = asyncio.create_task(process_all_urls(items, s, c))

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

        # Cancel
        if data in ("cancel", "cancel_process"):
            CANCELLED_TASKS.add("all")

            await callback_query.answer(
                "🛑 Task cancelled successfully!",
                show_alert=True,
            )
            return

        # Unknown callback
        await callback_query.answer(
            f"⚙️ Action received: {data}",
            show_alert=False,
        )

async def process_all_urls(items, s_msg, client, is_audio=False, audio_bitrate="192"):
    from config import STATS, CAPTION_PATH, CUSTOM_THUMB_PATH
    from database import clear_pending_tasks_db
    from config import save_stats
    
    succ, fail = [], []
    batch_start_time = time.time()
    total_count = len(items)

    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            item_url = item.get("url", "")
            item_chat_id = item.get("chat_id", DEFAULT_CHANNEL_ID)
            item_custom_name = item.get("custom_name", "")
        else:
            item_url = item[0] if len(item) > 0 else ""
            item_chat_id = item[1] if len(item) > 1 else DEFAULT_CHANNEL_ID
            item_custom_name = item[2] if len(item) > 2 else ""

        add_task_db(item_url, item_chat_id, item_custom_name)

    caption_template = ""
    if os.path.exists(CAPTION_PATH):
        try:
            with open(CAPTION_PATH, "r", encoding="utf-8") as cf:
                caption_template = cf.read().strip()
        except Exception:
            pass

    tasks = []
    for idx, item in enumerate(items, 1):
        if str(idx) in CANCELLED_TASKS or "all" in CANCELLED_TASKS:
            break
        task = process_single_url_safe_with_semaphore(client, s_msg, item, total_count, idx, is_audio, audio_bitrate)
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for idx, res_tuple in enumerate(results, 1):
        f, t, ch, sz, ok = res_tuple

        current_item = items[idx - 1]
        if isinstance(current_item, dict):
            orig_url = current_item.get("url", "")
        else:
            orig_url = current_item[0] if len(current_item) > 0 else ""
        if not ok or not f or not os.path.exists(f):
            fail.append((idx, orig_url, "Download or processing failed"))
            STATS["failed_items"] += 1
            continue

        tracker = DashboardTracker(s_msg, idx, total_count, t, time.time())
        clean_file_label = re.sub(r"[/\\*?:\"<>|]", "", t).strip()
        all_channels = [ch] + [ec for ec in SETTINGS.get("extra_channels", []) if ec != ch]

        split_files = split_large_file(f)
        for p_idx, part_path in enumerate(split_files, 1):
            part_label = f"{clean_file_label} (Part {p_idx})" if len(split_files) > 1 else clean_file_label
            p_sz = os.path.getsize(part_path) / 1048576
            dur, w, h = await async_get_video_metadata(part_path)

            # Pyrogram requires integer media metadata.
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

            th = CUSTOM_THUMB_PATH if os.path.exists(CUSTOM_THUMB_PATH) else await generate_auto_thumbnail(part_path, os.path.join(DOWNLOAD_DIR, f"t_{idx}_{p_idx}.jpg"))
            if th and (not os.path.exists(th) or os.path.getsize(th) == 0):
                th = None

            dur_m, dur_s = divmod(int(dur or 0), 60)
            dur_h, dur_m = divmod(dur_m, 60)
            dur_str = f"{dur_h:02d}:{dur_m:02d}:{dur_s:02d}" if dur_h > 0 else f"{dur_m:02d}:{dur_s:02d}"

            if caption_template:
                c_cap = caption_template.replace("{title}", part_label).replace("{size}", f"{p_sz:.2f} MB").replace("{duration}", dur_str)
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
                    "progress": tracker.callback if target_chat == ch else None
                }
                if th and os.path.exists(th):
                    v_kwargs["thumb"] = th

                while True:
                    try:
                        await client.send_video(**v_kwargs)
                        break
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value + 1)
                    except Exception:
                        raise

            if os.path.exists(part_path):
                os.remove(part_path)

        succ.append((idx, t, f"{sz:.2f} MB"))
        STATS["total_downloaded_items"] += 1
        STATS["total_downloaded_mb"] += sz

    clear_pending_tasks_db()
    save_stats()

    total_time = time.strftime("%H:%M:%S", time.gmtime(time.time() - batch_start_time))
    rep = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃ 📋 **ટાસ્ક પ્રોસેસિંગ રિપોર્ટ**\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ ⏱ **કુલ સમય:** `{total_time}`\n"
    )
    if succ:
        rep += f"┃ ✅ **સફળ:** `{len(succ)}/{total_count}` ફાઇલ્સ\n"
    if fail:
        rep += f"┃ ❌ **ફેલ:** `{len(fail)}` ફાઇલ્સ\n"
    rep += "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    try:
        await s_msg.edit_text(rep, reply_markup=get_main_keyboard())
    except Exception:
        await s_msg.reply_text(rep, reply_markup=get_main_keyboard())

async def process_single_url_safe(client, message, item, total_count, idx, user_settings, is_audio=False, audio_bitrate="192"):
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
            if str(idx) in CANCELLED_TASKS or "all" in CANCELLED_TASKS:
                break
            try:
                f, t = await asyncio.wait_for(
                    download_single_item(u, idx, message, custom_name=c_name, trim_info=trim_t, is_audio_mode=is_audio, audio_bitrate=audio_bitrate),
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

async def process_single_url_safe_with_semaphore(client, message, item, total_count, idx, is_audio, audio_bitrate="192"):
    async with download_semaphore:
        return await process_single_url_safe(client, message, item, total_count, idx, user_settings=None, is_audio=is_audio, audio_bitrate=audio_bitrate)
