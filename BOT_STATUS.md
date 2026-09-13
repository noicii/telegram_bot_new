# Telegram Bot — Current Status

> **Source of truth for the current `bot-vnext` deployment.** Update this file whenever the architecture or important runtime behavior changes.

## Current deployment

- Repository: `noicii/telegram_bot_new`
- Branch: `bot-vnext`
- Active V2 entrypoint: `bot_vnext/main.py`
- Legacy root files are not the active V2 pipeline unless explicitly stated.
- Production update model: fresh code from `origin/bot-vnext`; old queue/database/settings are disposable.

## Architecture

```text
bot_vnext/main.py
      |
      v
    Pipeline
      |
      +--> DownloadManager (2 workers)
      |       |
      |       v
      |   Downloader Engine
      |       |
      |       +--> direct download
      |       +--> HLS Multi (16 segments/video)
      |       +--> FFmpeg mux/remux
      |
      +--> UploadManager (4 workers)
              |
              v
          UploadEngine
              |
              v
        Telegram send_video/send_document/send_audio
```

## Current runtime values

| Component | Current value |
|---|---:|
| Download workers | 2 |
| Upload workers | 4 |
| HLS segment concurrency per video | **16** |
| HTTP connector total limit | 150 |
| HTTP connector per-host limit | 40 |
| HLS segment retries | 3 attempts |
| Retry delays | 1s, 2s |

The HLS implementation uses a semaphore for per-video segment concurrency and creates one async fetch task per segment. The active V2 value is 16.

## Queue / status

- Pipeline moves a successfully downloaded task to `uploading` and resets upload progress.
- Upload completion marks the task `completed` and refreshes the dashboard.
- Upload progress is persisted and forwarded to the live dashboard.
- Cancellation is propagated through the task context and upload manager.
- The local SQLite database is disposable for deployments; a fresh update intentionally removes the old DB/queue state.

## Upload

- Upload manager has 4 workers.
- Upload engine reports final progress after the Telegram send operation returns.
- Completed files may be deleted according to the upload manager configuration.

## Dashboard / commands

The V2 bot includes live queue/status dashboard functionality, including status and queue views and a refresh action. Exact command behavior must be checked in `bot_vnext/main.py` before changing it.

## Dependencies

Dependencies are defined in `requirements.txt`. Current project requirements include Pyrogram, TgCrypto, aiohttp, aiofiles, BeautifulSoup/lxml, yt-dlp, requests, python-dotenv, curl_cffi, psutil and Playwright.

## Deployment rule

Do **not** manually mix old local Python files with GitHub code. Use `./update.sh` from the repository root. The updater:

1. stops the bot service when a systemd service is detected;
2. fetches `origin/bot-vnext`;
3. hard-resets tracked code to the remote branch;
4. removes untracked/stale runtime files;
5. deletes the old SQLite queue/database;
6. preserves only runtime secrets such as `.env` and the local virtualenv;
7. installs dependencies;
8. installs the Playwright Chromium runtime;
9. runs syntax checks;
10. restarts the bot and prints its status/log tail.

## Important maintenance rule

If a future change affects download concurrency, queue state, upload behavior, metadata handling, startup, or deployment, update this document and `CHANGELOG.md` in the same change.

## Known architecture warning

There are legacy/root modules in the repository. Do not assume a change in root `downloader.py`, root `bot.py`, or root `queue_worker.py` changes the active V2 bot. Verify the call path from `bot_vnext/main.py` first.
