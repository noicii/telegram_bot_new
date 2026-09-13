# Project Handoff — Telegram Bot

## Read this first

This file is intentionally written for future ChatGPT chats. It prevents repeated explanations and prevents edits to the wrong architecture.

### Project identity

- Repo: `noicii/telegram_bot_new`
- Active branch: `bot-vnext`
- Active bot: **V2**
- Active entrypoint: `bot_vnext/main.py`

### Current bot design

- Download workers: **2**
- Upload workers: **4**
- HLS Multi concurrency: **16 segments per video**
- HTTP connector: total 150 / per-host 40
- HLS retries: 3 attempts
- HLS retry delays: 1s then 2s
- Playwright is used for HLS discovery where required.
- FFmpeg is used for muxing and encrypted HLS handling.

### Main code map

- `bot_vnext/main.py` — bot startup, Telegram handlers, dashboard/UI.
- `bot_vnext/app/pipeline.py` — download/upload orchestration and progress flow.
- `bot_vnext/app/downloader/engine.py` — active downloader and HLS Multi implementation.
- `bot_vnext/app/queue/upload_manager.py` — upload worker queue and completion/cancellation flow.
- `bot_vnext/app/uploader/engine.py` — Telegram file/video/audio sending and progress reporting.
- `requirements.txt` — Python dependencies.
- `BOT_STATUS.md` — current operational state.
- `ARCHITECTURE.md` — component/call-path documentation.
- `CHANGELOG.md` — important changes.
- `update.sh` — clean deployment/update entrypoint.

### Critical warning

Do not assume root-level legacy files control V2. In particular, changes to root `downloader.py`, `bot.py`, `queue_worker.py`, or `handlers.py` may have no effect on the active bot.

### Deployment philosophy

The user explicitly wants a **fresh deployment on every update**. Old task queue, SQLite database, settings, and stale local code do not need to be preserved.

Preserve only secrets/runtime prerequisites needed to run the bot, primarily `.env` and the local Python virtual environment. Do not preserve `bot_vnext.db` or old backup/rebuild directories.

### User workflow preference

The user has little coding knowledge. Prefer complete, ready-to-paste commands or complete files. Avoid instructions that require manually editing code line-by-line.

### When changing the bot

1. Verify the change against the V2 call path.
2. Update code on `bot-vnext`.
3. Update documentation when architecture/runtime behavior changes.
4. Record the change in `CHANGELOG.md`.
5. Run syntax/import tests.
6. Deploy with `./update.sh`.
7. Check service status and logs.

### Current known history

A previous runtime-hardening approach modified legacy root modules and therefore did not control the active V2 HLS downloader. The active V2 HLS semaphore was found in `bot_vnext/app/downloader/engine.py`. Future concurrency changes must target that file/call path.
