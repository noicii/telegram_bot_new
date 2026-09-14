#!/usr/bin/env python3
"""Test-only diagnostic: discover a signed HLS playlist in the browser, then
fetch a batch of its exact signed TS segment URLs concurrently.

Production files are NOT modified. The browser is used only to obtain the
actual playlist and request context; playback itself is not required to fetch
all segments serially. The test validates concurrent HTTP 200 bodies and
reports wall-clock speedup.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


def seg_num(url: str) -> int:
    m = re.search(r"seg-(\d+)-", url)
    return int(m.group(1)) if m else 10**9


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--segments", type=int, default=16)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--wait", type=int, default=45)
    args = parser.parse_args()

    print("[TEST] === BROWSER HLS PLAYLIST -> PARALLEL SEGMENT FETCH TEST ===")
    print(f"[TEST] Source: {args.url}")
    print(f"[TEST] Target segments: {args.segments}")
    print(f"[TEST] Fetch concurrency: {args.workers}")
    print("[TEST] Production files are NOT modified.")

    playlists: list[dict] = []
    seen_playlists: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        async def capture(response):
            url = response.url
            ct = (response.headers.get("content-type") or "").lower()
            if response.status != 200 or (".m3u8" not in url.lower() and "mpegurl" not in ct):
                return
            if url in seen_playlists:
                return
            try:
                body = await response.text()
            except Exception:
                return
            if "#EXTM3U" not in body:
                return
            seen_playlists.add(url)
            playlists.append({"url": url, "body": body})
            print(f"[PLAYLIST] status=200 bytes={len(body.encode())} host={urlparse(url).hostname} url={url[:260]}")

        page.on("response", lambda r: asyncio.create_task(capture(r)))

        print("[TEST] Opening source page...")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"[TEST] page.goto warning: {type(exc).__name__}: {exc}")

        await page.wait_for_timeout(5000)
        for frame in page.frames:
            try:
                videos = frame.locator("video")
                if await videos.count():
                    await videos.first.evaluate(
                        "v => { v.muted=true; v.autoplay=true; const p=v.play(); if(p) p.catch(()=>{}); }"
                    )
                for selector in (".jwplayer", "[class*='play' i]", "button[aria-label*='play' i]"):
                    try:
                        loc = frame.locator(selector)
                        if await loc.count():
                            await loc.first.click(timeout=2000, force=True)
                            print(f"[TEST] clicked {selector} in frame={frame.url}")
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        deadline = time.monotonic() + max(1, args.wait)
        while time.monotonic() < deadline and not playlists:
            await page.wait_for_timeout(250)

        if not playlists:
            print("[TEST] FINAL: FAIL - browser captured no successful HLS playlist")
            await context.close()
            await browser.close()
            return 1

        # Prefer a media playlist containing actual TS segment references.
        selected_playlist = None
        for item in playlists:
            if ".ts" in item["body"].lower() or "seg-" in item["body"]:
                selected_playlist = item
                break
        if selected_playlist is None:
            selected_playlist = playlists[-1]

        base = selected_playlist["url"]
        body = selected_playlist["body"]
        segment_urls = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            u = urljoin(base, line)
            if ".ts" in u.lower() or "seg-" in u.lower():
                segment_urls.append(u)

        segment_urls = sorted(set(segment_urls), key=seg_num)[: args.segments]
        print(f"[TEST] Playlist yielded {len(segment_urls)} segment URLs")
        if len(segment_urls) < args.segments:
            print("[TEST] FINAL: FAIL - selected playlist did not contain enough TS segments")
            await context.close()
            await browser.close()
            return 1

        # Use the browser's request context so cookies/proxy/TLS state remain browser-side.
        api = context.request
        headers = {
            "referer": page.url,
            "user-agent": await page.evaluate("navigator.userAgent"),
            "accept": "*/*",
        }
        start = time.monotonic()
        sem = asyncio.Semaphore(max(1, args.workers))
        results: list[dict] = []

        async def fetch_one(i: int, url: str):
            async with sem:
                t0 = time.monotonic()
                try:
                    r = await api.get(url, headers=headers, timeout=30000, fail_on_status_code=False)
                    data = await r.body()
                    elapsed = time.monotonic() - t0
                    result = {"i": i, "segment": seg_num(url), "status": r.status, "bytes": len(data), "seconds": elapsed}
                    results.append(result)
                    print(f"[FETCH] #{i} segment={result['segment']} status={r.status} bytes={result['bytes']} time={elapsed:.3f}s")
                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    results.append({"i": i, "segment": seg_num(url), "status": None, "bytes": 0, "seconds": elapsed, "error": str(exc)})
                    print(f"[FETCH] #{i} segment={seg_num(url)} ERROR={type(exc).__name__}: {exc} time={elapsed:.3f}s")

        await asyncio.gather(*(fetch_one(i, u) for i, u in enumerate(segment_urls, 1)))
        wall = time.monotonic() - start
        successful = [r for r in results if r["status"] == 200 and r["bytes"] > 0]
        total_individual = sum(r["seconds"] for r in results)
        print(f"[TEST] Parallel wall time: {wall:.3f}s")
        print(f"[TEST] Sum individual times: {total_individual:.3f}s")
        print(f"[TEST] Successful segments: {len(successful)}/{len(segment_urls)}")

        await context.close()
        await browser.close()

    if len(successful) != len(segment_urls):
        print("[TEST] FINAL: FAIL - one or more parallel segment fetches failed")
        return 1
    print("[TEST] FINAL: SUCCESS - signed playlist segments fetched concurrently")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
