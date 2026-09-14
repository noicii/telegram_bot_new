# Telegram Bot — Current Status

> **Source of truth for the current `bot-vnext` deployment.** Update this file whenever the architecture or important runtime behavior changes.

## Current deployment

- Repository: `noicii/telegram_bot_new`
- Branch: `bot-vnext`
- Active V2 entrypoint: `bot_vnext/main.py`
- Legacy root files are not the active V2 pipeline unless explicitly stated.
- Production update model: fresh code from `origin/bot-vnext`; old queue/database/settings are disposable.
- **Production launcher: `telegram-bot.service` only.**
- **Expected V2 process count: exactly 1.**

## Single-instance runtime architecture

```text
systemd: telegram-bot.service
             |
             v
bot_vnext/run_service.sh
             |
       exclusive flock
             |
             v
      bot_vnext/main.py
             |
          Pipeline
        /           \
       v             v
DownloadManager   UploadManager
  2 workers        4 workers
```

The service runner takes an exclusive filesystem lock before launching the V2 entrypoint. The updater stops the service, removes leftover V2 processes, reloads/enables the service, starts it, and verifies exactly one V2 process.

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
      |       +--> HLS Multi (16 segments/video target)
      |       +--> Browser HLS (16 authenticated requests/video)
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
| HLS segment concurrency per video | **16 target** |
| Browser HLS request concurrency | **16 target** |
| HTTP connector total limit | 150 |
| HTTP connector per-host limit | 40 |
| HLS segment retries | 3 attempts |
| Retry budget | **3 manual retries after the original attempt** |
| Retry history | Preserved; never reset to zero on manual retry |
| Production bot processes | **1** |

## Queue / status

- Pipeline moves a successfully downloaded task to `uploading` and resets upload progress.
- Upload completion marks the task `completed` and refreshes the dashboard.
- Upload progress is persisted and forwarded to the live dashboard.
- Cancellation is propagated through the task context and upload manager.
- The local SQLite database is disposable for deployments; a fresh update intentionally removes the old DB/queue state.
- Interrupted `downloading`/`uploading` tasks are marked failed on restart and are not automatically resumed.
- Manual retry increments and preserves `retry_count`; a task is not re-queued once its configured retry budget is exhausted.

## Media / storage lifecycle

- `downloads/` is temporary staging storage, not permanent media storage.
- Active download paths are recorded in the database so automatic cleanup can protect them.
- Failed downloads delete their local artifacts immediately.
- Cancelled downloads/uploads delete their local artifacts immediately.
- Successful uploads delete the source media immediately.
- Large-upload FFmpeg split parts are temporary and are removed after success or failure.
- Browser HLS temporary directories are registered with `TaskContext` and recursively removed on task cleanup.
- Old interrupted artifacts are removed at startup rather than resumed.
- Stale `.part`, `.tmp`, `.ytdl`, and abandoned split directories are automatically cleaned.

### Automatic disk cleanup

`bot_vnext/app/storage/cleanup.py` enforces the following policy:

- **80% disk usage:** pressure cleanup starts.
- **90% disk usage:** treated as critical pressure.
- **70% target:** cleanup removes the oldest non-active media until usage is back around this level.
- Cleanup runs at startup, every 30 seconds, after task completion/failure/cancellation, and during shutdown.
- Cleanup never removes an active task path.
- Startup cleanup runs before SQLite initialization so a full disk can be relieved before SQLite needs to write.

See `bot_vnext/STORAGE_CLEANUP.md` for the complete lifecycle and operational rules.

## Upload

- Upload manager has 4 workers.
- Upload engine reports final progress after the Telegram send operation returns.
- Files larger than 2000 MiB are automatically split into upload-safe parts targeting about 1900 MiB.
- Completed/failed upload artifacts are deleted automatically.
- Telegram upload pressure is controlled by the shared FloodGate, which adapts between 1 and 4 concurrent sends.

## Dashboard / commands

The V2 bot includes live queue/status dashboard functionality, including status and queue views and a refresh action. The existing browser method is displayed as **Browser HLS** without adding a second UI method. Exact command behavior must be checked in `bot_vnext/main.py` before changing it.

## Dependencies

Dependencies are defined in `requirements.txt`. Current project requirements include Pyrogram, TgCrypto, aiohttp, aiofiles, BeautifulSoup/lxml, yt-dlp, requests, python-dotenv, curl_cffi, psutil and Playwright.

## Deployment rule

Do **not** manually mix old local Python files with GitHub code. Use `./update.sh` from the repository root. The updater requires systemd and the canonical `telegram-bot.service`; it never falls back to `nohup`. It stops the service, cleans leftover V2 processes, fetches and hard-resets the branch, installs dependencies, validates protected contracts, starts it, and verifies one V2 process. Failed deployments are automatically rolled back to the last working Git revision.

## Important maintenance rule

If a future change affects download concurrency, queue state, upload behavior, metadata handling, storage cleanup, startup, single-instance protection, or deployment, update this document and `CHANGELOG.md` in the same change.

## Known architecture warning

There are legacy/root modules in the repository. Do not assume a change in root `downloader.py`, root `bot.py`, or root `queue_worker.py` changes the active V2 bot. Verify the call path from `bot_vnext/main.py` first.
