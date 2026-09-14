#!/usr/bin/env python3
"""Fail-closed checks for production Bot V2 releases."""
from __future__ import annotations

import ast
import re
import subprocess
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
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=False).decode("utf-8", errors="replace")
    return [ROOT / item for item in out.split("\0") if item]


def main() -> None:
    main_py = read(V2 / "main.py")
    engine = read(V2 / "app" / "downloader" / "engine.py")
    browser = read(V2 / "app" / "downloader" / "browser_hls.py")
    update = read(ROOT / "update.sh")
    gitignore = read(ROOT / ".gitignore")
    database = read(V2 / "app" / "storage" / "database.py")
    pipeline = read(V2 / "app" / "pipeline.py")
    task = read(V2 / "app" / "core" / "task.py")

    tracked_names = {p.relative_to(ROOT).as_posix() for p in tracked_files()}
    if ".env" in tracked_names:
        fail(".env is tracked by Git")
    if ".env" not in gitignore.splitlines():
        fail(".env is not protected by .gitignore")

    for path in [
        V2 / "main.py", V2 / "app" / "pipeline.py",
        V2 / "app" / "downloader" / "engine.py", V2 / "app" / "downloader" / "browser_hls.py",
        V2 / "app" / "queue" / "upload_manager.py", V2 / "app" / "uploader" / "engine.py",
        V2 / "app" / "storage" / "database.py", V2 / "app" / "core" / "task.py",
    ]:
        try:
            ast.parse(read(path), filename=str(path))
        except SyntaxError as exc:
            fail(f"syntax error in {path.relative_to(ROOT)}: {exc}")

    if "def owner_only(message)" not in main_py or "message.from_user.id == OWNER_ID" not in main_py:
        fail("owner-only message boundary missing")
    if "def callback_owner(query)" not in main_py or "query.from_user.id == OWNER_ID" not in main_py:
        fail("owner-only callback boundary missing")
    if "if not callback_owner(query)" not in main_py:
        fail("callback owner check missing")

    method_match = re.search(r'if data==["\']v2:method["\']:(.*?)(?=\n\s*if data==|\n\s*s=self\.sessions|\Z)', main_py, re.S)
    if not method_match:
        fail("v2:method callback missing")
    method_block = method_match.group(1)
    if "send_method_menu" in method_block or "reply_text" in method_block or "edit_text" not in method_block:
        fail("method callback does not safely edit the current message")

    status_match = re.search(r'if data==["\']v2:status["\']:(.*?)(?=\n\s*if data==|\Z)', main_py, re.S)
    if not status_match or "show_dashboard" not in status_match.group(1):
        fail("status callback is not wired to the canonical dashboard")

    if "asyncio.Semaphore(16)" not in engine and "Semaphore(16)" not in engine:
        fail("HLS segment concurrency is not locked to 16")
    if '"hls_completed"' not in engine or '"hls_total"' not in engine:
        fail("HLS segment progress fields missing")
    if "BrowserHLSDownloader" not in engine:
        fail("Browser HLS integration missing")
    if "context.request" not in browser:
        fail("Browser HLS is not using browser-context authenticated requests")
    if "task.register_temp(work)" not in browser:
        fail("Browser HLS work directory is not registered with task cleanup")
    if "shutil.rmtree(work, ignore_errors=True)" not in browser:
        fail("Browser HLS explicit work cleanup missing")

    if "🧩 Segments:" not in main_py or "hls_total" not in main_py or "hls_completed" not in main_py:
        fail("segmented realtime UI contract missing")

    if "async def transition(" not in database or "WHERE id = ? AND status IN" not in database:
        fail("atomic task state transition primitive missing")
    if "return await self.transition(task_id, (\"queued\",), \"downloading\"" not in database:
        fail("download start does not use atomic state transition")
    if "return await self.transition(task_id, (\"downloading\",), \"uploading\"" not in database:
        fail("upload handoff does not use atomic state transition")
    if "return await self.transition(task_id, (\"uploading\",), \"completed\"" not in database:
        fail("completion does not use atomic state transition")
    if "async def reset_for_retry" not in database or "retry_count=count" not in database:
        fail("retry counter preservation missing")
    if "count = int(task.get(\"retry_count\") or 0) + 1" not in database:
        fail("retry counter is not incremented")
    if "if count > maximum:" not in database:
        fail("retry budget enforcement missing")
    if "await self.db.mark_uploading(task_id)" not in pipeline:
        fail("pipeline upload handoff is not guarded by atomic transition")
    if "await self.db.mark_completed(task_id)" not in pipeline:
        fail("pipeline completion is not guarded by atomic transition")
    if "await self.db.mark_failed(task_id, str(error))" not in pipeline:
        fail("pipeline failure path missing")
    if "shutil.rmtree(path, ignore_errors=True)" not in task:
        fail("TaskContext directory cleanup missing")

    source_paths = [p for p in V2.rglob("*.py") if "__pycache__" not in p.parts]
    dangerous = [(r"\beval\s*\(", "eval()"), (r"\bexec\s*\(", "exec()"), (r"\bpickle\.(loads|load)\s*\(", "pickle deserialization"), (r"\bshell\s*=\s*True\b", "subprocess shell=True")]
    for path in source_paths:
        source = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in dangerous:
            if re.search(pattern, source):
                fail(f"unsafe construct {label} found in {path.relative_to(ROOT)}")

    if "release_guard.py" not in update:
        fail("update.sh does not invoke release_guard.py")
    if "apply_ui_runtime_patch.py" in update:
        fail("runtime patcher is still referenced by update.sh")
    if (V2 / "apply_ui_runtime_patch.py").exists():
        fail("runtime patcher file still exists")

    print("RELEASE GUARD: PASS")
    print("Protected: owner-only callbacks, single-message method menu, status dashboard")
    print("Protected: HLS 16-way segments + realtime segment progress")
    print("Protected: Browser HLS authenticated requests + cleanup")
    print("Protected: atomic task states + retry budget + recursive cleanup")
    print("Security: .env untracked + dangerous Python constructs rejected")
    print("Deployment: runtime patcher removed; GitHub source is production source")


if __name__ == "__main__":
    main()
