#!/usr/bin/env python3
"""Test-only: discover a fresh HLS URL, probe it with HTTP, then run FFmpeg.

The purpose is to distinguish CDN HTTP failure from an FFmpeg-specific failure.
No production bot files are modified.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BOT_ROOT))

import aiohttp
from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    out = ROOT / "test_results" / "downloader" / "media" / "hls_http_probe_test.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    downloader = HybridDownloader(out.parent, retries=2)
    headers = {"User-Agent": "Mozilla/5.0"}
    task = TaskContext(task_id=f"hls-probe-{int(time.time())}")

    print("[TEST] === ATTEMPT 12: HTTP PROBE -> PRODUCTION FFMPEG ===")
    print("[TEST] System: Playwright -> signed HLS -> aiohttp HTTP probe -> production FFmpeg -> ffprobe")

    try:
        print("[TEST] Step 1: discovering fresh HLS URL...")
        stream = await asyncio.wait_for(downloader._discover_hls(args.url, task, headers, None), timeout=args.timeout)
        if not stream:
            print("[TEST] FAIL: HLS discovery returned no stream")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")

        print("[TEST] Step 2: probing the same fresh HLS URL with aiohttp...")
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            try:
                async with session.get(stream, allow_redirects=True) as response:
                    body = await response.content.read(8192)
                    print(f"[TEST] HTTP STATUS: {response.status}")
                    print(f"[TEST] CONTENT-TYPE: {response.headers.get('Content-Type', '')}")
                    print(f"[TEST] PROBE BYTES: {len(body)}")
                    print(f"[TEST] FINAL HOST: {response.url.host}")
                    if response.status >= 500:
                        print("[TEST] CDN/HTTP layer returned 5xx before FFmpeg")
                    elif response.status >= 400:
                        print("[TEST] CDN/HTTP layer returned 4xx before FFmpeg")
                    elif response.status == 200:
                        print("[TEST] HTTP probe succeeded; FFmpeg-specific behavior can now be tested")
            except Exception as exc:
                print(f"[TEST] HTTP PROBE ERROR: {type(exc).__name__}: {exc}")

        print("[TEST] Step 3: running production _ffmpeg() on the SAME fresh URL...")
        started = time.monotonic()
        await asyncio.wait_for(downloader._ffmpeg(stream, out, task, None, headers=headers), timeout=args.timeout)
        elapsed = time.monotonic() - started
        print(f"[TEST] FFmpeg returned in {elapsed:.1f}s")
        if not out.is_file() or out.stat().st_size <= 0:
            print("[TEST] FAIL: FFmpeg output missing/empty")
            return 1

        print("[TEST] Step 4: ffprobe validation...")
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=format_name,duration,size",
            "-of", "default=noprint_wrappers=1", str(out),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await probe.communicate()
        print(stdout.decode(errors="replace").strip())
        if probe.returncode != 0:
            print(stderr.decode(errors="replace")[-1500:])
            print("[TEST] FINAL: FAIL - invalid media output")
            return 1
        print("[TEST] FINAL: SUCCESS - valid MP4")
        print(f"[TEST] Output: {out}")
        return 0
    except asyncio.TimeoutError:
        print(f"[TEST] FINAL: FAIL - timeout after {args.timeout:.0f}s")
        return 1
    except Exception as exc:
        print(f"[TEST] FINAL: FAIL - {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
