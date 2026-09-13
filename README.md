# Telegram Video Downloader Bot — V2

Production Telegram video downloader bot, maintained on the `bot-vnext` branch.

> **Active production entrypoint:** `bot_vnext/main.py`
>
> Root-level legacy modules such as `bot.py`, `handlers.py`, `downloader.py`, and `queue_worker.py` are not the production V2 call path.

## What the bot does

- Owner-only Telegram control interface.
- Crawls supported episode/list pages and presents selectable download options.
- Remembers the selected download method for URLs and supports changing the default method.
- Queues downloads and uploads through a persistent in-process pipeline.
- Runs up to **2 download workers** and **4 upload workers**.
- HLS multi-segment downloading uses **16 concurrent segment requests per video**.
- Supports direct/HLS/FFmpeg-based download paths used by the V2 downloader.
- Handles signed HLS discovery with Playwright when required.
- Retries HLS segment failures up to 3 attempts.
- Uploads videos/documents/audio through Pyrogram.
- Automatically splits videos that exceed the Telegram upload boundary into approximately 1900 MiB MP4 parts and uploads them sequentially.
- Deletes local media after successful upload, and removes failed/cancelled artifacts.
- Performs automatic disk-pressure cleanup at configured thresholds.
- Shows live queue state and **realtime disk usage** in `/status`.
- Provides `/health`, `/queue`, `/cancel`, `/retry`, `/clear`, `/crawl`, `/settings`, and `/method` controls.
- Uses `update.sh` for clean, reproducible deployments from `origin/bot-vnext`.

## Documentation map

| Document | Purpose |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Component architecture and request flow |
| [`BOT_STATUS.md`](BOT_STATUS.md) | Current runtime/source-of-truth status |
| [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md) | Fast handoff for another developer/AI |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Normal deployment and diagnostics |
| [`UPDATE_GUIDE.md`](UPDATE_GUIDE.md) | Update-only procedure and rules |
| [`TRANSFER_GUIDE.md`](TRANSFER_GUIDE.md) | Move the bot to another VM/system |
| [`OPERATIONS.md`](OPERATIONS.md) | Day-to-day operations, troubleshooting and safety |
| [`BOT_FEATURES.md`](BOT_FEATURES.md) | Complete feature/behavior reference |
| [`bot_vnext/STORAGE_CLEANUP.md`](bot_vnext/STORAGE_CLEANUP.md) | Media lifecycle and disk cleanup policy |
| [`CHANGELOG.md`](CHANGELOG.md) | Important dated changes and fixes |

## Production runtime

```text
Telegram
   |
   v
bot_vnext/main.py
   |
   v
app.pipeline.Pipeline
   |--------------------|
   v                    v
DownloadManager      UploadManager
2 workers            4 workers
   |                    |
   v                    v
Downloader engine    Uploader engine
   |
   +-- HLS / direct / FFmpeg
   +-- 16 HLS segments per video
```

## Deployment in one command

On the production VM:

```bash
cd ~/telegram_bot_new && bash update.sh
```

The updater fetches `bot-vnext`, resets tracked code to GitHub, removes disposable local state, preserves `.env` and the Python virtualenv, installs requirements, ensures Playwright Chromium, runs syntax/HLS checks, and starts the bot.

## Important state policy

The production update is intentionally clean:

- `.env` is preserved because it contains runtime secrets/configuration.
- The old SQLite queue/database is disposable and is removed by `update.sh`.
- Untracked stale runtime files are removed.
- Active/incomplete media is not treated as durable application state.
- The bot does not automatically resume interrupted download/upload tasks after a restart.

## Required secrets

Do **not** commit `.env` or secret values to GitHub. The runtime environment requires at least the values used by `config.py`, including:

```text
API_ID
API_HASH
BOT_TOKEN
OWNER_ID
```

Keep the real values private.

## Moving to another system

Start with [`TRANSFER_GUIDE.md`](TRANSFER_GUIDE.md). In the normal clean-deployment model, the essential items are:

1. GitHub repository access.
2. The `.env` file/secret values.
3. Any intentionally required assets such as a custom thumbnail.
4. The OS/runtime prerequisites documented in the transfer guide.
5. The systemd service file if systemd is being used.

Do not copy an old `venv`, database, downloads folder, logs, or temporary split files unless there is a specific reason to preserve them.

## Security

The bot is designed around owner-only command/callback checks. Keep `OWNER_ID`, `BOT_TOKEN`, `API_HASH`, and other secrets private. GitHub's repository security features such as secret scanning and push protection are useful safeguards for repositories containing application code. citeturn0search1

## Documentation rule

When changing production behavior, update the relevant documentation and add a dated `CHANGELOG.md` entry. Keep this README short and use the linked documents for detailed operational information; this follows the general GitHub README guidance that a README should orient users and link to deeper documentation. citeturn0search0
