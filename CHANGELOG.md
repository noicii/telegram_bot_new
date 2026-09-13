# Changelog

All important bot changes are recorded here so a new chat or deployment can quickly understand what changed and why.

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
