from pathlib import Path


def test_settings_keyboard_includes_safe_restart_button():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert 'callback_data="confirm_restart"' in source
    assert "Safe Restart" in source


def test_restart_is_graceful_and_scoped_to_handlers():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "await stop_queue_worker()" in source
    assert "await app.stop()" in source
    assert "_handlers.os._exit = _safe_restart_exit" in source
