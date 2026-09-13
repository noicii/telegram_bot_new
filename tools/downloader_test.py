#!/usr/bin/env python3
"""Standalone downloader diagnostic harness for Bot V2.

Runs the production HybridDownloader outside Telegram and records every run as
JSONL so source/CDN failures can be diagnosed before changing downloader code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = ROOT / "bot_vnext"
sys.path.insert(0, str(V2_ROOT))

from app.core.task import TaskContext  # noqa: E402
from app.downloader.engine import DownloadError, HybridDownloader  # noqa: E402

DEFAULT_RESULTS = ROOT / "test_results" / "downloader"
SECRET_QUERY_KEYS = {
    "t", "token", "sig", "signature", "expires", "exp", "e", "s", "key",
    "hdnts", "auth", "authorization", "password", "pass", "hmac",
}


def safe_url(value: str) -> str:
    """Redact common signed/auth query values before writing them to logs."""
    try:
        parts = urlsplit(value)
        if not parts.query:
            return value
        pairs = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            pairs.append((key, "<redacted>" if key.lower() in SECRET_QUERY_KEYS else val))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))
    except Exception:
        return "<unparseable-url>"


def exc_info(exc: BaseException) -> dict:
    text = str(exc) or exc.__class__.__name__
    status = None
    match = re.search(r"(?:status|HTTP|code)[^0-9]{0,8}(4\d\d|5\d\d)", text, re.I)
    if match:
        status = int(match.group(1))
    return {
        "type": exc.__class__.__name__,
        "message": text[:2000],
        "http_status": status,
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_once(downloader: HybridDownloader, url: str, method: str, output_dir: Path, run_no: int, timeout: float) -> dict:
    run_id = uuid.uuid4().hex[:12]
    task = TaskContext(task_id=f"diagnostic-{run_id}", metadata={"download_method": method})
    filename = f"diagnostic_{run_id}.mp4"
    started = time.monotonic()
    record = {
        "run_id": run_id,
        "run": run_no,
        "started_at": now(),
        "url": safe_url(url),
        "method": method,
        "status": "failed",
        "output": None,
        "output_bytes": 0,
        "duration_sec": None,
        "error": None,
        "traceback": None,
        "stream_url": None,
        "metadata": {},
    }

    async def progress(percent, current, total, speed, details=None):
        record["last_progress"] = {
            "percent": percent,
            "current_bytes": current,
            "total_bytes": total,
            "speed_bytes_sec": speed,
            "details": details or {},
        }

    try:
        output = await asyncio.wait_for(
            downloader.download(url, task, filename, progress),
            timeout=timeout if timeout > 0 else None,
        )
        size = output.stat().st_size if output.exists() else 0
        if size <= 0:
            raise DownloadError("Downloader returned an empty output file")
        record["status"] = "success"
        record["output"] = str(output)
        record["output_bytes"] = size
    except Exception as exc:
        record["error"] = exc_info(exc)
        record["traceback"] = traceback.format_exc(limit=12)[-6000:]
    finally:
        record["duration_sec"] = round(time.monotonic() - started, 3)
        record["stream_url"] = safe_url(str(task.metadata.get("stream_url"))) if task.metadata.get("stream_url") else None
        record["metadata"] = {
            k: v for k, v in task.metadata.items()
            if k not in {"cookies", "headers", "browser", "browser_page"}
            and isinstance(v, (str, int, float, bool, type(None), list, dict))
        }
        if record["status"] == "failed":
            await task.cleanup()
    return record


async def main(args: argparse.Namespace) -> int:
    results_dir = Path(args.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    output_dir = results_dir / "media"
    output_dir.mkdir(parents=True, exist_ok=True)
    session_file = results_dir / f"session_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}.jsonl"

    downloader = HybridDownloader(output_dir=output_dir, retries=args.retries)
    print(f"[TEST] URL: {safe_url(args.url)}")
    print(f"[TEST] Method: {args.method} | retries/attempt: {args.retries + 1} | timeout: {args.timeout}s")
    print(f"[TEST] Results: {session_file}")

    successes = 0
    runs = 0
    while runs < args.max_runs:
        runs += 1
        record = await run_once(downloader, args.url, args.method, output_dir, runs, args.timeout)
        with session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if record["status"] == "success":
            successes += 1
            print(f"[SUCCESS] run={runs} duration={record['duration_sec']}s size={record['output_bytes']} bytes")
            if args.until_success:
                break
        else:
            err = record["error"] or {}
            print(f"[FAILED ] run={runs} duration={record['duration_sec']}s type={err.get('type')} status={err.get('http_status')} message={err.get('message')}")
        if runs < args.max_runs and args.delay > 0:
            await asyncio.sleep(args.delay)

    summary = {
        "finished_at": now(),
        "runs": runs,
        "successes": successes,
        "failures": runs - successes,
        "until_success": args.until_success,
        "max_runs": args.max_runs,
        "method": args.method,
        "url": safe_url(args.url),
        "session_file": str(session_file),
    }
    summary_file = session_file.with_suffix(".summary.json")
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if successes else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bot V2 downloader diagnostic test harness")
    parser.add_argument("url", help="Source page/direct/HLS URL to test")
    parser.add_argument("--method", choices=("auto", "hls-multi", "yt-dlp", "browser", "ffmpeg", "aria2c", "direct"), default="hls-multi")
    parser.add_argument("--until-success", action="store_true", help="Keep testing until a successful download or --max-runs is reached")
    parser.add_argument("--max-runs", type=int, default=10, help="Maximum complete test runs; default: 10")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between runs; default: 2")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-run timeout in seconds; 0 disables timeout")
    parser.add_argument("--retries", type=int, default=2, help="Production downloader retries per selected method; default: 2")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS), help="Directory for JSONL results and downloaded test media")
    args = parser.parse_args()
    if args.max_runs < 1:
        parser.error("--max-runs must be >= 1")
    if args.retries < 0:
        parser.error("--retries must be >= 0")
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
