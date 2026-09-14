#!/usr/bin/env python3
"""Diagnostic only: compare Playwright request access to the same signed HLS URL.
Never prints the signed URL or cookie values. Does not modify production downloader behavior.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot_vnext"))
from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader

SOURCE = sys.argv[1] if len(sys.argv) > 1 else "https://luluvdo.com/d/x71u2m9rhqcd"

async def main():
    from playwright.async_api import async_playwright
    task = TaskContext(task_id="playwright-request-diag", metadata={})
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}
    d = HybridDownloader(Path("test_results/downloader/media"))
    print("[DIAG] Discovering HLS with production Playwright flow...")
    stream = await d._discover_hls(SOURCE, task, headers, None)
    if not stream:
        print("[DIAG] DISCOVERY FAILED")
        return
    cookies = task.metadata.get("cookies") or {}
    print(f"[DIAG] DISCOVERY OK | cookies={len(cookies)}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=headers["User-Agent"], extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Referer": SOURCE})
        try:
            if cookies:
                await context.add_cookies([{"name": k, "value": str(v), "domain": "." + __import__('urllib.parse').parse.urlparse(stream).hostname.split('.', 1)[-1], "path": "/"} for k, v in cookies.items()])
            print("[DIAG] Fetching SAME signed stream with Playwright context.request...")
            response = await context.request.get(stream, headers={"Accept": "*/*"}, timeout=60000)
            body = await response.body()
            print(f"[DIAG] PLAYWRIGHT CONTEXT HTTP: {response.status}")
            print(f"[DIAG] CONTENT-TYPE: {response.headers.get('content-type', '<none>')}")
            print(f"[DIAG] BYTES: {len(body)}")
            print("[DIAG] === PLAYWRIGHT REQUEST TEST COMPLETE ===")
        finally:
            await context.close()
            await browser.close()
    await task.cleanup()

asyncio.run(main())
