# Project Handoff — Telegram Bot

## Read this first

This file is intentionally written for future ChatGPT chats, developers, or operators. It prevents repeated explanations and prevents edits to the wrong architecture.

## Project identity

- Repo: `noicii/telegram_bot_new`
- Active branch: `bot-vnext`
- Active bot: **V2**
- Active entrypoint: `bot_vnext/main.py`

## Current bot design

- Download workers: **2**
- Upload workers: **4**
- HLS Multi concurrency: **16 segments per video**
- HTTP connector: total 150 / per-host 40
- HLS retries: 3 attempts
- HLS retry delays: 1s then 2s
- Playwright is used for HLS discovery where required.
- FFmpeg is used for muxing and encrypted HLS handling.
- Large uploads are split into approximately 1900 MiB MP4 parts before upload.
- Successful local media is deleted after upload.
- Failed/cancelled media and temporary split artifacts are cleaned.
- Disk cleanup triggers at 80%, targets 70%, and treats 90% as critical.
- `/status` reports realtime filesystem usage.

## Main code map

- `bot_vnext/main.py` — bot startup, Telegram handlers, dashboard/UI and status.
- `bot_vnext/app/pipeline.py` — download/upload orchestration and progress flow.
- `bot_vnext/app/downloader/engine.py` — active downloader and HLS Multi implementation.
- `bot_vnext/app/queue/upload_manager.py` — upload worker queue and completion/cancellation flow.
- `bot_vnext/app/uploader/engine.py` — Telegram file/video/audio sending, large-file splitting and progress reporting.
- `bot_vnext/app/storage/cleanup.py` — disk-pressure cleanup and artifact lifecycle.
- `bot_vnext/STORAGE_CLEANUP.md` — storage lifecycle policy.
- `requirements.txt` — Python dependencies.
- `README.md` — documentation index and project overview.
- `BOT_FEATURES.md` — complete feature reference.
- `BOT_STATUS.md` — current operational state.
- `ARCHITECTURE.md` — component/call-path documentation.
- `DEPLOYMENT.md` — normal deployment details.
- `UPDATE_GUIDE.md` — update-only runbook.
- `TRANSFER_GUIDE.md` — migration to another system.
- `OPERATIONS.md` — troubleshooting and day-to-day operations.
- `CHANGELOG.md` — important changes.
- `update.sh` — clean deployment/update entrypoint.
- `telegram-bot.service` — systemd service template.

## Critical warning

Do not assume root-level legacy files control V2. In particular, changes to root `downloader.py`, `bot.py`, `queue_worker.py`, or `handlers.py` may have no effect on the active bot.

Always trace imports from `bot_vnext/main.py` before modifying production behavior.

## Deployment philosophy

The user explicitly wants a **fresh deployment on every update**. Old task queue, SQLite database, settings, and stale local code do not need to be preserved.

Preserve only secrets/runtime prerequisites needed to run the bot, primarily `.env` and the local Python environment. Do not preserve the old queue database or old backup/rebuild directories.

## Migration philosophy

For a new VM, clone `bot-vnext`, restore `.env`, install OS prerequisites, run `update.sh`, install the service if needed, and verify `/status` and `/health`.

Do not copy an old `venv`, downloads folder, logs, temporary split files, or queue database unless there is a documented reason.

See `TRANSFER_GUIDE.md` for the complete migration checklist.

## User workflow preference

The user has little coding knowledge. Prefer complete, ready-to-paste commands or complete files. Avoid instructions that require manually editing code line-by-line.

When troubleshooting, use the user's preferred loop:

1. User sends the log/output.
2. Diagnose the exact error.
3. Explain what it means briefly.
4. Give **one next step/command**.
5. Wait for the next log.

Do not dump many unrelated commands at once unless necessary.

## When changing the bot

1. Verify the change against the V2 call path.
2. Update code on `bot-vnext`.
3. Update the relevant documentation.
4. Record the change in `CHANGELOG.md`.
5. Run syntax/import tests.
6. Deploy with `./update.sh`.
7. Check service status and logs.
8. Test the affected Telegram command/feature.

## Current known history

A previous runtime-hardening approach modified legacy root modules and therefore did not control the active V2 HLS downloader. The active V2 HLS semaphore was found in `bot_vnext/app/downloader/engine.py`. Future concurrency changes must target that file/call path.

A large-file upload failure was fixed by adding FFmpeg-based splitting before Telegram upload. A later production incident filled the 20 GB root filesystem while FFmpeg was creating split parts; this led to automatic storage cleanup and restart-safe disposable media policy being added.
