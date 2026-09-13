#!/usr/bin/env bash
set -Eeuo pipefail

# Clean deployment for the active bot-vnext V2 bot.
# Preserves .env and an existing local venv, but intentionally discards old code,
# queue/database state, backups, and other untracked runtime files.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BRANCH="bot-vnext"
REMOTE="origin"
PYTHON_BIN="$ROOT/venv/bin/python"
PIP_BIN="$ROOT/venv/bin/pip"
SERVICE="${BOT_SERVICE:-}"
PID_FILE="$ROOT/.bot_vnext.pid"

log(){ printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[ -d .git ] || fail "Run this from ~/telegram_bot_new (Git repository root)."

# Discover a likely systemd service unless the caller supplied BOT_SERVICE.
if [ -z "$SERVICE" ] && command -v systemctl >/dev/null 2>&1; then
  for candidate in telegram_bot.service telegram-bot.service bot.service telegram_bot_new.service; do
    if systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$candidate"; then
      SERVICE="$candidate"
      break
    fi
  done
fi

log "Stopping bot"
if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1; then
  sudo systemctl stop "$SERVICE" || true
elif [ -f "$PID_FILE" ]; then
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  sleep 2
fi

log "Fetching $REMOTE/$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"
git checkout "$BRANCH" >/dev/null 2>&1 || true

log "Resetting tracked code to $REMOTE/$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

log "Removing stale local files"
# Keep only runtime secrets and the virtualenv. Everything else untracked is disposable.
git clean -fdx -e .env -e venv/ -e .venv/
rm -f bot_vnext.db
rm -rf backup_upload_rebuild_* 2>/dev/null || true
find . -type d -name __pycache__ -prune -exec rm -rf {} +

# The active V2 downloader is the authoritative runtime path. Keep its requested
# 16-way HLS concurrency explicit even if an older remote commit still contains 4.
ACTIVE_HLS="$ROOT/bot_vnext/app/downloader/engine.py"
[ -f "$ACTIVE_HLS" ] || fail "Active V2 downloader not found: $ACTIVE_HLS"
sed -i 's/asyncio\.Semaphore(4)/asyncio.Semaphore(16)/g; s/4 segments download concurrently/16 segments download concurrently/g' "$ACTIVE_HLS"

log "Preparing Python environment"
if [ ! -x "$PYTHON_BIN" ]; then
  python3 -m venv "$ROOT/venv"
fi
"$PYTHON_BIN" -m pip install --upgrade pip
"$PIP_BIN" install -r requirements.txt

if "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  log "Ensuring Playwright Chromium is installed"
  "$PYTHON_BIN" -m playwright install chromium
fi

log "Running syntax checks"
"$PYTHON_BIN" -m py_compile bot_vnext/main.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py

log "Verifying active HLS concurrency"
grep -nE 'Semaphore\(16\)|16 segments download concurrently' "$ACTIVE_HLS" || fail "HLS concurrency is not 16"

log "Starting bot"
if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1; then
  sudo systemctl start "$SERVICE"
  sleep 3
  sudo systemctl --no-pager --full status "$SERVICE" || true
  echo
  echo "Recent logs:"
  sudo journalctl -u "$SERVICE" -n 40 --no-pager || true
else
  nohup "$PYTHON_BIN" bot_vnext/main.py >> bot_vnext.log 2>&1 &
  echo $! > "$PID_FILE"
  sleep 3
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    log "Bot started with PID $(cat "$PID_FILE")"
    tail -n 40 bot_vnext.log || true
  else
    tail -n 80 bot_vnext.log || true
    fail "Bot exited during startup"
  fi
fi

log "Clean deployment completed successfully"
echo "Branch: $BRANCH"
echo "HLS concurrency: 16 segments/video"
echo "Old SQLite queue/database: removed"
echo "Old local code: reset/cleaned"
echo "Secrets: .env preserved"
