#!/usr/bin/env python3
"""Test-only diagnostic: discover HLS then probe the exact CDN URL over IPv4.

Production files are not modified and no production downloader method is used.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    args = parser.parse_args()

    print("[TEST] === EXACT CDN HLS IPv4 PROBE ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    work = ROOT / "test_results" / "cdn_ipv4_probe_work"
    work.mkdir(parents=True, exist_ok=True)
    task = TaskContext(task_id="cdn-ipv4-probe")
    downloader = HybridDownloader(output_dir=work)

    stream = await downloader._discover_hls(
        args.url, task, {"User-Agent": "Mozilla/5.0"}, None
    )
    if not stream:
        print("[TEST] FINAL: FAIL - HLS discovery failed")
        return 1

    parsed = urlparse(stream)
    host = parsed.hostname or ""
    print(f"[TEST] HLS URL HOST: {host}")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, socket.AF_INET, socket.SOCK_STREAM)
        ips = sorted({item[4][0] for item in infos})
        print(f"[DNS] CDN IPv4: {ips}")
    except Exception as exc:
        print(f"[DNS] IPv4 resolution error: {type(exc).__name__}: {exc}")
        ips = []

    connector = aiohttp.TCPConnector(family=socket.AF_INET, limit=4, limit_per_host=4, ssl=True)
    timeout = aiohttp.ClientTimeout(total=35, connect=15, sock_read=25)
    headers = {"User-Agent": "Mozilla/5.0"}

    print("[TEST] Probing exact signed HLS URL with IPv4-only connector...")
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            async with session.get(stream, allow_redirects=False) as response:
                body = await response.content.read(4096)
                elapsed = time.monotonic() - started
                print(f"[PROBE] status={response.status} time={elapsed:.2f}s bytes={len(body)} host={response.url.host}")
                print(f"[PROBE] content-type={response.headers.get('content-type')}")
                print(f"[PROBE] server={response.headers.get('server')}")
                print(f"[PROBE] cf-ray={response.headers.get('cf-ray')}")
                print(f"[PROBE] retry-after={response.headers.get('retry-after')}")
                print(f"[PROBE] location={response.headers.get('location')}")
                preview = body[:300].decode(errors="replace").replace("\n", " ")
                print(f"[PROBE] body={preview}")
                if response.status == 200 and b"#EXTM3U" in body:
                    print("[TEST] FINAL: SUCCESS - exact CDN HLS playlist returned 200")
                    return 0
                print("[TEST] FINAL: CDN returned non-playlist/non-200 response")
                return 2
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[PROBE] ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")
        print("[TEST] FINAL: FAIL - IPv4 CDN probe could not complete")
        return 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
