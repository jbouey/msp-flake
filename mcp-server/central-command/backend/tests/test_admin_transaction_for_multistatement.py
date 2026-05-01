"""Regression test — multi-statement admin paths must use admin_transaction.

Session 214 P0 round-table 2026-04-30: the routing-pathology class
(Session 212 sigauth `303421cc`) is real and recurring. PgBouncer
transaction-pool can route the SET LOCAL app.is_admin and a subsequent
fetch to DIFFERENT backends; the latter sees app.is_admin='false' (mig
234 default), RLS hides every row, the call returns silent zero-rows.

Centralized fix: `tenant_middleware.admin_transaction(pool)` pins the
SET LOCAL + every subsequent statement to ONE PgBouncer backend via
explicit transaction. Single-statement reads can still use
`admin_connection`; multi-statement work MUST use `admin_transaction`.

This test is the per-line CI gate. If a future commit reverts any of
the 4 named sites back to `admin_connection` or raw `pool.acquire()`,
this test fails immediately. Adding a new entry here is a privileged
decision — see the round-table verdict.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


# Each entry is (file_relative_to_repo_root, anchor_substring,
# search_window_lines). The test asserts that within
# `search_window_lines` after the anchor, `admin_transaction(` appears
# and `admin_connection(` does NOT. The anchor must uniquely identify
# the function being checked.
_PINNED_SITES = [
    (
        "mcp-server/main.py",
        "async def _go_agent_status_decay_loop",
        120,
    ),
    (
        "mcp-server/central-command/backend/sites.py",
        "MIN_REPROVISION_VERSION = \"0.4.11\"",
        40,
    ),
    (
        "mcp-server/central-command/backend/provisioning.py",
        "admin_restore_appliance",  # function name in handler
        80,
    ),
    (
        "mcp-server/central-command/backend/background_tasks.py",
        "async def mark_stale_appliances_loop",
        50,
    ),
]


def test_pinned_admin_transaction_sites():
    failures = []
    for rel_path, anchor, window in _PINNED_SITES:
        path = REPO_ROOT / rel_path
        assert path.exists(), f"Pinned file not found: {rel_path}"
        text = path.read_text()
        idx = text.find(anchor)
        assert idx >= 0, f"Anchor {anchor!r} not found in {rel_path}"
        # Take the window after the anchor.
        tail = text[idx:]
        # Walk forward `window` lines.
        lines = tail.splitlines()[:window]
        chunk = "\n".join(lines)
        if "admin_transaction(" not in chunk:
            failures.append(
                f"{rel_path}: anchor {anchor!r} — admin_transaction( "
                f"NOT found within {window} lines after anchor. "
                f"Multi-statement admin paths must use admin_transaction "
                f"(see Session 214 P0 round-table 2026-04-30)."
            )
            continue
        # Also assert the older admin_connection( does not still appear
        # at the swapped site. The first admin_transaction occurrence
        # marks the swap; everything before it in the window must not
        # be a competing admin_connection on the same conn variable.
        first_tx = chunk.find("admin_transaction(")
        prefix = chunk[:first_tx]
        # The bare token "admin_connection(pool)" appearing in the
        # prefix means the swap was incomplete or the helper-import
        # is being shadowed. We tolerate the IMPORT line itself.
        for line_no, line in enumerate(prefix.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                continue
            if "admin_connection(" in line:
                failures.append(
                    f"{rel_path}: anchor {anchor!r} — admin_connection( "
                    f"appears before admin_transaction( on line +{line_no} "
                    f"after anchor (line: {stripped[:80]!r}). The pinned "
                    f"swap must be complete."
                )
                break

    if failures:
        msg = "\n".join(failures)
        raise AssertionError(
            f"admin_transaction pinning regressed in {len(failures)} "
            f"site(s):\n{msg}"
        )


def test_no_string_concat_minutes_interval_pattern_in_pinned_loops():
    """Banned pattern from Session 214 hot-fix (commit 903746bd):
    `($N || ' unit')::INTERVAL` is fragile under asyncpg int binding.

    This test pins the mark_stale_appliances_loop body specifically,
    since it was migrated from `($1 || ' minutes')::INTERVAL` to
    `make_interval(mins => $1)` as part of the Block 1 closure.
    """
    path = REPO_ROOT / "mcp-server/central-command/backend/background_tasks.py"
    text = path.read_text()
    idx = text.find("async def mark_stale_appliances_loop")
    assert idx >= 0
    # Take a 60-line window after the anchor.
    chunk = "\n".join(text[idx:].splitlines()[:60])
    bad_pattern = re.compile(r"\(\$\d+\s*\|\|\s*'[^']*'\)\s*::\s*INTERVAL", re.I)
    matches = bad_pattern.findall(chunk)
    assert not matches, (
        f"Banned `($N || ' unit')::INTERVAL` pattern resurfaced in "
        f"mark_stale_appliances_loop: {matches}. Use "
        f"`make_interval(units => $N)` instead."
    )
