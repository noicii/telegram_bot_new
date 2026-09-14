#!/usr/bin/env python3
"""Test-only diagnostic: compare direct HLS access with a free public HTTP proxy.

Production files are not modified. No proxy is stored in the repository; a live
public list is fetched at runtime and candidates are validated before use.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot_vnext"
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader

PROXY_LIST = "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/http.txt"
IP_CHECK = "https://api.ipify.org"


async def fetch_candidates(limit: int) -> list[str]:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(PROXY_LIST) as r:
            r.raise_for_status()
            text = await r.text()
    out: list[str] = []
    for line in text.splitlines():
        p = line.strip()
        if p and not p.startswith("#") and ":" in p:
            out.append("http://" + p)
        if len(out) >= limit:
            break
    return out


async def validate_proxy(session: aiohttp.ClientSession, proxy: str) -> str | None:
    try:
        async with session.get(IP_CHECK, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                ip = (await r.text()).strip()
                return ip or None
    except Exception:
        return None
    return None


async def find_working_proxy(limit: int) -> tuple[str, str] | None:
    candidates = await fetch_candidates(limit)
    print(f"[TEST] Downloaded {len(candidates)} public HTTP proxy candidates")
    connector = aiohttp.TCPConnector(limit=20, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Return the actual proxy together with its exit IP so there is no
        # ambiguity when asyncio.as_completed() wraps coroutine futures.
        async def check(proxy: str) -> tuple[str, str] | None:
            ip = await validate_proxy(session, proxy)
            return (proxy, ip) if ip else None

        pending = [asyncio.create_task(check(proxy)) for proxy in candidates]
        for future in asyncio.as_completed(pending):
            result = await future
            if result:
                proxy, ip = result
                for other in pending:
                    if not other.done():
                        other.cancel()
                print(f"[TEST] FREE PROXY VALID: {proxy} exit_ip={ip}")
                return proxy, ip
        return None


async def probe(session, url: str, label: str, proxy: str | None) -> None:
    started = time.monotonic()
    try:
        async with session.get(url, proxy=proxy, allow_redirects=True) as response:
            body = await response.content.read(4096)
            elapsed = time.monotonic() - started
            print(
                f"[PROBE] {label}: status={response.status} time={elapsed:.2f}s "
                f"bytes={len(body)} final_host={response.url.host}"
            )
            print(
                f"[PROBE] {label}: content-type={response.headers.get('content-type')} "
                f"server={response.headers.get('server')}"
            )
            retry_after = response.headers.get("retry-after")
            if retry_after:
                print(f"[PROBE] {label}: retry-after={retry_after}")
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[PROBE] {label}: ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--candidates", type=int, default=30, help="public proxies to validate")
    args = parser.parse_args()

    print("[TEST] === DIRECT vs FREE PUBLIC PROXY HLS PROBE ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")
    print("[TEST] Public proxy source: HProxy live HTTP list")

    proxy_info = await find_working_proxy(max(1, min(args.candidates, 100)))
    if not proxy_info:
        print("[TEST] FINAL: FAIL - no free public HTTP proxy passed validation")
        return 1
    proxy, exit_ip = proxy_info

    # HybridDownloader requires an output_dir. This is an isolated test-only
    # directory and is never used by the production bot process.
    test_output = ROOT / "test_results" / "proxy_compare_work"
    test_output.mkdir(parents=True, exist_ok=True)
    task = TaskContext(task_id="free-proxy-compare-test")
    downloader = HybridDownloader(output_dir=test_output)
    try:
        print("[TEST] Step 1: fresh HLS discovery")
        stream = await downloader._discover_hls(args.url, task, {"User-Agent": "Mozilla/5.0"}, None)
        if not stream:
            print("[TEST] FINAL: FAIL - no HLS URL discovered")
            return 1
        print(f"[TEST] HLS DISCOVERY SUCCESS host={stream.split('/')[2] if '://' in stream else 'unknown'}")

        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
            await probe(session, stream, "DIRECT", None)
            await probe(session, stream, f"FREE_PROXY exit={exit_ip}", proxy)
    finally:
        await downloader.close()

    print("[TEST] FINAL: COMPLETE - compare DIRECT and FREE_PROXY results above")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
