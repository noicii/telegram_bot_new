#!/usr/bin/env python3
"""Test-only: probe one freshly discovered HLS URL repeatedly.

This isolates whether the CDN/origin consistently returns 5xx or intermittently
allows the same signed URL. No production bot files are modified.
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
    parser.add_argument("--requests", type=int, default=5)
    args = parser.parse_args()

    downloader = HybridDownloader(ROOT / "test_results" / "downloader" / "media", retries=0)
    task = TaskContext(task_id=f"cdn-repeat-{int(time.time())}")
    headers = {"User-Agent": "Mozilla/5.0"}

    print("[TEST] === ATTEMPT 13: REPEATED CDN PROBE ON ONE FRESH URL ===")
    print("[TEST] System: Playwright discovery -> same signed HLS URL -> repeated aiohttp probes")
    print("[TEST] No FFmpeg and no production files are touched")

    try:
        stream = await asyncio.wait_for(
            downloader._discover_hls(args.url, task, headers, None), timeout=180
        )
        if not stream:
            print("[TEST] FINAL: FAIL - HLS discovery returned no stream")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")
        print("[TEST] Reusing the exact same signed URL for every probe")

        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        connector = aiohttp.TCPConnector(limit=1, force_close=True)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for i in range(1, args.requests + 1):
                started = time.monotonic()
                try:
                    async with session.get(stream, headers=headers, allow_redirects=True) as response:
                        body = await response.content.read(8192)
                        elapsed = time.monotonic() - started
                        print(
                            f"[PROBE {i}/{args.requests}] status={response.status} "
                            f"bytes={len(body)} time={elapsed:.2f}s "
                            f"host={response.url.host}"
                        )
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    print(f"[PROBE {i}/{args.requests}] ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")
                if i < args.requests:
                    await asyncio.sleep(1)

        print("[TEST] FINAL: COMPLETE - use the probe status pattern to diagnose CDN behavior")
        return 0
    except asyncio.TimeoutError:
        print("[TEST] FINAL: FAIL - discovery timeout")
        return 1
    except Exception as exc:
        print(f"[TEST] FINAL: FAIL - {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
