#!/usr/bin/env bash
set -Eeuo pipefail

# Canonical production updater.
# Update flow:
#   1) snapshot the currently running/working Git revision WITHOUT stopping the service
#   2) fetch/reset/install the new revision
#   3) validate and start the new revision
#   4) if any deployment/start validation fails, automatically restore the last working revision
#      and restart it
# .env and the virtualenv are preserved. Disposable runtime state is reset.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BRANCH="bot-vnext"
REMOTE="origin"
PYTHON_BIN="$ROOT/venv/bin/python"
PIP_BIN="$ROOT/venv/bin/pip"
SERVICE="telegram-bot.service"
BACKUP_ROOT="$HOME/.telegram_bot_v2_backups"
LAST_WORKING_FILE="$BACKUP_ROOT/last_working_commit"
UPDATE_BACKUP=""
PREVIOUS_SHA=""
ROLLING_BACK="0"

log(){ printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[ -d .git ] || fail "Run this from ~/telegram_bot_new (Git repository root)."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required for production."
command -v git >/dev/null 2>&1 || fail "git is required."

mkdir -p "$BACKUP_ROOT"

# Keep the backup outside the repository so git clean/reset can never remove it.
# The marker is only advanced after a deployment has passed all checks and is active.
if [ -s "$LAST_WORKING_FILE" ]; then
  SAVED_SHA="$(tr -d '[:space:]' < "$LAST_WORKING_FILE")"
  if git cat-file -e "${SAVED_SHA}^{commit}" 2>/dev/null; then
    PREVIOUS_SHA="$SAVED_SHA"
  fi
fi
if [ -z "$PREVIOUS_SHA" ]; then
  PREVIOUS_SHA="$(git rev-parse HEAD)"
fi

date_tag="$(date '+%Y%m%d_%H%M%S')"
UPDATE_BACKUP="$BACKUP_ROOT/${date_tag}_${PREVIOUS_SHA:0:12}"
mkdir -p "$UPDATE_BACKUP"

log "Creating last-working-version backup WITHOUT stopping the service"
echo "$PREVIOUS_SHA" > "$UPDATE_BACKUP/commit"
echo "$PREVIOUS_SHA" > "$LAST_WORKING_FILE.pending"
# git archive gives us a complete tracked-code snapshot; .env is intentionally not included.
git archive --format=tar "$PREVIOUS_SHA" | gzip -c > "$UPDATE_BACKUP/source.tar.gz"
cp -f "$LAST_WORKING_FILE" "$UPDATE_BACKUP/previous_marker" 2>/dev/null || true
ln -sfn "$UPDATE_BACKUP" "$BACKUP_ROOT/latest"
log "Backup ready: $UPDATE_BACKUP (commit $PREVIOUS_SHA)"

rollback(){
  [ "$ROLLING_BACK" = "1" ] && return 1
  ROLLING_BACK="1"
  echo >&2
  echo "============================================================" >&2
  echo "UPDATE FAILED — RESTORING LAST WORKING VERSION" >&2
  echo "Commit: $PREVIOUS_SHA" >&2
  echo "============================================================" >&2

  sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true

  # Restore the exact known-good tracked revision.
  git reset --hard "$PREVIOUS_SHA" >/dev/null 2>&1 || {
    echo "ERROR: Git rollback to $PREVIOUS_SHA failed." >&2
    return 1
  }
  git clean -fdx -e .env -e venv/ -e .venv/ >/dev/null 2>&1 || true
  rm -f bot_vnext.db bot_vnext/app/storage/bot_vnext.db
  rm -rf backup_upload_rebuild_* 2>/dev/null || true
  find . -type d -name __pycache__ -prune -exec rm -rf {} +

  SERVICE_FILE="$ROOT/telegram-bot.service"
  if [ -f "$SERVICE_FILE" ]; then
    sudo install -m 0644 "$SERVICE_FILE" "/etc/systemd/system/$SERVICE"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE" >/dev/null || true
  fi

  if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: Python virtualenv is missing; cannot complete automatic rollback." >&2
    return 1
  fi

  "$PIP_BIN" install -r requirements.txt >/tmp/telegram-bot-v2-rollback-pip.log 2>&1 || {
    cat /tmp/telegram-bot-v2-rollback-pip.log >&2 || true
    echo "ERROR: Rollback dependency installation failed." >&2
    return 1
  }

  if "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
    "$PYTHON_BIN" -m playwright install chromium >/dev/null 2>&1 || true
  fi

  if [ -f bot_vnext/apply_ui_runtime_patch.py ]; then
    "$PYTHON_BIN" bot_vnext/apply_ui_runtime_patch.py || {
      echo "ERROR: Rollback UI compatibility patch failed." >&2
      return 1
    }
  fi

  "$PYTHON_BIN" -m py_compile bot_vnext/main.py bot_vnext/apply_ui_runtime_patch.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/downloader/browser_hls.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py || {
    echo "ERROR: Rolled-back revision failed syntax validation." >&2
    return 1
  }
  "$PYTHON_BIN" tools/release_guard.py || {
    echo "ERROR: Rolled-back revision failed the protected release guard." >&2
    return 1
  }

  sudo systemctl start "$SERVICE" >/dev/null 2>&1 || {
    echo "ERROR: Rolled-back service failed to start." >&2
    return 1
  }
  sleep 3
  sudo systemctl is-active "$SERVICE" >/dev/null || {
    echo "ERROR: Rolled-back service is not active." >&2
    sudo systemctl --no-pager --full status "$SERVICE" >&2 || true
    return 1
  }

  mapfile -t ROLLBACK_RUNNING < <(pgrep -u "$(id -u)" -x -f "$PYTHON_BIN[[:space:]]+$ROOT/bot_vnext/main.py([[:space:]]|$)" || true)
  [ "${#ROLLBACK_RUNNING[@]}" -eq 1 ] || {
    echo "ERROR: Rollback expected exactly 1 V2 Python process, found ${#ROLLBACK_RUNNING[@]}" >&2
    return 1
  }

  printf '%s\n' "$PREVIOUS_SHA" > "$LAST_WORKING_FILE"
  rm -f "$LAST_WORKING_FILE.pending"
  log "Automatic rollback SUCCESS — service restored to $PREVIOUS_SHA"
  echo "Backup kept at: $UPDATE_BACKUP"
  return 0
}

trap 'rc=$?; if [ "$rc" -ne 0 ] && [ "$ROLLING_BACK" = "0" ]; then rollback || true; fi; exit "$rc"' EXIT

log "Stopping canonical service (if installed)"
sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true

log "Stopping any leftover V2 main.py process"
mapfile -t PIDS < <(pgrep -u "$(id -u)" -f "python.*$ROOT/bot_vnext/main.py" || true)
for pid in "${PIDS[@]}"; do
  [ "$pid" = "$$" ] && continue
  kill "$pid" 2>/dev/null || true
done
if [ "${#PIDS[@]}" -gt 0 ]; then sleep 2; fi
mapfile -t PIDS2 < <(pgrep -u "$(id -u)" -f "python.*$ROOT/bot_vnext/main.py" || true)
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
rm -f bot_vnext.db bot_vnext/app/storage/bot_vnext.db
rm -rf backup_upload_rebuild_* 2>/dev/null || true
find . -type d -name __pycache__ -prune -exec rm -rf {} +

SERVICE_FILE="$ROOT/telegram-bot.service"
[ -f "$SERVICE_FILE" ] || fail "Tracked service file missing after Git reset: $SERVICE_FILE"

log "Installing canonical systemd service"
sudo install -m 0644 "$SERVICE_FILE" "/etc/systemd/system/$SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null

ACTIVE_HLS="$ROOT/bot_vnext/app/downloader/engine.py"
[ -f "$ACTIVE_HLS" ] || fail "Active V2 downloader not found: $ACTIVE_HLS"

log "Preparing Python environment"
if [ ! -x "$PYTHON_BIN" ]; then python3 -m venv "$ROOT/venv"; fi
"$PYTHON_BIN" -m pip install --upgrade pip
"$PIP_BIN" install -r requirements.txt

if "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  log "Ensuring Playwright Chromium is installed"
  "$PYTHON_BIN" -m playwright install chromium
fi

log "Applying tracked UI compatibility patches"
"$PYTHON_BIN" bot_vnext/apply_ui_runtime_patch.py

log "Running protected release guard"
"$PYTHON_BIN" tools/release_guard.py

log "Running syntax checks"
"$PYTHON_BIN" -m py_compile bot_vnext/main.py bot_vnext/apply_ui_runtime_patch.py bot_vnext/app/pipeline.py bot_vnext/app/downloader/engine.py bot_vnext/app/downloader/browser_hls.py bot_vnext/app/queue/upload_manager.py bot_vnext/app/uploader/engine.py

log "Verifying active HLS concurrency"
grep -nE 'Semaphore\(16\)|16 segments download concurrently' "$ACTIVE_HLS" || fail "HLS concurrency is not 16"

log "Verifying canonical systemd unit"
sudo systemctl cat "$SERVICE" >/dev/null || fail "Canonical systemd unit is not available after installation."
sudo systemctl is-enabled "$SERVICE" >/dev/null || fail "Canonical systemd service is not enabled."

log "Starting canonical service"
sudo systemctl start "$SERVICE"
sleep 3
sudo systemctl is-active "$SERVICE" >/dev/null || {
  sudo systemctl --no-pager --full status "$SERVICE" || true
  fail "Canonical service failed to start."
}
sudo systemctl --no-pager --full status "$SERVICE" || true

log "Verifying exactly one V2 Python process"
mapfile -t RUNNING < <(pgrep -u "$(id -u)" -x -f "$PYTHON_BIN[[:space:]]+$ROOT/bot_vnext/main.py([[:space:]]|$)" || true)
[ "${#RUNNING[@]}" -eq 1 ] || fail "Expected exactly 1 V2 Python process, found ${#RUNNING[@]}"

printf '%s\n' "$(git rev-parse HEAD)" > "$LAST_WORKING_FILE"
rm -f "$LAST_WORKING_FILE.pending"

log "Clean deployment completed successfully"
echo "Branch: $BRANCH"
echo "Service: $SERVICE (systemd only)"
echo "V2 processes: 1"
echo "HLS concurrency: 16 segments/video"
echo "Old SQLite queue/database: removed"
echo "Old local code: reset/cleaned"
echo "Secrets: .env preserved"
echo "Selection method menu: same-message"
echo "Protected release guard: PASS"
echo "Last-working backup: $UPDATE_BACKUP"
echo "Last-working commit: $(git rev-parse HEAD)"