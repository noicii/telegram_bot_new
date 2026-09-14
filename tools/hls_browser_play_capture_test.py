#!/usr/bin/env python3
"""Test-only diagnostic: open a source page, start playback, capture media requests.

Production files are NOT modified. The goal is to observe what a real browser
successfully requests while the video is playing, including HLS URLs, status,
content type, redirect target, and relevant request headers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def is_media(url: str, content_type: str = "") -> bool:
    u = url.lower()
    ct = content_type.lower()
    return (
        ".m3u8" in u
        or ".mp4" in u
        or ".ts" in u
        or ".m4s" in u
        or ".aac" in u
        or ".mpd" in u
        or "mpegurl" in ct
        or "video/" in ct
        or "audio/" in ct
        or "application/dash+xml" in ct
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--wait", type=int, default=30, help="seconds to observe playback")
    args = parser.parse_args()

    print("[TEST] === BROWSER PLAYBACK MEDIA CAPTURE TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    captured: list[dict] = []
    seen: set[str] = set()
    request_count = 0

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

        async def on_response(response) -> None:
            nonlocal request_count
            request_count += 1
            url = response.url
            ct = response.headers.get("content-type", "")
            if not is_media(url, ct):
                return
            key = f"{response.status}|{url}"
            if key in seen:
                return
            seen.add(key)
            item = {
                "time": round(time.monotonic(), 3),
                "status": response.status,
                "url": url,
                "host": urlparse(url).hostname,
                "content_type": ct,
                "content_length": response.headers.get("content-length"),
                "server": response.headers.get("server"),
                "location": response.headers.get("location"),
                "retry_after": response.headers.get("retry-after"),
                "cf_ray": response.headers.get("cf-ray"),
            }
            captured.append(item)
            print(
                f"[MEDIA] status={response.status} ct={ct!r} "
                f"host={item['host']} url={url[:500]}"
            )
            if response.headers.get("location"):
                print(f"[MEDIA] location={response.headers.get('location')}")

        page.on("response", on_response)

        print("[TEST] Opening source page...")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"[TEST] page.goto warning: {type(exc).__name__}: {exc}")

        await page.wait_for_timeout(5000)

        # Try native HTML5 video elements first.
        videos = await page.locator("video").all()
        print(f"[TEST] video elements: {len(videos)}")
        for i, video in enumerate(videos):
            try:
                await video.evaluate(
                    """v => { v.muted = true; v.autoplay = true; const p=v.play(); if(p) p.catch(()=>{}); }"""
                )
                print(f"[TEST] play() requested for video element #{i+1}")
            except Exception as exc:
                print(f"[TEST] video #{i+1} play warning: {type(exc).__name__}: {exc}")

        # Generic click fallback for custom players.
        selectors = [
            "button[aria-label*='play' i]",
            "button[title*='play' i]",
            ".vjs-big-play-button",
            ".plyr__control--overlaid",
            "[data-plyr='play']",
            ".jw-display-icon-container",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                if count:
                    await loc.first.click(timeout=2000, force=True)
                    print(f"[TEST] clicked player control: {selector}")
                    break
            except Exception:
                pass

        print(f"[TEST] Observing network for {args.wait}s...")
        await page.wait_for_timeout(max(1, args.wait) * 1000)

        # Inspect current browser media state.
        try:
            state = await page.locator("video").evaluate_all(
                """els => els.map(v => ({src:v.currentSrc || v.src, readyState:v.readyState, paused:v.paused, currentTime:v.currentTime, duration:v.duration}))"""
            )
            print("[TEST] VIDEO STATE:")
            print(json.dumps(state, indent=2, default=str))
        except Exception as exc:
            print(f"[TEST] video state unavailable: {type(exc).__name__}: {exc}")

        result_path = ROOT / "test_results" / "browser_play_capture.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        print(f"[TEST] Captured unique media responses: {len(captured)}")
        print(f"[TEST] All observed requests: {request_count}")
        print(f"[TEST] Saved: {result_path}")

        successful = [x for x in captured if 200 <= int(x["status"]) < 300]
        playlists = [x for x in successful if ".m3u8" in x["url"].lower() or "mpegurl" in x["content_type"].lower()]
        segments = [x for x in successful if any(ext in x["url"].lower() for ext in (".ts", ".m4s", ".mp4"))]
        print(f"[TEST] Successful media responses: {len(successful)}")
        print(f"[TEST] Successful HLS/DASH playlists: {len(playlists)}")
        print(f"[TEST] Successful media/segment responses: {len(segments)}")

        await context.close()
        await browser.close()

    if successful:
        print("[TEST] FINAL: SUCCESS - browser received successful media traffic")
        return 0
    print("[TEST] FINAL: FAIL - browser capture saw no successful media response")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
