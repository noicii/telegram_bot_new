from pathlib import Path

HANDLERS = Path("handlers.py")
WORKER = Path("queue_worker.py")

s = HANDLERS.read_text()

old = '''MAX_CONCURRENT_UPLOADS = 4\nUPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)\nUPLOAD_TASKS = {}\n\ndef register_upload_task(task_id, task):\n    UPLOAD_TASKS[int(task_id)] = task\n\ndef get_upload_task(task_id):\n    task = UPLOAD_TASKS.get(int(task_id))\n    return task if task and not task.done() else None\n\ndef cancel_upload_task(task_id):\n    task = get_upload_task(task_id)\n    if task:\n        task.cancel()\n        return True\n    return False\n\ndef cancel_all_upload_tasks():\n    tasks = list(UPLOAD_TASKS.values())\n    for task in tasks:\n        if task and not task.done():\n            task.cancel()\n    return len(tasks)\n'''

new = '''MAX_CONCURRENT_UPLOADS = 4\nUPLOAD_TASKS = {}\nUPLOAD_QUEUE = None\nUPLOAD_WORKERS = set()\nUPLOAD_CANCELLED = set()\n\ndef register_upload_task(task_id, task):\n    UPLOAD_TASKS[int(task_id)] = task\n\ndef get_upload_task(task_id):\n    task = UPLOAD_TASKS.get(int(task_id))\n    return task if task and not task.done() else None\n\ndef cancel_upload_task(task_id):\n    tid = int(task_id)\n    UPLOAD_CANCELLED.add(tid)\n    task = get_upload_task(tid)\n    if task:\n        task.cancel()\n        return True\n    return True\n\ndef cancel_all_upload_tasks():\n    tids = list(UPLOAD_TASKS)\n    for tid in tids:\n        UPLOAD_CANCELLED.add(int(tid))\n        task = UPLOAD_TASKS.get(int(tid))\n        if task and not task.done():\n            task.cancel()\n    return len(tids)\n\n\ndef _ensure_upload_workers():\n    global UPLOAD_QUEUE\n    if UPLOAD_QUEUE is None:\n        UPLOAD_QUEUE = asyncio.Queue()\n    alive = {t for t in UPLOAD_WORKERS if not t.done()}\n    UPLOAD_WORKERS.clear()\n    UPLOAD_WORKERS.update(alive)\n    while len(UPLOAD_WORKERS) < MAX_CONCURRENT_UPLOADS:\n        worker = asyncio.create_task(_upload_worker_loop())\n        UPLOAD_WORKERS.add(worker)\n\n\ndef _cleanup_upload_file(file_path, task_id):\n    if file_path:\n        try:\n            original = Path(file_path)\n            if original.exists():\n                original.unlink()\n            for part in original.parent.glob(f"{original.stem}.part*{original.suffix}"):\n                if part.is_file():\n                    part.unlink()\n        except OSError:\n            pass\n    try:\n        for thumb in Path(DOWNLOAD_DIR).glob(f"t_{task_id}_*.jpg"):\n            if thumb.is_file():\n                thumb.unlink()\n    except OSError:\n        pass\n\n\nasync def _upload_worker_loop():\n    while True:\n        job = await UPLOAD_QUEUE.get()\n        if job is None:\n            UPLOAD_QUEUE.task_done()\n            return\n        task_id = job[-1]\n        try:\n            if int(task_id) in UPLOAD_CANCELLED:\n                update_task_status_db(task_id, "cancelled")\n                _cleanup_upload_file(job[3], task_id)\n                clear_live_task(task_id)\n                continue\n            upload_task = asyncio.create_task(_run_upload_pipeline(*job))\n            register_upload_task(task_id, upload_task)\n            try:\n                await upload_task\n            except asyncio.CancelledError:\n                if not upload_task.done():\n                    upload_task.cancel()\n                    await asyncio.gather(upload_task, return_exceptions=True)\n                raise\n            finally:\n                UPLOAD_TASKS.pop(int(task_id), None)\n                UPLOAD_CANCELLED.discard(int(task_id))\n        except asyncio.CancelledError:\n            current = get_task_db(task_id)\n            if current and current.get("status") == "processing":\n                update_task_status_db(task_id, "pending")\n            raise\n        except Exception:\n            logger.exception("Upload worker failed for task %s", task_id)\n        finally:\n            UPLOAD_QUEUE.task_done()\n\n\nasync def stop_upload_workers():\n    if UPLOAD_QUEUE is None:\n        return\n    workers = list(UPLOAD_WORKERS)\n    cancel_all_upload_tasks()\n    for _ in workers:\n        await UPLOAD_QUEUE.put(None)\n    if workers:\n        await asyncio.gather(*workers, return_exceptions=True)\n    UPLOAD_WORKERS.clear()\n'''

if old not in s:
    raise SystemExit("handlers.py upload manager header not found")
s = s.replace(old, new, 1)

start = s.index("async def finalize_queue_file(")
end = s.index("async def process_single_url_safe(", start)

block = r'''async def finalize_queue_file(client, status_message, file_path, title, chat_id, task_id):
    from config import CUSTOM_THUMB_PATH, CAPTION_PATH, MAX_RETRIES
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError("Downloaded file not found")
    split_files = split_large_file(file_path)
    if not split_files:
        raise FileNotFoundError("No output file after splitting")
    primary = DEFAULT_CHANNEL_ID
    channels = [primary] + [ec for ec in SETTINGS.get("extra_channels", []) if ec != primary]
    for p_idx, part_path in enumerate(split_files, 1):
        if int(task_id) in UPLOAD_CANCELLED:
            raise asyncio.CancelledError()
        part_label = f"{title} (Part {p_idx})" if len(split_files) > 1 else title
        dur, w, h = await async_get_video_metadata(part_path)
        try: dur = int(round(float(dur or 0)))
        except (TypeError, ValueError): dur = 0
        try: w = int(round(float(w or 0)))
        except (TypeError, ValueError): w = 0
        try: h = int(round(float(h or 0)))
        except (TypeError, ValueError): h = 0
        th = CUSTOM_THUMB_PATH if os.path.exists(CUSTOM_THUMB_PATH) else await generate_auto_thumbnail(part_path, os.path.join(DOWNLOAD_DIR, f"t_{task_id}_{p_idx}.jpg"))
        if th and (not os.path.exists(th) or os.path.getsize(th) == 0): th = None
        size_mb = os.path.getsize(part_path) / 1048576
        dm, ds = divmod(dur, 60); dh, dm = divmod(dm, 60)
        duration = f"{dh:02d}:{dm:02d}:{ds:02d}" if dh else f"{dm:02d}:{ds:02d}"
        caption_template = ""
        if os.path.exists(CAPTION_PATH):
            try:
                caption_template = Path(CAPTION_PATH).read_text(encoding="utf-8").strip()
            except Exception:
                pass
        caption = (caption_template.replace("{title}", part_label).replace("{size}", f"{size_mb:.2f} MB").replace("{duration}", duration)
                   if caption_template else f"🎬 **{part_label}**\n⏱ `{duration}` | 💾 `{size_mb:.2f} MB`")
        tracker = DashboardTracker(status_message, 1, 1, part_label, time.time(), task_id=task_id)
        for target in channels:
            kwargs = dict(chat_id=target, video=part_path, file_name=f"{part_label}.mp4", caption=caption,
                          duration=dur, width=w, height=h, supports_streaming=True,
                          progress=tracker.callback if target == primary else None)
            if th and os.path.exists(th): kwargs["thumb"] = th
            last_error = None
            for attempt in range(1, MAX_RETRIES + 1):
                if int(task_id) in UPLOAD_CANCELLED: raise asyncio.CancelledError()
                try:
                    await client.send_video(**kwargs)
                    last_error = None
                    break
                except FloodWait as fw:
                    await asyncio.sleep(int(fw.value) + 1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < MAX_RETRIES: await asyncio.sleep(2 ** (attempt - 1))
            if last_error is not None:
                raise RuntimeError(f"Upload failed for task {task_id} to channel {target}: {last_error}")
        try:
            if os.path.exists(part_path): os.remove(part_path)
        except OSError: pass
        if th and os.path.abspath(th) != os.path.abspath(CUSTOM_THUMB_PATH):
            try:
                if os.path.exists(th): os.remove(th)
            except OSError: pass
    try:
        if os.path.exists(file_path): os.remove(file_path)
    except OSError: pass
    return True


async def update_batch_summary_message(client, task):
    batch_id = task.get("batch_id")
    if not batch_id: return
    try:
        text = build_batch_summary_text(batch_id)
        info = get_batch_status_message_db(batch_id)
        if info:
            try:
                await client.edit_message_text(info["batch_status_chat_id"], info["batch_status_message_id"], text)
                return
            except Exception: logger.exception("Could not edit batch summary for batch %s", batch_id)
        chat_id = task.get("status_chat_id") or task.get("chat_id")
        if not chat_id: return
        msg = await client.send_message(chat_id, text)
        set_batch_status_message_db(batch_id, msg.chat.id, msg.id)
    except Exception: logger.exception("Failed to update batch summary for batch %s", batch_id)


async def process_queue_task(client, task, status_message):
    task_id = task["id"]
    item = {"url": task.get("url", ""), "chat_id": task.get("chat_id", DEFAULT_CHANNEL_ID),
            "custom_name": task.get("custom_name") or "", "preset": task.get("preset") or "custom"}
    file_path = None
    try:
        _ensure_upload_workers()
        if status_message is None:
            status_message = await client.send_message(task.get("status_chat_id") or task.get("chat_id") or DEFAULT_CHANNEL_ID,
                f"🎬 **{_display_task_name(task)[:55]}**\n\n⬇️ **DOWNLOADING**\nPreparing...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel_task_{task_id}")]]))
            update_task_status_message_db(task_id, status_message.chat.id, status_message.id)
        set_live_task(task_id, title=_display_task_name(task), percent=0, current_mb=0, speed_mb=0, eta="—", operation="Downloading")
        result = await process_single_url_safe(client, status_message, item, 1, 1, user_settings=None, task_id=task_id)
        file_path, title, result_chat_id, size_mb, ok = result
        if not ok or not file_path or not os.path.exists(file_path):
            current = get_task_db(task_id)
            if not current or current.get("status") != "cancelled": update_task_status_db(task_id, "failed")
            clear_live_task(task_id); await update_batch_summary_message(client, task); return None, None, result_chat_id, 0, False
        STATS["total_downloaded_items"] += 1; STATS["total_downloaded_mb"] += size_mb; save_stats()
        await UPLOAD_QUEUE.put((client, task, status_message, file_path, title, result_chat_id, size_mb, task_id))
        file_path = None
        return None, title, result_chat_id, size_mb, True
    except asyncio.CancelledError:
        clear_live_task(task_id)
        if file_path: _cleanup_upload_file(file_path, task_id)
        raise
    except Exception:
        logger.exception("Queue task %s processing error", task_id)
        if file_path: _cleanup_upload_file(file_path, task_id)
        current = get_task_db(task_id)
        if not current or current.get("status") != "cancelled": update_task_status_db(task_id, "failed")
        clear_live_task(task_id); await update_batch_summary_message(client, task)
        return None, None, item["chat_id"], 0, False


async def _run_upload_pipeline(client, task, status_message, file_path, title, result_chat_id, size_mb, task_id):
    try:
        set_live_task(task_id, title=title, percent=0, current_mb=0, speed_mb=0, eta="—", operation="Telegram upload")
        await finalize_queue_file(client, status_message, file_path, title, result_chat_id, task_id)
        update_task_status_db(task_id, "completed"); clear_live_task(task_id); await update_batch_summary_message(client, task)
        try:
            await status_message.edit_text(f"🎬 **{title[:55]}**\n\n✅ **UPLOAD COMPLETE**\n📢 Sent to Channel",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Download Again", callback_data=f"retry_task_{task_id}")]]))
        except Exception: pass
    except asyncio.CancelledError:
        current = get_task_db(task_id)
        if current and current.get("status") == "processing": update_task_status_db(task_id, "pending")
        clear_live_task(task_id); _cleanup_upload_file(file_path, task_id); raise
    except Exception:
        logger.exception("Upload pipeline failed for task %s", task_id)
        if file_path: _cleanup_upload_file(file_path, task_id)
        current = get_task_db(task_id)
        if not current or current.get("status") != "cancelled": update_task_status_db(task_id, "failed")
        clear_live_task(task_id); await update_batch_summary_message(client, task)
        try:
            await status_message.edit_text(f"🎬 **{title[:55]}**\n\n❌ **UPLOAD FAILED**\n⚠️ Upload failed after retries.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry", callback_data=f"retry_task_{task_id}")]]))
        except Exception: pass

'''

s = s[:start] + block + s[end:]
HANDLERS.write_text(s)

w = WORKER.read_text()
old_finally = '''        try:\n            from handlers import cancel_all_upload_tasks\n            n = cancel_all_upload_tasks()\n            if n:\n                logger.info("Stopping %s upload task(s)", n)\n        except Exception:\n            logger.exception("Could not stop upload tasks")\n        logger.info("Queue worker stopped")'''
new_finally = '''        try:\n            from handlers import cancel_all_upload_tasks, stop_upload_workers\n            n = cancel_all_upload_tasks()\n            if n:\n                logger.info("Stopping %s upload task(s)", n)\n            await stop_upload_workers()\n        except Exception:\n            logger.exception("Could not stop upload workers")\n        logger.info("Queue worker stopped")'''
if old_finally not in w:
    raise SystemExit("queue_worker shutdown block not found")
w = w.replace(old_finally, new_finally, 1)
WORKER.write_text(w)

print("COMPLETE 4-WORKER UPLOAD REBUILD APPLIED")
