#!/usr/bin/env python3
"""Test-only diagnostic: replay browser-captured HLS segments concurrently.

Production files are NOT modified. The browser first discovers real TS segment
URLs and the request headers Chrome actually sends. The test then replays a
small batch of those exact signed URLs concurrently through Playwright's
browser-context request client and validates every response body.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright


def segment_number(url: str) -> int:
    import re
    m = re.search(r"seg-(\d+)-", url)
    return int(m.group(1)) if m else 10**9


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--segments", type=int, default=4)
    parser.add_argument("--wait", type=int, default=90)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    print("[TEST] === BROWSER HLS PARALLEL REQUEST REPLAY TEST ===")
    print(f"[TEST] Source: {args.url}")
    print(f"[TEST] Capture target: {args.segments} segments")
    print(f"[TEST] Replay concurrency: {args.workers}")
    print("[TEST] Production files are NOT modified.")

    captured: dict[str, dict] = {}
    request_headers: dict[str, dict[str, str]] = {}

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
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable", {
            "maxTotalBufferSize": 64 * 1024 * 1024,
            "maxResourceBufferSize": 16 * 1024 * 1024,
        })
        await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        def on_request(params):
            req = params.get("request") or {}
            url = str(req.get("url") or "")
            if ".ts" not in url.lower():
                return
            headers = {str(k): str(v) for k, v in (req.get("headers") or {}).items()}
            request_headers[url] = headers

        def on_response(params):
            response = params.get("response") or {}
            url = str(response.get("url") or "")
            status = int(response.get("status") or 0)
            if ".ts" not in url.lower() or status != 200:
                return
            if url in captured:
                return
            captured[url] = {"headers": dict(request_headers.get(url, {}))}
            print(f"[CAPTURE] segment={segment_number(url)} status=200 host={urlparse(url).hostname}")

        cdp.on("Network.requestWillBeSent", on_request)
        cdp.on("Network.responseReceived", on_response)

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
                    print(f"[TEST] play() requested in frame={frame.url}")
                for selector in (".jwplayer", "[class*='play' i]", "button[aria-label*='play' i]", ".jw-icon-playback"):
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
        while time.monotonic() < deadline and len(captured) < max(1, args.segments):
            await page.wait_for_timeout(250)

        selected = sorted(captured.items(), key=lambda kv: segment_number(kv[0]))[:args.segments]
        if len(selected) < args.segments:
            print(f"[TEST] FINAL: FAIL - browser captured only {len(selected)} successful TS URLs; need {args.segments}")
            await context.close()
            await browser.close()
            return 1

        print("[TEST] Captured segments:", [segment_number(u) for u, _ in selected])
        print("[TEST] Starting parallel replay now...")

        api = context.request
        sem = asyncio.Semaphore(max(1, args.workers))
        results: list[dict] = []
        start = time.monotonic()

        async def replay(index: int, url: str, info: dict) -> None:
            async with sem:
                t0 = time.monotonic()
                try:
                    headers = dict(info["headers"])
                    for key in list(headers):
                        if key.lower() in {":authority", ":method", ":path", ":scheme", "content-length"}:
                            headers.pop(key, None)
                    response = await api.get(
                        url,
                        headers=headers,
                        timeout=30000,
                        fail_on_status_code=False,
                    )
                    body = await response.body()
                    elapsed = time.monotonic() - t0
                    result = {"index": index, "segment": segment_number(url), "status": response.status, "bytes": len(body), "seconds": elapsed}
                    results.append(result)
                    print(f"[REPLAY] #{index} segment={result['segment']} status={result['status']} bytes={result['bytes']} time={elapsed:.3f}s")
                except Exception as exc:
                    elapsed = time.monotonic() - t0
                    results.append({"index": index, "segment": segment_number(url), "status": None, "bytes": 0, "seconds": elapsed, "error": str(exc)})
                    print(f"[REPLAY] #{index} segment={segment_number(url)} ERROR={type(exc).__name__}: {exc} time={elapsed:.3f}s")

        await asyncio.gather(*(replay(i, url, info) for i, (url, info) in enumerate(selected, 1)))
        total = time.monotonic() - start
        successful = [r for r in results if r.get("status") == 200 and r.get("bytes", 0) > 0]
        max_single = max((r.get("seconds", 0) for r in results), default=0)
        sum_single = sum(r.get("seconds", 0) for r in results)
        print(f"[TEST] Parallel wall time: {total:.3f}s")
        print(f"[TEST] Sum of individual times: {sum_single:.3f}s")
        print(f"[TEST] Max individual time: {max_single:.3f}s")
        print(f"[TEST] Successful replays: {len(successful)}/{len(selected)}")

        await context.close()
        await browser.close()

    if len(successful) != len(selected):
        print("[TEST] FINAL: FAIL - not all parallel browser-context replays returned non-empty HTTP 200 bodies")
        return 1
    if len(selected) > 1 and total >= sum_single * 0.9:
        print("[TEST] FINAL: PASS - all replay requests succeeded, but little parallelism was observed")
        return 0
    print("[TEST] FINAL: SUCCESS - exact browser-captured HLS requests replayed concurrently")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
