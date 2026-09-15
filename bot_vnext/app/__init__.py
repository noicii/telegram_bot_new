"""Clean Telegram bot vNext application package."""
from datetime import datetime
from pyrogram.types import Message

_original_dashboard_edit_text = Message.edit_text
_dashboard_refresh_revision = 0


async def _dashboard_safe_edit_text(self, text, *args, **kwargs):
    """Keep the single live dashboard editable even when task data is unchanged."""
    global _dashboard_refresh_revision
    if isinstance(text, str) and text.startswith("📊 **LIVE DOWNLOAD / UPLOAD**"):
        _dashboard_refresh_revision += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        text = f"{text}\n\n🕒 Updated: {stamp} • Refresh #{_dashboard_refresh_revision}"
    return await _original_dashboard_edit_text(self, text, *args, **kwargs)


Message.edit_text = _dashboard_safe_edit_text
