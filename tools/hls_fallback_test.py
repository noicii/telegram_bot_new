#!/usr/bin/env python3
"""Diagnostic only: test the generic signed-HLS -> yt-dlp fallback path.

This tool does not modify production downloader behavior. The URL is supplied
at runtime; no source-specific URL or provider is embedded in the repository.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot_vnext"))

from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader


async def main(source: str) -> int:
    if not shutil.which("yt-dlp"):
        print("[TEST] yt-dlp is not installed")
        return 2

    out_dir = Path("test_results/downloader/media")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "signed_hls_fallback_test.mp4"
    output.unlink(missing_ok=True)

    task = TaskContext(task_id="signed-hls-fallback-test", metadata={})
    downloader = HybridDownloader(out_dir)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    try:
        print("[TEST] Discovering fresh signed HLS URL...")
        stream = await downloader._discover_hls(source, task, headers, None)
        if not stream:
            print("[TEST] DISCOVERY FAILED")
            return 1
        print("[TEST] HLS DISCOVERY SUCCESS")

        task.metadata["stream_url"] = stream
        task.metadata["headers"] = headers
        print("[TEST] Running yt-dlp against the fresh signed HLS URL...")
        await downloader._ytdlp(stream, output, task, None)

        if not output.is_file() or output.stat().st_size <= 0:
            print("[TEST] DOWNLOAD FAILED: no valid output")
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
