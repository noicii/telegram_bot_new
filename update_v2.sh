#!/usr/bin/env bash
set -Eeuo pipefail

# Production updater. It copies itself outside the Git worktree before any
# checkout/reset, so Git can never replace the running updater in memory.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_COPY="/tmp/telegram-bot-update-$$.sh"
MODE="${1:-start}"

log(){ printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

if [ "$MODE" != "--run-from-copy" ]; then
  cp -f "${BASH_SOURCE[0]}" "$SELF_COPY"
  chmod 700 "$SELF_COPY"
  exec bash "$SELF_COPY" --run-from-copy
fi

cd "$ROOT"
BRANCH="bot-vnext-fixed"
REMOTE="origin"
SERVICE="telegram-bot.service"
PYTHON_BIN="$ROOT/venv/bin/python"
PIP_BIN="$ROOT/venv/bin/pip"
BACKUP_ROOT="$HOME/.telegram_bot_v2_backups"
LAST_WORKING_FILE="$BACKUP_ROOT/last_working_commit"
mkdir -p "$BACKUP_ROOT"

[ -d .git ] || fail "Run this from the repository root."
command -v git >/dev/null 2>&1 || fail "git is required."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
command -v tar >/dev/null 2>&1 || fail "tar is required."

log "Fetching $REMOTE/$BRANCH"
git fetch --prune "$REMOTE" "$BRANCH"
TARGET_SHA="$(git rev-parse "$REMOTE/$BRANCH")"
CURRENT_SHA="$(git rev-parse HEAD)"

PREVIOUS_SHA="$CURRENT_SHA"
if [ -s "$LAST_WORKING_FILE" ]; then
  SAVED_SHA="$(tr -d '[:space:]' < "$LAST_WORKING_FILE")"
  if git cat-file -e "${SAVED_SHA}^{commit}" 2>/dev/null; then PREVIOUS_SHA="$SAVED_SHA"; fi
fi

DATE_TAG="$(date '+%Y%m%d_%H%M%S')"
BACKUP="$BACKUP_ROOT/${DATE_TAG}_${PREVIOUS_SHA:0:12}"
mkdir -p "$BACKUP"
echo "$PREVIOUS_SHA" > "$BACKUP/commit"
git archive --format=tar "$PREVIOUS_SHA" | gzip -c > "$BACKUP/source.tar.gz"
ln -sfn "$BACKUP" "$BACKUP_ROOT/latest"
log "Last-working backup ready: $BACKUP"

log "Stopping production service"
sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true

log "Resetting source to $REMOTE/$BRANCH at $TARGET_SHA"
git checkout "$BRANCH" >/dev/null 2>&1 || true
git reset --hard "$TARGET_SHA"
git clean -fdx -e .env -e venv/ -e .venv/

# Continue from the immutable copy, never from the Git worktree copy.
SERVICE_FILE="$ROOT/telegram-bot.service"
ACTIVE_HLS="$ROOT/bot_vnext/app/downloader/engine.py"
[ -f "$SERVICE_FILE" ] || fail "Tracked systemd service file missing."
[ -f "$ACTIVE_HLS" ] || fail "Active V2 downloader missing."

log "Installing canonical systemd service"
sudo install -m 0644 "$SERVICE_FILE" "/etc/systemd/system/$SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null

if [ ! -x "$PYTHON_BIN" ]; then python3 -m venv "$ROOT/venv"; fi
"$PYTHON_BIN" -m pip install --upgrade pip
"$PIP_BIN" install -r requirements.txt

if "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  log "Ensuring Playwright Chromium is installed"
  "$PYTHON_BIN" -m playwright install chromium
fi

log "Running protected release guard"
"$PYTHON_BIN" tools/release_guard.py

log "Running syntax checks"
"$PYTHON_BIN" -m py_compile bot_vnext/main.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/downloader/browser_hls.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py bot_vnext/app/storage/database.py

log "Verifying HLS concurrency remains 16"
grep -nE 'Semaphore\(16\)|16 segments download concurrently' "$ACTIVE_HLS" || fail "HLS concurrency is not 16"

log "Starting canonical service"
sudo systemctl start "$SERVICE"
sleep 3
sudo systemctl is-active "$SERVICE" >/dev/null || {
  sudo systemctl --no-pager --full status "$SERVICE" || true
  fail "Canonical service failed to start."
}

mapfile -t RUNNING < <(pgrep -u "$(id -u)" -x -f "$PYTHON_BIN[[:space:]]+$ROOT/bot_vnext/main.py([[:space:]]|$)" || true)
[ "${#RUNNING[@]}" -eq 1 ] || fail "Expected exactly 1 V2 process, found ${#RUNNING[@]}"
printf '%s\n' "$(git rev-parse HEAD)" > "$LAST_WORKING_FILE"

log "Clean deployment completed successfully"
echo "Branch: $BRANCH"
echo "Service: $SERVICE (systemd only)"
echo "V2 processes: 1"
echo "HLS concurrency: 16 segments/video"
echo "Protected release guard: PASS"
echo "Last-working commit: $(git rev-parse HEAD)"
echo "Backup: $BACKUP"
rm -f "$SELF_COPY"
