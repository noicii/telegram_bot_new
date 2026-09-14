#!/usr/bin/env python3
"""Test-only diagnostic: capture bytes from HLS responses actually received by Chromium.

Production files are NOT modified. This intentionally does not replay the URL
through aiohttp or APIRequestContext; it records the response body that the
browser itself successfully received.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def is_hls(url: str, content_type: str) -> bool:
    u = url.lower()
    ct = content_type.lower()
    return ".m3u8" in u or "mpegurl" in ct


def is_segment(url: str, content_type: str) -> bool:
    u = url.lower()
    ct = content_type.lower()
    return any(x in u for x in (".ts", ".m4s", ".aac")) or "video/" in ct or "audio/" in ct


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--wait", type=int, default=30)
    parser.add_argument("--segments", type=int, default=3)
    args = parser.parse_args()

    print("[TEST] === BROWSER RECEIVED HLS RESPONSE BODY TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    out = ROOT / "test_results" / "browser_response_bodies"
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    body_tasks = []

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
            try:
                ct = response.headers.get("content-type", "")
                url = response.url
                if response.status != 200 or not (is_hls(url, ct) or is_segment(url, ct)):
                    return
                body = await response.body()
                kind = "playlist" if is_hls(url, ct) else "segment"
                if kind == "segment" and sum(1 for x in saved if x["kind"] == "segment") >= args.segments:
                    return
                digest = hashlib.sha256(body).hexdigest()[:16]
                suffix = ".m3u8" if kind == "playlist" else ".bin"
                path = out / f"{len(saved)+1:03d}_{kind}_{digest}{suffix}"
                path.write_bytes(body)
                item = {
                    "kind": kind,
                    "status": response.status,
                    "url": url,
                    "host": urlparse(url).hostname,
                    "content_type": ct,
                    "bytes": len(body),
                    "sha256_16": digest,
                    "path": str(path),
                    "time": round(time.monotonic(), 3),
                }
                saved.append(item)
                print(f"[BODY] {kind} status=200 bytes={len(body)} host={item['host']}")
                print(f"[BODY] saved={path}")
                if kind == "playlist":
                    preview = body[:500].decode(errors="replace").replace("\n", " | ")
                    print(f"[BODY] playlist-preview={preview}")
            except Exception as exc:
                print(f"[BODY] capture warning: {type(exc).__name__}: {exc}")

        page.on("response", lambda response: body_tasks.append(asyncio.create_task(capture(response))))

        print("[TEST] Opening source page...")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"[TEST] page.goto warning: {type(exc).__name__}: {exc}")

        await page.wait_for_timeout(5000)

        # Discover the actual embedded player frame and request playback.
        for frame in page.frames:
            try:
                videos = frame.locator("video")
                count = await videos.count()
                if count:
                    print(f"[TEST] frame={frame.url} video_count={count}")
                    for i in range(count):
                        try:
                            await videos.nth(i).evaluate("v => { v.muted=true; const p=v.play(); if(p) p.catch(()=>{}); }")
                            print(f"[TEST] play() requested for video #{i+1}")
                        except Exception as exc:
                            print(f"[TEST] play warning: {type(exc).__name__}: {exc}")
                for selector in (".jwplayer", ".jw-display-icon-container", "button[aria-label*='play' i]", "[class*='play' i]"):
                    try:
                        loc = frame.locator(selector)
                        if await loc.count():
                            await loc.first.click(timeout=2000, force=True)
                            print(f"[TEST] clicked {selector} in {frame.url}")
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        print(f"[TEST] Observing browser traffic for {args.wait}s...")
        await page.wait_for_timeout(max(1, args.wait) * 1000)
        if body_tasks:
            await asyncio.gather(*body_tasks, return_exceptions=True)

        meta = out / "capture.json"
        meta.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        playlists = [x for x in saved if x["kind"] == "playlist"]
        segments = [x for x in saved if x["kind"] == "segment"]
        print(f"[TEST] Saved response bodies: {len(saved)}")
        print(f"[TEST] Playlists saved: {len(playlists)}")
        print(f"[TEST] Segment/media bodies saved: {len(segments)}")
        print(f"[TEST] Metadata: {meta}")

        await context.close()
        await browser.close()

    if playlists and segments:
        print("[TEST] FINAL: SUCCESS - browser itself delivered playlist + media bytes")
        return 0
    if playlists:
        print("[TEST] FINAL: PARTIAL - browser delivered playlist bytes but no segment body captured")
        return 2
    print("[TEST] FINAL: FAIL - browser delivered no successful HLS response bodies")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
