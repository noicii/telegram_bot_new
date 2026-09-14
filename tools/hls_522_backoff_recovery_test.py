"""Test-only real 522 recovery experiment.

Flow: discover fresh signed HLS -> probe it -> if 522, honor Retry-After (max
120s for this experiment) -> rediscover a fresh HLS URL -> probe again.
No production downloader files are imported or modified.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader

DEFAULT_URL = "https://luluvdo.com/d/x71u2m9rhqcd"


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return max(0.0, float(value))
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


async def probe(url: str) -> tuple[int | None, str | None]:
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=45, connect=30, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
                await r.read()
                return r.status, r.headers.get("Retry-After")
        except Exception as exc:
            print(f"[TEST] probe exception: {type(exc).__name__}: {exc}")
            return None, None


async def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print("[TEST] === ATTEMPT 19: REAL 522 BACKOFF + FRESH DISCOVERY ===")
    print(f"[TEST] Source: {source}")
    downloader = HybridDownloader("test_results/attempt19")
    task = TaskContext(task_id="attempt19")

    first = await downloader._discover_hls(source, task, {"User-Agent": "Mozilla/5.0"})
    if not first:
        print("[TEST] FINAL: FAIL - first HLS discovery returned no stream")
        return 1
    print("[TEST] Round 1: HLS DISCOVERY SUCCESS")
    status, retry = await probe(first)
    print(f"[TEST] Round 1 probe: status={status} Retry-After={retry!r}")

    if status != 522:
        print("[TEST] No 522 encountered; recovery branch not applicable.")
        print("[TEST] FINAL: COMPLETE")
        return 0

    delay = retry_after_seconds(retry) or 120.0
    delay = min(delay, 120.0)
    print(f"[TEST] 522 detected; waiting {delay:.1f}s before fresh discovery")
    started = time.monotonic()
    await asyncio.sleep(delay)
    print(f"[TEST] Backoff elapsed={time.monotonic() - started:.1f}s")

    task2 = TaskContext(task_id="attempt19-round2")
    second = await downloader._discover_hls(source, task2, {"User-Agent": "Mozilla/5.0"})
    if not second:
        print("[TEST] FINAL: FAIL - fresh HLS discovery after backoff returned no stream")
        return 1
    print("[TEST] Round 2: FRESH HLS DISCOVERY SUCCESS")
    status2, retry2 = await probe(second)
    print(f"[TEST] Round 2 probe: status={status2} Retry-After={retry2!r}")
    if status2 == 200:
        print("[TEST] FINAL: RECOVERY SUCCESS - fresh URL became reachable after backoff")
        return 0
    print("[TEST] FINAL: RECOVERY NOT OBSERVED - fresh URL still unavailable")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
