# Changelog

## 2026-09-15

### Reliability hardening
- Added an atomic database task-state transition primitive so completion, upload handoff, failure, cancellation, and download start cannot overwrite a newer terminal state after a race.
- Updated the pipeline to use guarded state transitions during download-to-upload handoff and final completion/failure paths.
- Added a regression test proving two concurrent state transitions can only claim the same queued task once.
- Browser HLS temporary work directories are registered with task cleanup and explicitly removed after browser shutdown; task-level cleanup also recursively removes registered directories.
- Release guard now verifies the atomic state-transition contract and Browser HLS cleanup contract before deployment.

### Retry safety
- Retry history remains monotonic and is enforced atomically when a failed/cancelled task is requeued.

## 2026-09-13

### Downloader diagnostic test harness
- Added `tools/downloader_test.py` to run the production `HybridDownloader` directly on the server without Telegram.
- Added bounded repeated testing with `--until-success` and `--max-runs` so intermittent CDN/HLS failures can be reproduced safely until a successful run is observed or the test limit is reached.
- Added per-run JSONL diagnostics containing success/failure, duration, output size, exception type/message, detected HTTP status, traceback, last progress details, and discovered HLS stream URL with common signed/auth query values redacted.
- Added `DOWNLOADER_TESTING.md` documenting the evidence-first troubleshooting process and interpretation of source/CDN versus downloader failures.
- Added `test_results/` to `.gitignore` so downloaded diagnostic media and local result logs are not committed.

### Telegram FloodGate / dashboard resilience
- Added adaptive Telegram FloodGate for upload attempts and live dashboard edits.
- Upload FloodWaits now pause new send attempts for the exact Telegram cooldown and retry without cancelling the task.
- Upload concurrency gate adapts between 1 and 4 based on successful sends and FloodWait feedback.
- Dashboard updates now use latest-state coalescing with an adaptive edit interval and exact FloodWait cooldown.
- Dashboard message lookup is cached at runtime to avoid unnecessary Telegram `get_messages` calls.
- Removed the old fixed 3-second progress forwarding throttle from the pipeline.
- Pyrogram `sleep_threshold` is set to `0` so FloodWait handling is controlled by the shared gate.
- Existing 2 download workers, 4 upload workers, and HLS 16-segment concurrency are preserved.

### HLS dashboard progress
- Fixed live HLS dashboard rendering so segmented downloads use **completed segments / total segments** as the authoritative percentage.
- HLS progress no longer displays a misleading `Downloaded MB / 0 MB` total.
- HLS dashboard now shows segment count, calculated percentage, downloaded bytes, speed, ETA when available, and retries.
- Non-HLS downloads keep the existing byte-based `current / total` display.

### Callback / selection controls
- Restored individual episode checkbox handling for `v2:t:<index>` callbacks.
- Restored the selection-page **Change method** callback and session method updates.
- Restored queue **Cancel** button callback handling for `v2:cancel:<task_id>`.
