#!/usr/bin/env bash
set -Eeuo pipefail

# Stable entrypoint. update_v2.sh copies itself outside the Git worktree
# before checkout/reset, so the running updater can never be replaced in-place.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/update_v2.sh" "$@"
