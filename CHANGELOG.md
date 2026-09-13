# Changelog — Telegram Bot V2

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
