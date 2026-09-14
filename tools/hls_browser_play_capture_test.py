#!/usr/bin/env python3
"""Test-only diagnostic: inspect iframe/custom players and capture browser media traffic.

Production files are NOT modified. This test specifically handles pages where the
player is embedded in an iframe and where Service Workers can hide network events.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def media_like(url: str, ct: str = "") -> bool:
    u = url.lower()
    c = ct.lower()
    return (
        any(x in u for x in (".m3u8", ".mpd", ".mp4", ".m4s", ".ts", ".aac", ".webm"))
        or "mpegurl" in c
        or "dash+xml" in c
        or c.startswith("video/")
        or c.startswith("audio/")
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--wait", type=int, default=30)
    args = parser.parse_args()

    print("[TEST] === BROWSER PLAYBACK + IFRAME MEDIA CAPTURE TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    captured = []
    all_requests = []
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            service_workers="block",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1365, "height": 768},
        )
        page = await context.new_page()

        async def on_request(request):
            all_requests.append({"method": request.method, "url": request.url})

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if not media_like(url, ct):
                return
            key = (response.status, url)
            if key in seen:
                return
            seen.add(key)
            item = {
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
            print(f"[MEDIA] status={response.status} ct={ct!r} host={item['host']} url={url[:500]}")
            if item["location"]:
                print(f"[MEDIA] location={item['location']}")

        page.on("request", on_request)
        page.on("response", on_response)

        print("[TEST] Opening source page...")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"[TEST] page.goto warning: {type(exc).__name__}: {exc}")

        await page.wait_for_timeout(5000)

        frames = page.frames
        print(f"[TEST] FRAME COUNT: {len(frames)}")
        for i, frame in enumerate(frames):
            print(f"[FRAME {i}] url={frame.url[:500]}")
            try:
                print(f"[FRAME {i}] title={await frame.title()!r}")
            except Exception:
                pass
            try:
                print(f"[FRAME {i}] video_count={await frame.locator('video').count()}")
            except Exception:
                print(f"[FRAME {i}] video_count=ERROR")

        # Try playback controls and native video in every frame, not only main page.
        selectors = [
            "video",
            "button[aria-label*='play' i]",
            "button[title*='play' i]",
            ".vjs-big-play-button",
            ".plyr__control--overlaid",
            "[data-plyr='play']",
            ".jw-display-icon-container",
            ".jwplayer",
            "[class*='play' i]",
        ]
        for fi, frame in enumerate(list(page.frames)):
            for selector in selectors:
                try:
                    loc = frame.locator(selector)
                    count = await loc.count()
                    if not count:
                        continue
                    print(f"[TEST] frame={fi} selector={selector!r} count={count}")
                    if selector == "video":
                        for n in range(min(count, 3)):
                            try:
                                await loc.nth(n).evaluate("v => { v.muted=true; v.autoplay=true; const p=v.play(); if(p) p.catch(()=>{}); }")
                                print(f"[TEST] frame={fi} video #{n+1}: play() requested")
                            except Exception as exc:
                                print(f"[TEST] frame={fi} video play warning: {type(exc).__name__}: {exc}")
                    else:
                        try:
                            await loc.first.click(timeout=2500, force=True)
                            print(f"[TEST] frame={fi}: clicked {selector}")
                        except Exception as exc:
                            print(f"[TEST] frame={fi}: click warning for {selector}: {type(exc).__name__}")
                except Exception:
                    pass

        print(f"[TEST] Observing network for {args.wait}s...")
        await page.wait_for_timeout(max(1, args.wait) * 1000)

        print("[TEST] FINAL FRAME STATE:")
        for i, frame in enumerate(page.frames):
            try:
                state = await frame.locator("video").evaluate_all(
                    "els => els.map(v => ({src:v.currentSrc||v.src,readyState:v.readyState,paused:v.paused,currentTime:v.currentTime,duration:v.duration}))"
                )
                print(f"[FRAME {i}] videos={json.dumps(state, default=str)}")
            except Exception:
                pass

        result_path = ROOT / "test_results" / "browser_play_capture.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps({"media": captured, "requests": all_requests}, indent=2),
            encoding="utf-8",
        )
        print(f"[TEST] Captured unique media responses: {len(captured)}")
        print(f"[TEST] All observed requests: {len(all_requests)}")
        print(f"[TEST] Saved: {result_path}")

        successful = [x for x in captured if 200 <= int(x["status"]) < 300]
        playlists = [x for x in successful if ".m3u8" in x["url"].lower() or "mpegurl" in x["content_type"].lower()]
        segments = [x for x in successful if any(xext in x["url"].lower() for xext in (".ts", ".m4s", ".mp4"))]
        print(f"[TEST] Successful media responses: {len(successful)}")
        print(f"[TEST] Successful HLS/DASH playlists: {len(playlists)}")
        print(f"[TEST] Successful media/segment responses: {len(segments)}")

        await context.close()
        await browser.close()

    if successful:
        print("[TEST] FINAL: SUCCESS - browser received successful media traffic")
        return 0
    print("[TEST] FINAL: FAIL - no successful media response captured")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
