# Update Guide — Bot V2

This is the canonical update-only runbook for production.

## Production rule: one bot process only

The Telegram bot must run through **`telegram-bot.service` only**. Never launch `bot_vnext/main.py` manually with `python`, `nohup`, screen, tmux, cron, or another supervisor.

The service starts the tracked `bot_vnext/run_service.sh`, which uses an exclusive `flock` lock before starting `main.py`. This prevents a second service-runner instance from starting the bot at the same time.

`update.sh` is also systemd-only: it refuses to use a direct `nohup` fallback, stops the canonical service, terminates leftover V2 `main.py` processes, installs/reloads the tracked service, enables it for boot, starts it, and verifies that exactly one V2 process is running.

## Canonical update command

On the production VM:

```bash
cd ~/telegram_bot_new
bash update.sh
```

Do not manually copy individual Python files from an older checkout into production.

## What `update.sh` does

1. Requires `telegram-bot.service` and systemd; there is no fallback launcher.
2. Stops the canonical service.
3. Removes any leftover V2 `bot_vnext/main.py` process before deployment.
4. Fetches `origin/bot-vnext`.
5. Resets tracked files to the remote branch.
6. Removes stale/untracked disposable files while preserving `.env` and the virtualenv.
7. Removes the disposable SQLite queue/database.
8. Removes stale Python caches and old rebuild/backup runtime directories.
9. Installs requirements and Playwright Chromium.
10. Runs syntax checks.
11. Verifies HLS concurrency is 16 segments per video.
12. Installs the tracked systemd service and reloads systemd.
13. Enables the service for automatic boot/restart.
14. Starts the service.
15. Verifies **exactly one** V2 `main.py` process is running.

## What survives an update

- `.env`
- `venv/` or `.venv/`
- Git history on the remote repository

## What is intentionally reset/deleted

- Local tracked code modifications.
- Old SQLite queue/database state.
- Old untracked runtime files.
- Python caches.
- Stale disposable downloads/logs.
- Old backup/rebuild directories.

## Service operations

Use systemd for production operations:

```bash
sudo systemctl status telegram-bot.service --no-pager
sudo systemctl restart telegram-bot.service
sudo journalctl -u telegram-bot.service -n 100 --no-pager
```

Never use `python bot_vnext/main.py` as a production start command.

## After updating

Confirm the updater reports:

```text
Clean deployment completed successfully
Service: telegram-bot.service (systemd only)
V2 processes: 1
HLS concurrency: 16 segments/video
Secrets: .env preserved
```

Then check `/status` and `/health` in Telegram.

## If update fails

Do not manually start another bot process. Fix the service/update error first.

For service diagnostics:

```bash
sudo systemctl status telegram-bot.service --no-pager
sudo journalctl -u telegram-bot.service -n 100 --no-pager
```

For duplicate-process diagnostics:

```bash
pgrep -af 'bot_vnext/main.py'
```

If more than one process appears, stop the service before investigating; the next canonical `bash update.sh` will clean leftover V2 processes before starting one instance.

## Code-change workflow

1. Diagnose from the actual V2 call path.
2. Change the smallest correct V2 component.
3. Update relevant documentation.
4. Add a dated `CHANGELOG.md` entry.
5. Commit to `bot-vnext`.
6. Push to GitHub.
7. Run `bash update.sh` on production.
8. Test the affected feature.
9. Record any new operational limitation.

## Important architectural rule

The production entrypoint is `bot_vnext/main.py`. Root legacy files are not production. Production startup is exclusively managed by `telegram-bot.service` -> `bot_vnext/run_service.sh` -> `bot_vnext/main.py`.
