"""Unit tests for phantom_detector_healthy and heartbeat_write_divergence
substrate invariants (Session 209).

Both check functions are exercised with stub connections + in-memory
heartbeat-registry state. A real asyncpg connection is heavy; we only
need conn.fetch / .fetchrow here, so a tiny duck-typed stub does the job.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

import pytest

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
for p in (backend_dir, mcp_server_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_assertions():
    try:
        from dashboard_api import assertions as _a
    except Exception:
        import assertions as _a  # type: ignore
    return _a


def _load_bg_heartbeat():
    try:
        from dashboard_api import bg_heartbeat as _b
    except Exception:
        import bg_heartbeat as _b  # type: ignore
    return _b


class StubConn:
    """Duck-typed stand-in for asyncpg.Connection. Rows are plain dicts
    with __getitem__ — asyncpg Records support both index + keyword."""

    def __init__(self, fetch_rows=None, fetchrow_row=None):
        self._fetch_rows = fetch_rows or []
        self._fetchrow_row = fetchrow_row

    async def fetch(self, sql, *args):
        return self._fetch_rows

    async def fetchrow(self, sql, *args):
        return self._fetchrow_row


# ── phantom_detector_healthy ────────────────────────────────────────────

def test_phantom_detector_no_heartbeat_yet_is_quiet():
    """Process just started, detector hasn't registered yet. Don't
    alert — give it a cycle."""
    a = _load_assertions()
    bhb = _load_bg_heartbeat()
    bhb._heartbeats.clear()

    result = asyncio.run(a._check_phantom_detector_healthy(StubConn()))
    assert result == []


def test_phantom_detector_fresh_heartbeat_is_quiet():
    a = _load_assertions()
    bhb = _load_bg_heartbeat()
    bhb._heartbeats.clear()

    bhb.record_heartbeat("phantom_detector")
    result = asyncio.run(a._check_phantom_detector_healthy(StubConn()))
    assert result == []


def test_phantom_detector_stale_heartbeat_fires_violation():
    """Backdate the heartbeat past the 15-min threshold — must fire."""
    a = _load_assertions()
    bhb = _load_bg_heartbeat()
    bhb._heartbeats.clear()

    bhb.record_heartbeat("phantom_detector")
    bhb._heartbeats["phantom_detector"]["last_seen"] = time.time() - 1000

    result = asyncio.run(a._check_phantom_detector_healthy(StubConn()))
    assert len(result) == 1
    v = result[0]
    assert v.site_id is None  # process-wide, not site-scoped
    assert v.details["loop"] == "phantom_detector"
    assert v.details["age_s"] > 900
    assert "interpretation" in v.details


def test_phantom_detector_border_not_fires():
    """At 14 min stale, we are still under the 3x expected window
    (900s). Must not fire. Prevents one-cycle-delayed tick false
    positives."""
    a = _load_assertions()
    bhb = _load_bg_heartbeat()
    bhb._heartbeats.clear()

    bhb.record_heartbeat("phantom_detector")
    bhb._heartbeats["phantom_detector"]["last_seen"] = time.time() - 840  # 14 min

    result = asyncio.run(a._check_phantom_detector_healthy(StubConn()))
    assert result == []


# ── heartbeat_write_divergence ──────────────────────────────────────────

def test_heartbeat_divergence_no_rows_is_quiet():
    a = _load_assertions()
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=[])))
    assert result == []


def test_heartbeat_divergence_healthy_pair_is_quiet():
    """last_checkin + last_heartbeat both recent, < 10 min apart."""
    a = _load_assertions()
    now = datetime.now(timezone.utc)
    rows = [{
        "site_id": "site-a",
        "appliance_id": "a1",
        "hostname": "osiriscare-1",
        "last_checkin": now,
        "last_heartbeat": now - timedelta(seconds=30),
    }]
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=rows)))
    assert result == []


def test_heartbeat_divergence_null_heartbeat_fires():
    """A fresh-checked-in appliance with ZERO heartbeats — the
    heartbeat INSERT is failing. Must fire."""
    a = _load_assertions()
    now = datetime.now(timezone.utc)
    rows = [{
        "site_id": "site-a",
        "appliance_id": "a1",
        "hostname": "osiriscare-1",
        "last_checkin": now,
        "last_heartbeat": None,
    }]
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=rows)))
    assert len(result) == 1
    v = result[0]
    assert v.site_id == "site-a"
    assert v.details["appliance_id"] == "a1"
    assert v.details["last_heartbeat"] is None
    assert v.details["lag_s"] is None


def test_heartbeat_divergence_15min_lag_fires():
    """last_checkin 15 min ahead of last_heartbeat — heartbeat writes
    stopped landing at some point in the last 15 min."""
    a = _load_assertions()
    now = datetime.now(timezone.utc)
    rows = [{
        "site_id": "site-a",
        "appliance_id": "a1",
        "hostname": "osiriscare-1",
        "last_checkin": now,
        "last_heartbeat": now - timedelta(minutes=15),
    }]
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=rows)))
    assert len(result) == 1
    assert result[0].details["lag_s"] > 600


def test_heartbeat_divergence_9min_lag_is_quiet():
    """At 9 min lag we are still under the 10-min threshold. Not
    firing prevents one-cycle-delayed false positives."""
    a = _load_assertions()
    now = datetime.now(timezone.utc)
    rows = [{
        "site_id": "site-a",
        "appliance_id": "a1",
        "hostname": "osiriscare-1",
        "last_checkin": now,
        "last_heartbeat": now - timedelta(minutes=9),
    }]
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=rows)))
    assert result == []


def test_heartbeat_divergence_multiple_appliances():
    """Only the bad rows fire, good ones stay quiet — verifies per-row
    filter logic."""
    a = _load_assertions()
    now = datetime.now(timezone.utc)
    rows = [
        {
            "site_id": "site-a",
            "appliance_id": "good",
            "hostname": "h1",
            "last_checkin": now,
            "last_heartbeat": now - timedelta(seconds=10),
        },
        {
            "site_id": "site-b",
            "appliance_id": "bad",
            "hostname": "h2",
            "last_checkin": now,
            "last_heartbeat": None,
        },
    ]
    result = asyncio.run(a._check_heartbeat_write_divergence(StubConn(fetch_rows=rows)))
    assert len(result) == 1
    assert result[0].details["appliance_id"] == "bad"


# ── journal_upload_never_received ───────────────────────────────────────

def test_journal_never_received_empty_is_quiet():
    """No appliances that fit the criteria. Must not fire."""
    a = _load_assertions()
    result = asyncio.run(
        a._check_journal_upload_never_received(StubConn(fetch_rows=[]))
    )
    assert result == []


def test_journal_never_received_fires_per_row():
    """Appliance alive >24h, zero uploads → one violation per row.
    The SQL handles the filtering; the check function just maps rows
    to Violation objects. Verify the mapping is correct."""
    a = _load_assertions()
    rows = [
        {
            "site_id": "site-a",
            "appliance_id": "a1",
            "hostname": "osiriscare-1",
            "agent_version": "0.4.4",
            "first_checkin": datetime.now(timezone.utc) - timedelta(days=3),
            "alive_hours": 72.5,
        },
        {
            "site_id": "site-b",
            "appliance_id": "b1",
            "hostname": "osiriscare-2",
            "agent_version": "0.4.5",
            "first_checkin": datetime.now(timezone.utc) - timedelta(days=10),
            "alive_hours": 240.0,
        },
    ]
    result = asyncio.run(
        a._check_journal_upload_never_received(StubConn(fetch_rows=rows))
    )
    assert len(result) == 2
    assert {v.site_id for v in result} == {"site-a", "site-b"}
    for v in result:
        assert "hostname" in v.details
        assert "agent_version" in v.details
        assert "alive_hours" in v.details
        assert v.details["alive_hours"] > 0
        assert "remediation" in v.details


def test_journal_never_received_carries_version_for_triage():
    """The violation MUST include agent_version — without it, an
    operator can't tell whether re-imaging is the right action (old
    agent = feature missing, new agent = real bug)."""
    a = _load_assertions()
    rows = [{
        "site_id": "site-a",
        "appliance_id": "a1",
        "hostname": "h1",
        "agent_version": "0.4.4",
        "first_checkin": datetime.now(timezone.utc) - timedelta(days=3),
        "alive_hours": 72.0,
    }]
    result = asyncio.run(
        a._check_journal_upload_never_received(StubConn(fetch_rows=rows))
    )
    assert result[0].details["agent_version"] == "0.4.4"


# ── heartbeat_partition_maintainer URL plumbing ─────────────────────────

def test_partition_maintainer_uses_migration_url_when_set(monkeypatch):
    """The maintainer MUST use MIGRATION_DATABASE_URL (superuser) for
    DDL. Using the app-pool DATABASE_URL (mcp_app) fails with
    InsufficientPrivilegeError — the exact bug this fix addresses."""
    try:
        from dashboard_api import background_tasks as bt
    except Exception:
        import background_tasks as bt  # type: ignore

    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql://mcp:pw@mcp-postgres:5432/mcp",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://mcp_app:pw@pgbouncer:6432/mcp",
    )
    got = bt._migration_db_url()
    assert got == "postgresql://mcp:pw@mcp-postgres:5432/mcp", (
        "DDL must go direct to postgres as mcp superuser, not through "
        "PgBouncer as mcp_app"
    )


def test_partition_maintainer_falls_back_to_database_url(monkeypatch):
    """Dev environments without the extra env var should still boot —
    the permission error will fire loudly at runtime, not at startup."""
    try:
        from dashboard_api import background_tasks as bt
    except Exception:
        import background_tasks as bt  # type: ignore

    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://mcp:pw@localhost:5432/mcp"
    )
    got = bt._migration_db_url()
    assert got.startswith("postgresql://")


def test_partition_maintainer_strips_asyncpg_dialect(monkeypatch):
    """SQLAlchemy-dialect URLs must be rewritten before asyncpg.connect
    — otherwise asyncpg raises UnknownProtocolError."""
    try:
        from dashboard_api import background_tasks as bt
    except Exception:
        import background_tasks as bt  # type: ignore

    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://mcp:pw@localhost:5432/mcp",
    )
    got = bt._migration_db_url()
    assert got == "postgresql://mcp:pw@localhost:5432/mcp"
    assert "+asyncpg" not in got
