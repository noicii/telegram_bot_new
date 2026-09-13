# Bot V2 — Complete Feature Reference

## 1. Access control

The V2 command center is owner-only.

- Incoming commands are checked against `OWNER_ID`.
- Callback buttons are also checked against `OWNER_ID`.
- Unauthorized users are ignored or receive the callback denial response.
- Do not share `BOT_TOKEN` or `OWNER_ID` configuration with untrusted users.

## 2. Telegram commands

| Command | Purpose |
|---|---|
| `/start` | Shows the V2 command center and runtime summary |
| `/status` | Queue counts, active workers, selected method and realtime disk usage |
| `/queue` | Shows active/queued tasks with progress and cancellation buttons |
| `/cancel TASK_ID` | Cancels an active download/upload task |
| `/retry TASK_ID` | Re-queues a failed/cancelled task using the selected default method |
| `/clear` | Clears completed/failed/cancelled task records |
| `/crawl URL` | Crawls a page and presents downloadable options |
| `/settings` | Shows bot settings and control buttons |
| `/method` | Selects the default download method |
| `/health` | Checks pipeline, SQLite, FFmpeg, aria2c, Playwright cache and worker limits |

The bot also accepts a plain `http://` or `https://` URL from the owner and routes it through the crawl flow.

## 3. Crawl and selection flow

1. Owner sends `/crawl <URL>` or a plain URL.
2. The crawler discovers episode/download options.
3. The bot groups the selection into pages.
4. Each option shows episode, resolution and source host.
5. Owner can select individual items, select all, clear selection, change method, or cancel.
6. Selected items are submitted to the V2 pipeline as independent tasks.

## 4. Download methods

The V2 UI supports a method selector and stores the chosen method for a URL. The exact method implementations are maintained under `bot_vnext/app/downloader/` and the method store.

The `auto` method is the normal default unless another method is explicitly selected.

## 5. Download pipeline

- Maximum simultaneous download workers: **2**.
- A task is stored with metadata such as source URL, provider, resolution, selected method and optional cookie/header information.
- Download progress is persisted and forwarded to the dashboard.
- Download completion hands the file to the upload stage.
- Download failure marks the task failed and removes its local artifact.
- Cancellation removes the active artifact and marks the task cancelled.

## 6. HLS Multi behavior

Current production HLS behavior:

- Playwright can discover signed HLS URLs when the page requires browser execution.
- The HLS playlist is fetched with `aiohttp`.
- Master playlists can be resolved to a suitable variant.
- Encrypted HLS is handled through FFmpeg.
- Unencrypted segments are fetched concurrently.
- **16 segment requests are allowed concurrently per video.**
- HLS HTTP connector limits are currently `limit=150` and `limit_per_host=40`.
- A failed segment is retried up to 3 attempts.
- Downloaded MPEG-TS segments are muxed into the final media file with FFmpeg.

With 2 download workers, the theoretical HLS segment concurrency can reach 32 across two simultaneous HLS videos, subject to task type, network, host limits and runtime behavior.

## 7. Upload pipeline

- Maximum simultaneous upload workers: **4**.
- Pyrogram performs Telegram sends.
- Progress is throttled to avoid excessive dashboard/API updates.
- A final progress update is forced after successful upload.
- Successful uploads delete the local source file when the pipeline completes the upload stage.

### Large-file splitting

Telegram upload size is treated as a hard boundary by the uploader.

When a video/document exceeds the configured upload boundary:

1. FFmpeg creates MP4 parts targeting approximately 1900 MiB.
2. Parts are uploaded sequentially.
3. Captions identify `Part X/Y`.
4. Progress is aggregated across all parts.
5. Temporary parts are deleted after success or failure.

This avoids the previous failure mode where a multi-gigabyte file reached Pyrogram directly and was rejected by Telegram.

## 8. Queue and restart behavior

The SQLite database is local runtime state. The clean deployment policy deliberately removes the old database on update.

Interrupted `downloading` and `uploading` tasks are not automatically resumed after a restart. Their local artifacts are disposable and startup cleanup removes stale artifacts.

This is intentional: the project prefers a clean, deterministic deployment over resuming potentially stale queue state.

## 9. Automatic storage cleanup

The storage cleanup module protects active/referenced paths and removes disposable media when disk pressure requires it.

Current policy:

- Cleanup trigger: **80%** disk usage.
- Target after pressure cleanup: **70%**.
- Critical pressure: **90%**.
- Stale temporary artifacts: approximately 30 minutes old before normal stale-temp cleanup.
- Cleanup runs at startup, periodically, and after relevant task lifecycle events.

See `bot_vnext/STORAGE_CLEANUP.md` for exact lifecycle rules.

## 10. Live status

`/status` calculates disk usage from the filesystem at request time.

It displays:

```text
🟢 Disk: 8.7/19.2 GB used • 10.5 GB free (45%)
```

The icon is:

- 🟢 below 80%
- 🟠 from 80% to below 90%
- 🔴 at or above 90%

The Refresh button recalculates the current values.

## 11. Health checks

`/health` checks:

- Pipeline started state.
- SQLite database presence.
- FFmpeg availability.
- aria2c availability.
- Playwright Chromium cache.
- Download worker limit.
- Upload worker limit.

## 12. Deployment behavior

`update.sh` is the canonical deployment mechanism.

It:

1. Stops the current bot.
2. Fetches `origin/bot-vnext`.
3. Resets tracked code to the remote branch.
4. Removes disposable local files/state.
5. Preserves `.env` and the Python virtualenv.
6. Installs Python requirements.
7. Ensures Playwright Chromium.
8. Runs syntax checks.
9. Verifies HLS concurrency is 16.
10. Starts the systemd service when available, otherwise uses the fallback launcher.

## 13. Legacy code warning

Do not assume root-level legacy modules are production V2 code. Trace imports from `bot_vnext/main.py` before changing behavior.
