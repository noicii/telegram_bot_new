# Changelog

All important bot changes are recorded here so a new chat, developer, or deployment can quickly understand what changed and why.

## 2026-09-13 — Complete documentation and handoff system

- Added `README.md` as the repository documentation index.
- Added `BOT_FEATURES.md` as the complete feature/behavior reference.
- Added `TRANSFER_GUIDE.md` with the required files, prerequisites, migration procedure and verification checklist for moving the bot to another system.
- Added `UPDATE_GUIDE.md` as a dedicated update/release runbook.
- Added `OPERATIONS.md` for day-to-day operation and troubleshooting.
- Expanded `PROJECT_HANDOFF.md` so another developer or AI can understand the architecture, state policy, migration model and troubleshooting workflow without rediscovering project history.
- Documented the rule that production changes must target the active V2 call path and update the relevant documentation/changelog.

## 2026-09-13 — Realtime disk status

- Added live filesystem usage to `/status`.
- Status now reports used/total GB, free GB and usage percentage.
- Added visual disk pressure indicators: green below 80%, orange at 80–89%, red at 90% or higher.
- The Refresh button recalculates the filesystem values at request time.

## 2026-09-13 — Automatic storage cleanup and restart safety

- Added `bot_vnext/app/storage/cleanup.py` for automatic disk-pressure cleanup.
- Cleanup triggers at 80% disk usage and removes oldest non-active media until usage is back to 70%; 90% is treated as critical pressure.
- Cleanup runs at startup, every 30 seconds, and after download/upload completion, failure, cancellation, and shutdown.
- Failed/cancelled media and generated split artifacts are deleted instead of accumulating on the VM.
- Successful uploads delete the local source immediately.
- Interrupted downloading/uploading tasks are no longer automatically resumed after a restart; their local artifacts are disposable and removed during startup cleanup.
- Active download paths are recorded in the database so automatic cleanup cannot delete an in-progress file.
- Added `bot_vnext/STORAGE_CLEANUP.md` documenting the complete media lifecycle, thresholds, restart behavior, and operational policy.

## 2026-09-13 — Automatic large-video upload splitting

- Fixed V2 uploads failing when a downloaded video exceeded Telegram's 2000 MiB upload boundary.
- `bot_vnext/app/uploader/engine.py` now detects oversized video/document uploads before calling Pyrogram.
- Large files are split with FFmpeg into MP4 parts targeting about 1900 MiB, with a safety margin for variable bitrate and container overhead.
- Parts are uploaded sequentially as `Part X/Y`, with progress aggregated across the parts.
- Temporary split files are removed after the upload completes or fails.
- Added explicit validation so an unexpectedly oversized generated part fails with a clear error instead of reaching Telegram.

## 2026-09-13 — Documentation and clean deployment system

- Established `BOT_STATUS.md` as the current architecture/runtime source of truth.
- Established this changelog for important project changes.
- Standardized the project around the active V2 entrypoint: `bot_vnext/main.py`.
- Documented current runtime targets: 2 download workers, 4 upload workers, and 16 HLS segments per video.
- Added the clean-update workflow through `update.sh` so stale local code, queue state, and database state do not get mixed with GitHub code.

## 2026-09-12 — Upload completion / runtime hardening

- Upload completion/progress refresh handling was hardened.
- Runtime hardening work was added, but later verification showed that the active V2 downloader lives under `bot_vnext/app/downloader/engine.py`; future changes must target the active V2 call path rather than assuming legacy root modules are active.

## Maintenance rule

Every future major change should add a dated entry describing:

- what changed;
- why it changed;
- the affected files/components;
- how it was tested.
