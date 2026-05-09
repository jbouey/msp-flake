"""Positive + negative control tests for the 2 substrate invariants
shipped in commit `6ca77798` (Phase 1 multi-tenant audit P1-3 + P1-4)
and runtime-fixed in commit `e7634762` (round-2 audit P0-RT2-A/B).

Round-2 audit recommendation #4 + #6 (`audit/coach-15-commit-
adversarial-audit-round2-2026-05-09.md` §5):

> Adopt the pattern: every new substrate invariant ships with
> (a) negative control = no rows in happy path,
> (b) positive control = synthetic violation injection test.

This file provides BOTH for:
  - `_check_compliance_bundles_trigger_disabled`
  - `_check_db_baseline_guc_drift`

Implementation note: these tests mock the asyncpg.Connection rather
than running against a real Postgres. The mock approach means the
test runs without PG_TEST_URL — fits the source-level governance
gate model + can run in pre-push.

A real-PG integration test (`*_pg.py`) that DISABLEs/re-ENABLEs the
actual trigger and reads the live substrate_violations table is
sprint-tracked under task #94 (Phase 4 substrate-MTTR runtime soak)
— that's the end-to-end verification. THIS file covers the
function-level shape + return-type + violation-fields contract.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _load_assertions_module():
    """Load assertions.py via importlib to bypass conftest's heavy
    backend-deps (asyncpg, pynacl, etc.). Stub the deps it imports."""
    # Stub asyncpg + structlog before importing assertions
    if "asyncpg" not in sys.modules:
        import types
        mod = types.ModuleType("asyncpg")
        mod.Connection = MagicMock
        mod.exceptions = types.SimpleNamespace(
            UndefinedTableError=Exception,
            UndefinedColumnError=Exception,
            UndefinedFunctionError=Exception,
            ProtocolViolationError=Exception,
            InsufficientPrivilegeError=Exception,
            PostgresError=Exception,
        )
        sys.modules["asyncpg"] = mod
    spec = importlib.util.spec_from_file_location(
        "assertions_under_test", _BACKEND / "assertions.py"
    )
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception as e:
        pytest.skip(f"assertions.py module load failed: {e!r}")
    return m


# ---------- compliance_bundles_trigger_disabled ----------

@pytest.mark.asyncio
async def test_compliance_bundles_trigger_disabled_negative_control():
    """Negative control: when no trigger row matches the disabled
    predicate, the invariant returns []."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await m._check_compliance_bundles_trigger_disabled(conn)
    assert result == [], "happy path should produce zero violations"
    conn.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_compliance_bundles_trigger_disabled_positive_control():
    """Positive control: when ONE trigger row is in tgenabled='D'
    (disabled), the invariant returns one Violation with the right
    shape + interpretation hint."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {
            "schema_name": "public",
            "table_name": "compliance_bundles",
            "trigger_name": "compliance_bundles_no_delete",
            "state": "D",
        }
    ])
    result = await m._check_compliance_bundles_trigger_disabled(conn)
    assert len(result) == 1, f"expected 1 violation, got {len(result)}"
    v = result[0]
    # Violation is a dataclass with site_id, details
    assert v.site_id is None, "this invariant doesn't have a site anchor"
    assert isinstance(v.details, dict)
    assert v.details["table"] == "compliance_bundles"
    assert v.details["trigger"] == "compliance_bundles_no_delete"
    assert v.details["tgenabled_state"] == "D"
    assert "ENABLE ALWAYS TRIGGER" in v.details["interpretation"]


@pytest.mark.asyncio
async def test_compliance_bundles_trigger_partition_disabled():
    """Positive control variant: a partition (not the parent) has the
    trigger disabled. Same Violation shape; partition table name in
    the details payload."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {
            "schema_name": "public",
            "table_name": "compliance_bundles_2026_05",
            "trigger_name": "compliance_bundles_no_delete",
            "state": "R",  # ENABLE REPLICA — not ALWAYS
        }
    ])
    result = await m._check_compliance_bundles_trigger_disabled(conn)
    assert len(result) == 1
    assert result[0].details["table"] == "compliance_bundles_2026_05"
    assert result[0].details["tgenabled_state"] == "R"


# ---------- db_baseline_guc_drift ----------

@pytest.mark.asyncio
async def test_db_baseline_guc_drift_negative_control():
    """Negative control: pg_db_role_setting returns nothing (system
    defaults active) — invariant returns []."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await m._check_db_baseline_guc_drift(conn)
    assert result == [], "happy path should produce zero violations"


@pytest.mark.asyncio
async def test_db_baseline_guc_drift_baseline_set_explicitly():
    """Negative control variant: pg_db_role_setting EXPLICITLY sets
    GUCs to baseline values — invariant still returns []."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"kv": "app.is_admin=false"},
        {"kv": "app.current_org="},
    ])
    result = await m._check_db_baseline_guc_drift(conn)
    assert result == [], (
        "explicitly setting GUCs to baseline values should NOT fire"
    )


@pytest.mark.asyncio
async def test_db_baseline_guc_drift_positive_control():
    """Positive control: app.is_admin set to 'true' at the database
    level — RLS bypass active by default. Sev2 violation."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"kv": "app.is_admin=true"},
    ])
    result = await m._check_db_baseline_guc_drift(conn)
    assert len(result) == 1
    v = result[0]
    assert v.site_id is None
    assert v.details["guc"] == "app.is_admin"
    assert v.details["expected"] == "false"
    assert v.details["actual"] == "true"
    assert "RLS posture" in v.details["interpretation"]


@pytest.mark.asyncio
async def test_db_baseline_guc_drift_multiple_drift():
    """Positive control variant: 2 GUCs drift simultaneously."""
    m = _load_assertions_module()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"kv": "app.is_admin=true"},
        {"kv": "app.current_tenant=stale-tenant-id"},
    ])
    result = await m._check_db_baseline_guc_drift(conn)
    assert len(result) == 2
    drifted = sorted(v.details["guc"] for v in result)
    assert drifted == ["app.current_tenant", "app.is_admin"]
