#!/usr/bin/env python3
"""Test-only HLS freshness/timing diagnostic.

Discovers a signed HLS URL, probes it immediately, then repeats after delays.
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


async def probe(session: aiohttp.ClientSession, url: str, label: str) -> None:
    started = time.monotonic()
    try:
        async with session.get(url, allow_redirects=True) as response:
            body = await response.content.read(4096)
            elapsed = time.monotonic() - started
            print(f"[PROBE {label}] status={response.status} bytes={len(body)} time={elapsed:.2f}s host={response.url.host}")
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[PROBE {label}] ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--delays", default="0,2,5,10", help="seconds after discovery")
    args = parser.parse_args()
    delays = [float(x.strip()) for x in args.delays.split(",") if x.strip()]

    downloader = HybridDownloader(ROOT / "test_results" / "downloader" / "media", retries=0)
    task = TaskContext(task_id=f"hls-freshness-{int(time.time())}")
    headers = {"User-Agent": "Mozilla/5.0"}

    print("[TEST] === ATTEMPT 14: HLS FRESHNESS/TIMING PROBE ===")
    print("[TEST] System: Playwright discovery -> one signed HLS URL -> probes at controlled delays")
    print("[TEST] No FFmpeg and no production files are touched")

    try:
        stream = await asyncio.wait_for(downloader._discover_hls(args.url, task, headers, None), timeout=180)
        if not stream:
            print("[TEST] FINAL: FAIL - no HLS URL discovered")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")

        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        connector = aiohttp.TCPConnector(limit=1, force_close=True)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            previous = 0.0
            for delay in delays:
                wait = delay - previous
                if wait > 0:
                    print(f"[TEST] waiting {wait:.1f}s (target +{delay:.1f}s)")
                    await asyncio.sleep(wait)
                await probe(session, stream, f"+{delay:.1f}s")
                previous = delay

        print("[TEST] FINAL: COMPLETE")
        return 0
    except asyncio.TimeoutError:
        print("[TEST] FINAL: FAIL - HLS discovery timeout")
        return 1
    except Exception as exc:
        print(f"[TEST] FINAL: FAIL - {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
