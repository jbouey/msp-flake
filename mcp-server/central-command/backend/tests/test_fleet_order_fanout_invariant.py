"""CI gates for #128 fleet_order_fanout_partial_completion sev2
substrate invariant (per audit/coach-128-fanout-completion-orphan-
gate-a-2026-05-16.md).

Source-shape sentinels pin Gate A's binding requirements:

  P0-1 — LEFT JOIN uses `WHERE foc.fleet_order_id IS NULL`, NOT
         `foc.id IS NULL` (composite PK, no id column).
  P0-2 — PREREQ_SCHEMA in test_startup_invariants_pg.py includes
         fleet_order_completions DROP + CREATE in lockstep.
  P0-3 — 'skipped' status counts as ack (else update_daemon fan-
         outs to already-updated boxes false-positive).
  P1-1 — Action narrowing via action LIKE 'PRIVILEGED_ACCESS_%'
         (else COUNT(*)-class timeout on admin_audit_log).
  P1-2 — Registered at sev2 (NOT sev3 — sibling parity with
         enable_emergency_access_failed_unack).

Plus structural:
  - function exists
  - registration in ALL_ASSERTIONS
  - 6h threshold (matches daemon ack cadence)
  - LIMIT 100 (bound log spam)
  - runbook + _DISPLAY_METADATA present
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ASSERTIONS = _BACKEND / "assertions.py"
_RUNBOOK = _BACKEND / "substrate_runbooks" / "fleet_order_fanout_partial_completion.md"
_PG_FIXTURE = _BACKEND / "tests" / "test_startup_invariants_pg.py"


def _read_src() -> str:
    return _ASSERTIONS.read_text(encoding="utf-8")


def _read_function_body() -> str:
    src = _read_src()
    m = re.search(
        r"async def _check_fleet_order_fanout_partial_completion.*?"
        r"(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "could not locate _check_fleet_order_fanout_partial_completion"
    return m.group(0)


def test_function_exists_and_registered_at_sev2():
    """Gate A P1-2: sev2 (NOT sev3 — sibling parity with
    enable_emergency_access_failed_unack)."""
    src = _read_src()
    assert "async def _check_fleet_order_fanout_partial_completion" in src
    m = re.search(
        r'Assertion\(\s*name="fleet_order_fanout_partial_completion"\s*,\s*'
        r'severity="(\w+)"',
        src,
    )
    assert m, (
        "fleet_order_fanout_partial_completion not registered in "
        "ALL_ASSERTIONS"
    )
    assert m.group(1) == "sev2", (
        f"severity={m.group(1)!r} — must be sev2 (NOT sev3). "
        f"Gate A P1-2: sibling enable_emergency_access_failed_unack "
        f"is sev2; fan-out partial completion is operationally "
        f"comparable. sev3 falls below operator-attention threshold."
    )


def test_p0_1_left_join_uses_fleet_order_id_not_id():
    """Gate A P0-1: fleet_order_completions has composite PK
    (fleet_order_id, appliance_id) — NO `id` column. `foc.id IS NULL`
    would 500 at runtime."""
    body = _read_function_body()
    assert "WHERE foc.fleet_order_id IS NULL" in body or \
           "foc.fleet_order_id IS NULL" in body, (
        "Gate A P0-1: LEFT JOIN unmatched-side check must be "
        "`WHERE foc.fleet_order_id IS NULL`. The fleet_order_"
        "completions table has composite PK (fleet_order_id, "
        "appliance_id) — NO `id` column. `foc.id IS NULL` would "
        "raise UndefinedColumnError every 60s tick."
    )
    # Sentinel — the wrong shape must NOT re-appear in actual SQL
    # (the docstring + comments may reference it as a "don't do this"
    # warning — strip docstrings before scanning so the warning text
    # doesn't false-trip the sentinel).
    body_sql_only = re.sub(r'"""[^"]*?"""', "", body, flags=re.DOTALL)
    body_sql_only = re.sub(r"#.*?\n", "\n", body_sql_only)
    assert "foc.id IS NULL" not in body_sql_only, (
        "Query references foc.id (which doesn't exist on fleet_"
        "order_completions). Use foc.fleet_order_id IS NULL."
    )


def test_p0_2_prereq_schema_includes_fleet_order_completions():
    """Gate A P0-2: test_startup_invariants_pg.py PREREQ_SCHEMA
    must DROP + CREATE fleet_order_completions in lockstep with the
    new invariant (per Session 220 #77 fixture-parity rule)."""
    fixture = _PG_FIXTURE.read_text()
    # Must have both DROP and CREATE
    assert "DROP TABLE IF EXISTS fleet_order_completions" in fixture, (
        "PREREQ_SCHEMA missing `DROP TABLE IF EXISTS fleet_order_"
        "completions CASCADE;` — per Session 220 #77, every CREATE "
        "in PREREQ_SCHEMA must have a matching DROP earlier in the "
        "same string. Otherwise test #2 in the same run hits "
        "DuplicateTableError."
    )
    assert "CREATE TABLE fleet_order_completions" in fixture, (
        "PREREQ_SCHEMA missing `CREATE TABLE fleet_order_completions` "
        "— required so the #128 invariant's LEFT JOIN doesn't fail "
        "in the pg-fixture tests."
    )


def test_p0_3_skipped_status_counts_as_ack():
    """Gate A P0-3: 'skipped' status (appliance at skip_version) is
    a successful completion. Omitting it would false-positive on
    every update_daemon fan-out to already-updated boxes."""
    body = _read_function_body()
    # The status filter must include 'skipped' alongside 'completed' /
    # 'acknowledged'.
    assert "'skipped'" in body, (
        "'skipped' status must be in the LEFT JOIN's status filter. "
        "Per Gate A P0-3: appliance at skip_version is a successful "
        "completion; omitting 'skipped' false-positives on every "
        "update_daemon fan-out to already-updated boxes."
    )


def test_p1_1_action_narrowing_for_timeout_safety():
    """Gate A P1-1: action LIKE 'PRIVILEGED_ACCESS_%' narrows the
    admin_audit_log scan. Without it the 24h scan COULD hit the
    Session 219 COUNT(*)-class timeout on this large audit table."""
    body = _read_function_body()
    assert "PRIVILEGED_ACCESS_%" in body or "action LIKE" in body, (
        "Gate A P1-1: query must narrow via "
        "`action LIKE 'PRIVILEGED_ACCESS_%'` to avoid unbounded "
        "24h scan on admin_audit_log (Session 219 COUNT(*) timeout "
        "class)."
    )


def test_6h_threshold():
    """6h is the right threshold per Gate A: daemon heartbeats every
    60s + mig 161 retries after 1h. Anything unacked at 6h is beyond
    retry budget."""
    body = _read_function_body()
    assert "INTERVAL '6 hours'" in body, (
        "Threshold must be 6h (matches daemon ack cadence + mig 161 "
        "retry window). Tighter (1h) would false-positive on "
        "legitimate offline appliances; looser (24h) wouldn't "
        "surface in time for operator action."
    )


def test_24h_window_for_partition_pruning():
    """24h window for partition pruning on admin_audit_log (table
    can be large)."""
    body = _read_function_body()
    assert "INTERVAL '24 hours'" in body, (
        "Query must bound the admin_audit_log scan to last 24h."
    )


def test_limit_100_bounds_log_spam():
    body = _read_function_body()
    assert "LIMIT 100" in body, (
        "Query must LIMIT 100 — bounds violation count per tick "
        "under widespread fan-out failure (e.g., partner outage)."
    )


def test_runbook_exists():
    assert _RUNBOOK.exists(), (
        f"substrate_runbooks/fleet_order_fanout_partial_completion."
        f"md missing. Looked at: {_RUNBOOK}"
    )
    content = _RUNBOOK.read_text()
    assert "Severity:** sev2" in content
    assert "fan-out" in content.lower() or "fanout" in content.lower()
    # Must cite the 4 root cause categories from Gate A
    assert "offline" in content.lower(), "runbook must cover offline root cause"
    assert "writer" in content.lower(), "runbook must cover completion-writer broken root cause"


def test_display_metadata_entry_exists():
    src = _read_src()
    assert '"fleet_order_fanout_partial_completion": {' in src, (
        "_DISPLAY_METADATA missing entry for "
        "fleet_order_fanout_partial_completion."
    )
