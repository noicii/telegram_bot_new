# Deployment — Bot V2

## Production rule

There is exactly **one** supported production deployment/update path:

```bash
cd ~/telegram_bot_new
./update.sh
```

`update.sh` is self-bootstrapping. The service does not need to exist before the update starts. The updater fetches `bot-vnext`, resets tracked code, installs the tracked `telegram-bot.service`, installs dependencies, validates the active V2 code, and starts the bot through systemd.

## Never use alternative launch/update methods

Do not use manual file copying, `git pull` + manual restart, `python bot_vnext/main.py`, `nohup`, `screen`, `tmux`, cron, or a second process supervisor for production.

Production startup is:

```text
telegram-bot.service
        ↓
bot_vnext/run_service.sh
        ↓
flock singleton lock
        ↓
bot_vnext/main.py
```

## Canonical update sequence

`update.sh` performs the following in a fixed order:

1. Validate repository, Git, and systemd.
2. Stop the canonical service if present.
3. Stop leftover V2 processes.
4. Fetch `origin/bot-vnext`.
5. Checkout/reset tracked code to the remote branch.
6. Clean disposable untracked state while preserving `.env` and the virtualenv.
7. Remove disposable SQLite queue/database state and Python caches.
8. Install the tracked `telegram-bot.service`.
9. Reload systemd and enable the service.
10. Prepare the Python environment and install `requirements.txt`.
11. Ensure Playwright Chromium.
12. Run syntax checks for the active V2 call path.
13. Verify HLS concurrency is 16 segments per video.
14. Verify the canonical service is installed and enabled.
15. Start the service.
16. Verify the service is active.
17. Verify exactly one V2 `main.py` process.

The sequence is intentionally deterministic so production does not drift into multiple update methods.

## Preserved state

- `.env` — secrets/runtime configuration.
- `venv/` or `.venv/` — local Python environment; recreated if missing.

## Reset state

- Local tracked code changes.
- Disposable SQLite queue/database state.
- Stale untracked runtime files.
- Python caches.
- Old `backup_upload_rebuild_*` directories.

The bot intentionally does not treat interrupted media/queue state as durable restart state.

## Success criteria

A successful update ends with:

```text
Clean deployment completed successfully
Service: telegram-bot.service (systemd only)
V2 processes: 1
HLS concurrency: 16 segments/video
Secrets: .env preserved
```

Then verify `/status`, `/health`, and the changed feature in Telegram.

## Failure procedure

If `./update.sh` fails, **do not manually launch the bot**.

Collect:

```bash
cd ~/telegram_bot_new
sudo systemctl status telegram-bot.service --no-pager
sudo journalctl -u telegram-bot.service -n 100 --no-pager
pgrep -af 'bot_vnext/main.py'
```

Send the output for diagnosis. After the cause is fixed in GitHub, run the same canonical `./update.sh` again.

## Service operations

For normal runtime administration use systemd:

```bash
sudo systemctl status telegram-bot.service --no-pager
sudo systemctl restart telegram-bot.service
sudo journalctl -u telegram-bot.service -n 100 --no-pager
```

Do not run `python bot_vnext/main.py` directly.

## Manual diagnostics

```bash
cd ~/telegram_bot_new
git status --short
git log -1 --oneline
grep -nE 'Semaphore\(16\)|16 segments download concurrently' bot_vnext/app/downloader/engine.py
./venv/bin/python -m py_compile bot_vnext/main.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py
```

## Migration to a new VM

Use `TRANSFER_GUIDE.md`. The normal clean-deployment model requires the repository and `.env`; the updater recreates disposable runtime state. Do not copy old databases, downloads, logs, or temporary split files unless a specific recovery procedure requires them.
