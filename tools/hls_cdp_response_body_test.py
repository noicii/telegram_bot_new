#!/usr/bin/env python3
"""Test-only diagnostic: capture HLS segment bodies through Chrome CDP.

Production files are NOT modified. Chromium playback is allowed to request the
media normally. CDP Network.responseReceived/loadingFinished events identify
successful HLS TS responses, and Network.getResponseBody retrieves the exact
browser-received bytes after loading finishes. Captured consecutive segments
are then remuxed to MP4 with ffmpeg and validated with ffprobe.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def segment_number(url: str) -> int:
    m = re.search(r"seg-(\d+)-", url)
    return int(m.group(1)) if m else 10**9


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--segments", type=int, default=10, help="number of successful TS segments")
    parser.add_argument("--wait", type=int, default=90, help="seconds to observe browser playback")
    args = parser.parse_args()

    print("[TEST] === BROWSER HLS CDP RESPONSE BODY TEST ===")
    print(f"[TEST] Source: {args.url}")
    print(f"[TEST] Target segments: {args.segments}")
    print("[TEST] Production files are NOT modified.")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[TEST] FINAL: FAIL - ffmpeg/ffprobe not installed")
        return 1

    work = ROOT / "test_results" / "hls_cdp_response_body"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("seg_*.ts"):
        old.unlink()
    output = work / "cdp_capture.mp4"
    if output.exists():
        output.unlink()

    responses: dict[str, dict] = {}
    captured: dict[str, bytes] = {}
    pending: set[asyncio.Task] = set()

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
            "maxTotalBufferSize": 128 * 1024 * 1024,
            "maxResourceBufferSize": 32 * 1024 * 1024,
        })
        await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        def on_response(params):
            request_id = params.get("requestId")
            response = params.get("response") or {}
            url = str(response.get("url") or "")
            mime = str(response.get("mimeType") or "")
            status = int(response.get("status") or 0)
            if request_id and ".ts" in url.lower() and status == 200:
                responses[request_id] = {"url": url, "mime": mime, "status": status}

        async def get_body(request_id: str) -> None:
            info = responses.get(request_id)
            if not info or info["url"] in captured:
                return
            try:
                result = await cdp.send("Network.getResponseBody", {"requestId": request_id})
                raw = result.get("body", "")
                if result.get("base64Encoded"):
                    body = base64.b64decode(raw)
                else:
                    body = raw.encode("latin-1")
                if body:
                    captured[info["url"]] = body
                    print(
                        f"[CDP] #{len(captured)} status=200 bytes={len(body)} "
                        f"host={urlparse(info['url']).hostname} url={info['url'][:260]}"
                    )
            except Exception as exc:
                print(f"[CDP] getResponseBody failed: {type(exc).__name__}: {exc}")

        def on_finished(params):
            request_id = params.get("requestId")
            if request_id in responses:
                task = asyncio.create_task(get_body(request_id))
                pending.add(task)
                task.add_done_callback(pending.discard)

        cdp.on("Network.responseReceived", on_response)
        cdp.on("Network.loadingFinished", on_finished)

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
        while time.monotonic() < deadline and len(captured) < max(1, args.segments):
            await page.wait_for_timeout(500)

        # Give the final loadingFinished handlers time to retrieve bodies.
        await page.wait_for_timeout(1500)
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)

        await cdp.detach()
        await context.close()
        await browser.close()

    if len(captured) < args.segments:
        print(f"[TEST] FINAL: FAIL - CDP captured {len(captured)} successful TS bodies; need {args.segments}")
        return 1

    ordered = sorted(captured.items(), key=lambda kv: segment_number(kv[0]))[: args.segments]
    nums = [segment_number(u) for u, _ in ordered]
    print(f"[TEST] Captured segment numbers: {nums}")
    if any(b - a != 1 for a, b in zip(nums, nums[1:])):
        print("[TEST] FINAL: FAIL - captured segments are not consecutive")
        return 1

    files: list[Path] = []
    for i, (_, body) in enumerate(ordered, 1):
        path = work / f"seg_{i:04d}.ts"
        path.write_bytes(body)
        files.append(path)

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in files), encoding="utf-8")
    print(f"[TEST] Wrote {len(files)} CDP-captured TS segments")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    print("[TEST] Remuxing CDP-captured media with ffmpeg...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[TEST] ffmpeg ERROR: {proc.stderr[-2000:]}")
        return 1

    if not output.is_file() or output.stat().st_size <= 0:
        print("[TEST] FINAL: FAIL - ffmpeg produced no valid output file")
        return 1

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration,size", "-of", "default=noprint_wrappers=1", str(output)],
        capture_output=True, text=True,
    )
    print("[TEST] ffprobe:")
    print(probe.stdout.strip() or probe.stderr.strip())
    if probe.returncode != 0:
        print("[TEST] FINAL: FAIL - ffprobe rejected captured MP4")
        return 1

    print(f"[TEST] OUTPUT: {output}")
    print(f"[TEST] OUTPUT SIZE: {output.stat().st_size} bytes")
    print("[TEST] FINAL: SUCCESS - CDP browser response bodies assembled into valid MP4")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
