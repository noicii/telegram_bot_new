#!/usr/bin/env python3
"""Test-only: inspect the downloader's retry policy for a synthetic Cloudflare 522.

This does not contact the production source and does not modify production code.
It answers one question: does HybridDownloader honor Retry-After on a 522, or
use its current short fixed retry delay?
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_ROOT))

from app.core.task import TaskContext
from app.downloader.engine import DownloadError, HybridDownloader


async def main() -> int:
    outdir = ROOT / "test_results" / "downloader" / "media"
    outdir.mkdir(parents=True, exist_ok=True)
    downloader = HybridDownloader(outdir, retries=2)
    task = TaskContext(task_id="retry-policy-522")
    calls: list[float] = []

    async def fake_hls(url, output, task_ctx, progress):
        calls.append(time.monotonic())
        raise DownloadError(
            "HTTP 522 Error: Connection timed out; Retry-After: 120; "
            "Cloudflare origin TCP connection timeout"
        )

    downloader._hls_multi = fake_hls
    task.metadata["download_method"] = "hls-multi"

    print("[TEST] === ATTEMPT 16: 522 RETRY POLICY AUDIT ===")
    print("[TEST] Synthetic error: Cloudflare 522 + Retry-After: 120")
    print("[TEST] Calling production HybridDownloader.download() only; no production files changed")

    started = time.monotonic()
    try:
        await downloader.download("https://test.invalid/video", task, "retry-policy-test.mp4")
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[TEST] FINAL ERROR: {type(exc).__name__}: {exc}")
        print(f"[TEST] ATTEMPTS: {len(calls)}")
        if len(calls) > 1:
            gaps = [round(calls[i] - calls[i - 1], 2) for i in range(1, len(calls))]
            print(f"[TEST] RETRY GAPS: {gaps}")
        print(f"[TEST] TOTAL ELAPSED: {elapsed:.2f}s")
        print("[TEST] Cloudflare documents 522 Retry-After=120s; this test checks whether current code honors it.")
        return 0

    print("[TEST] UNEXPECTED: download succeeded")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
