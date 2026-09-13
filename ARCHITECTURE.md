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
- There is **no fixed 3-second progress throttle** anymore; Telegram-facing dashboard updates are controlled by `TelegramFloodGate`.

## Telegram FloodGate

`bot_vnext/app/core/telegram_gate.py` is the shared Telegram-facing control layer.

### Upload gate

- starts with the existing 4-worker ceiling;
- allows up to the current adaptive upload limit;
- on `FloodWait`, pauses new Telegram send attempts for Telegram's exact requested duration;
- does **not** cancel the owning upload task or delete its file;
- lowers upload concurrency after FloodWait pressure;
- cautiously raises concurrency again after sustained successful uploads;
- ordinary network errors remain under the uploader's existing retry policy.

### Dashboard gate

- coalesces progress into the newest dashboard state;
- never replays stale progress updates after a cooldown;
- uses an adaptive interval instead of a fixed 3-second timer;
- respects `FloodWait` exactly;
- locally caches the persistent dashboard message so normal refreshes do not call `get_messages` before every edit;
- gates only the persistent live-dashboard message, leaving selection/UI edits independent.

Pyrogram's automatic short FloodWait sleep is disabled so the shared gate receives the server's exact FloodWait signal and can coordinate it centrally.

## Upload

`bot_vnext/app/queue/upload_manager.py` schedules uploads with 4 workers.

`bot_vnext/app/uploader/engine.py` performs Telegram sends and reports progress, including a forced final progress update after a successful send. Telegram sends are routed through the shared FloodGate; FloodWait pauses/retries instead of cancelling the task.

## Database / queue

The bot uses its local SQLite database for task state. For this deployment model the database is disposable: a clean update intentionally removes the old database so old queue/settings state cannot contaminate the new deployment.

## Legacy code warning

Root-level modules such as `bot.py`, `downloader.py`, `queue_worker.py`, and `handlers.py` are legacy/parallel code. They must not be treated as the active V2 implementation without tracing imports from `bot_vnext/main.py`.

## Deployment

Use `./update.sh`. Do not manually copy individual old Python files over the GitHub checkout.
