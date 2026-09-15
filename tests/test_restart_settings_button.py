from pathlib import Path


def test_settings_keyboard_includes_safe_restart_button():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert 'callback_data="confirm_restart"' in source
    assert "Safe Restart" in source
