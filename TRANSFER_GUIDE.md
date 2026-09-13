# Transfer Guide — Move Bot V2 to Another System

This document is the migration checklist for moving the production bot to another VM/server.

## Golden rule

The repository is the source of truth for code. The `.env` is the source of truth for private runtime secrets. Runtime queue/database/download state is disposable in the current deployment model.

Do **not** blindly copy the old VM's whole home directory.

## What is required

### 1. GitHub repository

The new machine needs access to:

```text
https://github.com/noicii/telegram_bot_new
```

Use the `bot-vnext` branch for production.

### 2. `.env` — REQUIRED

Copy the production `.env` securely. Never commit it to GitHub.

At minimum the current `config.py` consumes:

```text
API_ID
API_HASH
BOT_TOKEN
OWNER_ID
```

Other environment values may be added later; inspect `config.py` before migration.

### 3. Optional/operational assets

Preserve only assets that are intentionally used by the deployment, for example:

- `bot_vnext/assets/thumb.jpg` or another configured custom thumbnail.
- `data/cookies.txt` if a particular deployment intentionally maintains cookies there.
- Any other documented runtime asset required by a specific download method.

Do not copy stale generated downloads or temporary split directories.

## What does NOT need to be migrated

Normally do not copy:

- `venv/` or `.venv/` — recreate it with the deployment script.
- `bot_vnext.db` / old SQLite queue state.
- `downloads/` from the old machine.
- temporary FFmpeg split directories.
- `__pycache__/`.
- old logs.
- `.bot_vnext.pid`.
- stale local backup/rebuild directories.

The clean updater intentionally removes disposable state.

## New VM prerequisites

Install the basic OS packages needed to clone and run the project, including:

- Git
- Python 3.10+ compatible with the current requirements
- FFmpeg
- a working network connection
- systemd if using the repository service unit

Playwright Chromium is installed/ensured by `update.sh`.

The project currently also checks for `aria2c` in `/health`; it is useful when the selected downloader path requires it, but it is not the primary V2 entrypoint itself.

## Migration procedure

### Step 1 — Clone

```bash
git clone -b bot-vnext https://github.com/noicii/telegram_bot_new.git ~/telegram_bot_new
cd ~/telegram_bot_new
```

If the repository is private, authenticate with the appropriate GitHub method.

### Step 2 — Restore `.env`

Place the production `.env` at:

```text
~/telegram_bot_new/.env
```

Verify permissions and never paste its contents into chat or commit it.

### Step 3 — Check host paths

The repository service file assumes the production path:

```text
/home/benoicii/telegram_bot_new
```

If the new Linux username or path is different, update the systemd service accordingly before enabling it.

### Step 4 — Run the canonical deployment

```bash
cd ~/telegram_bot_new
bash update.sh
```

### Step 5 — Verify runtime

```bash
cd ~/telegram_bot_new
systemctl status telegram-bot.service --no-pager
```

Then test in Telegram:

```text
/status
/health
```

### Step 6 — Verify HLS concurrency

```bash
grep -nE 'Semaphore\(16\)|16 segments download concurrently' bot_vnext/app/downloader/engine.py
```

### Step 7 — Verify disk

```bash
df -hT /
```

The Telegram `/status` disk line should reflect the same filesystem used by the download directory.

## Systemd setup

The repository contains `telegram-bot.service`. The current production service runs:

```text
/home/benoicii/telegram_bot_new/venv/bin/python /home/benoicii/telegram_bot_new/bot_vnext/main.py
```

For a different Linux username/path, edit the service paths before installing/enabling it.

Typical installation pattern:

```bash
sudo cp telegram-bot.service /etc/systemd/system/telegram-bot.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot.service
sudo systemctl restart telegram-bot.service
```

## Verification checklist

- [ ] GitHub `bot-vnext` checked out.
- [ ] `.env` restored securely.
- [ ] Python environment created by `update.sh`.
- [ ] Requirements installed.
- [ ] Playwright Chromium available.
- [ ] FFmpeg available.
- [ ] `telegram-bot.service` points to the correct path/user.
- [ ] Bot starts successfully.
- [ ] `/status` works.
- [ ] `/health` works.
- [ ] Owner-only access still works.
- [ ] HLS shows 16-segment concurrency.
- [ ] Disk cleanup documentation/thresholds remain unchanged.

## Handoff to another developer or AI

Give them this repository plus this document and `PROJECT_HANDOFF.md`. They should read `README.md` first, then `PROJECT_HANDOFF.md`, `ARCHITECTURE.md`, `BOT_FEATURES.md`, `DEPLOYMENT.md`, and `UPDATE_GUIDE.md` before changing production code.

They should never infer production behavior from legacy root files without tracing the V2 imports.
