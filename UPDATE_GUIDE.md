# Update Guide — Bot V2

This file is the **update-only** runbook. It is intentionally separate from the full deployment and migration documents.

## Canonical update command

On the production VM:

```bash
cd ~/telegram_bot_new
bash update.sh
```

Do not manually copy individual Python files from an older checkout into production.

## What `update.sh` does

The canonical updater:

1. Stops the running bot/service.
2. Fetches `origin/bot-vnext`.
3. Resets tracked files to the remote `bot-vnext` branch.
4. Removes stale/untracked disposable files while preserving `.env` and the virtualenv.
5. Removes the disposable SQLite queue/database.
6. Removes stale Python caches and old rebuild/backup runtime directories.
7. Installs `requirements.txt`.
8. Ensures Playwright Chromium.
9. Runs Python syntax checks.
10. Verifies the V2 HLS downloader is configured for 16 concurrent segments.
11. Starts the bot through systemd when the known service is present; otherwise uses the fallback launcher.
12. Prints deployment status.

## What survives an update

- `.env`
- `venv/` or `.venv/`
- Git history on the remote repository

## What is intentionally reset/deleted

- Local tracked code modifications.
- Old SQLite queue/database state.
- Old untracked runtime files.
- Python caches.
- Stale downloads/logs generated in disposable local directories.
- Old backup/rebuild directories.

If a future feature creates state that must survive updates, document it and modify `update.sh` before relying on it.

## Before updating

For a normal update, no manual backup is required for queue/download state because that state is intentionally disposable.

If you have an important new asset or secret, make sure it is documented and preserved before updating.

## After updating

Check the final lines from `update.sh` for:

```text
Clean deployment completed successfully
Branch: bot-vnext
HLS concurrency: 16 segments/video
Secrets: .env preserved
```

Then check:

```bash
systemctl status telegram-bot.service --no-pager
```

And in Telegram:

```text
/status
/health
```

## If update fails

### Git fetch fails with `No space left on device`

First inspect:

```bash
df -hT /
sudo du -xhd1 / 2>/dev/null | sort -h
```

Do not immediately delete random system directories. Find the largest disposable runtime data first.

### Bot does not start

Check:

```bash
sudo journalctl -u telegram-bot.service -n 100 --no-pager
```

Then check syntax manually:

```bash
python -m py_compile bot_vnext/main.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py
```

### HLS concurrency is wrong

Check:

```bash
grep -nE 'Semaphore\(16\)|16 segments download concurrently' bot_vnext/app/downloader/engine.py
```

The current target is 16 segments per video.

## Code-change workflow

When changing production behavior:

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

Never treat a root legacy file as production merely because it contains similar code. The production entrypoint is `bot_vnext/main.py`; follow its imports into the V2 application before editing.
