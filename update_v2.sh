#!/usr/bin/env bash
set -Eeuo pipefail

# Stable production updater. This script copies itself outside the Git worktree
# before any checkout/reset so Git cannot replace the running updater.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_COPY="/tmp/telegram-bot-update-$$.sh"
cp -f "${BASH_SOURCE[0]}" "$SELF_COPY"
chmod 700 "$SELF_COPY"
trap 'rm -f "$SELF_COPY"' EXIT
exec bash "$SELF_COPY" --run-from-copy
