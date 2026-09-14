from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
ENGINE = Path(__file__).resolve().parent / "app" / "downloader" / "engine.py"
BROWSER_HLS = Path(__file__).resolve().parent / "app" / "downloader" / "browser_hls.py"

# ---------------------------------------------------------------------------
# Existing UI compatibility patches
# ---------------------------------------------------------------------------
text = MAIN.read_text(encoding="utf-8")

old_commands = 'COMMANDS = [("start","🚀 Start Bot V2"),("status","📊 Live download/upload progress"),("queue","📋 Running & queued tasks"),("cancel","🛑 Cancel a download/upload task"),("retry","🔁 Retry a failed task"),("clear","🧹 Clear completed/failed/cancelled tasks")'
new_commands = 'COMMANDS = [("start","🚀 Start Bot V2"),("status","📊 Live download/upload progress"),("queue","📋 Running & queued tasks"),("cancel","🛑 Cancel a download/upload task"),("retry","🔁 Retry a failed task"),("failed","❌ Show failed download links"),("clear","🧹 Clear completed/failed/cancelled tasks")'
if '("failed","❌ Show failed download links")' in text:
    print("/failed command menu already applied")
elif old_commands in text:
    text = text.replace(old_commands, new_commands, 1)
    print("/failed command menu applied")
else:
    raise SystemExit("ERROR: COMMANDS anchor not found; refusing unsafe patch")

old_clear = '    async def clear_cmd(self,client,message):\n        if not owner_only(message): return\n'
new_failed = '''    async def failed_cmd(self,client,message):
        if not owner_only(message): return
        if not self.pipeline:
            await message.reply_text("❌ V2 pipeline is offline.")
            return
        rows=await self.pipeline.db.get_tasks(statuses=("failed",),limit=10)
        if not rows:
            await message.reply_text("✅ **NO FAILED TASKS**\\n\\nThere are no failed downloads/uploads.")
            return
        lines=[f"❌ **FAILED DOWNLOADS** • {len(rows)} latest",""]
        buttons=[]
        for i,r in enumerate(rows[:6],1):
            title=str(r.get("title") or r.get("url") or r.get("id") or "Unknown")[:80]
            url=str(r.get("url") or "—")[:700]
            error=str(r.get("error") or "Unknown error")[:300]
            lines += [f"**{i}. {title}**",f"🔗 {url}",f"🆔 `{r.get('id')}`",f"⚠️ {error}",""]
            buttons.append([InlineKeyboardButton(f"🔁 Retry {i}",callback_data=f"v2:retry:{r.get('id')}")])
        if len(rows)>6:
            lines.append("Showing the latest 6 with retry buttons.")
        await message.reply_text("\\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

    async def clear_cmd(self,client,message):
        if not owner_only(message): return
'''
if 'async def failed_cmd(self,client,message):' in text:
    print("/failed handler already applied")
elif old_clear in text:
    text = text.replace(old_clear, new_failed, 1)
    print("/failed handler applied")
else:
    raise SystemExit("ERROR: clear_cmd anchor not found; refusing unsafe patch")

old_handler = 'app.add_handler(MessageHandler(bot.retry_cmd,filters.command("retry"))); app.add_handler(MessageHandler(bot.clear_cmd,filters.command("clear")));'
new_handler = 'app.add_handler(MessageHandler(bot.retry_cmd,filters.command("retry"))); app.add_handler(MessageHandler(bot.failed_cmd,filters.command("failed"))); app.add_handler(MessageHandler(bot.clear_cmd,filters.command("clear")));'
if 'MessageHandler(bot.failed_cmd,filters.command("failed"))' in text:
    print("/failed handler registration already applied")
elif old_handler in text:
    text = text.replace(old_handler, new_handler, 1)
    print("/failed handler registration applied")
else:
    raise SystemExit("ERROR: command registration anchor not found; refusing unsafe patch")

OLD = '        if data=="v2:method": await query.answer(); await self.send_method_menu(query.message); return\n'
NEW = '''        if data=="v2:method":
            await query.answer()
            current_method=(self.sessions.get(query.from_user.id) or {}).get("method") or await get_default_method()
            await query.message.edit_text(
                f"🎯 **DOWNLOAD METHOD**\\n\\nCurrent selection: **{method_label(current_method)}**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(("✅ " if m==current_method else "")+method_label(m),callback_data=f"v2:m:{m}")]
                    for m in METHODS
                ]),
            )
            return
'''
if 'current selection: **{method_label(current_method)}**' in text:
    print("UI method-menu patch already applied")
elif OLD in text:
    text = text.replace(OLD, NEW, 1)
    print("UI method-menu patch applied")
else:
    raise SystemExit("ERROR: expected v2:method callback block was not found; refusing unsafe patch")

if 'failed_rows_for_retry=await self.pipeline.db.get_tasks' in text:
    print("UI retry-button patch already applied")
else:
    old_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); counts=await self.pipeline.db.counts()'
    new_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); failed_rows_for_retry=await self.pipeline.db.get_tasks(statuses=("failed",),limit=10); counts=await self.pipeline.db.counts()'
    if old_rows not in text:
        raise SystemExit("ERROR: dashboard task query was not found; refusing unsafe patch")
    text = text.replace(old_rows, new_rows, 1)

    old_markup='reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])'
    new_markup='reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🔁 Retry {str(r.get(\'title\') or r.get(\'id\'))[:24]}",callback_data=f"v2:retry:{r.get(\'id\')}")] for r in failed_rows_for_retry]+[[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])'
    if old_markup not in text:
        raise SystemExit("ERROR: dashboard keyboard was not found; refusing unsafe patch")
    text = text.replace(old_markup, new_markup, 1)
    print("UI retry-button patch applied")

if 'data.startswith("v2:retry:")' in text:
    print("UI retry callback already applied")
else:
    old_retry_anchor='        if data=="v2:clear":'
    new_retry_anchor='''        if data.startswith("v2:retry:"):
            tid=data.split(":",2)[2]
            m=await get_default_method()
            ok=bool(self.pipeline and await self.pipeline.retry(tid,method=m))
            await query.answer("🔁 Retried" if ok else "⚠️ Task is no longer retryable",show_alert=not ok)
            await self.render_dashboard(query.message.chat.id,query.message.id,True)
            return
        if data=="v2:clear":'''
    if old_retry_anchor not in text:
        raise SystemExit("ERROR: v2:clear callback anchor was not found; refusing unsafe patch")
    text = text.replace(old_retry_anchor, new_retry_anchor, 1)
    print("UI retry callback patch applied")

# Fix the status/refresh callback so it always opens or refreshes the real
# dashboard message instead of trying to render into an unrelated UI message.
old_status = '        if data=="v2:status": await query.answer(); self.dashboard_messages[query.message.chat.id]=query.message.id; await self.render_dashboard(query.message.chat.id,query.message.id,True); return\n'
new_status = '''        if data=="v2:status":
            await query.answer("📊 Refreshing status…")
            await self.show_dashboard(query.message.chat.id, query.message)
            return
'''
if 'await self.show_dashboard(query.message.chat.id, query.message)' in text:
    print("status callback patch already applied")
elif old_status in text:
    text = text.replace(old_status, new_status, 1)
    print("status callback patch applied")
else:
    raise SystemExit("ERROR: v2:status callback anchor not found; refusing unsafe patch")

MAIN.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Browser HLS production integration
# ---------------------------------------------------------------------------
if not BROWSER_HLS.is_file():
    raise SystemExit("ERROR: browser_hls.py is missing; refusing unsafe integration")

engine = ENGINE.read_text(encoding="utf-8")
import_anchor = 'from app.core.task import TaskCancelled, TaskContext\n'
import_line = 'from app.downloader.browser_hls import BrowserHLSDownloader\n'
if import_line not in engine:
    if import_anchor not in engine:
        raise SystemExit("ERROR: downloader import anchor not found; refusing unsafe Browser HLS patch")
    engine = engine.replace(import_anchor, import_anchor + import_line, 1)
    print("Browser HLS import integrated")

old_browser = '''    async def _browser(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        stream = await self._discover_hls(url, task, {"User-Agent": "Mozilla/5.0"}, progress)
        if not stream: raise DownloadError("Browser could not discover media stream")
        await self._ffmpeg(stream, output, task, progress)
'''
new_browser = '''    async def _browser(self, url: str, output: Path, task: TaskContext, progress: ProgressCallback | None) -> None:
        """Browser HLS: browser discovers/authenticates the stream, then fetches media concurrently."""
        downloader = BrowserHLSDownloader(self.output_dir)
        try:
            await downloader.download(url, output, task, progress)
        except BrowserHLSError as exc:
            raise DownloadError(str(exc)) from exc
'''
if new_browser in engine:
    print("Browser HLS engine integration already applied")
elif old_browser in engine:
    engine = engine.replace(old_browser, new_browser, 1)
    engine = engine.replace('from app.downloader.browser_hls import BrowserHLSDownloader\n', 'from app.downloader.browser_hls import BrowserHLSDownloader, BrowserHLSError\n', 1)
    print("Browser HLS engine integration applied")
else:
    raise SystemExit("ERROR: existing _browser method anchor not found; refusing unsafe Browser HLS patch")

ENGINE.write_text(engine, encoding="utf-8")

# Validate the new standalone engine before the service is allowed to start.
import py_compile
py_compile.compile(str(BROWSER_HLS), doraise=True)
py_compile.compile(str(ENGINE), doraise=True)
print("Browser HLS syntax validation passed")
