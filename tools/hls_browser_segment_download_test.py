#!/usr/bin/env python3
"""Test-only diagnostic: browser playback -> signed HLS -> real segment bytes.

Production files are NOT modified. This test proves whether the same
browser-context request path that successfully fetched the playlists can also
fetch actual media segments and produce a valid local transport stream.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def abs_url(base: str, value: str) -> str:
    return urljoin(base, value.strip())


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--segments", type=int, default=8, help="number of media segments to fetch")
    parser.add_argument("--wait", type=int, default=20, help="seconds to allow browser playback/discovery")
    args = parser.parse_args()

    print("[TEST] === BROWSER HLS SEGMENT DOWNLOAD TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

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
        playlist_url = None

        async def on_response(response) -> None:
            nonlocal playlist_url
            ct = (response.headers.get("content-type") or "").lower()
            if response.status == 200 and (".m3u8" in response.url.lower() or "mpegurl" in ct):
                if playlist_url is None:
                    playlist_url = response.url
                    print(f"[CAPTURE] HLS playlist: {playlist_url}")

        page.on("response", on_response)

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
            except Exception:
                pass
            for selector in (".jwplayer", ".jw-display-icon-container", "[class*='play' i]"):
                try:
                    loc = frame.locator(selector)
                    if await loc.count():
                        await loc.first.click(timeout=2000, force=True)
                        break
                except Exception:
                    pass

        await page.wait_for_timeout(max(1, args.wait) * 1000)
        if not playlist_url:
            print("[TEST] FINAL: FAIL - no successful browser HLS playlist captured")
            await context.close()
            await browser.close()
            return 1

        request = context.request
        print("[TEST] Fetching captured master playlist through browser context...")
        master = await request.get(playlist_url, timeout=30000, fail_on_status_code=False)
        master_text = await master.text()
        print(f"[MASTER] status={master.status} bytes={len(master_text.encode())} content-type={master.headers.get('content-type')}")
        if master.status != 200 or "#EXTM3U" not in master_text:
            print("[TEST] FINAL: FAIL - master playlist replay failed")
            await context.close()
            await browser.close()
            return 1

        variant = None
        for line in master_text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                variant = abs_url(playlist_url, line)
                break
        if not variant:
            print("[TEST] FINAL: FAIL - master playlist has no media/variant URL")
            await context.close()
            await browser.close()
            return 1

        print(f"[TEST] Variant URL: {variant}")
        variant_resp = await request.get(variant, timeout=30000, fail_on_status_code=False)
        variant_text = await variant_resp.text()
        print(f"[VARIANT] status={variant_resp.status} bytes={len(variant_text.encode())} content-type={variant_resp.headers.get('content-type')}")
        if variant_resp.status != 200 or "#EXTM3U" not in variant_text:
            print("[TEST] FINAL: FAIL - variant playlist replay failed")
            await context.close()
            await browser.close()
            return 1

        segment_urls = []
        for line in variant_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            segment_urls.append(abs_url(variant, line))
            if len(segment_urls) >= max(1, args.segments):
                break

        if not segment_urls:
            print("[TEST] FINAL: FAIL - variant playlist has no segment URLs")
            await context.close()
            await browser.close()
            return 1

        out_dir = ROOT / "test_results" / "browser_segments"
        out_dir.mkdir(parents=True, exist_ok=True)
        total = 0
        ok = 0
        started = time.monotonic()
        for idx, seg_url in enumerate(segment_urls, 1):
            try:
                response = await request.get(seg_url, timeout=30000, fail_on_status_code=False)
                body = await response.body()
                size = len(body)
                print(f"[SEG {idx}/{len(segment_urls)}] status={response.status} bytes={size} ct={response.headers.get('content-type')} time={time.monotonic()-started:.2f}s")
                if response.status == 200 and size > 0:
                    (out_dir / f"seg-{idx:04d}.ts").write_bytes(body)
                    total += size
                    ok += 1
            except Exception as exc:
                print(f"[SEG {idx}/{len(segment_urls)}] ERROR={type(exc).__name__}: {exc}")

        print(f"[TEST] Successful segments: {ok}/{len(segment_urls)}")
        print(f"[TEST] Total downloaded media bytes: {total}")
        print(f"[TEST] Output: {out_dir}")

        await context.close()
        await browser.close()

    if ok == len(segment_urls) and total > 0:
        print("[TEST] FINAL: SUCCESS - browser context downloaded actual HLS media segments")
        return 0
    print("[TEST] FINAL: FAIL - not all requested HLS segments downloaded")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
