# Deployment

## Normal update

From the VM:

```bash
cd ~/telegram_bot_new
chmod +x update.sh
./update.sh
```

The updater fetches `origin/bot-vnext`, resets tracked code, removes disposable local state, preserves `.env` and the virtualenv, installs requirements, installs Playwright Chromium, checks syntax, verifies the active HLS concurrency, and starts the bot.

## What is intentionally deleted

- `bot_vnext.db`
- old task/queue state stored in the local SQLite database
- backup/rebuild directories matching `backup_upload_rebuild_*`
- stale untracked local files
- tracked local code modifications (Git reset)
- Python `__pycache__` directories

## What is preserved

- `.env` — runtime secrets/configuration
- `venv/` or `.venv/` — local Python environment, recreated if missing

If another secret file becomes necessary, document it here before adding it to the updater's preserve list.

## Service selection

The updater automatically checks common systemd service names. If the actual service has a different name, run:

```bash
BOT_SERVICE=YOUR_SERVICE_NAME.service ./update.sh
```

The service definition itself is outside the repository, so the updater does not delete it.

## No systemd fallback

If no supported systemd service is found, the updater starts:

```text
venv/bin/python bot_vnext/main.py
```

in the background and stores its PID in `.bot_vnext.pid`. Output is written to `bot_vnext.log`.

## Manual diagnostics

```bash
cd ~/telegram_bot_new

git status --short
git log -1 --oneline
grep -nE 'Semaphore\(16\)|16 segments download concurrently' bot_vnext/app/downloader/engine.py
python -m py_compile bot_vnext/main.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py
```

For systemd logs:

```bash
sudo journalctl -u YOUR_SERVICE_NAME.service -n 100 --no-pager
```
