# Changelog — Telegram Bot V2

## 2026-09-14 — Adaptive Telegram FloodGate

- Removed the fixed 3-second pipeline progress throttle.
- Added a dedicated adaptive `TelegramFloodGate` for Telegram-facing upload and dashboard operations.
- Upload FloodWaits now pause new Telegram send attempts and resume after Telegram's exact wait instead of cancelling the upload task.
- Upload concurrency adapts between 1 and 4 based on observed FloodWait pressure and sustained successful sends.
- Dashboard updates are coalesced to latest state; stale progress edits are not replayed after a FloodWait.
- Dashboard messages are locally cached so routine progress refreshes no longer require an extra `get_messages` API request before every edit.
- Pyrogram short FloodWait auto-sleep is disabled so the shared gate receives Telegram's exact FloodWait signal and coordinates retries centrally.
- Existing 2 download workers, 4 upload workers, HLS 16-segment concurrency, destination routing, large-file splitting, thumbnail handling, and storage cleanup remain unchanged.

## 2026-09-13 — Canonical self-bootstrapping deployment

- Reworked `update.sh` into the single canonical production deployment path.
- Removed the fragile preflight that required `telegram-bot.service` to already exist before the updater could install/update it.
- The updater now stops the service if present, fetches/resets `bot-vnext`, installs the tracked `telegram-bot.service`, reloads/enables systemd, validates the active V2 code, starts the service, and verifies exactly one V2 process.
- Fixed the production service name to `telegram-bot.service` so different update paths cannot silently target different supervisors.
- Removed the updater's runtime HLS `sed` mutation; HLS concurrency is now validated from tracked source instead of being changed during deployment.
- Strengthened deployment documentation so future developers/AI agents have one update method and one failure/recovery procedure.
- `.env` and the Python virtualenv remain preserved; disposable queue/database/runtime state remains intentionally reset.

## 2026-09-13 — Single-instance production service

- Made `telegram-bot.service` the only supported production launcher.
- Added `bot_vnext/run_service.sh` with an exclusive `flock` lock before `main.py` starts.
- Removed the update workflow's `nohup` fallback concept; deployment now requires systemd.
- `update.sh` stops the canonical service, cleans leftover V2 `main.py` processes, reloads/enables the tracked service, starts it, and verifies exactly one V2 process.
- Updated `BOT_STATUS.md` and `UPDATE_GUIDE.md` with the single-instance architecture and operating rules.
- Goal: prevent duplicate bot processes, duplicate Telegram polling, queue/dashboard conflicts, and future orphan-process starts.

## 2026-09-13 — Realtime dashboard / storage / HLS work

- V2 live dashboard and progress reporting updated in prior commits.
- HLS target concurrency is 16 segments per video.
- Automatic storage cleanup protects active task paths and removes disposable artifacts.
- Large uploads are split into Telegram-safe parts and cleaned after completion/failure.
