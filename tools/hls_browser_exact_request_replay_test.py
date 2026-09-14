#!/usr/bin/env python3
"""Test-only diagnostic: capture a successful browser HLS request, then replay it
through the same Playwright browser context with its cookies and headers.

Production files are NOT modified. This isolates whether browser-specific request
state is what makes the signed HLS URL succeed while plain HTTP gets 522/302.
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


def is_hls(url: str, content_type: str = "") -> bool:
    u = url.lower()
    ct = content_type.lower()
    return ".m3u8" in u or "mpegurl" in ct


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--wait", type=int, default=20)
    args = parser.parse_args()

    print("[TEST] === BROWSER EXACT HLS REQUEST REPLAY TEST ===")
    print(f"[TEST] Source: {args.url}")
    print("[TEST] Production files are NOT modified.")

    captured = []
    seen = set()

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

        async def on_response(response):
            if response.status != 200:
                return
            if not is_hls(response.url, response.headers.get("content-type", "")):
                return
            if response.url in seen:
                return
            seen.add(response.url)
            req = response.request
            try:
                headers = await req.all_headers()
            except Exception:
                headers = req.headers
            item = {
                "url": response.url,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "request_headers": headers,
                "response_headers": await response.all_headers(),
                "frame_url": req.frame.url if req.frame else None,
            }
            captured.append(item)
            print(f"[CAPTURE] status=200 host={urlparse(response.url).hostname}")
            print(f"[CAPTURE] url={response.url}")
            print(f"[CAPTURE] frame={item['frame_url']}")
            print(f"[CAPTURE] request headers keys={sorted(headers.keys())}")

        page.on("response", on_response)

        print("[TEST] Opening source page...")
        try:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            print(f"[TEST] page.goto warning: {type(exc).__name__}: {exc}")

        await page.wait_for_timeout(5000)

        # The working player is normally inside an iframe. Trigger playback in
        # every frame containing a native video element and common player controls.
        for frame in page.frames:
            try:
                videos = frame.locator("video")
                for i in range(await videos.count()):
                    await videos.nth(i).evaluate(
                        "v => { v.muted=true; v.autoplay=true; const p=v.play(); if(p) p.catch(()=>{}); }"
                    )
            except Exception:
                pass
            for selector in [
                ".jw-display-icon-container",
                ".jwplayer",
                "button[aria-label*='play' i]",
                "button[title*='play' i]",
                "[class*='play' i]",
            ]:
                try:
                    loc = frame.locator(selector)
                    if await loc.count():
                        await loc.first.click(timeout=2000, force=True)
                        break
                except Exception:
                    pass

        print(f"[TEST] Waiting {args.wait}s for browser HLS traffic...")
        await page.wait_for_timeout(max(1, args.wait) * 1000)

        if not captured:
            print("[TEST] FINAL: FAIL - no successful browser HLS request captured")
            await context.close()
            await browser.close()
            return 1

        target = captured[0]
        out = ROOT / "test_results" / "browser_exact_hls_request.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(target, indent=2), encoding="utf-8")

        print(f"[TEST] Captured HLS URL: {target['url']}")
        print("[TEST] Replaying exact captured request via browser context.request...")
        started = time.monotonic()
        try:
            replay = await context.request.get(
                target["url"],
                headers=target["request_headers"],
                timeout=45000,
                fail_on_status_code=False,
            )
            body = await replay.body()
            elapsed = time.monotonic() - started
            print(f"[REPLAY] status={replay.status} time={elapsed:.2f}s bytes={len(body)} host={urlparse(replay.url).hostname}")
            print(f"[REPLAY] content-type={replay.headers.get('content-type')} server={replay.headers.get('server')}")
            print(f"[REPLAY] retry-after={replay.headers.get('retry-after')} cf-ray={replay.headers.get('cf-ray')}")
            if replay.status == 200 and is_hls(replay.url, replay.headers.get("content-type", "")):
                print("[TEST] FINAL: SUCCESS - exact browser request replayed successfully")
                await context.close()
                await browser.close()
                return 0
        except Exception as exc:
            print(f"[REPLAY] ERROR={type(exc).__name__}: {exc}")

        await context.close()
        await browser.close()

    print("[TEST] FINAL: FAIL - browser request succeeded but exact replay did not")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
