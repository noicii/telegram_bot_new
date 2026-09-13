#!/usr/bin/env bash
set -Eeuo pipefail

# Canonical production updater: systemd is the only supported bot launcher.
# .env and the virtualenv survive; disposable runtime state is reset.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BRANCH="bot-vnext"
REMOTE="origin"
PYTHON_BIN="$ROOT/venv/bin/python"
PIP_BIN="$ROOT/venv/bin/pip"
SERVICE="${BOT_SERVICE:-telegram-bot.service}"

log(){ printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[ -d .git ] || fail "Run this from ~/telegram_bot_new (Git repository root)."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required for production."
systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$SERVICE" || fail "$SERVICE not installed. Install the tracked service file first. No fallback launcher is used."

log "Stopping canonical service"
sudo systemctl stop "$SERVICE" || true

log "Stopping any leftover V2 main.py process"
mapfile -t PIDS < <(pgrep -u "$(id -u)" -f "$ROOT/bot_vnext/main.py" || true)
for pid in "${PIDS[@]}"; do
  [ "$pid" = "$$" ] && continue
  kill "$pid" 2>/dev/null || true
done
if [ "${#PIDS[@]}" -gt 0 ]; then sleep 2; fi
mapfile -t PIDS2 < <(pgrep -u "$(id -u)" -f "$ROOT/bot_vnext/main.py" || true)
for pid in "${PIDS2[@]}"; do
  [ "$pid" = "$$" ] && continue
  kill -9 "$pid" 2>/dev/null || true
done

log "Fetching $REMOTE/$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"
git checkout "$BRANCH" >/dev/null 2>&1 || true

log "Resetting tracked code to $REMOTE/$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

log "Removing stale local files"
git clean -fdx -e .env -e venv/ -e .venv/
rm -f bot_vnext.db
rm -rf backup_upload_rebuild_* 2>/dev/null || true
find . -type d -name __pycache__ -prune -exec rm -rf {} +

ACTIVE_HLS="$ROOT/bot_vnext/app/downloader/engine.py"
[ -f "$ACTIVE_HLS" ] || fail "Active V2 downloader not found: $ACTIVE_HLS"
sed -i 's/asyncio\.Semaphore(4)/asyncio.Semaphore(16)/g; s/4 segments download concurrently/16 segments download concurrently/g' "$ACTIVE_HLS"

log "Preparing Python environment"
if [ ! -x "$PYTHON_BIN" ]; then python3 -m venv "$ROOT/venv"; fi
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

log "Installing/updating canonical systemd service"
sudo install -m 0644 "$ROOT/telegram-bot.service" "/etc/systemd/system/$SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null

log "Starting canonical service"
sudo systemctl start "$SERVICE"
sleep 3
sudo systemctl --no-pager --full status "$SERVICE" || true

log "Verifying exactly one V2 process"
mapfile -t RUNNING < <(pgrep -u "$(id -u)" -f "$ROOT/bot_vnext/main.py" || true)
[ "${#RUNNING[@]}" -eq 1 ] || fail "Expected exactly 1 V2 main.py process, found ${#RUNNING[@]}"

log "Clean deployment completed successfully"
echo "Branch: $BRANCH"
echo "Service: $SERVICE (systemd only)"
echo "V2 processes: 1"
echo "HLS concurrency: 16 segments/video"
echo "Old SQLite queue/database: removed"
echo "Old local code: reset/cleaned"
echo "Secrets: .env preserved"
