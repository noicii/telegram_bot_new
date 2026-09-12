from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main = ROOT / "bot_vnext" / "main.py"
engine = ROOT / "bot_vnext" / "app" / "downloader" / "engine.py"

s = main.read_text()

# Add dashboard lock if missing.
if "self.dashboard_locks: dict[int, asyncio.Lock]" not in s:
    needle = '        self.dashboard_messages: dict[int, int] = {}\n'
    if needle not in s:
        raise SystemExit("dashboard init block not found")
    s = s.replace(needle, needle + '        self.dashboard_locks: dict[int, asyncio.Lock] = {}\n', 1)

# Clean provider/site names from the final media title.
if "def _clean_media_title" not in s:
    marker = '    async def enqueue_selected(self, message, session, selected):\n'
    helper = '''    @staticmethod\n    def _clean_media_title(title: str, provider: str | None = None, url: str | None = None) -> str:\n        import re\n        from urllib.parse import urlparse\n        text = str(title or "Video").strip()\n        values = [str(provider or "").strip(), urlparse(str(url or "")).netloc]\n        for value in values:\n            if not value:\n                continue\n            host = value.split(":", 1)[0]\n            text = re.sub(rf"\\s*\\[?{re.escape(host)}\\]?\\s*", " ", text, flags=re.I)\n            text = re.sub(rf"\\s*\\(?{re.escape(host)}\\)?\\s*[-|:]?\\s*", " ", text, flags=re.I)\n        text = re.sub(r"\\s*\\[(?:KahaniPlay|upfiles(?:\\.com)?|send(?:\\.now)?|lulu(?:vdo|vid|vdoo|video|stream)?(?:\\.com)?)\\]", "", text, flags=re.I)\n        text = re.sub(r"\\s+", " ", text).strip(" -|:")\n        return text or "Video"\n\n'''
    if marker not in s:
        raise SystemExit("enqueue marker not found")
    s = s.replace(marker, helper + marker, 1)

# Replace enqueue title/caption payload when the old payload is present.
old_payload = '''            filename = sanitize_filename(item.get("title") or f"{task_id}.mp4")\n            if not Path(filename).suffix: filename += ".mp4"\n            await set_method(item.get("url") or "", selected_method)\n            metadata = {"source_url": item.get("source_url"), "provider": item.get("source"), "resolution": item.get("resolution"), "cookiefile": str(COOKIES_PATH) if COOKIES_PATH.is_file() else None, "download_method": selected_method, "headers": {"Referer": item.get("source_url")} if item.get("source_url") else {}}\n            metadata = {k: v for k, v in metadata.items() if v}\n            payload = {"task_type": "download", "url": item["url"], "filename": filename, "chat_id": message.chat.id, "caption": item.get("title") or session["series"], "thumbnail": str(THUMB_PATH) if THUMB_PATH.is_file() else None, "mode": "video", "title": item.get("title"), "provider": item.get("source"), "resolution": item.get("resolution"), "metadata": metadata}\n'''
new_payload = '''            media_title = self._clean_media_title(item.get("title") or session["series"], item.get("source"), item.get("url"))\n            filename = sanitize_filename(media_title or f"{task_id}.mp4")\n            if not Path(filename).suffix: filename += ".mp4"\n            await set_method(item.get("url") or "", selected_method)\n            metadata = {"source_url": item.get("source_url"), "provider": item.get("source"), "resolution": item.get("resolution"), "cookiefile": str(COOKIES_PATH) if COOKIES_PATH.is_file() else None, "download_method": selected_method, "headers": {"Referer": item.get("source_url")} if item.get("source_url") else {}}\n            metadata = {k: v for k, v in metadata.items() if v}\n            payload = {"task_type": "download", "url": item["url"], "filename": filename, "chat_id": message.chat.id, "caption": media_title, "thumbnail": str(THUMB_PATH) if THUMB_PATH.is_file() else None, "mode": "video", "title": media_title, "provider": item.get("source"), "resolution": item.get("resolution"), "metadata": metadata}\n'''
if old_payload in s:
    s = s.replace(old_payload, new_payload, 1)

# Replace live dashboard and progress callback as one block.
start = s.index('    async def _render_live_dashboard(self, chat_id: int):')
end = s.index('    async def on_complete', start)
block = '''    @staticmethod\n    def _progress_bar(percent: float, width: int = 14) -> str:\n        pct = max(0.0, min(100.0, float(percent or 0.0)))\n        filled = int(round(width * pct / 100.0))\n        return "[" + "█" * filled + "░" * (width - filled) + "]"\n\n    @staticmethod\n    def _mb(value) -> float:\n        return float(value or 0) / 1048576.0\n\n    async def _render_live_dashboard(self, chat_id: int):\n        message_id = self.dashboard_messages.get(chat_id)\n        if not message_id or not self.pipeline:\n            return\n        rows = await self.pipeline.db.get_tasks(statuses=("queued", "downloading", "uploading"), limit=25)\n        counts = await self.pipeline.db.counts()\n        d, u = counts.get("download", {}), counts.get("upload", {})\n        lines = [\n            "📊 **LIVE DOWNLOAD / UPLOAD**",\n            "",\n            f"⬇️ Downloads: **{self.pipeline.download.active_workers()}/2 active** • {d.get('queued', 0)} queued",\n            f"⬆️ Uploads: **{self.pipeline.upload.active_workers()}/4 active** • {u.get('queued', 0)} queued",\n            "",\n        ]\n        if not rows:\n            lines += [\n                "✅ No active tasks.",\n                f"Completed: {d.get('completed',0) + u.get('completed',0)} • Failed: {d.get('failed',0) + u.get('failed',0)} • Cancelled: {d.get('cancelled',0) + u.get('cancelled',0)}",\n            ]\n        else:\n            for row in rows[:10]:\n                task_id = str(row.get("id"))\n                kind = "⬇️" if row.get("task_type") == "download" else "⬆️"\n                is_upload = row.get("task_type") == "upload"\n                status = str(row.get("status") or "queued").upper()\n                pct = float(row.get("progress") or 0)\n                method = (row.get("metadata") or {}).get("download_method") or "auto"\n                title = self._clean_media_title(row.get("title") or row.get("url") or task_id, row.get("provider"), row.get("url"))\n                lines.append(f"{kind} **{status}** • **{pct:.1f}%**" + (f" • {method_label(method)}" if not is_upload else ""))\n                lines.append(f"🎬 **{title}**")\n                lines.append(f"{self._progress_bar(pct)} **{pct:.1f}%**")\n                live = self.progress_cache.get(task_id)\n                if live and len(live) >= 6:\n                    _, _, current, total, speed, eta = live\n                    if is_upload:\n                        if total and total > 0:\n                            lines.append(f"📦 Processed: **{self._mb(current):.1f} / {self._mb(total):.1f} MB**")\n                    else:\n                        if total and total > 0:\n                            lines.append(f"📦 Segments: **{int(current or 0)}/{int(total)}**")\n                    if speed:\n                        lines.append(f"⚡ **{self._mb(speed):.2f} MB/s**")\n                    if eta and eta > 0:\n                        lines.append(f"⏱ ETA: **{int(eta)}s**")\n                elif row.get("speed"):\n                    lines.append(f"⚡ **{self._mb(row.get('speed')):.2f} MB/s**")\n                lines.append("")\n        text = "\\n".join(lines)\n        lock = self.dashboard_locks.setdefault(chat_id, asyncio.Lock())\n        async with lock:\n            try:\n                msg = await self.client.get_messages(chat_id, message_id)\n                if msg and msg.text != text:\n                    await msg.edit_text(\n                        text,\n                        reply_markup=InlineKeyboardMarkup([[\n                            InlineKeyboardButton("🔄 Refresh", callback_data="v2:live_refresh"),\n                            InlineKeyboardButton("📋 Queue", callback_data="v2:queue"),\n                        ]]),\n                    )\n            except Exception:\n                logger.debug("live dashboard edit failed", exc_info=True)\n\n    async def on_progress(self, kind, task_id, percent=None, current=None, total=None, speed=None, eta=None):\n        task_id = str(task_id)\n        now = asyncio.get_running_loop().time()\n        last = self.progress_cache.get(task_id)\n        if last and now - last[0] < 1.25:\n            return\n        self.progress_cache[task_id] = (now, percent, current, total, speed, eta)\n        try:\n            row = await self.pipeline.db.get_task(task_id) if self.pipeline else None\n            if row and row.get("chat_id"):\n                await self._render_live_dashboard(int(row["chat_id"]))\n        except Exception:\n            logger.debug("live dashboard update failed", exc_info=True)\n\n'''
s = s[:start] + block + s[end:]

# Ensure the live-refresh callback exists.
if 'data == "v2:live_refresh"' not in s:
    needle = '        if data == "v2:status": await query.answer(); await self.send_status(query.message); return\n'
    if needle not in s:
        raise SystemExit("status callback not found")
    s = s.replace(needle, needle + '        if data == "v2:live_refresh":\n            await query.answer("Refreshing…")\n            await self._render_live_dashboard(int(query.message.chat.id))\n            return\n', 1)

main.write_text(s)

# HLS Multi: report segment count as current/total, plus ETA.
e = engine.read_text()
old = 'await self._report(progress, completed * 100 / len(segments), total_bytes, None, total_bytes / elapsed)'
new = '''rate = total_bytes / elapsed\n                                eta = ((len(segments) - completed) * elapsed / completed) if completed else None\n                                await self._report(progress, completed * 100 / len(segments), completed, len(segments), rate, eta)'''
if old in e:
    e = e.replace(old, new, 1)
engine.write_text(e)

print("UPLOAD DASHBOARD FIX + CLEAN TITLES + DOWNLOAD SEGMENTS APPLIED")
