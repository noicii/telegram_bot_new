from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
text = MAIN.read_text(encoding="utf-8")

OLD = '        if data=="v2:method": await query.answer(); await self.send_method_menu(query.message); return\n'
NEW = '''        if data=="v2:method":
            await query.answer()
            current_method=s.get("method") or await get_default_method()
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
    text=text.replace(OLD,NEW,1)
    print("UI method-menu patch applied")
else:
    raise SystemExit("ERROR: expected v2:method callback block was not found; refusing unsafe patch")

# Add direct Retry buttons to the live dashboard for failed tasks.
if 'failed_rows_for_retry=await self.pipeline.db.get_tasks' in text:
    print("UI retry-button patch already applied")
else:
    old_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); counts=await self.pipeline.db.counts()'
    new_rows='rows=await self.pipeline.db.get_tasks(statuses=("queued","downloading","uploading"),limit=25); failed_rows_for_retry=await self.pipeline.db.get_tasks(statuses=("failed",),limit=10); counts=await self.pipeline.db.counts()'
    if old_rows not in text:
        raise SystemExit("ERROR: dashboard task query was not found; refusing unsafe patch")
    text=text.replace(old_rows,new_rows,1)

    old_markup='reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="v2:status"),InlineKeyboardButton("📋 Queue",callback_data="v2:queue")]])'
    new_markup="""reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f\"🔁 Retry {str(r.get('title') or r.get('id'))[:24]}\",callback_data=f\"v2:retry:{r.get('id')}\")] for r in failed_rows_for_retry]+[[InlineKeyboardButton(\"🔄 Refresh\",callback_data=\"v2:status\"),InlineKeyboardButton(\"📋 Queue\",callback_data=\"v2:queue\")]])"""
    if old_markup not in text:
        raise SystemExit("ERROR: dashboard keyboard was not found; refusing unsafe patch")
    text=text.replace(old_markup,new_markup,1)
    print("UI retry-button patch applied")

# Add callback handler for direct dashboard retry buttons.
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
    text=text.replace(old_retry_anchor,new_retry_anchor,1)
    print("UI retry callback patch applied")

MAIN.write_text(text,encoding="utf-8")
