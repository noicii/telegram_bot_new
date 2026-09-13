#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT/venv/bin/python"
MAIN="$ROOT/bot_vnext/main.py"
LOCK="$ROOT/.bot_vnext.singleton.lock"

exec /usr/bin/flock -n "$LOCK" "$PYTHON_BIN" "$MAIN"
