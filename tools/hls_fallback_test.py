#!/usr/bin/env python3
"""Diagnostic only: test signed-HLS -> yt-dlp with browser request context.

This tool does not modify production downloader behavior. The URL is supplied
at runtime; no source-specific URL or provider is embedded in the repository.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bot_vnext"))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader
from config import COOKIES_PATH


def _safe_line(line: str) -> str:
    line = re.sub(r"https?://[^\s]+", "<redacted-url>", line)
    return line[-1000:]


async def main(source: str) -> int:
    binary = shutil.which("yt-dlp")
    if not binary:
        print("[TEST] yt-dlp is not installed")
        return 2

    out_dir = ROOT / "test_results/downloader/media"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "signed_hls_fallback_test.mp4"
    output.unlink(missing_ok=True)

    task = TaskContext(task_id="signed-hls-fallback-test", metadata={})
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
            binary,
            "--newline",
            "--no-part",
            "--user-agent", user_agent,
            "--referer", source,
            "--add-header", "Accept-Language: en-US,en;q=0.9",
            "--add-header", "Accept: */*",
            "--add-header", "Sec-Fetch-Dest: empty",
            "--add-header", "Sec-Fetch-Mode: cors",
            "--add-header", "Sec-Fetch-Site: cross-site",
        ]
        if COOKIES_PATH.is_file():
            command += ["--cookies", str(COOKIES_PATH)]
            print("[TEST] Using configured browser cookies")
        else:
            print("[TEST] No configured cookie file found")
        command += [
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", str(output),
            stream,
        ]

        print("[TEST] Running yt-dlp with browser-like headers + cookies...")
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
            print("[TEST] DOWNLOAD FAILED: yt-dlp timeout after 600s")
            return 1

        lines = stdout.decode(errors="replace").splitlines()
        if proc.returncode != 0:
            print(f"[TEST] yt-dlp exit code: {proc.returncode}")
            print("[TEST] yt-dlp diagnostic output (last 30 lines):")
            for line in lines[-30:]:
                print("[YTDLP]", _safe_line(line))
            return 1

        if not output.is_file() or output.stat().st_size <= 0:
            print("[TEST] DOWNLOAD FAILED: yt-dlp exited 0 but no valid output")
            return 1

        print(f"[TEST] DOWNLOAD SUCCESS: {output.stat().st_size} bytes")
        return 0
    except Exception as exc:
        print(f"[TEST] DOWNLOAD FAILED: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await task.cleanup()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: hls_fallback_test.py '<source-url>'")
    raise SystemExit(asyncio.run(main(sys.argv[1])))
