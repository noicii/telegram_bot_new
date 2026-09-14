#!/usr/bin/env python3
"""Test-only diagnostic: follow the signed HLS redirect over IPv4.

Production files are not modified. This isolates the redirect target and
checks whether the actual CDN playlist can be reached after the initial 302.
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

    print("[TEST] === SIGNED HLS REDIRECT FOLLOW IPv4 TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    out = ROOT / "test_results" / "redirect_follow_work"
    out.mkdir(parents=True, exist_ok=True)
    task = TaskContext(task_id="redirect-follow-test")
    downloader = HybridDownloader(output_dir=out)

    try:
        stream = await downloader._discover_hls(
            args.url, task, {"User-Agent": "Mozilla/5.0"}, None
        )
        if not stream:
            print("[TEST] FINAL: FAIL - HLS discovery failed")
            return 1

        parsed = urlparse(stream)
        print(f"[TEST] Signed HLS host: {parsed.hostname}")
        try:
            addrs = sorted({x[4][0] for x in socket.getaddrinfo(parsed.hostname, 443, socket.AF_INET, socket.SOCK_STREAM)})
        except Exception as exc:
            print(f"[DNS] IPv4 resolution ERROR: {type(exc).__name__}: {exc}")
            return 1
        print(f"[DNS] Signed HLS IPv4: {addrs}")

        connector = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False, limit=4)
        timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=30)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            started = time.monotonic()
            async with session.get(stream, allow_redirects=False) as first:
                location = first.headers.get("location")
                body = await first.content.read(4096)
                print(f"[STEP 1] status={first.status} time={time.monotonic()-started:.2f}s host={first.url.host} bytes={len(body)}")
                print(f"[STEP 1] server={first.headers.get('server')} location={location}")

            if not location:
                print("[TEST] FINAL: FAIL - signed HLS response had no redirect location")
                return 1

            redirect_url = str(aiohttp.client_reqrep.URL(location, encoded=True))
            target_host = urlparse(redirect_url).hostname
            print(f"[STEP 2] Redirect target host: {target_host}")
            try:
                target_ips = sorted({x[4][0] for x in socket.getaddrinfo(target_host, 443, socket.AF_INET, socket.SOCK_STREAM)})
            except Exception as exc:
                print(f"[DNS] Redirect target IPv4 resolution ERROR: {type(exc).__name__}: {exc}")
                return 1
            print(f"[DNS] Redirect target IPv4: {target_ips}")

            started = time.monotonic()
            try:
                async with session.get(redirect_url, allow_redirects=False) as final:
                    final_body = await final.content.read(4096)
                    elapsed = time.monotonic() - started
                    print(f"[STEP 2] status={final.status} time={elapsed:.2f}s host={final.url.host} bytes={len(final_body)}")
                    print(f"[STEP 2] server={final.headers.get('server')} content-type={final.headers.get('content-type')} retry-after={final.headers.get('retry-after')}")
                    print(f"[STEP 2] cf-ray={final.headers.get('cf-ray')} cf-error-type={final.headers.get('cf-error-type')}")
                    preview = final_body[:300].decode(errors="replace").replace("\n", " ")
                    print(f"[STEP 2] body-preview={preview}")
                    if final.status == 200 and "mpegurl" in (final.headers.get("content-type") or "").lower():
                        print("[TEST] FINAL: SUCCESS - actual CDN playlist reached over IPv4")
                        return 0
                    if final.status in {301, 302, 303, 307, 308}:
                        print("[TEST] FINAL: REDIRECT - CDN returned another redirect")
                        return 2
                    print("[TEST] FINAL: FAIL - actual CDN target did not return a playlist")
                    return 1
            except Exception as exc:
                print(f"[STEP 2] ERROR={type(exc).__name__}: {exc} time={time.monotonic()-started:.2f}s")
                print("[TEST] FINAL: FAIL - redirect target could not be reached over IPv4")
                return 1
    finally:
        # HybridDownloader has no close() API; this test has no persistent session to close.
        pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
