# Bot V2 — Storage, Temporary Files & Automatic Disk Cleanup

## Why this exists

Bot V2 downloads media to local disk before uploading it to Telegram. Large videos can temporarily require several gigabytes because a file over Telegram's upload limit is split into upload-safe parts.

The storage policy is therefore deliberately **ephemeral**: local media is staging data, not permanent storage.

## Artifact lifecycle

### Download in progress

- The expected output path is registered in the task database as soon as the worker starts.
- The active file is protected from automatic disk cleanup.
- Temporary downloader files such as `.part`, `.tmp` and `.ytdl` are treated as disposable.
- Cancellation terminates child processes and removes registered temporary files.

### Download succeeds

The completed file is handed directly to the upload queue. It is not intended to remain on disk permanently.

### Download fails

The task is marked failed and its local output/temporary artifacts are deleted immediately. A failed download therefore does not consume disk space waiting for a manual cleanup.

### Upload succeeds

The upload manager deletes the source media immediately after Telegram accepts the upload. Pipeline cleanup is idempotent and performs a second safety cleanup.

### Upload fails or is cancelled

The local source media is deleted. Retrying the task starts a fresh download instead of relying on an old local artifact.

### Bot restart / crash

`downloading` and `uploading` tasks are changed to `failed` with `Interrupted by bot restart` during startup. They are **not automatically resumed**. Their local artifacts are removed during startup cleanup.

This prevents old partial downloads from being mistaken for valid resumable state.

Queued tasks that have not started remain queued and can be dispatched normally.

## Large-file splitting

Files above Telegram's configured 2000 MiB upload ceiling are split into approximately 1900 MiB MP4 parts using FFmpeg stream copy.

- Parts are temporary.
- Parts are uploaded sequentially.
- Parts are deleted immediately after the upload operation finishes, whether successful or failed.
- The original downloaded file is also deleted after upload/failure.
- If FFmpeg cannot create safe parts, the task fails and its source is removed.

## Automatic disk cleanup

Cleanup runs:

1. Before SQLite initialization on every bot start, so a completely full disk can be recovered before SQLite needs to write its WAL/database files.
2. Immediately after download failure.
3. Immediately after upload completion/failure/cancellation.
4. Every 30 seconds while the bot is running.
5. During shutdown.

### Thresholds

| Threshold | Behaviour |
|---|---|
| Below 80% | No pressure cleanup; normal temporary cleanup still applies. |
| 80% or higher | Oldest non-active files are removed until usage reaches 70%. |
| 90% or higher | Emergency cleanup is triggered with the same 70% target. |
| Startup | Temporary/split artifacts are removed immediately; disk pressure cleanup can also remove old non-active media. |

The cleanup code never deletes paths currently registered as active queued/downloading/uploading task files.

## Important design rule

The `downloads/` directory is a cache/staging directory. **Do not rely on files in it surviving a bot restart.** The database contains task metadata, but interrupted local media is intentionally disposable.

## Operational result

The bot should not gradually fill the VM disk with old videos, failed downloads, split parts, or abandoned partial files. A 20 GB root disk can still become full if multiple very large active downloads run simultaneously, so the long-term recommended deployment is a VM disk sized for the expected concurrent workload.
