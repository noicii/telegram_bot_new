#!/usr/bin/env python3
"""Test-only diagnostic: compare direct vs proxy access to a freshly discovered HLS URL.

No production files are imported or modified. The proxy is supplied at runtime via
--proxy, so credentials are not committed to the repository.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def probe(session, url: str, label: str, proxy: str | None) -> None:
    started = time.monotonic()
    try:
        async with session.get(url, proxy=proxy, allow_redirects=True) as response:
            body = await response.content.read(4096)
            elapsed = time.monotonic() - started
            print(f"[PROBE] {label}: status={response.status} time={elapsed:.2f}s bytes={len(body)} final_host={response.url.host}")
            print(f"[PROBE] {label}: content-type={response.headers.get('content-type')} server={response.headers.get('server')}")
            retry_after = response.headers.get("retry-after")
            if retry_after:
                print(f"[PROBE] {label}: retry-after={retry_after}")
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[PROBE] {label}: ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--proxy", required=True, help="HTTP/HTTPS/SOCKS proxy URL; supplied only at runtime")
    args = parser.parse_args()

    task = TaskContext(task_id="proxy-compare-test")
    downloader = HybridDownloader()
    print("[TEST] === DIRECT vs PROXY HLS HTTP PROBE ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")
    print("[TEST] Step 1: fresh HLS discovery")

    try:
        stream = await downloader._discover_hls(args.url, task, {"User-Agent": "Mozilla/5.0"}, None)
        if not stream:
            print("[TEST] FINAL: FAIL - no HLS URL discovered")
            return 1
        print(f"[TEST] HLS DISCOVERY SUCCESS host={stream.split('/')[2] if '://' in stream else 'unknown'}")

        timeout = __import__("aiohttp").ClientTimeout(total=35)
        async with __import__("aiohttp").ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
            await probe(session, stream, "DIRECT", None)
            await probe(session, stream, "PROXY", args.proxy)
    finally:
        await downloader.close()

    print("[TEST] FINAL: COMPLETE - compare DIRECT and PROXY results above")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
