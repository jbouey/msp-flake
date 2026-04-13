"""Flywheel Spine — end-to-end state-machine test (Session 206 redesign).

This is the test that would have caught today's silent auto-disable
bug in 30 seconds of CI time instead of a 2-hour audit.

The spine guarantees ONE thing: every state transition writes one
ledger row atomically, via advance_lifecycle(). The test suite proves:

  1. advance_lifecycle validates transitions (illegal transitions raise)
  2. advance_lifecycle is the ONLY way to change lifecycle_state (direct
     UPDATEs are blocked by trigger — tamper-evident)
  3. The orchestrator's RegimeAbsoluteLowTransition CORRECTLY disables
     a rule when absolute_low regime event is present (today's bug)
  4. The orchestrator's RolloutAckedTransition moves rolling_out→active
  5. The orchestrator's ZombieSiteTransition retires rules on dead sites
  6. One failing transition doesn't block the rest of the tick
  7. Idempotent: running the tick twice produces the same state

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres spine test",
)

MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS promoted_rule_events CASCADE;
DROP TABLE IF EXISTS promoted_rule_lifecycle_transitions CASCADE;
DROP TABLE IF EXISTS fleet_order_completions CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS l1_rule_regime_events CASCADE;
DROP TABLE IF EXISTS l1_rules CASCADE;
DROP TABLE IF EXISTS promoted_rules CASCADE;
DROP TABLE IF EXISTS site_appliances CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP FUNCTION IF EXISTS advance_lifecycle(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT, TEXT) CASCADE;
DROP FUNCTION IF EXISTS enforce_lifecycle_via_advance() CASCADE;
DROP FUNCTION IF EXISTS prule_events_append_only_guard() CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sites (site_id TEXT PRIMARY KEY, status TEXT DEFAULT 'active');

CREATE TABLE site_appliances (
    appliance_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    last_checkin TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE promoted_rules (
    rule_id TEXT PRIMARY KEY,
    site_id TEXT,
    status TEXT DEFAULT 'active',
    rule_yaml TEXT,
    deployment_count INTEGER DEFAULT 0,
    last_deployed_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE l1_rules (
    rule_id TEXT PRIMARY KEY,
    runbook_id TEXT,
    incident_pattern JSONB DEFAULT '{}'::jsonb,
    enabled BOOLEAN DEFAULT true,
    promoted_from_l2 BOOLEAN DEFAULT false,
    match_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE l1_rule_regime_events (
    id BIGSERIAL PRIMARY KEY,
    rule_id TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    window_7d_rate NUMERIC(4,3) DEFAULT 0,
    baseline_30d_rate NUMERIC(4,3) DEFAULT 0,
    delta NUMERIC(5,3) DEFAULT 0,
    sample_size_7d INTEGER DEFAULT 0,
    sample_size_30d INTEGER DEFAULT 0,
    severity TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    resolution TEXT
);

CREATE TABLE fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type TEXT NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE fleet_order_completions (
    fleet_order_id UUID REFERENCES fleet_orders(id) ON DELETE CASCADE,
    appliance_id TEXT NOT NULL,
    status TEXT DEFAULT 'completed',
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fleet_order_id, appliance_id)
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        # Apply migration 181 — the spine
        mig = (MIGRATIONS_DIR / "181_flywheel_spine.sql").read_text()
        await c.execute(mig)
        yield c
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS promoted_rule_events CASCADE;
            DROP TABLE IF EXISTS promoted_rule_lifecycle_transitions CASCADE;
            DROP TABLE IF EXISTS fleet_order_completions CASCADE;
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS l1_rule_regime_events CASCADE;
            DROP TABLE IF EXISTS l1_rules CASCADE;
            DROP TABLE IF EXISTS promoted_rules CASCADE;
            DROP TABLE IF EXISTS site_appliances CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
            DROP FUNCTION IF EXISTS advance_lifecycle CASCADE;
            DROP FUNCTION IF EXISTS enforce_lifecycle_via_advance CASCADE;
            DROP FUNCTION IF EXISTS prule_events_append_only_guard CASCADE;
        """)
        await c.close()


# ─── advance_lifecycle invariants ─────────────────────────────────


@pytest.mark.asyncio
async def test_advance_validates_illegal_transition(conn):
    """proposed → auto_disabled is NOT a valid transition; must raise."""
    from flywheel_state import advance
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id) VALUES ('rule-1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, promoted_from_l2) VALUES ('rule-1', true)"
    )
    # Rule starts in lifecycle_state='proposed' (via migration UPDATE)
    # Wait — we need to flip it to 'approved' first via migration's inference.
    # Migration sets it based on l1_rules existence. Let's check:
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='rule-1'"
    )
    # Migration only runs UPDATE on rows that existed at migration time.
    # For rows INSERTed after, default is 'proposed'.
    assert state == 'proposed'

    with pytest.raises(Exception, match=r"illegal transition"):
        await advance(
            conn, rule_id="rule-1", new_state="auto_disabled",
            event_type="auto_disabled", actor="system:test",
            stage="regime",
        )


@pytest.mark.asyncio
async def test_advance_writes_ledger_and_updates_state(conn):
    """Valid transition: writes exactly one ledger row + updates
    lifecycle_state atomically."""
    from flywheel_state import advance
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('rule-2')")
    await advance(
        conn, rule_id="rule-2", new_state="approved",
        event_type="promotion_approved", actor="system:test",
        stage="promotion", reason="test",
    )
    # State updated
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='rule-2'"
    )
    assert state == "approved"
    # Event written
    events = await conn.fetch(
        "SELECT event_type, actor, reason FROM promoted_rule_events "
        "WHERE rule_id='rule-2'"
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "promotion_approved"
    assert events[0]["reason"] == "test"


@pytest.mark.asyncio
async def test_direct_update_of_lifecycle_state_is_blocked(conn):
    """Tamper-evident: bare UPDATE of lifecycle_state fails."""
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('rule-3')")
    with pytest.raises(Exception, match=r"Direct UPDATE"):
        await conn.execute(
            "UPDATE promoted_rules SET lifecycle_state='active' "
            "WHERE rule_id='rule-3'"
        )


@pytest.mark.asyncio
async def test_ledger_is_append_only(conn):
    """DELETE + UPDATE on promoted_rule_events raise."""
    from flywheel_state import advance
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('rule-4')")
    await advance(
        conn, rule_id="rule-4", new_state="approved",
        event_type="promotion_approved", actor="system:test",
        stage="promotion",
    )
    with pytest.raises(Exception, match=r"append-only"):
        await conn.execute(
            "DELETE FROM promoted_rule_events WHERE rule_id='rule-4'"
        )
    with pytest.raises(Exception, match=r"append-only"):
        await conn.execute(
            "UPDATE promoted_rule_events SET outcome='failed' "
            "WHERE rule_id='rule-4'"
        )


# ─── RegimeAbsoluteLowTransition — THE bug today's audit caught ────


@pytest.mark.asyncio
async def test_regime_absolute_low_auto_disables(conn):
    """This is the test that would have caught today's prod bug.
    Seed: active rule + unack'd absolute_low regime event.
    Expected: RegimeAbsoluteLowTransition fires, rule auto_disabled."""
    from flywheel_state import advance, RegimeAbsoluteLowTransition

    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-abs', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled) "
        "VALUES ('r-abs', 'RB-X', true, true)"
    )
    # Walk the rule to 'active' via the real state machine
    await advance(conn, rule_id="r-abs", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-abs", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-abs", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")

    # Seed the regime event (the SCREEN_LOCK scenario)
    await conn.execute(
        "INSERT INTO l1_rule_regime_events "
        "(rule_id, severity, sample_size_7d, window_7d_rate, baseline_30d_rate) "
        "VALUES ('r-abs', 'absolute_low', 83, 0.000, 0.000)"
    )

    # Run the transition
    transition = RegimeAbsoluteLowTransition()
    candidates = await transition.find_candidates(conn)
    assert len(candidates) == 1
    assert candidates[0]["rule_id"] == "r-abs"

    result = await transition.apply(conn, candidates[0])
    assert result.success
    assert result.to_state == "auto_disabled"
    assert result.event_type == "regime_absolute_low"

    # Verify side effects
    rule = await conn.fetchrow(
        "SELECT lifecycle_state, operator_ack_required FROM promoted_rules "
        "WHERE rule_id='r-abs'"
    )
    assert rule["lifecycle_state"] == "auto_disabled"
    assert rule["operator_ack_required"] is True

    l1 = await conn.fetchrow(
        "SELECT enabled FROM l1_rules WHERE rule_id='r-abs'"
    )
    assert l1["enabled"] is False

    # Event logged with proof
    event = await conn.fetchrow(
        "SELECT event_type, proof, reason FROM promoted_rule_events "
        "WHERE rule_id='r-abs' AND event_type='regime_absolute_low'"
    )
    assert event is not None
    proof = event["proof"]
    if isinstance(proof, str):
        proof = json.loads(proof)
    assert proof["severity"] == "absolute_low"
    assert proof["sample_size_7d"] == 83


@pytest.mark.asyncio
async def test_regime_does_not_fire_on_acknowledged_event(conn):
    """Operator can acknowledge a regime event to suppress auto-disable."""
    from flywheel_state import advance, RegimeAbsoluteLowTransition

    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-ack', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled) "
        "VALUES ('r-ack', 'RB-Y', true, true)"
    )
    await advance(conn, rule_id="r-ack", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-ack", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-ack", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events "
        "(rule_id, severity, sample_size_7d, window_7d_rate, baseline_30d_rate, "
        " acknowledged_at) "
        "VALUES ('r-ack', 'absolute_low', 50, 0.1, 0.1, NOW())"
    )
    transition = RegimeAbsoluteLowTransition()
    candidates = await transition.find_candidates(conn)
    assert len(candidates) == 0


# ─── RolloutAckedTransition ───────────────────────────────────────


@pytest.mark.asyncio
async def test_rollout_acked_moves_rolling_out_to_active(conn):
    from flywheel_state import advance, RolloutAckedTransition

    await conn.execute("INSERT INTO sites (site_id) VALUES ('s-roll')")
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-roll', 's-roll')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2) "
        "VALUES ('r-roll', 'RB-R', true)"
    )
    await advance(conn, rule_id="r-roll", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-roll", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    # Simulate an order + ack
    oid = await conn.fetchval(
        "INSERT INTO fleet_orders (order_type, parameters) "
        "VALUES ('sync_promoted_rule', '{\"rule_id\":\"r-roll\"}'::jsonb) "
        "RETURNING id"
    )
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status) "
        "VALUES ($1, 'app-x', 'completed')",
        oid,
    )
    transition = RolloutAckedTransition()
    cands = await transition.find_candidates(conn)
    assert len(cands) == 1
    r = await transition.apply(conn, cands[0])
    assert r.success and r.to_state == "active"
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-roll'"
    )
    assert state == "active"


# ─── ZombieSiteTransition ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_zombie_site_retires_rule(conn):
    from flywheel_state import advance, ZombieSiteTransition
    await conn.execute("INSERT INTO sites (site_id) VALUES ('dead-site')")
    # No site_appliances rows at all → definitely zombie
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-zomb', 'dead-site')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2) "
        "VALUES ('r-zomb', 'RB-Z', true)"
    )
    await advance(conn, rule_id="r-zomb", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    transition = ZombieSiteTransition()
    cands = await transition.find_candidates(conn)
    assert len(cands) == 1
    r = await transition.apply(conn, cands[0])
    assert r.success and r.to_state == "retired"


@pytest.mark.asyncio
async def test_live_site_not_retired(conn):
    from flywheel_state import advance, ZombieSiteTransition
    await conn.execute("INSERT INTO sites (site_id) VALUES ('live-site')")
    await conn.execute(
        "INSERT INTO site_appliances (appliance_id, site_id, last_checkin) "
        "VALUES ('app-live', 'live-site', NOW())"
    )
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-live', 'live-site')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2) "
        "VALUES ('r-live', 'RB-L', true)"
    )
    await advance(conn, rule_id="r-live", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    transition = ZombieSiteTransition()
    cands = await transition.find_candidates(conn)
    assert not any(c["rule_id"] == "r-live" for c in cands)


# ─── Orchestrator — isolation between transitions ──────────────────


@pytest.mark.asyncio
async def test_orchestrator_isolation_one_failure_does_not_block_others(conn):
    """If one transition raises, others still run. The whole reason
    the spine exists."""
    from flywheel_state import run_orchestrator_tick, Transition, TransitionResult

    # Seed a rule that the RegimeAbsoluteLow transition will handle
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-iso', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled) "
        "VALUES ('r-iso', 'RB-I', true, true)"
    )
    from flywheel_state import advance
    await advance(conn, rule_id="r-iso", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-iso", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-iso", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events "
        "(rule_id, severity, sample_size_7d, window_7d_rate, baseline_30d_rate) "
        "VALUES ('r-iso', 'absolute_low', 75, 0, 0)"
    )

    # A deliberately broken transition
    class BrokenTransition(Transition):
        name = "broken"
        stage = "monitoring"
        async def find_candidates(self, conn):
            raise RuntimeError("kaboom")

    from flywheel_state import RegimeAbsoluteLowTransition
    result = await run_orchestrator_tick(
        conn,
        transitions=[BrokenTransition(), RegimeAbsoluteLowTransition()],
        enforce=True,
    )
    # Broken transition counted as failure
    assert result.failures_by_name.get("broken", 0) >= 1
    # Regime transition still succeeded
    assert result.transitions_by_name.get("regime_absolute_low_auto_disable", 0) >= 1
    # Side effect of the working one is visible
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-iso'"
    )
    assert state == "auto_disabled"


@pytest.mark.asyncio
async def test_orchestrator_idempotent(conn):
    """Running the tick twice produces the same final state."""
    from flywheel_state import advance, run_orchestrator_tick

    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-idem', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled) "
        "VALUES ('r-idem', 'RB-D', true, true)"
    )
    await advance(conn, rule_id="r-idem", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-idem", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-idem", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events "
        "(rule_id, severity, sample_size_7d, window_7d_rate, baseline_30d_rate) "
        "VALUES ('r-idem', 'absolute_low', 50, 0, 0)"
    )

    result1 = await run_orchestrator_tick(conn, enforce=True)
    state1 = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-idem'"
    )
    events1 = await conn.fetchval(
        "SELECT COUNT(*) FROM promoted_rule_events WHERE rule_id='r-idem'"
    )

    result2 = await run_orchestrator_tick(conn, enforce=True)
    state2 = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-idem'"
    )
    events2 = await conn.fetchval(
        "SELECT COUNT(*) FROM promoted_rule_events WHERE rule_id='r-idem'"
    )

    # State unchanged
    assert state1 == state2 == "auto_disabled"
    # No extra events on the second tick (candidate disappears from
    # find_candidates because state is no longer in ('active',...))
    assert events1 == events2


@pytest.mark.asyncio
async def test_canary_failure_auto_disables(conn):
    """Rule promoted < 48h ago with < 70% success AND >= 3 executions
    → auto_disabled by CanaryFailureTransition."""
    from flywheel_state import advance, CanaryFailureTransition
    # Need execution_telemetry table in the test schema
    await conn.execute("""
        DROP TABLE IF EXISTS execution_telemetry;
        CREATE TABLE execution_telemetry (
            id BIGSERIAL PRIMARY KEY,
            runbook_id TEXT,
            success BOOLEAN,
            resolution_level TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-can', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled, created_at) "
        "VALUES ('r-can', 'RB-C', true, true, NOW() - INTERVAL '24 hours')"
    )
    await advance(conn, rule_id="r-can", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-can", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-can", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    # 5 executions, 1 success = 20% rate — under 70%
    for i in range(4):
        await conn.execute(
            "INSERT INTO execution_telemetry (runbook_id, success) "
            "VALUES ('RB-C', false)"
        )
    await conn.execute(
        "INSERT INTO execution_telemetry (runbook_id, success) "
        "VALUES ('RB-C', true)"
    )
    tr = CanaryFailureTransition()
    cands = await tr.find_candidates(conn)
    assert len(cands) == 1
    r = await tr.apply(conn, cands[0])
    assert r.success and r.to_state == "auto_disabled"
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-can'"
    )
    assert state == "auto_disabled"


@pytest.mark.asyncio
async def test_graduation_transition(conn):
    """Rule > 72h old with >= 70% success → graduated."""
    from flywheel_state import advance, GraduationTransition
    await conn.execute("""
        DROP TABLE IF EXISTS execution_telemetry;
        CREATE TABLE execution_telemetry (
            id BIGSERIAL PRIMARY KEY, runbook_id TEXT, success BOOLEAN,
            resolution_level TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-grad', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules "
        "(rule_id, runbook_id, promoted_from_l2, enabled, created_at) "
        "VALUES ('r-grad', 'RB-G', true, true, NOW() - INTERVAL '96 hours')"
    )
    await advance(conn, rule_id="r-grad", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-grad", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-grad", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    # 10 executions, 9 success = 90%
    for _ in range(9):
        await conn.execute(
            "INSERT INTO execution_telemetry (runbook_id, success) "
            "VALUES ('RB-G', true)"
        )
    await conn.execute(
        "INSERT INTO execution_telemetry (runbook_id, success) "
        "VALUES ('RB-G', false)"
    )
    tr = GraduationTransition()
    cands = await tr.find_candidates(conn)
    assert len(cands) == 1
    r = await tr.apply(conn, cands[0])
    assert r.success and r.to_state == "graduated"
    state, source = await conn.fetchrow(
        "SELECT lifecycle_state, (SELECT source FROM l1_rules WHERE rule_id='r-grad')::text "
        "FROM promoted_rules WHERE rule_id='r-grad'"
    )
    assert state == "graduated"
    assert source == "synced"


@pytest.mark.asyncio
async def test_graduation_does_not_fire_before_72h(conn):
    """Young rule (<72h) with high success is NOT graduated."""
    from flywheel_state import advance, GraduationTransition
    await conn.execute("""
        DROP TABLE IF EXISTS execution_telemetry;
        CREATE TABLE execution_telemetry (
            id BIGSERIAL PRIMARY KEY, runbook_id TEXT, success BOOLEAN,
            resolution_level TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-young', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules "
        "(rule_id, runbook_id, promoted_from_l2, enabled, created_at) "
        "VALUES ('r-young', 'RB-Y', true, true, NOW() - INTERVAL '24 hours')"
    )
    await advance(conn, rule_id="r-young", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-young", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-young", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    for _ in range(5):
        await conn.execute(
            "INSERT INTO execution_telemetry (runbook_id, success) "
            "VALUES ('RB-Y', true)"
        )
    tr = GraduationTransition()
    cands = await tr.find_candidates(conn)
    assert not any(c["rule_id"] == "r-young" for c in cands)


@pytest.mark.asyncio
async def test_orchestrator_shadow_mode_does_not_mutate(conn):
    """enforce=False: log intent, change nothing. Safety net for cutover."""
    from flywheel_state import advance, run_orchestrator_tick

    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('r-sh', 's1')"
    )
    await conn.execute(
        "INSERT INTO l1_rules (rule_id, runbook_id, promoted_from_l2, enabled) "
        "VALUES ('r-sh', 'RB-S', true, true)"
    )
    await advance(conn, rule_id="r-sh", new_state="approved",
                  event_type="promotion_approved", actor="system:test",
                  stage="promotion")
    await advance(conn, rule_id="r-sh", new_state="rolling_out",
                  event_type="rollout_issued", actor="system:test",
                  stage="rollout")
    await advance(conn, rule_id="r-sh", new_state="active",
                  event_type="rollout_acked", actor="system:test",
                  stage="rollout")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events "
        "(rule_id, severity, sample_size_7d, window_7d_rate, baseline_30d_rate) "
        "VALUES ('r-sh', 'absolute_low', 60, 0, 0)"
    )

    before_events = await conn.fetchval(
        "SELECT COUNT(*) FROM promoted_rule_events WHERE rule_id='r-sh'"
    )
    result = await run_orchestrator_tick(conn, enforce=False)
    state = await conn.fetchval(
        "SELECT lifecycle_state FROM promoted_rules WHERE rule_id='r-sh'"
    )
    after_events = await conn.fetchval(
        "SELECT COUNT(*) FROM promoted_rule_events WHERE rule_id='r-sh'"
    )

    # Shadow mode: no mutation
    assert state == "active"  # unchanged
    assert after_events == before_events  # no new events
