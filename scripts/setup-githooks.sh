#!/usr/bin/env bash
# scripts/setup-githooks.sh — one-time clone setup.
#
# Sets `core.hooksPath = .githooks` so the project's pre-push +
# pre-commit + commit-msg gates (full backend test sweep, frontend
# tsc/eslint/vitest, python3.11 syntax check, banned-words sweep,
# CI-parity guards) actually fire on local commits + pushes.
#
# WITHOUT this, git falls back to `.git/hooks/` which contains only
# the default samples — every gate the project relies on is silently
# bypassed. The 2026-05-08 DangerousActionModal sprint surfaced this:
# the agent's worktree clone had hooksPath unset, so its pre-push
# "ran" (it didn't), and CI-parity verification fell entirely to
# server-side status checks. Auto-rollback held production safe but
# the local cycle lost ~7 minutes per regression class.
#
# Run once after `git clone`:
#   bash scripts/setup-githooks.sh
#
# Idempotent — safe to re-run. Verifies the hooks dir exists, sets
# the config, and prints the verification one-liner.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="${REPO_ROOT}/.githooks"

if [ ! -d "$HOOKS_DIR" ]; then
    echo "[setup-githooks] ❌ ${HOOKS_DIR} does not exist."
    echo "[setup-githooks]    This script must run from the OsirisCare repo root."
    exit 1
fi

# Set the hookspath. Use a relative path so it works across
# worktree clones too.
git config --local core.hooksPath .githooks

echo "[setup-githooks] ✓ core.hooksPath = .githooks"
echo "[setup-githooks] ✓ pre-commit + pre-push + commit-msg now active"
echo ""
echo "Verify with:"
echo "  git config --get core.hooksPath"
echo ""
echo "If you cloned a fresh worktree and pre-push isn't firing, re-run"
echo "this script from inside that worktree."
