#!/usr/bin/env python3
"""Compare the production HLS browser-discovery context with aiohttp requests.

Diagnostic only: no downloader behavior is changed. Signed URLs, cookies and
other secrets are never printed. The test is intentionally source-agnostic.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = ROOT / "bot_vnext"
sys.path.insert(0, str(V2_ROOT))

from app.core.task import TaskContext  # noqa: E402
from app.downloader.engine import HybridDownloader  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"


def safe_error(exc: BaseException) -> str:
    text = str(exc)
    return text.split("url=")[0].strip()[:500]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[DIAG] %(levelname)s %(name)s: %(message)s")
    task = TaskContext(task_id="hls-request-diagnostic", metadata={})
    downloader = HybridDownloader(ROOT / "test_results" / "downloader" / "media")
    headers = {"User-Agent": UA}

    print("[DIAG] Discovering HLS with production Playwright flow...")
    try:
        stream = await downloader._discover_hls(args.url, task, headers, None)
    except Exception as exc:
        print(f"[DIAG] DISCOVERY FAILED: {type(exc).__name__}: {safe_error(exc)}")
        return 1
    if not stream:
        print("[DIAG] DISCOVERY FAILED: no HLS stream URL")
        return 1

    cookies = dict(task.metadata.get("cookies") or {})
    print(f"[DIAG] DISCOVERY OK: stream host={stream.split('/', 3)[2] if '://' in stream else '<unknown>'}")
    print(f"[DIAG] Browser cookies captured: {len(cookies)}")

    import aiohttp
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)

    async def fetch(label: str, use_cookies: bool, use_ua: bool) -> None:
        h = {"User-Agent": UA} if use_ua else {}
        c = cookies if use_cookies else {}
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=10, ttl_dns_cache=0, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(headers=h, cookies=c, timeout=timeout, connector=connector) as session:
                async with session.get(stream) as response:
                    body = await response.read()
                    print(f"[DIAG] {label}: HTTP {response.status} type={response.headers.get('Content-Type','')} bytes={len(body)}")
        except Exception as exc:
            print(f"[DIAG] {label}: FAILED {type(exc).__name__}: {safe_error(exc)}")

    await fetch("aiohttp + UA + cookies", True, True)
    await fetch("aiohttp + UA only", False, True)
    await fetch("aiohttp + cookies only", True, False)
    await fetch("aiohttp bare", False, False)
    await task.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
