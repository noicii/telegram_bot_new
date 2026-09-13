# Update Guide — Bot V2

This is the **only** production update procedure. Every production update must use the same path:

```text
GitHub bot-vnext
      ↓
./update.sh
      ↓
git fetch + hard reset
      ↓
tracked telegram-bot.service installed
      ↓
requirements + Playwright + syntax checks
      ↓
systemd start
      ↓
exactly 1 V2 process verified
```

## One canonical command

On the production VM, always run:

```bash
cd ~/telegram_bot_new
./update.sh
```

If execute permission is missing once, use:

```bash
cd ~/telegram_bot_new
bash update.sh
```

After that, return to `./update.sh` when possible.

### No alternative production update paths

Do **not**:

- copy individual Python files into production;
- run `git pull` and then manually restart the bot;
- run `python bot_vnext/main.py`;
- run `nohup`, `screen`, `tmux`, cron, or another supervisor;
- manually install a different service file;
- edit production Python code by hand as a permanent fix.

`update.sh` owns the complete deployment procedure and always installs the tracked `telegram-bot.service` from GitHub before starting the bot.

## Why the updater does not require the service to exist first

The updater is intentionally **self-bootstrapping**. It does not fail merely because `telegram-bot.service` is missing or stale.

It first stops the service if present, fetches/resets `bot-vnext`, then installs the repository's tracked service file and reloads systemd. This prevents the previous failure mode where the updater refused to run because it was waiting for the very service file that the updater was supposed to install/update.

The service name is fixed to:

```text
telegram-bot.service
```

There is no `BOT_SERVICE` override in the canonical production workflow.

## What `update.sh` does

1. Confirms the command is running from the Git repository root.
2. Confirms Git and systemd are available.
3. Stops `telegram-bot.service` if it is installed; absence is not fatal.
4. Stops leftover V2 `bot_vnext/main.py` processes.
5. Fetches `origin/bot-vnext`.
6. Checks out `bot-vnext` and hard-resets tracked code to the remote branch.
7. Removes stale/untracked disposable files while preserving `.env` and `venv/` / `.venv/`.
8. Removes the disposable SQLite queue/database and Python caches.
9. Installs the **tracked** `telegram-bot.service` into `/etc/systemd/system/`.
10. Runs `systemctl daemon-reload` and enables the service.
11. Creates the virtualenv if missing and installs `requirements.txt`.
12. Ensures Playwright Chromium is installed when Playwright is available.
13. Runs Python syntax checks on the active V2 call path.
14. Verifies HLS concurrency is 16 segments per video.
15. Verifies the canonical systemd unit is installed and enabled.
16. Starts the service.
17. Verifies the service is active.
18. Verifies **exactly one** V2 `main.py` process is running.
19. Prints a success summary.

## What survives an update

- `.env` — runtime secrets/configuration.
- `venv/` or `.venv/` — Python environment, recreated if missing.
- Git history on GitHub.

## What is intentionally reset/deleted

- Local tracked code modifications.
- Old disposable SQLite queue/database state.
- Stale untracked runtime files.
- Python `__pycache__` directories.
- Old `backup_upload_rebuild_*` directories.

Incomplete download/upload media is disposable application state and is not used as a restart/resume mechanism.

## Service architecture

Production startup is exclusively:

```text
systemd telegram-bot.service
        ↓
bot_vnext/run_service.sh
        ↓
exclusive flock lock
        ↓
bot_vnext/main.py
```

The lock prevents a second runner from starting another V2 bot instance.

## After every update

The updater must end with:

```text
Clean deployment completed successfully
Service: telegram-bot.service (systemd only)
V2 processes: 1
HLS concurrency: 16 segments/video
Secrets: .env preserved
```

Then test `/status` and `/health` in Telegram and test the feature that was changed.

## If an update fails

**Do not start the bot manually.** Do not create a second launcher.

First collect:

```bash
cd ~/telegram_bot_new
sudo systemctl status telegram-bot.service --no-pager
sudo journalctl -u telegram-bot.service -n 100 --no-pager
pgrep -af 'bot_vnext/main.py'
```

Send the output for diagnosis. The production recovery path remains `./update.sh` after the underlying error is fixed.

## Code-change workflow

1. Diagnose from the actual V2 call path.
2. Fix the smallest correct V2 component.
3. Update relevant documentation in the same change.
4. Add a dated `CHANGELOG.md` entry.
5. Commit and push to `bot-vnext`.
6. Production runs **only** `./update.sh`.
7. Test the affected feature.
8. Record any new operational limitation.

## Architectural source of truth

The production entrypoint is `bot_vnext/main.py`. Root legacy modules are not the production V2 call path. The tracked service and updater are the source of truth for production startup/deployment.
