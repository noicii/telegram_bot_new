# Operations Runbook — Bot V2

## Daily checks

The bot exposes the most useful runtime checks directly in Telegram:

```text
/status
/queue
/health
```

### `/status`

Use it for:

- active download count;
- active upload count;
- queued counts;
- completed/failed/cancelled totals;
- selected download method;
- realtime filesystem usage.

### `/queue`

Use it to inspect active/queued tasks and cancel individual active tasks.

### `/health`

Use it to detect missing runtime components and worker-state problems.

## Filesystem monitoring

Check the real filesystem from SSH with:

```bash
df -hT /
```

For a high-level directory breakdown:

```bash
sudo du -xhd1 / 2>/dev/null | sort -h
```

For deeper investigation of the common heavy directories:

```bash
sudo du -xhd2 /var /tmp /home /root /opt 2>/dev/null | sort -h | tail -40
```

The V2 cleanup policy starts normal pressure cleanup at 80% and treats 90% as critical. See `bot_vnext/STORAGE_CLEANUP.md`.

## Logs

For the systemd service:

```bash
sudo journalctl -u telegram-bot.service -n 100 --no-pager
```

Follow live logs:

```bash
sudo journalctl -u telegram-bot.service -f
```

## Common failure: Telegram upload size

If a video exceeds Telegram's supported upload boundary, the V2 uploader should split it into approximately 1900 MiB parts before sending.

If splitting fails with `No space left on device`, check:

```bash
df -hT /
sudo du -xhd1 / 2>/dev/null | sort -h
```

Do not assume the error is a Telegram limit problem if the filesystem is full; FFmpeg itself needs enough free space to create the split parts.

## Common failure: SQLite `disk I/O error`

A SQLite `disk I/O error` during a full-disk incident can be a consequence of filesystem exhaustion rather than database corruption.

Check disk usage first.

## Common failure: `FLOOD_WAIT`

Telegram `FLOOD_WAIT` is a Telegram rate-limit condition. It is separate from local disk exhaustion. Do not treat it as evidence that the filesystem is broken.

## Common failure: bot starts twice

Check the service and running processes:

```bash
systemctl status telegram-bot.service --no-pager
pgrep -af 'bot_vnext/main.py'
```

There should normally be one production V2 process managed by systemd. Avoid manually launching another copy while the service is active.

## Common failure: old code appears to be running

Check the deployed commit:

```bash
cd ~/telegram_bot_new
git log -1 --oneline
git status --short
```

Then run the canonical updater:

```bash
bash update.sh
```

The updater resets tracked code to `origin/bot-vnext`.

## Common failure: HLS concurrency changed

Verify:

```bash
grep -nE 'Semaphore\(16\)|16 segments download concurrently' bot_vnext/app/downloader/engine.py
```

The current production target is 16 concurrent HLS segments per video.

## Clean-state philosophy

The bot intentionally treats the following as disposable:

- local queue database;
- incomplete downloads;
- temporary split parts;
- stale runtime logs/files;
- Python caches.

The `.env` is not disposable because it contains runtime secrets/configuration.

## Before changing production behavior

Read:

1. `README.md`
2. `PROJECT_HANDOFF.md`
3. `ARCHITECTURE.md`
4. `BOT_FEATURES.md`
5. `UPDATE_GUIDE.md`
6. `bot_vnext/STORAGE_CLEANUP.md`

Then trace imports from `bot_vnext/main.py` to the component you intend to change.
