# Changelog

## 2026-09-13

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
