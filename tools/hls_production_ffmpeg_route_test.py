#!/usr/bin/env python3
"""Test-only: source page -> production HLS discovery -> production-equivalent FFmpeg.

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

from app.downloader.engine import HybridDownloader
from app.downloader.models import TaskContext


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("[TEST] ERROR: ffmpeg is not installed")
        return 2
    if not shutil.which("ffprobe"):
        print("[TEST] ERROR: ffprobe is not installed")
        return 2

    out_dir = ROOT / "test_results" / "downloader" / "media"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "production_equivalent_hls_test.mp4"
    output.unlink(missing_ok=True)

    task = TaskContext(id=f"production-equivalent-{int(time.time())}", chat_id=0, url=args.url, metadata={})
    downloader = HybridDownloader()
    headers = {"User-Agent": "Mozilla/5.0"}

    print("[TEST] === PRODUCTION-EQUIVALENT HLS ROUTE ===")
    print(f"[TEST] Source page: {args.url}")
    print("[TEST] Step 1: production _discover_hls() via Playwright")
    started = time.monotonic()

    try:
        stream = await asyncio.wait_for(
            downloader._discover_hls(args.url, task, headers, None),
            timeout=args.timeout,
        )
        if not stream:
            print("[TEST] FAIL: HLS discovery returned no stream")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")
        print("[TEST] Step 2: production _ffmpeg() invocation against discovered stream")

        # This calls the existing production _ffmpeg implementation directly.
        await asyncio.wait_for(
            downloader._ffmpeg(stream, output, task, None, headers=headers),
            timeout=args.timeout,
        )

        if not output.is_file() or output.stat().st_size <= 0:
            print("[TEST] FAIL: FFmpeg returned but output is missing/empty")
            return 1

        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=format_name,duration,size",
            "-of", "default=noprint_wrappers=1", str(output),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await probe.communicate()
        print("[TEST] ffprobe:")
        print(stdout.decode(errors="replace").strip())
        if probe.returncode != 0:
            print("[TEST] FAIL: ffprobe rejected output")
            print(stderr.decode(errors="replace")[-1000:])
            return 1

        print(f"[TEST] SUCCESS: valid MP4 produced in {time.monotonic() - started:.1f}s")
        print(f"[TEST] Output: {output}")
        return 0
    except asyncio.TimeoutError:
        print(f"[TEST] FAIL: timeout after {args.timeout:.0f}s")
        return 1
    except Exception as exc:
        print(f"[TEST] FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
