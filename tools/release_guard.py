#!/usr/bin/env python3
"""Fail-closed checks for production Bot V2 releases.

This is intentionally independent of documentation.  update.sh runs it after
any compatibility/build step, so a future change that regresses protected
behaviour stops the deployment before systemd is started.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "bot_vnext"


def fail(message: str) -> None:
    raise SystemExit(f"RELEASE GUARD FAILED: {message}")


def read(path: Path) -> str:
    if not path.is_file():
        fail(f"required file missing: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def tracked_files() -> list[Path]:
    out = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT, text=False
    ).decode("utf-8", errors="replace")
    return [ROOT / item for item in out.split("\0") if item]


def main() -> None:
    main_py = read(V2 / "main.py")
    engine = read(V2 / "app" / "downloader" / "engine.py")
    browser = read(V2 / "app" / "downloader" / "browser_hls.py")
    patcher = read(V2 / "apply_ui_runtime_patch.py")
    update = read(ROOT / "update.sh")
    gitignore = read(ROOT / ".gitignore")

    # Secrets must stay outside Git.
    tracked = tracked_files()
    tracked_names = {p.relative_to(ROOT).as_posix() for p in tracked}
    if ".env" in tracked_names:
        fail(".env is tracked by Git")
    if ".env" not in gitignore.splitlines():
        fail(".env is not protected by .gitignore")

    # Python must parse cleanly before deployment.
    for path in [V2 / "main.py", V2 / "apply_ui_runtime_patch.py", V2 / "app" / "pipeline.py", V2 / "app" / "downloader" / "engine.py", V2 / "app" / "downloader" / "browser_hls.py", V2 / "app" / "queue" / "upload_manager.py", V2 / "app" / "uploader" / "engine.py"]:
        try:
            ast.parse(read(path), filename=str(path))
        except SyntaxError as exc:
            fail(f"syntax error in {path.relative_to(ROOT)}: {exc}")

    # Protected owner-only boundary.
    if "def owner_only(message)" not in main_py or "message.from_user.id == OWNER_ID" not in main_py:
        fail("owner-only message boundary missing")
    if "def callback_owner(query)" not in main_py or "query.from_user.id == OWNER_ID" not in main_py:
        fail("owner-only callback boundary missing")
    if "if not callback_owner(query)" not in main_py:
        fail("callback owner check missing")

    # The method menu must edit the current message. A regression to
    # send_method_menu() in the v2:method callback creates duplicate menus.
    method_match = re.search(r'if data==["\']v2:method["\']:(.*?)(?=\n\s*if data==|\n\s*s=self\.sessions|\Z)', main_py, re.S)
    if not method_match:
        fail("v2:method callback missing")
    method_block = method_match.group(1)
    if "send_method_menu" in method_block or "reply_text" in method_block:
        fail("method callback can create a duplicate/new message")
    if "edit_text" not in method_block:
        fail("method callback does not edit the existing message")

    # Status refresh must use the canonical dashboard message flow.
    status_match = re.search(r'if data==["\']v2:status["\']:(.*?)(?=\n\s*if data==|\Z)', main_py, re.S)
    if not status_match or "show_dashboard" not in status_match.group(1):
        fail("status callback is not wired to the canonical dashboard")

    # HLS performance contract.
    if "asyncio.Semaphore(16)" not in engine and "Semaphore(16)" not in engine:
        fail("HLS segment concurrency is not locked to 16")
    if '"hls_completed"' not in engine or '"hls_total"' not in engine:
        fail("HLS segment progress fields missing")
    if "BrowserHLSDownloader" not in engine:
        fail("Browser HLS integration missing")
    if "browser_context.request" not in browser:
        fail("Browser HLS is not using browser-context authenticated requests")
    if "shutil.rmtree(work" not in browser:
        fail("Browser HLS work-directory cleanup missing")

    # UI contract: segmented downloads must expose segments as the primary
    # progress metric, not only a byte counter.
    if "🧩 Segments:" not in main_py:
        fail("segmented realtime UI does not show segment count")
    if "hls_total" not in main_py or "hls_completed" not in main_py:
        fail("segmented realtime UI is not wired to HLS progress")

    # Fail closed on common dangerous Python constructs in production code.
    source_paths = [p for p in V2.rglob("*.py") if "__pycache__" not in p.parts]
    dangerous = [
        (r"\beval\s*\(", "eval()"),
        (r"\bexec\s*\(", "exec()"),
        (r"\bpickle\.(loads|load)\s*\(", "pickle deserialization"),
        (r"\bshell\s*=\s*True\b", "subprocess shell=True"),
    ]
    for path in source_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in dangerous:
            if re.search(pattern, text):
                fail(f"unsafe construct {label} found in {path.relative_to(ROOT)}")

    # The compatibility patcher is allowed for the moment, but it must be
    # explicitly guarded and idempotent. This prevents silent text-replacement
    # drift from being treated as a successful deployment.
    if "release_guard.py" not in update:
        fail("update.sh does not invoke release_guard.py")
    if "Browser HLS syntax validation passed" not in patcher:
        fail("Browser HLS patcher validation missing")

    print("RELEASE GUARD: PASS")
    print("Protected: owner-only callbacks, single-message method menu, status dashboard")
    print("Protected: HLS 16-way segments + realtime segment progress")
    print("Protected: Browser HLS authenticated requests + temp cleanup")
    print("Security: .env untracked + dangerous Python constructs rejected")


if __name__ == "__main__":
    main()
