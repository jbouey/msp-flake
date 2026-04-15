#!/usr/bin/env bash
# setup-hooks.sh — one-shot: point git at our committed hooks.
#
# git's default hook path is `.git/hooks/` which is NOT tracked. Any
# hook we want everyone in the repo to share has to live in a tracked
# directory (`.githooks/`) and each clone must opt in by pointing
# `core.hooksPath` at it. That's what this script does.
#
# Run once per clone:
#
#     scripts/setup-hooks.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [ ! -d .githooks ]; then
    echo "❌ .githooks/ not found at $REPO_ROOT"
    exit 1
fi

# Make every committed hook executable — git won't run a non-exec hook.
find .githooks -type f -exec chmod +x {} \;

git config core.hooksPath .githooks
echo "✓ git core.hooksPath → .githooks"
echo "✓ installed hooks:"
ls -1 .githooks | sed 's/^/   - /'

echo ""
echo "Verify by making a harmless change + pushing; the pre-push smoke"
echo "check runs in <1s and blocks push on an import error."
