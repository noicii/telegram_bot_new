from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bot_vnext" / "main.py"
ENGINE = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"

# ---------------- HLS: aggressive + resilient ----------------
e = ENGINE.read_text()

# Final agreed performance envelope.
e, n = re.subn(r"asyncio\.Semaphore\(\d+\)", "asyncio.Semaphore(16)", e, count=1)
if n != 1:
    raise RuntimeError("HLS semaphore anchor not found")
e, n = re.subn(r"aiohttp\.TCPConnector\(limit=\d+, limit_per_host=\d+", "aiohttp.TCPConnector(limit=250, limit_per_host=80", e, count=1)
if n != 1:
    raise RuntimeError("HLS connector anchor not found")

# Make HLS discovery retry the complete browser discovery path before declaring failure.
old = '''        stream_url = url if ".m3u8" in url.lower() else await self._discover_hls(url, task, headers, progress)\n        if not stream_url: raise DownloadError("HLS stream URL could not be discovered")'''
new = '''        stream_url = url if ".m3u8" in url.lower() else None\n        if not stream_url:\n            last_discovery_error = None\n            for discovery_attempt in range(1, 4):\n                task.check_cancelled()\n                try:\n                    stream_url = await self._discover_hls(url, task, headers, progress)\n                    if stream_url:\n                        break\n                except TaskCancelled:\n                    raise\n                except Exception as exc:\n                    last_discovery_error = exc\n                    logger.warning("task=%s HLS discovery attempt=%s failed: %s", task.task_id, discovery_attempt, exc)\n                if discovery_attempt < 3:\n                    await asyncio.sleep(1.5 * discovery_attempt)\n        if not stream_url:\n            raise DownloadError("HLS stream URL could not be discovered after 3 discovery passes")'''
if old not in e:
    raise RuntimeError("HLS discovery anchor not found")
e = e.replace(old, new, 1)

# Give the browser a little more time to initialize dynamic players, while keeping cancellation responsive.
e = e.replace('await page.goto(candidate, wait_until="domcontentloaded", timeout=30000)', 'await page.goto(candidate, wait_until="domcontentloaded", timeout=45000)', 1)
e = e.replace('for tick in range(25):', 'for tick in range(35):', 1)

# HLS segment retries: shorter backoff and explicit status logging; preserve the 3-attempt task policy.
old_fetch = '''                            async with session.get(segment_url) as response:\n                                response.raise_for_status()\n                                data = await response.read()'''
new_fetch = '''                            async with session.get(segment_url) as response:\n                                if response.status in {403, 404, 410}:\n                                    raise DownloadError(f"HTTP {response.status} for HLS segment {index + 1}")\n                                response.raise_for_status()\n                                data = await response.read()\n                                if not data:\n                                    raise DownloadError(f"Empty HLS segment {index + 1}")'''
if old_fetch not in e:
    raise RuntimeError("HLS segment fetch anchor not found")
e = e.replace(old_fetch, new_fetch, 1)

ENGINE.write_text(e)

# ---------------- Per-task download method selection ----------------
m = MAIN.read_text()

# Keep the existing global method, but add an independent method map to every crawl session.
old_session = '''session = {"items": items, "page": 0, "selected": set(), "series": series, "source_url": url, "message_id": wait.id, "method": saved_method}'''
new_session = '''session = {"items": items, "page": 0, "selected": set(), "series": series, "source_url": url, "message_id": wait.id, "method": saved_method, "methods": {}}'''
if old_session not in m:
    raise RuntimeError("crawl session anchor not found")
m = m.replace(old_session, new_session, 1)

# Add a compact per-task method button under each episode. It cycles only that episode's method.
old_buttons = '''buttons = [[InlineKeyboardButton(self._button_label(items[i], i, selected), callback_data=f"v2:t:{i}")] for i in range(start, end)]'''
new_buttons = '''buttons = []\n        for i in range(start, end):\n            buttons.append([InlineKeyboardButton(self._button_label(items[i], i, selected), callback_data=f"v2:t:{i}"), InlineKeyboardButton(f"🎯 {method_label(session.get('methods', {}).get(i, session.get('method', 'auto')))}", callback_data=f"v2:tm:{i}")])'''
if old_buttons not in m:
    raise RuntimeError("selection button anchor not found")
m = m.replace(old_buttons, new_buttons, 1)

# Add the callback before the existing global method callback.
marker = '''        if data == "v2:method":\n            session["method"] = next_method(session.get("method", "auto")); await query.answer(f"Method: {method_label(session['method'])}"); await self.render_selection(query.message, session); return'''
replacement = '''        if data.startswith("v2:tm:"):\n            index = int(data.rsplit(":", 1)[1])\n            methods = session.setdefault("methods", {})\n            current = methods.get(index, session.get("method", "auto"))\n            methods[index] = next_method(current)\n            await query.answer(f"Episode method: {method_label(methods[index])}")\n            await self.render_selection(query.message, session)\n            return\n        if data == "v2:method":\n            session["method"] = next_method(session.get("method", "auto")); await query.answer(f"Method: {method_label(session['method'])}"); await self.render_selection(query.message, session); return'''
if marker not in m:
    raise RuntimeError("method callback anchor not found")
m = m.replace(marker, replacement, 1)

# Use the per-item method when enqueueing, and persist it into each task's metadata.
old_enqueue = '''selected_method = session.get("method", await get_default_method())\n        dashboard = await message.edit_text(f"🚀 **Queued {len(selected)} task(s)**\\n🎬 {session['series']}\\n🎯 Method: {method_label(selected_method)}\\n\\n⬇️ Downloads: 2\\n⬆️ Uploads: 4\\n\\n⏳ Starting download…")'''
new_enqueue = '''selected_method = session.get("method", await get_default_method())\n        task_methods = session.get("methods", {})\n        dashboard = await message.edit_text(f"🚀 **Queued {len(selected)} task(s)**\\n🎬 {session['series']}\\n🎯 Default: {method_label(selected_method)}\\n\\n⬇️ Downloads: 2\\n⬆️ Uploads: 6\\n\\n⏳ Starting download…")'''
if old_enqueue not in m:
    raise RuntimeError("enqueue header anchor not found")
m = m.replace(old_enqueue, new_enqueue, 1)

# Replace only the selected-method references inside enqueue_selected's task loop.
start = m.find("    async def enqueue_selected")
end = m.find("    async def ", start + 10)
if start < 0:
    raise RuntimeError("enqueue_selected not found")
if end < 0:
    end = len(m)
block = m[start:end]
old_line = '            item = session["items"][index]; task_id = uuid.uuid4().hex\n'
new_line = '            item = session["items"][index]; task_id = uuid.uuid4().hex\n            task_method = task_methods.get(index, selected_method)\n'
if old_line not in block:
    raise RuntimeError("enqueue loop anchor not found")
block = block.replace(old_line, new_line, 1)
block = block.replace('await set_method(item.get("url") or "", selected_method)', 'await set_method(item.get("url") or "", task_method)', 1)
block = block.replace('"download_method": selected_method,', '"download_method": task_method,', 1)
m = m[:start] + block + m[end:]

# Settings/health text should reflect the experimental 6-worker upload configuration.
m = m.replace('"⬆️ Upload workers: **4**', '"⬆️ Upload workers: **6**', 1)
m = m.replace('"Upload workers", bool(self.pipeline and len(self.pipeline.upload.running_tasks) <= 4)', '"Upload workers", bool(self.pipeline and len(self.pipeline.upload.running_tasks) <= 6)', 1)
m = m.replace('f"⬆️ Active uploads: {self.pipeline.upload.active_workers()}/4"', 'f"⬆️ Active uploads: {self.pipeline.upload.active_workers()}/6"', 1)

MAIN.write_text(m)

print("PER-TASK METHOD + ROBUST HLS UPGRADE APPLIED")
print("Each episode can now use its own download method")
print("HLS: 16 segments/task | 32 max across 2 workers")
print("HLS network: 250 total / 80 per-host")
print("HLS discovery: 3 full browser passes + dynamic-player wait")
print("Upload: 6 workers (Pyrogram transmission setting preserved)")
