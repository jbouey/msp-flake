"""Unit tests for `_check_sensitive_workflow_advanced_without_baa`
(Task #96, #92 Gate B P1 carry).

The substrate invariant covers 3 workflows via one UNION ALL query +
per-row predicate (`baa_status.baa_enforcement_ok`) + per-state-machine
bypass-row lookup. This test mocks asyncpg + the predicate to exercise
each branch deterministically.

Coverage:
  1. evidence_export violation SKIPS the bypass-row lookup (the
     Coach P0 guard from #92 — evidence_export uses raise-403 not
     log-bypass, so the bypass-row branch must never query for it).
  2. cross_org_relocate / owner_transfer with matching bypass row →
     EXCLUDED (legitimate operator carve-out).
  3. cross_org_relocate / owner_transfer with no bypass row → Violation.
  4. Org with active BAA → no violation across all 3 workflows.
  5. Per-org cache: 2 rows for the same org call the predicate ONCE.
  6. Source-shape: the SQL contains the method filter literal
     `IN ('client_portal','partner_portal')` — pins Carol carve-outs.

Mocking notes:
  - `conn.fetch(SQL)` returns the canned UNION ALL output.
  - `conn.fetchval(SQL, workflow, org_id)` returns 1 (bypass exists) or
    None (no bypass), keyed by the `workflow` arg in the side-effect.
  - `baa_status.baa_enforcement_ok` is patched to return False for the
    test org_id (we're testing the violation paths).
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import assertions  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _make_conn(union_rows, bypass_map=None):
    """Build a mocked asyncpg connection.

    - `union_rows`: list of dicts the UNION ALL fetch should return.
    - `bypass_map`: dict keyed by (workflow, org_id) → 1 or None.
       Maps to what fetchval should return for the bypass-row lookup.
       Defaults to all-None (no bypass entries).
    """
    bypass_map = bypass_map or {}
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=union_rows)

    async def _fetchval(_sql, workflow, org_id):
        return bypass_map.get((workflow, org_id))

    conn.fetchval = _fetchval
    return conn


def _patch_baa_ok(verdicts):
    """Patch baa_status.baa_enforcement_ok with a per-org verdict map."""
    import baa_status
    async def _ok(_conn, org_id):
        return verdicts.get(org_id, False)
    return patch.object(baa_status, "baa_enforcement_ok", _ok)


def test_evidence_export_violation_skips_bypass_lookup():
    """The Coach P0 guard from #92: evidence_export rows must NOT
    query the baa_enforcement_bypass table. Confirms the
    `if workflow != 'evidence_export'` branch holds — a violation
    fires without ever consulting fetchval."""
    conn = _make_conn([
        {
            "workflow": "evidence_export",
            "org_id": "org-A",
            "site_id": "site-A",
            "row_id": "audit-1",
            "advanced_at": _ts("2026-05-15T12:00:00"),
        },
    ])
    # Sentinel: track whether fetchval was called at all.
    fetchval_calls = []
    original_fetchval = conn.fetchval

    async def _tracked_fetchval(sql, workflow, org_id):
        fetchval_calls.append((workflow, org_id))
        return await original_fetchval(sql, workflow, org_id)

    conn.fetchval = _tracked_fetchval

    with _patch_baa_ok({"org-A": False}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert len(violations) == 1, (
        f"evidence_export with no BAA must fire; got {len(violations)}"
    )
    assert violations[0].details["workflow"] == "evidence_export"
    assert not fetchval_calls, (
        f"evidence_export must NOT consult the bypass-row table; "
        f"saw fetchval calls: {fetchval_calls}"
    )


def test_state_machine_workflow_with_bypass_row_is_excluded():
    """A cross_org_relocate row with no BAA but a matching
    baa_enforcement_bypass admin_audit_log entry MUST be excluded
    (legitimate operator carve-out)."""
    conn = _make_conn(
        union_rows=[{
            "workflow": "cross_org_relocate",
            "org_id": "org-B",
            "site_id": "site-B",
            "row_id": "relocate-1",
            "advanced_at": _ts("2026-05-15T12:00:00"),
        }],
        bypass_map={("cross_org_relocate", "org-B"): 1},  # bypass exists
    )
    with _patch_baa_ok({"org-B": False}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert violations == [], (
        f"matching bypass row must exclude the violation; got {len(violations)}"
    )


def test_state_machine_workflow_no_bypass_fires():
    """owner_transfer with no BAA and no matching bypass → Violation."""
    conn = _make_conn(
        union_rows=[{
            "workflow": "owner_transfer",
            "org_id": "org-C",
            "site_id": None,
            "row_id": "transfer-1",
            "advanced_at": _ts("2026-05-15T12:00:00"),
        }],
        bypass_map={},  # no bypass
    )
    with _patch_baa_ok({"org-C": False}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert len(violations) == 1
    assert violations[0].details["workflow"] == "owner_transfer"
    assert violations[0].details["client_org_id"] == "org-C"


def test_org_with_active_baa_no_violations_any_workflow():
    """All 3 workflows for an org whose baa_enforcement_ok=True
    produce zero violations."""
    conn = _make_conn([
        {"workflow": "cross_org_relocate", "org_id": "org-D",
         "site_id": "site-D", "row_id": "r1",
         "advanced_at": _ts("2026-05-15T12:00:00")},
        {"workflow": "owner_transfer", "org_id": "org-D",
         "site_id": None, "row_id": "t1",
         "advanced_at": _ts("2026-05-15T12:00:00")},
        {"workflow": "evidence_export", "org_id": "org-D",
         "site_id": "site-D", "row_id": "a1",
         "advanced_at": _ts("2026-05-15T12:00:00")},
    ])
    with _patch_baa_ok({"org-D": True}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert violations == []


def test_per_org_predicate_cache_called_once_for_repeat_org():
    """Two rows for the same org call baa_enforcement_ok exactly
    ONCE. Pins the ok_cache mechanism."""
    conn = _make_conn([
        {"workflow": "cross_org_relocate", "org_id": "org-E",
         "site_id": "site-E", "row_id": "r1",
         "advanced_at": _ts("2026-05-15T12:00:00")},
        {"workflow": "evidence_export", "org_id": "org-E",
         "site_id": "site-E", "row_id": "a1",
         "advanced_at": _ts("2026-05-15T13:00:00")},
    ])
    call_counter = {"n": 0}
    import baa_status

    async def _counting_ok(_conn, org_id):
        call_counter["n"] += 1
        return False

    with patch.object(baa_status, "baa_enforcement_ok", _counting_ok):
        _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert call_counter["n"] == 1, (
        f"baa_enforcement_ok must be called once per distinct org; "
        f"got {call_counter['n']} calls for 1 unique org"
    )


def test_invariant_sql_pins_evidence_export_method_filter():
    """Source-shape pin: the UNION ALL SQL for evidence_export MUST
    filter to client_portal+partner_portal auth_methods. Without this
    filter, the invariant would falsely flag admin downloads (Carol
    carve-out #3) and legacy ?token= auditor pulls (carve-out #4,
    §164.524 access right). Pinned at the source level so a refactor
    can't widen the filter accidentally.
    """
    src = pathlib.Path(assertions.__file__).read_text()
    start = src.find("_check_sensitive_workflow_advanced_without_baa")
    assert start != -1
    body = src[start:start + 5000]  # generous window past the SQL
    assert (
        "details->>'auth_method' IN ('client_portal','partner_portal')"
        in body
    ), (
        "evidence_export UNION ALL lost its method-filter — admin or "
        "legacy ?token= auditor rows could now leak into violations. "
        "Carol carve-outs #3 + #4 require this filter."
    )


def test_new_site_onboarding_and_credential_entry_branches_fire():
    """Task #98 extensions: new_site_onboarding (scans sites) +
    new_credential_entry (scans site_credentials JOIN sites). Both
    use the state-machine bypass-row exclusion (they go through
    enforce_or_log_admin_bypass, which writes a baa_enforcement_bypass
    row on admin carve-out). A row with no BAA and no matching bypass
    → Violation."""
    conn = _make_conn(
        union_rows=[
            {"workflow": "new_site_onboarding", "org_id": "org-F",
             "site_id": "site-F", "row_id": "site-F",
             "advanced_at": _ts("2026-05-15T12:00:00")},
            {"workflow": "new_credential_entry", "org_id": "org-G",
             "site_id": "site-G", "row_id": "cred-1",
             "advanced_at": _ts("2026-05-15T13:00:00")},
        ],
        bypass_map={},  # no bypass rows
    )
    with _patch_baa_ok({"org-F": False, "org-G": False}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    workflows = sorted(v.details["workflow"] for v in violations)
    assert workflows == ["new_credential_entry", "new_site_onboarding"], (
        f"both new workflows must fire when no BAA + no bypass; got {workflows}"
    )


def test_new_site_onboarding_with_bypass_row_excluded():
    """An admin advancing new_site_onboarding for a non-BAA org writes
    a baa_enforcement_bypass row — the invariant excludes it."""
    conn = _make_conn(
        union_rows=[{
            "workflow": "new_site_onboarding",
            "org_id": "org-H",
            "site_id": "site-H",
            "row_id": "site-H",
            "advanced_at": _ts("2026-05-15T12:00:00"),
        }],
        bypass_map={("new_site_onboarding", "org-H"): 1},
    )
    with _patch_baa_ok({"org-H": False}):
        violations = _run(
            assertions._check_sensitive_workflow_advanced_without_baa(conn)
        )
    assert violations == [], (
        "matching bypass row must exclude new_site_onboarding violations"
    )


def test_invariant_sql_pins_evidence_export_action_filter():
    """Same shape pin for the action filter — the evidence_export
    branch MUST query `action='auditor_kit_download'` specifically.
    A widened filter would scan unrelated audit rows."""
    src = pathlib.Path(assertions.__file__).read_text()
    start = src.find("_check_sensitive_workflow_advanced_without_baa")
    body = src[start:start + 5000]
    assert "aal.action = 'auditor_kit_download'" in body, (
        "evidence_export UNION ALL lost its auditor_kit_download "
        "action filter — scan would broaden to all admin_audit_log."
    )
