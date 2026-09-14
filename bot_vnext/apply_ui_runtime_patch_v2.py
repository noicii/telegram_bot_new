from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
ENGINE = ROOT / "app" / "downloader" / "engine.py"
BROWSER_HLS = ROOT / "app" / "downloader" / "browser_hls.py"


def replace_once(text: str, old: str, new: str, label: str, already: str | None = None) -> str:
    if already and already in text:
        print(already)
        return text
    if old not in text:
        raise SystemExit(f"ERROR: {label} anchor not found; refusing unsafe patch")
    print(label)
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# UI compatibility patches. Every operation is idempotent and fail-closed.
# ---------------------------------------------------------------------------
text = MAIN.read_text(encoding="utf-8")
text = replace_once(
    text,
    'COMMANDS = [("start","🚀 Start Bot V2"),("status","📊 Live download/upload progress"),("queue","📋 Running & queued tasks"),("cancel","🛑 Cancel a download/upload task"),("retry","🔁 Retry a failed task"),("clear","🧹 Clear completed/failed/cancelled tasks")',
    'COMMANDS = [("start","🚀 Start Bot V2"),("status","📊 Live download/upload progress"),("queue","📋 Running & queued tasks"),("cancel","🛑 Cancel a download/upload task"),("retry","🔁 Retry a failed task"),("failed","❌ Show failed download links"),("clear","🧹 Clear completed/failed/cancelled tasks")',
    "/failed command menu applied",
    "/failed command menu already applied",
)

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
text = replace_once(text, old_clear, new_failed, "/failed handler applied", "/failed handler already applied")

old_handler = 'app.add_handler(MessageHandler(bot.retry_cmd,filters.command("retry"))); app.add_handler(MessageHandler(bot.clear_cmd,filters.command("clear")));'
new_handler = 'app.add_handler(MessageHandler(bot.retry_cmd,filters.command("retry"))); app.add_handler(MessageHandler(bot.failed_cmd,filters.command("failed"))); app.add_handler(MessageHandler(bot.clear_cmd,filters.command("clear")));'
text = replace_once(text, old_handler, new_handler, "/failed handler registration applied", "/failed handler registration already applied")

old_method_cb = '        if data=="v2:method": await query.answer(); await self.send_method_menu(query.message); return\n'
new_method_cb = '''        if data=="v2:method":
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
text = replace_once(text, old_method_cb, new_method_cb, "UI method-menu patch applied", "UI method-menu patch already applied")

if 'failed_rows_for_retry=await self.pipeline.db.get_tasks' not in text:
    old_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); counts=await self.pipeline.db.counts()'
    new_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); failed_rows_for_retry=await self.pipeline.db.get_tasks(statuses=("failed",),limit=10); counts=await self.pipeline.db.counts()'
    text = replace_once(text, old_rows, new_rows, "UI retry-button task query applied")
    old_markup='reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])'
    new_markup='reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"🔁 Retry {str(r.get(\'title\') or r.get(\'id\'))[:24]}",callback_data=f"v2:retry:{r.get(\'id\')}")] for r in failed_rows_for_retry]+[[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])'
    text = replace_once(text, old_markup, new_markup, "UI retry-button keyboard applied")
else:
    print("UI retry-button patch already applied")

if 'data.startswith("v2:retry:")' not in text:
    old_retry='        if data=="v2:clear":'
    new_retry='''        if data.startswith("v2:retry:"):
            tid=data.split(":",2)[2]
            m=await get_default_method()
            ok=bool(self.pipeline and await self.pipeline.retry(tid,method=m))
            await query.answer("🔁 Retried" if ok else "⚠️ Task is no longer retryable",show_alert=not ok)
            await self.render_dashboard(query.message.chat.id,query.message.id,True)
            return
        if data=="v2:clear":'''
    text = replace_once(text, old_retry, new_retry, "UI retry callback patch applied")
else:
    print("UI retry callback patch already applied")

old_status = '        if data=="v2:status": await query.answer(); self.dashboard_messages[query.message.chat.id]=query.message.id; await self.render_dashboard(query.message.chat.id,query.message.id,True); return\n'
new_status = '''        if data=="v2:status":
            await query.answer("📊 Refreshing status…")
            await self.show_dashboard(query.message.chat.id, query.message)
            return
'''
text = replace_once(text, old_status, new_status, "status callback patch applied", "status callback patch already applied")

MAIN.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Browser HLS production integration.
# ---------------------------------------------------------------------------
if not BROWSER_HLS.is_file():
    raise SystemExit("ERROR: browser_hls.py is missing; refusing unsafe integration")
engine = ENGINE.read_text(encoding="utf-8")
if "from app.downloader.browser_hls import BrowserHLSDownloader" not in engine:
    anchor='from app.core.task import TaskCancelled, TaskContext\n'
    engine = replace_once(engine, anchor, anchor+'from app.downloader.browser_hls import BrowserHLSDownloader, BrowserHLSError\n', "Browser HLS import integrated")
else:
    if "BrowserHLSError" not in engine:
        engine=engine.replace('from app.downloader.browser_hls import BrowserHLSDownloader\n','from app.downloader.browser_hls import BrowserHLSDownloader, BrowserHLSError\n',1)
    print("Browser HLS import already integrated")

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
    engine=engine.replace(old_browser,new_browser,1)
    print("Browser HLS engine integration applied")
else:
    raise SystemExit("ERROR: existing _browser method anchor not found; refusing unsafe Browser HLS patch")
ENGINE.write_text(engine, encoding="utf-8")

# ---------------------------------------------------------------------------
# Browser HLS hardening: broader discovery, repeated playback, URL fallback,
# and explicit temporary-directory cleanup.
# ---------------------------------------------------------------------------
browser=BROWSER_HLS.read_text(encoding="utf-8")
old_gate='            if response.status != 200 or (".m3u8" not in low and "mpegurl" not in ct):\n                return\n'
new_gate='''            manifest_hint = any(token in low for token in (".m3u8", "manifest", "playlist", "master"))
            manifest_ct = any(token in ct for token in ("mpegurl", "m3u8", "vnd.apple.mpegurl"))
            try:
                content_length = int(response.headers.get("content-length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            text_candidate = ct.startswith(("text/", "application/json", "application/octet-stream")) and (not content_length or content_length <= MAX_PLAYLIST_BYTES)
            if response.status != 200 or not (manifest_hint or manifest_ct or text_candidate):
                return
'''
browser=replace_once(browser,old_gate,new_gate,"Browser HLS broad playlist response matching applied","Browser HLS broad playlist response matching already applied")

old_loop='''        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not captured:
            task.check_cancelled()
            await self._report(progress, 1.0, 0, None, 0.0, {"browser_hls_stage": "discovering"})
            await asyncio.sleep(0.5)
'''
new_loop='''        deadline = time.monotonic() + 45
        tick = 0
        while time.monotonic() < deadline and not captured:
            task.check_cancelled()
            if tick % 2 == 0:
                await self._trigger_playback(page, task)
            await self._report(progress, 1.0, 0, None, 0.0, {"browser_hls_stage": "discovering"})
            tick += 1
            await asyncio.sleep(0.5)
'''
browser=replace_once(browser,old_loop,new_loop,"Browser HLS repeated playback trigger applied","Browser HLS repeated playback trigger already applied")

old_candidate='                        if isinstance(item, str) and ".m3u8" in item and item not in seen:\n'
new_candidate='                        if isinstance(item, str) and item.startswith(("http://", "https://")) and item not in seen:\n'
browser=replace_once(browser,old_candidate,new_candidate,"Browser HLS config URL fallback applied","Browser HLS config URL fallback already applied")

if 'shutil.rmtree(work, ignore_errors=True)' not in browser:
    old_cleanup='''                try:
                    await browser.close()
                except Exception:
                    pass
'''
    new_cleanup='''                try:
                    await browser.close()
                except Exception:
                    pass
                shutil.rmtree(work, ignore_errors=True)
                task.temp_paths.discard(work)
'''
    browser=replace_once(browser,old_cleanup,new_cleanup,"Browser HLS work-directory cleanup applied")
else:
    print("Browser HLS work-directory cleanup already applied")
BROWSER_HLS.write_text(browser,encoding="utf-8")

# ---------------------------------------------------------------------------
# Segmented realtime dashboard contract.
# ---------------------------------------------------------------------------
main=MAIN.read_text(encoding="utf-8")
old_hls_ui='lines.append(f"🧩 HLS: **{hls_done} / {hls_total} segments** • **{hls_pct:.0f}%**")\n                        lines.append(f"📥 Downloaded: **{fmt_bytes(cur)}** • ⚡ {fmt_speed(sp)} • ETA {fmt_eta(eta)}")'
new_hls_ui='lines.append(f"🧩 Segments: **{hls_done} / {hls_total}** • **{hls_pct:.0f}%**")\n                        lines.append(f"💾 Data: **{fmt_bytes(cur)}** • ⚡ {fmt_speed(sp)} • ETA {fmt_eta(eta)}")'
if '🧩 Segments:' in main:
    print("Segment realtime dashboard format already applied")
elif old_hls_ui in main:
    main=main.replace(old_hls_ui,new_hls_ui,1)
    MAIN.write_text(main,encoding="utf-8")
    print("Segment realtime dashboard format applied")
else:
    raise SystemExit("ERROR: segmented dashboard UI anchor not found; refusing unsafe patch")

# ---------------------------------------------------------------------------
# Fail-closed syntax validation. This exact marker is protected by release_guard.
# ---------------------------------------------------------------------------
for path in (MAIN, ENGINE, BROWSER_HLS):
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise SystemExit(f"ERROR: syntax validation failed for {path.name}: {exc}")
print("Browser HLS syntax validation passed")
