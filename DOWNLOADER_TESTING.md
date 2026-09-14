# Downloader Diagnostic Testing

## Purpose

Use this harness to test a real source against the **same production `HybridDownloader` code** before changing downloader logic. It is intended for problems such as HTTP 4xx/5xx responses, signed HLS failures, intermittent CDN errors, discovery failures, segment failures, FFmpeg failures, and method-specific regressions.

The test runs outside Telegram, so a failure can be isolated as a downloader/source problem instead of mixing it with queue, dashboard, or Telegram upload behavior.

## 🚨 Generic-fix rule — mandatory for every AI/agent

A test URL is a **diagnostic sample only**. It must never become the target of a source-specific workaround.

When a downloader problem is found:

- Fix the **generic production downloader behavior**, not the individual URL, domain, CDN, token, path, or episode.
- Do not add hardcoded special cases for a test URL, hostname, provider, CDN, signed parameter, or page pattern unless the user explicitly approves a provider-specific feature.
- Before accepting a fix, ask: **“Would this change improve the downloader for other valid links using the same failure pattern?”** If not, do not treat it as a generic downloader fix.
- Validate the fix with the original failing link, then validate with other different links/source patterns where practical.
- One successful test URL does **not** prove the downloader is fixed globally.
- Never reduce or change global speed/concurrency/retry behavior merely to make one source pass unless the evidence shows the production-wide setting is actually the root cause and the user approves the change when required.

This rule must be followed whenever an AI agent, developer, or future troubleshooting session runs this test harness.

## Golden process

Follow this order for downloader incidents:

1. **Do not edit the downloader first.** Capture the failing source URL from `/failed` or the original test case.
2. Run the diagnostic harness on the server using the exact failing URL.
3. Start with the method that failed in production, for example `hls-multi`.
4. For the user's evidence-first workflow, run **one bounded attempt at a time**. Do not automatically loop until success. A successful run is evidence, not permission to stop investigating a reproducible failure.
5. Keep the complete JSONL session result. It records every run, duration, output size, error type/message, HTTP status when detectable, traceback, last progress details, and discovered stream URL (with common signed query values redacted).
6. Compare failures and successes. Look for HTTP status, the failing phase, retry behavior, timing, and whether the same URL succeeds later.
7. **Only after evidence is collected**, decide whether the problem is source/CDN-side, discovery/browser-side, HLS segment handling, FFmpeg/muxing, or downloader code.
8. If a code change is justified, change the smallest **generic production component** possible, document the hypothesis and evidence, then rerun the same diagnostic test.
9. If the test fails again, stop after that attempt, diagnose the new evidence, make the next generic fix if justified, and test again. Continue as **Attempt 1 → diagnose/fix → Attempt 2 → diagnose/fix → ... → SUCCESS**.
10. After the original case succeeds, validate that the change does not regress other link types/source patterns when practical. Do not claim a global fix from one URL alone.
11. After validation, clean up test media/results as appropriate. Do not commit downloaded media or signed URLs containing secrets.

## Server command

From the repository root:

```bash
cd ~/telegram_bot_new && ./venv/bin/python tools/downloader_test.py 'FAILED_URL' --method hls-multi --max-runs 1 --retries 2
```

Run one diagnostic attempt at a time. Do **not** use `--until-success` for the normal troubleshooting workflow. The next attempt should happen only after the previous result has been reviewed and the downloader change, if any, has been made.

For the complete automatic production plan:

```bash
cd ~/telegram_bot_new && ./venv/bin/python tools/downloader_test.py 'FAILED_URL' --method auto --max-runs 1 --retries 2
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

The downloader test harness is deliberately separate from `update.sh`: running diagnostics must **not** deploy, restart, or modify the production bot.
