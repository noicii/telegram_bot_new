#!/usr/bin/env python3
"""Test-only HLS network-path diagnostic.

Compares DNS answers and direct HLS access over IPv4 vs IPv6. Production files
and downloader behavior are not modified.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import time
from urllib.parse import urlparse

import aiohttp


async def resolve(host: str, family: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, 443, family=family, type=socket.SOCK_STREAM)
    )
    return list(dict.fromkeys(info[4][0] for info in infos))


async def probe(url: str, family: int, label: str) -> None:
    started = time.monotonic()
    connector = aiohttp.TCPConnector(family=family, force_close=True, ssl=False)
    timeout = aiohttp.ClientTimeout(total=40)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as session:
            async with session.get(url, allow_redirects=True) as response:
                body = await response.content.read(4096)
                elapsed = time.monotonic() - started
                print(
                    f"[PROBE] {label}: status={response.status} time={elapsed:.2f}s "
                    f"bytes={len(body)} final_host={response.url.host}"
                )
                print(
                    f"[PROBE] {label}: server={response.headers.get('server')} "
                    f"content-type={response.headers.get('content-type')} "
                    f"retry-after={response.headers.get('retry-after')}"
                )
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[PROBE] {label}: ERROR={type(exc).__name__}: {exc} time={elapsed:.2f}s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="fresh signed HLS URL")
    args = parser.parse_args()

    parsed = urlparse(args.url)
    host = parsed.hostname
    if not host:
        print("[TEST] FAIL: URL has no hostname")
        return 1

    print("[TEST] === HLS IPv4 vs IPv6 NETWORK-PATH TEST ===")
    print(f"[TEST] Host: {host}")
    print("[TEST] Production files are NOT modified.")

    for family, label in ((socket.AF_INET, "IPv4"), (socket.AF_INET6, "IPv6")):
        try:
            addresses = await resolve(host, family)
            print(f"[DNS] {label}: {addresses or 'NO ADDRESS'}")
        except Exception as exc:
            print(f"[DNS] {label}: ERROR={type(exc).__name__}: {exc}")

    print("[TEST] Probing exact signed HLS URL over each address family...")
    await probe(args.url, socket.AF_INET, "IPv4")
    await probe(args.url, socket.AF_INET6, "IPv6")

    print("[TEST] FINAL: COMPLETE - compare IPv4/IPv6 results above")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
