#!/usr/bin/env python3
"""Test-only: capture Cloudflare diagnostics from a freshly discovered HLS URL."""
from __future__ import annotations
import argparse, asyncio, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; BOT_ROOT=ROOT/'bot_vnext'
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(BOT_ROOT))
import aiohttp
from app.core.task import TaskContext
from app.downloader.engine import HybridDownloader

async def main():
    p=argparse.ArgumentParser(); p.add_argument('url'); a=p.parse_args()
    d=HybridDownloader(ROOT/'test_results'/'downloader'/'media',retries=0)
    task=TaskContext(task_id=f'cf-diag-{int(time.time())}')
    headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'}
    print('[TEST] === ATTEMPT 15: CLOUDFLARE 522 DIAGNOSTIC ===')
    print('[TEST] Discovery -> same signed HLS URL -> diagnostic response headers/body')
    try:
        stream=await asyncio.wait_for(d._discover_hls(a.url,task,{'User-Agent':'Mozilla/5.0'},None),180)
        if not stream: print('[TEST] FAIL: no HLS URL'); return 1
        print('[TEST] HLS DISCOVERY SUCCESS')
        print(f'[TEST] CDN HOST: {stream.split("/",3)[2]}')
        timeout=aiohttp.ClientTimeout(total=35,connect=10,sock_read=25)
        async with aiohttp.ClientSession(timeout=timeout,headers=headers) as s:
            try:
                async with s.get(stream,allow_redirects=True) as r:
                    body=await r.text(errors='replace')
                    print(f'[TEST] STATUS: {r.status}')
                    print(f'[TEST] CONTENT-TYPE: {r.headers.get("Content-Type","")}')
                    for k in ('cf-error-type','cf-error-origin','cf-ray','cf-cache-status','retry-after','server','date'):
                        print(f'[TEST] {k}: {r.headers.get(k,"<absent>")}')
                    print('[TEST] BODY PREVIEW:')
                    print(body[:1200].replace('\n',' ')[:1200])
            except Exception as e:
                print(f'[TEST] REQUEST ERROR: {type(e).__name__}: {e}')
        print('[TEST] FINAL: COMPLETE')
        return 0
    except Exception as e:
        print(f'[TEST] FINAL: FAIL - {type(e).__name__}: {e}'); return 1

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
