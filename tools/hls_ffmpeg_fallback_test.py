#!/usr/bin/env python3
"""Diagnostic only: test fresh signed HLS -> FFmpeg -> valid MP4.

This tool does not modify production downloader behavior. The source URL is
supplied at runtime and is never embedded in the repository.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bot_vnext"))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def main(source: str) -> int:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("[TEST] ffmpeg/ffprobe is not installed")
        return 2

    out_dir = ROOT / "test_results/downloader/media"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "signed_hls_ffmpeg_test.mp4"
    output.unlink(missing_ok=True)

    task = TaskContext(task_id="signed-hls-ffmpeg-test", metadata={})
    downloader = HybridDownloader(out_dir)
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
    headers = {"User-Agent": user_agent, "Referer": source}

    try:
        print("[TEST] Discovering fresh signed HLS URL...")
        stream = await downloader._discover_hls(source, task, headers, None)
        if not stream:
            print("[TEST] DISCOVERY FAILED")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")

        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "warning",
            "-user_agent", user_agent,
            "-referer", source,
            "-i", stream,
            "-map", "0:v:0?",
            "-map", "0:a:0?",
            "-c", "copy",
            "-movflags", "+faststart",
            "-y", str(output),
        ]

        print("[TEST] Running FFmpeg against fresh signed HLS...")
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            print("[TEST] DOWNLOAD FAILED: FFmpeg timeout after 600s")
            return 1

        elapsed = time.monotonic() - started
        log = stdout.decode(errors="replace")
        if proc.returncode != 0:
            print(f"[TEST] FFmpeg exit code: {proc.returncode}")
            if log:
                print("[TEST] FFmpeg output:")
                print(log[-5000:])
            return 1

        if not output.is_file() or output.stat().st_size <= 0:
            print("[TEST] DOWNLOAD FAILED: FFmpeg exited 0 but no valid output")
            return 1

        probe = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration,size,format_name",
            "-of", "default=noprint_wrappers=1",
            str(output),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, probe_err = await probe.communicate()
        if probe.returncode != 0:
            print("[TEST] OUTPUT INVALID: ffprobe failed")
            print(probe_err.decode(errors="replace")[-3000:])
            return 1

        print(f"[TEST] DOWNLOAD SUCCESS: {output.stat().st_size} bytes in {elapsed:.1f}s")
        print("[TEST] ffprobe:")
        print(probe_out.decode(errors="replace").strip())
        return 0
    except Exception as exc:
        print(f"[TEST] DOWNLOAD FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await task.cleanup()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: hls_ffmpeg_fallback_test.py '<source-url>'")
    raise SystemExit(asyncio.run(main(sys.argv[1])))
