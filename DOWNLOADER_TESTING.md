# Downloader Diagnostic Testing

## Purpose

Use this harness to test a real source against the **same production `HybridDownloader` code** before changing downloader logic. It is intended for problems such as HTTP 4xx/5xx responses, signed HLS failures, intermittent CDN errors, discovery failures, segment failures, FFmpeg failures, and method-specific regressions.

The test runs outside Telegram, so a failure can be isolated as a downloader/source problem instead of mixing it with queue, dashboard, or Telegram upload behavior.

## Golden process

Follow this order for downloader incidents:

1. **Do not edit the downloader first.** Capture the failing source URL from `/failed` or the original test case.
2. Run the diagnostic harness on the server using the exact failing URL.
3. Start with the method that failed in production, for example `hls-multi`.
4. If the failure is intermittent, use `--until-success` with a sensible `--max-runs` limit. Never run an unbounded loop.
5. Keep the complete JSONL session result. It records every run, duration, output size, error type/message, HTTP status when detectable, traceback, last progress details, and discovered stream URL (with common signed query values redacted).
6. Compare failures and successes. Look for HTTP status, the failing phase, retry behavior, timing, and whether the same URL succeeds later.
7. **Only after evidence is collected**, decide whether the problem is source/CDN-side, discovery/browser-side, HLS segment handling, FFmpeg/muxing, or downloader code.
8. If a code change is justified, change the smallest production component possible, document the hypothesis and evidence, then rerun the same diagnostic test.
9. A fix is not accepted merely because one run succeeds. For intermittent failures, repeat enough times to demonstrate materially improved reliability.
10. After validation, clean up test media/results as appropriate. Do not commit downloaded media or signed URLs containing secrets.

## Server command

From the repository root:

```bash
cd ~/telegram_bot_new && ./venv/bin/python tools/downloader_test.py 'FAILED_URL' --method hls-multi --until-success --max-runs 10
```

The command stops at the first successful download, or after 10 complete runs. Change `--max-runs` when a different bounded sample is needed.

For a single reproduction:

```bash
cd ~/telegram_bot_new && ./venv/bin/python tools/downloader_test.py 'FAILED_URL' --method hls-multi --max-runs 1
```

For the complete automatic production plan:

```bash
cd ~/telegram_bot_new && ./venv/bin/python tools/downloader_test.py 'FAILED_URL' --method auto --until-success --max-runs 10
```

## What the result means

Each run is written as one JSON object in:

`test_results/downloader/session_<UTC timestamp>_<id>.jsonl`

A summary is written beside it as:

`test_results/downloader/session_<UTC timestamp>_<id>.summary.json`

Important fields:

- `status`: `success` or `failed`
- `duration_sec`: complete downloader runtime for that run
- `output_bytes`: successful output size
- `error.type`: Python exception class
- `error.message`: exact failure message captured by the harness
- `error.http_status`: detected HTTP 4xx/5xx status when present
- `traceback`: bounded traceback for identifying the production code path
- `last_progress`: final progress/details emitted by the downloader
- `stream_url`: discovered HLS URL when available, with common signed/auth query values redacted
- `metadata`: safe serializable diagnostic metadata; cookies, headers, and browser objects are excluded

### Example diagnosis

If the result repeatedly shows:

`http_status: 522`

and the failure occurs while fetching the discovered HLS URL, treat that first as a **source/CDN connectivity problem**, not proof that HLS concurrency or downloader code is wrong.

If the same source repeatedly succeeds in the harness but fails only through the Telegram bot, investigate the bot pipeline/queue/runtime path instead of changing the downloader blindly.

If the same method fails consistently while another method succeeds, compare the method implementations and only then consider a downloader change.

## Evidence to send back for analysis

When asking for a downloader fix, send:

1. The complete terminal output from the test command.
2. The relevant `session_*.jsonl` result file if available.
3. The final summary JSON.
4. The production error from `/failed`.
5. Whether the same URL eventually succeeded and on which run.

Do **not** paste raw signed URLs or cookies into public issues/chats. The harness redacts common signed query parameters in its recorded URL fields, but treat source URLs as sensitive and review logs before sharing.

## Result retention

`test_results/downloader/` is diagnostic runtime output, not source code. Keep useful result files long enough to compare a fix, but do not commit downloaded video files, cookies, headers, or raw authentication material.

The downloader test harness is deliberately separate from `update.sh`: running diagnostics must **not deploy, restart, or modify the production bot**.
