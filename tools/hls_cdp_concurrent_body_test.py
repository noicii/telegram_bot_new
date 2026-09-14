#!/usr/bin/env python3
"""Test-only diagnostic: retrieve browser HLS response bodies with concurrent CDP workers.

Production files are NOT modified. The browser plays the source normally. CDP
Network events identify successful TS responses; a bounded asyncio worker pool
then calls Network.getResponseBody concurrently. Captured consecutive segments
are remuxed with ffmpeg and validated with ffprobe.
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
    parser.add_argument("--segments", type=int, default=9)
    parser.add_argument("--wait", type=int, default=90)
    parser.add_argument("--workers", type=int, default=4, help="concurrent CDP body workers")
    args = parser.parse_args()
    target = max(1, args.segments)
    workers_n = max(1, args.workers)

    print("[TEST] === BROWSER HLS CDP CONCURRENT BODY TEST ===")
    print(f"[TEST] Source: {args.url}")
    print(f"[TEST] Target segments: {target}")
    print(f"[TEST] CDP body workers: {workers_n}")
    print("[TEST] Production files are NOT modified.")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[TEST] FINAL: FAIL - ffmpeg/ffprobe not installed")
        return 1

    work = ROOT / "test_results" / "hls_cdp_concurrent_body"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("seg_*.ts"):
        old.unlink()
    output = work / "cdp_concurrent_capture.mp4"
    if output.exists():
        output.unlink()

    responses: dict[str, dict] = {}
    captured: dict[str, bytes] = {}
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    queued: set[str] = set()
    state = {"inflight": 0, "max_inflight": 0}

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
            status = int(response.get("status") or 0)
            if request_id and ".ts" in url.lower() and status == 200:
                responses[request_id] = {"url": url, "status": status}

        cdp.on("Network.responseReceived", on_response)

        async def body_worker(worker_id: int) -> None:
            while True:
                request_id = await queue.get()
                try:
                    if request_id is None:
                        return
                    info = responses.get(request_id)
                    if not info or info["url"] in captured:
                        continue
                    state["inflight"] += 1
                    state["max_inflight"] = max(state["max_inflight"], state["inflight"])
                    started = time.monotonic()
                    try:
                        result = await cdp.send("Network.getResponseBody", {"requestId": request_id})
                        raw = result.get("body", "")
                        body = base64.b64decode(raw) if result.get("base64Encoded") else raw.encode("latin-1")
                        if body:
                            captured[info["url"]] = body
                            print(
                                f"[CDP-W{worker_id}] #{len(captured)} bytes={len(body)} "
                                f"time={time.monotonic()-started:.3f}s "
                                f"host={urlparse(info['url']).hostname} url={info['url'][:220]}"
                            )
                    except Exception as exc:
                        print(f"[CDP-W{worker_id}] getResponseBody failed: {type(exc).__name__}: {exc}")
                    finally:
                        state["inflight"] -= 1
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(body_worker(i + 1)) for i in range(workers_n)]

        def on_finished(params):
            request_id = params.get("requestId")
            if request_id in responses and request_id not in queued:
                queued.add(request_id)
                queue.put_nowait(request_id)

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
        while time.monotonic() < deadline and len(captured) < target:
            await page.wait_for_timeout(500)

        await page.wait_for_timeout(1500)
        await queue.join()
        print(f"[TEST] CDP maximum concurrent body calls observed: {state['max_inflight']}")

        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers, return_exceptions=True)
        await cdp.detach()
        await context.close()
        await browser.close()

    if len(captured) < target:
        print(f"[TEST] FINAL: FAIL - captured {len(captured)} successful TS bodies; need {target}")
        return 1

    ordered = sorted(captured.items(), key=lambda kv: segment_number(kv[0]))[:target]
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
    print(f"[TEST] Wrote {len(files)} concurrent-CDP-captured TS segments")

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(output)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"[TEST] ffmpeg ERROR: {proc.stderr[-2000:]}")
        return 1

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration,size", "-of", "default=noprint_wrappers=1", str(output)],
        capture_output=True, text=True,
    )
    print("[TEST] ffprobe:")
    print(probe.stdout.strip() or probe.stderr.strip())
    if probe.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        print("[TEST] FINAL: FAIL - output validation failed")
        return 1

    print(f"[TEST] OUTPUT: {output}")
    print(f"[TEST] OUTPUT SIZE: {output.stat().st_size} bytes")
    print("[TEST] FINAL: SUCCESS - concurrent CDP response bodies assembled into valid MP4")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
