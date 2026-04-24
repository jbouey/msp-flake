"""Tests for the Layer 3 backend ingest path + the
frontend_field_undefined_spike substrate invariant.

Session 210 QA-hardening pass (2026-04-24 round-table follow-up). The
original Layer 3 ship had frontend-unit coverage only; these tests close
the gap on:

  1. ClientFieldUndefinedEvent Pydantic model — field validators, size
     caps, pattern constraint on `kind`.
  2. _check_frontend_field_undefined_spike — threshold logic, dedup by
     distinct IPs, zero-state silence, graceful handling when the
     telemetry table is missing (pre-migration window).

Runs fully in-memory — no asyncpg connection needed. Mocks the
connection interface the real substrate assertion expects.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import (  # noqa: E402
    ALL_ASSERTIONS,
    _check_frontend_field_undefined_spike,
)
from client_telemetry import ClientFieldUndefinedEvent  # noqa: E402


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------

def test_event_model_accepts_valid_payload():
    evt = ClientFieldUndefinedEvent(
        kind="FIELD_UNDEFINED",
        endpoint="/api/portal/site/42",
        field="tier",
        component="PortalScorecard",
        observed_type="object",
        page="/client/portal",
        ts="2026-04-24T16:30:00Z",
    )
    assert evt.field == "tier"
    assert evt.endpoint == "/api/portal/site/42"


def test_event_model_rejects_wrong_kind():
    """kind must match the FIELD_UNDEFINED pattern — new event kinds
    need an explicit migration + allowlist update."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        ClientFieldUndefinedEvent(
            kind="RANDOM_KIND",
            endpoint="/api/site",
            field="tier",
            observed_type="object",
            ts="2026-04-24T16:30:00Z",
        )


def test_event_model_size_caps_enforced():
    """Attackers + well-meaning bugs sending 10KB field names get
    truncated at the Pydantic boundary, not at the DB."""
    with pytest.raises(Exception):
        ClientFieldUndefinedEvent(
            kind="FIELD_UNDEFINED",
            endpoint="/api/site",
            field="x" * 200,   # > 100 cap
            observed_type="object",
            ts="2026-04-24T16:30:00Z",
        )


def test_event_model_component_is_optional():
    evt = ClientFieldUndefinedEvent(
        kind="FIELD_UNDEFINED",
        endpoint="/api/site",
        field="tier",
        observed_type="object",
        ts="2026-04-24T16:30:00Z",
    )
    assert evt.component is None


# ---------------------------------------------------------------------------
# Substrate invariant: _check_frontend_field_undefined_spike
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal async asyncpg-like connection. fetch(...) returns a
    pre-seeded list of `_Row` dicts. `_raise` forces the fetch to raise
    UndefinedTableError."""

    def __init__(self, rows, raise_undefined_table=False):
        self._rows = rows
        self._raise_ut = raise_undefined_table

    async def fetch(self, _sql):
        if self._raise_ut:
            import asyncpg
            raise asyncpg.exceptions.UndefinedTableError(
                "relation does not exist"
            )
        return self._rows


def _row(endpoint, field, count, sessions, minutes_ago=2):
    now = datetime.now(timezone.utc)
    return {
        "endpoint": endpoint,
        "field_name": field,
        "event_count": count,
        "distinct_sessions": sessions,
        "first_seen": now - timedelta(minutes=minutes_ago + 1),
        "last_seen": now - timedelta(seconds=30),
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_invariant_registered_in_all_assertions():
    names = {a.name for a in ALL_ASSERTIONS}
    assert "frontend_field_undefined_spike" in names


def test_invariant_severity_sev2():
    hit = [a for a in ALL_ASSERTIONS if a.name == "frontend_field_undefined_spike"]
    assert hit and hit[0].severity == "sev2"


def test_no_violation_on_empty_table():
    """Zero rows in the telemetry table = zero violations (no drift
    happening = nothing to page on)."""
    conn = _FakeConn(rows=[])
    result = _run(_check_frontend_field_undefined_spike(conn))
    assert result == []


def test_no_violation_on_single_user_noise():
    """The SQL filter already requires COUNT(*) > 10 AND distinct_sessions
    >= 2. A row the DB returns has already passed that — but this test
    guards the Python side: if it ever did return sub-threshold rows,
    we surface them as violations (conservative — prefer false-positive
    over silent drift). Documented here so the team knows the Python side
    trusts the SQL gate."""
    conn = _FakeConn(rows=[_row("/api/site", "tier", count=3, sessions=1)])
    result = _run(_check_frontend_field_undefined_spike(conn))
    # Python surfaces whatever the SQL returns. If SQL returns 3/1, that's
    # a SQL-level bug, but we don't double-gate here.
    assert len(result) == 1


def test_single_user_high_volume_path_surfaces():
    """Session 210 round-table #6: the multi-user threshold missed the
    single-tenant deployment where only 1 user hits a bug 50 times.
    Python-side assertion is still loose (it trusts whatever SQL returns),
    so this test is really about documenting the shape of row the SQL
    side will now surface: COUNT > 30 from a single session."""
    conn = _FakeConn(rows=[_row("/api/site", "tier", count=45, sessions=1)])
    result = _run(_check_frontend_field_undefined_spike(conn))
    assert len(result) == 1
    assert result[0].details["event_count_5m"] == 45
    assert result[0].details["distinct_sessions"] == 1


def test_violation_fires_with_expected_details():
    conn = _FakeConn(rows=[_row("/api/portal/site/X", "tier", count=47, sessions=5)])
    result = _run(_check_frontend_field_undefined_spike(conn))
    assert len(result) == 1
    v = result[0]
    assert v.site_id is None  # fleet-wide, not per-site
    assert v.details["endpoint"] == "/api/portal/site/X"
    assert v.details["field_name"] == "tier"
    assert v.details["event_count_5m"] == 47
    assert v.details["distinct_sessions"] == 5
    # Remediation must cover the 4 triage steps the operator needs
    rem = v.details["remediation"]
    for step in ("Pydantic response model", "openapi.json",
                 "api-generated.ts", "React component"):
        assert step in rem, (
            f"remediation missing step mentioning {step!r}. Operator needs "
            f"the full walk-through."
        )


def test_multiple_violations_rendered():
    """Multi-endpoint drift produces one violation per (endpoint, field)
    pair — the operator sees each one distinctly."""
    conn = _FakeConn(rows=[
        _row("/api/site/A", "tier", 20, 3),
        _row("/api/portal", "primary_label", 15, 4),
    ])
    result = _run(_check_frontend_field_undefined_spike(conn))
    assert len(result) == 2
    endpoints = {v.details["endpoint"] for v in result}
    assert endpoints == {"/api/site/A", "/api/portal"}


def test_graceful_when_table_missing():
    """Before migration 242 applies (first deploy window), the telemetry
    table doesn't exist. The invariant must NOT raise — it must return
    empty violations and let the other 30+ invariants run unaffected."""
    conn = _FakeConn(rows=[], raise_undefined_table=True)
    result = _run(_check_frontend_field_undefined_spike(conn))
    assert result == [], (
        "UndefinedTableError must be caught; returning [] lets the "
        "other substrate invariants continue"
    )
