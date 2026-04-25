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
    """Default row shape — Session 210-B refined the gate:
    `failed_l1_steps_48h >= 3` is the only signal that L2 stalled
    (L1 had failures that should have escalated). 85 successful L1
    steps + zero L2 decisions = healthy (L1 covers everything).
    Tests pass overrides via kwargs."""
    return {
        "decisions_48h": 0,
        "latest_decision_at": None,
        "online": 3,
        "failed_l1_steps_48h": 5,  # pipeline IS broken (default for fire-tests)
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


def test_no_violation_when_l1_covers_everything():
    """Session 210-B: when every L1 step in 48h succeeded
    (failed_l1_steps_48h == 0), L2 staying silent is correct — L1
    covered every incident type the fleet saw. The previous version
    fired here for 12 days at NVB2 with 85 successful L1 steps + 0
    L2 decisions, which is the healthy steady state of an L1-saturated
    fleet, not a stall."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        failed_l1_steps_48h=0,  # L1 100% successful — nothing for L2 to do
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == [], (
        "L1-saturated state (0 failures) must not fire l2_decisions_stalled — "
        "the previous version mis-fired for 12 days at NVB2 because the "
        "pipeline_signal gate counted L1 successes as evidence of an L2 stall"
    )


def test_no_violation_when_l1_failures_below_threshold():
    """≤2 failed L1 steps is below the noise floor — could be a single
    flaky runbook, not a system-wide L2 stall."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        failed_l1_steps_48h=2,
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert result == []


# ---------------------------------------------------------------------------
# Firing condition
# ---------------------------------------------------------------------------

def test_fires_when_l1_failures_pile_up_with_zero_l2_decisions():
    """The one scenario this invariant exists for: L1 failed ≥3 times
    in 48h (each natural escalation point to L2), yet l2_decisions is
    empty. That means L2 is silently broken — the cost-gate stack
    walk-through tells the operator how to debug."""
    conn = _FakeConn(_row(
        decisions_48h=0,
        latest_decision_at=None,
        online=3,
        failed_l1_steps_48h=12,  # L1 had 12 failures → L2 should have run on each
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert len(result) == 1, f"expected 1 violation, got {len(result)}: {result}"
    v = result[0]
    assert v.site_id is None, "this is a fleet-wide invariant, not per-site"
    assert v.details["l2_decisions_48h"] == 0
    assert v.details["online_appliances_1h"] == 3
    assert v.details["failed_l1_steps_48h"] == 12
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


def test_fires_when_decisions_under_threshold():
    """3 decisions in 48h → still too few → fires when L1 had failures."""
    conn = _FakeConn(_row(
        decisions_48h=3,
        latest_decision_at=datetime.now(timezone.utc) - timedelta(hours=20),
        online=2,
        failed_l1_steps_48h=8,
    ))
    with patch.dict(os.environ, {"L2_ENABLED": "true"}, clear=False):
        result = _run(_check_l2_decisions_stalled(conn))
    assert len(result) == 1
    assert result[0].details["l2_decisions_48h"] == 3
