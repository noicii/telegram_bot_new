#!/usr/bin/env python3
"""Test-only diagnostic: capture real browser HLS segment bodies and assemble them.

Production files are NOT modified. The browser plays the source page, successful
HLS .ts responses are captured from the browser itself, then the captured TS
bodies are concatenated and remuxed to MP4 with ffmpeg. This proves that the
browser-delivered media bytes can form a valid local video artifact.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def segment_number(url: str) -> int:
    m = re.search(r"seg-(\d+)-", url)
    return int(m.group(1)) if m else 10**9


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="source page URL")
    parser.add_argument("--segments", type=int, default=8, help="number of successful TS segments to capture")
    parser.add_argument("--wait", type=int, default=45, help="seconds to observe browser playback")
    parser.add_argument("--rate", type=float, default=1.0, help="browser playback rate; >1 speeds up capture for testing")
    args = parser.parse_args()

    print("[TEST] === BROWSER HLS CAPTURE -> MP4 TEST ===")
    print(f"[TEST] Source: {args.url}")
    print(f"[TEST] Playback rate: {args.rate}x")
    print("[TEST] Production files are NOT modified.")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[TEST] FINAL: FAIL - ffmpeg/ffprobe not installed")
        return 1

    work = ROOT / "test_results" / "browser_capture_mp4"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("seg_*.ts"):
        old.unlink()
    output = work / "browser_capture.mp4"
    if output.exists():
        output.unlink()

    captured: dict[str, bytes] = {}
    seen: set[str] = set()

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
            url = response.url
            if ".ts" not in url.lower():
                return
            if response.status != 200 or url in seen:
                return
            seen.add(url)
            try:
                body = await response.body()
            except Exception as exc:
                print(f"[SEGMENT] body read failed: {type(exc).__name__}: {exc}")
                return
            if not body:
                return
            captured[url] = body
            print(f"[SEGMENT] #{len(captured)} status=200 bytes={len(body)} url={url[:260]}")

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
                        """(v, rate) => {
                            v.muted = true;
                            v.autoplay = true;
                            v.playbackRate = rate;
                            const p = v.play();
                            if (p) p.catch(() => {});
                        }""",
                        args.rate,
                    )
                    print(f"[TEST] play() requested in frame={frame.url} rate={args.rate}x")
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
            for frame in page.frames:
                try:
                    videos = frame.locator("video")
                    if await videos.count():
                        await videos.first.evaluate(
                            """(v, rate) => {
                                if (v.playbackRate !== rate) v.playbackRate = rate;
                                if (v.paused) { const p = v.play(); if (p) p.catch(() => {}); }
                            }""",
                            args.rate,
                        )
                except Exception:
                    pass
            await page.wait_for_timeout(500)

        await page.wait_for_timeout(1000)
        await context.close()
        await browser.close()

    if len(captured) < args.segments:
        print(f"[TEST] FINAL: FAIL - captured only {len(captured)} successful TS segments; need {args.segments}")
        return 1

    ordered = sorted(captured.items(), key=lambda kv: segment_number(kv[0]))[: args.segments]
    nums = [segment_number(u) for u, _ in ordered]
    print(f"[TEST] Captured segment numbers: {nums}")
    if any(b - a != 1 for a, b in zip(nums, nums[1:])):
        print("[TEST] FINAL: FAIL - captured segments are not consecutive")
        return 1

    files: list[Path] = []
    for i, (url, body) in enumerate(ordered, 1):
        path = work / f"seg_{i:04d}.ts"
        path.write_bytes(body)
        files.append(path)

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in files), encoding="utf-8")
    print(f"[TEST] Wrote {len(files)} browser-delivered TS segments")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    print("[TEST] Remuxing captured browser media with ffmpeg...")
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
    print("[TEST] FINAL: SUCCESS - browser-delivered HLS segments assembled into valid MP4")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
