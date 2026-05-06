#!/usr/bin/env bash
# .githooks/full-test-sweep.sh — CI-parity backend test sweep.
#
# Runs every tests/test_*.py file in its own subprocess (matches CI's
# stub-isolation behavior) with parallelism `-P ${PRE_PUSH_PARALLEL:-6}`.
# Treats collection-time ImportError on backend deps (asyncpg, pynacl,
# pydantic_core, nacl, sqlalchemy.ext.asyncio, aiohttp, cryptography,
# google.auth) as SKIPPED — those run server-side. Real test failures
# are FATAL and abort the push.
#
# Why: the SOURCE_LEVEL_TESTS array in pre-push is a curated fast lane.
# CI runs the full backend tests directory. On 2026-05-06 the deploy of
# 18af959c failed at CI on test_auditor_kit_endpoint.py — that test was
# NOT in the curated list but WAS in CI. Local pre-push said clean; CI
# failed. This sweep closes that gap.
#
# Cost on a fully-equipped dev box: ~80-100s with -P 6. Faster on
# minimal dev boxes (more dep-skips). Opt-out: PRE_PUSH_SKIP_FULL=1.
#
# Args: $1 = backend directory (tests/ relative to it).

set -e

BACKEND_DIR="${1:-mcp-server/central-command/backend}"
PARALLEL="${PRE_PUSH_PARALLEL:-6}"

# Move to backend dir for pytest's tests/ collection root.
cd "$BACKEND_DIR"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Per-worker invocation. Writes "PASS|FAIL|SKIP <path>" to stdout for
# the parent to collect; on FAIL, dumps last 15 lines of pytest output
# to $TMPDIR/<basename>.log so the parent can surface them.
SWEEP_WORKER='
f="$1"
TMPDIR="$2"
out=$(python3 -m pytest "$f" -q --tb=line --no-header 2>&1)
st=$?
DEP_PATTERN="asyncpg|pynacl|pydantic_core|nacl\\.|sqlalchemy\\.ext\\.asyncio|aiohttp|cryptography\\.|google\\.auth"
if [ $st -ne 0 ] && echo "$out" | grep -qE "ERROR collecting|ModuleNotFoundError|ImportError" \
   && echo "$out" | grep -qE "$DEP_PATTERN"; then
    echo "SKIP $f"
elif [ $st -ne 0 ]; then
    echo "FAIL $f"
    base=$(basename "$f")
    echo "$out" | tail -15 > "$TMPDIR/$base.log"
else
    echo "PASS $f"
fi
'

RESULTS=$(
    find tests -maxdepth 1 -name "test_*.py" -not -name "*_pg.py" \
        | sort \
        | xargs -n1 -P "$PARALLEL" -I{} sh -c "$SWEEP_WORKER" _ {} "$TMPDIR"
)

PASSED=$(echo "$RESULTS" | grep -c "^PASS " || true)
SKIPPED=$(echo "$RESULTS" | grep -c "^SKIP " || true)
FAILED_LIST=$(echo "$RESULTS" | grep "^FAIL " | awk '{print $2}' || true)
FAILED=$(echo "$FAILED_LIST" | grep -c "^tests/" || true)

if [ "$FAILED" -gt 0 ]; then
    echo "❌ ${FAILED} file(s) failed (out of ${PASSED} passed, ${SKIPPED} skipped):"
    echo "$FAILED_LIST" | sed 's/^/  - /'
    echo ""
    # Show first 3 failure logs inline for fast diagnosis.
    count=0
    for f in $FAILED_LIST; do
        count=$((count + 1))
        if [ "$count" -gt 3 ]; then break; fi
        echo "=== $f ==="
        base=$(basename "$f")
        cat "$TMPDIR/$base.log" 2>/dev/null | sed 's/^/    /'
        echo ""
    done
    exit 1
fi

echo "✓ ${PASSED} passed, ${SKIPPED} skipped (need backend deps)"
exit 0
