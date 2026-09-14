#!/usr/bin/env python3
"""Test-only: source page -> production HLS discovery -> FFmpeg route diagnostics.

No production bot files are modified by this test.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_ROOT))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def run_ffmpeg(label, downloader, stream, output, task, headers, timeout):
    print(f"[TEST] FFmpeg mode: {label}")
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            downloader._ffmpeg(stream, output, task, None, headers=headers),
            timeout=timeout,
        )
        elapsed = time.monotonic() - started
        if not output.is_file() or output.stat().st_size <= 0:
            print(f"[TEST] {label}: FAIL - output missing/empty")
            return False
        print(f"[TEST] {label}: SUCCESS - {output.stat().st_size} bytes in {elapsed:.1f}s")
        return True
    except Exception as exc:
        print(f"[TEST] {label}: FAIL - {type(exc).__name__}: {exc}")
        return False


async def probe(path: Path) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries",
        "format=format_name,duration,size", "-of", "default=noprint_wrappers=1",
        str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    print("[TEST] ffprobe:")
    print(stdout.decode(errors="replace").strip())
    if proc.returncode != 0:
        print(stderr.decode(errors="replace")[-1500:])
        return False
    return True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            print(f"[TEST] ERROR: {binary} is not installed")
            return 2

    out_dir = ROOT / "test_results" / "downloader" / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "production_equivalent_hls_test.mp4"
    output.unlink(missing_ok=True)

    downloader = HybridDownloader(out_dir, retries=2)
    headers = {"User-Agent": "Mozilla/5.0"}

    print("[TEST] === ATTEMPT 11: FRESH HLS + PRODUCTION FFMPEG RETRY ===")
    print(f"[TEST] Source page: {args.url}")
    print("[TEST] System: Playwright discovery -> FFmpeg HLS demuxer -> ffprobe validation")

    try:
        for round_no in (1, 2):
            task = TaskContext(task_id=f"attempt11-{int(time.time())}-{round_no}")
            print(f"[TEST] Round {round_no}: discovering fresh signed HLS URL")
            stream = await asyncio.wait_for(
                downloader._discover_hls(args.url, task, headers, None), timeout=args.timeout
            )
            if not stream:
                print(f"[TEST] Round {round_no}: HLS discovery returned no stream")
                continue
            print(f"[TEST] Round {round_no}: HLS DISCOVERY SUCCESS")
            output.unlink(missing_ok=True)
            ok = await run_ffmpeg(f"fresh-url-round-{round_no}", downloader, stream, output, task, headers, args.timeout)
            if ok and await probe(output):
                print(f"[TEST] FINAL: SUCCESS on round {round_no}")
                print(f"[TEST] Output: {output}")
                return 0
            print(f"[TEST] Round {round_no}: FFmpeg route failed; moving to next fresh URL")

        print("[TEST] FINAL: FAIL - two fresh signed HLS URLs both failed with production FFmpeg")
        return 1
    except asyncio.TimeoutError:
        print(f"[TEST] FAIL: timeout after {args.timeout:.0f}s")
        return 1
    except Exception as exc:
        print(f"[TEST] FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
