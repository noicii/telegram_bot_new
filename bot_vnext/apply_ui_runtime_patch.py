from pathlib import Path

MAIN = Path(__file__).resolve().parent / "main.py"
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

text = MAIN.read_text(encoding="utf-8")
if 'current selection: **{method_label(current_method)}**' in text:
    print("UI method-menu patch already applied")
elif OLD in text:
    MAIN.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("UI method-menu patch applied")
else:
    raise SystemExit("ERROR: expected v2:method callback block was not found; refusing unsafe patch")
