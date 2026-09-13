# Architecture — bot-vnext

## Entry point

`bot_vnext/main.py` is the active V2 bot entrypoint.

## Request flow

```text
Telegram command / dashboard action
              |
              v
       bot_vnext/main.py
              |
              v
        app.pipeline.Pipeline
          /             \
         /               \
        v                 v
 DownloadManager     UploadManager
   (2 workers)         (4 workers)
        |                 |
        v                 v
 downloader/engine   uploader/engine
        |                 |
        +-- HLS          +-- Telegram upload
        +-- direct
        +-- FFmpeg
```

## Downloader

Primary implementation: `bot_vnext/app/downloader/engine.py`.

HLS Multi currently:

- discovers signed HLS URLs with Playwright when needed;
- fetches the playlist with aiohttp;
- selects the best variant when a master playlist is returned;
- uses FFmpeg for encrypted HLS;
- downloads unencrypted segments concurrently;
- uses **16 concurrent segment fetches per video**;
- retries failed segments up to 3 attempts;
- muxes the downloaded MPEG-TS segments with FFmpeg.

HTTP connection settings currently are `limit=150` and `limit_per_host=40`.

## Pipeline

`bot_vnext/app/pipeline.py` owns the download-to-upload handoff and progress callbacks.

- Download success => task becomes `uploading`.
- Upload progress is forwarded to the dashboard and persisted.
- Upload success => task becomes `completed` and completion callbacks refresh the UI.
- Cancellation is propagated to the relevant task context.

## Upload

`bot_vnext/app/queue/upload_manager.py` schedules uploads with 4 workers.

`bot_vnext/app/uploader/engine.py` performs Telegram sends and reports progress, including a forced final progress update after a successful send.

## Database / queue

The bot uses its local SQLite database for task state. For this deployment model the database is disposable: a clean update intentionally removes the old database so old queue/settings state cannot contaminate the new deployment.

## Legacy code warning

Root-level modules such as `bot.py`, `downloader.py`, `queue_worker.py`, and `handlers.py` are legacy/parallel code. They must not be treated as the active V2 implementation without tracing imports from `bot_vnext/main.py`.

## Deployment

Use `./update.sh`. Do not manually copy individual old Python files over the GitHub checkout.
