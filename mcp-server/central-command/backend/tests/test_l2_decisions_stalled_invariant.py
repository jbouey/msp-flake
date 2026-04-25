"""Unit tests for the l2_decisions_stalled substrate invariant.

Background: 2026-04-12 the L2_ENABLED kill switch was flipped to false
after Session 205 found the L2 LLM pipeline had produced 0 promoted rules
over 14 days while consuming ~$X/day in API spend. 2026-04-24 (Session 210)
L2 was re-enabled, but only with this invariant as a tripwire so the next
silent death pages inside 48h.

This file verifies the invariant's behavior in isolation without touching
a live Postgres. It exercises the pure-Python env check and a fake
connection's fetchrow response.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import _check_l2_decisions_stalled, ALL_ASSERTIONS  # noqa: E402


class _FakeConn:
    """Minimal asyncpg-like connection that returns a scripted fetchrow."""

    def __init__(self, row):
        self._row = row

    async def fetchrow(self, _sql):
        return self._row


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Invariant is registered
# ---------------------------------------------------------------------------

def test_l2_decisions_stalled_registered_in_all_assertions():
    names = {a.name for a in ALL_ASSERTIONS}
    assert "l2_decisions_stalled" in names, (
        "l2_decisions_stalled missing from ALL_ASSERTIONS — "
        "re-add the Assertion(...) entry in assertions.py after appliance_disk_pressure"
    )


def test_l2_decisions_stalled_severity_sev2():
    hit = [a for a in ALL_ASSERTIONS if a.name == "l2_decisions_stalled"]
    assert hit, "invariant not registered"
    assert hit[0].severity == "sev2", (
        "l2_decisions_stalled should be sev2 — a silent planner pipe "
        "is operationally bad but not a P0 like evidence_chain_stalled"
    )


# ---------------------------------------------------------------------------
# Env gating
# ---------------------------------------------------------------------------

def _row(**kwargs):
    """Default row shape — Session 210-B added remediation_steps_48h +
    silent_count to the query so the assertion can distinguish
    quiet-but-healthy from L2-actually-broken. Tests pass overrides
    via kwargs."""
    return {
        "decisions_48h": 0,
        "latest_decision_at": None,
        "online": 3,
        "remediation_steps_48h": 10,  # pipeline IS active (default)
        "silent_count": 0,
        **kwargs,
    }


def test_no_violation_when_l2_disabled():
    """When L2_ENABLED=false, the invariant MUST stay silent no matter
    what the DB says — operators should be able to disable L2 without
    being paged."""
    conn = _FakeConn(_row())
    with patch.dict(os.environ, {"L2_ENABLED": "false"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == [], (
        f"L2_ENABLED=false must silence the invariant; got violations: {result}"
    )


def test_no_violation_when_l2_env_unset():
    """Default env (L2_ENABLED unset) reads as false → silent."""
    conn = _FakeConn(_row())
    env_stripped = {k: v for k, v in os.environ.items() if k != "L2_ENABLED"}
    with patch.dict(os.environ, env_stripped, clear=True):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == []


# ---------------------------------------------------------------------------
# Fleet state gating
# ---------------------------------------------------------------------------

def test_no_violation_when_fleet_offline():
    """Zero-fleet (all appliances offline) is handled by other invariants;
    this one should stay quiet so operators aren't double-paged."""
    conn = _FakeConn(_row(online=0))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_no_violation_when_decisions_above_threshold():
    """Healthy state: ≥5 L2 decisions in 48h, fleet online → silent."""
    conn = _FakeConn(_row(
        decisions_48h=12,
        latest_decision_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == []


def test_no_violation_when_quiet_but_healthy():
    """Session 210-B: when L1 covers every incident type the fleet is
    seeing, L2 produces zero decisions but that is NOT a stall — it's
    L1 successfully handling the load. Both pipeline_signal gates
    (remediation_steps and silent_escalations) must be below threshold
    for the invariant to stay quiet here."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        remediation_steps_48h=2,  # L1 firing a tiny bit but not enough to expect L2
        silent_count=0,           # no incidents going un-touched
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == [], (
        "Quiet-but-healthy state (low L1 activity, zero silent escalations) "
        "must not fire l2_decisions_stalled — the previous version mis-fired "
        "for 12 days at NVB2 because L1 covered every incident type"
    )


# ---------------------------------------------------------------------------
# Firing condition
# ---------------------------------------------------------------------------

def test_fires_when_l2_on_fleet_active_but_zero_decisions():
    """The one scenario this invariant exists for: operator flipped
    L2_ENABLED=true 48h+ ago, appliances are actively checking in, the
    healing pipeline IS firing (remediation_steps_48h ≥ 5), yet
    l2_decisions is empty. Fires with a detailed remediation."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        latest_decision_at=None,
        online=3,
        remediation_steps_48h=12,  # L1 IS firing → L2 should occasionally fire too
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert len(result) == 1, f"expected 1 violation, got {len(result)}: {result}"
    v = result[0]
    assert v.site_id is None, "this is a fleet-wide invariant, not per-site"
    assert v.details["l2_decisions_48h"] == 0
    assert v.details["online_appliances_1h"] == 3
    assert v.details["remediation_steps_48h"] == 12
    assert v.details["silent_escalations_48h"] == 0
    assert "remediation" in v.details
    # The remediation must walk the operator through the cost-gate stack
    # — at minimum API key + circuit breaker + daily cap + zero-result + kill switch.
    rem = v.details["remediation"]
    for beat in ("API key", "circuit breaker", "MAX_DAILY_L2_CALLS",
                 "zero-result", "L2_ENABLED"):
        assert beat in rem or beat.lower() in rem.lower(), (
            f"remediation missing step mentioning {beat!r}; "
            f"operator needs the full walk-through:\n{rem}"
        )


def test_fires_when_silent_escalations_pile_up():
    """Alternative pipeline_signal gate: even if L1 isn't firing,
    ≥3 unresolved incidents older than 30min with NO remediation
    steps means something is silently escalating past the healing
    pipeline entirely — also a real stall worth paging on."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        remediation_steps_48h=0,  # L1 also silent
        silent_count=5,           # but 5 incidents got nothing
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert len(result) == 1
    assert result[0].details["silent_escalations_48h"] == 5


def test_fires_when_decisions_under_threshold():
    """3 decisions in 48h → still too few → fires when pipeline is active."""
    conn = _FakeConn(_row(
        decisions_48h=3,
        latest_decision_at=datetime.now(timezone.utc) - timedelta(hours=20),
        online=2,
        remediation_steps_48h=8,
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert len(result) == 1
    assert result[0].details["l2_decisions_48h"] == 3
