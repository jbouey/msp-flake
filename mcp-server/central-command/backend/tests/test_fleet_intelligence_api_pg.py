"""Phase 11 fleet-intelligence API surface test (Phase 15 closing).

Round-table audit: 'Phase 11 fleet intelligence API: C — partial
threat-model, 0 tests.' Existing test_fleet_intelligence_scope_pg.py
covers the _partner_site_ids helper. This file covers the SQL
patterns the API endpoints actually run:

  /summary          — partner_fleet_intelligence_summary
  /rules            — partner_fleet_intelligence_rules (the big CTE)
  /regime-alerts    — partner_regime_alerts (filter + scope)
  /regime-alerts/{id}/ack — ack_regime_alert (atomic update)

Plus the deterministic narrative builder.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping fleet-intelligence API test",
)


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS l1_rule_regime_events CASCADE;
DROP TABLE IF EXISTS execution_telemetry CASCADE;
DROP TABLE IF EXISTS fleet_order_completions CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS runbooks CASCADE;
DROP TABLE IF EXISTS l1_rules CASCADE;
DROP TABLE IF EXISTS promoted_rules CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP TABLE IF EXISTS client_orgs CASCADE;
DROP TABLE IF EXISTS partners CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT
);
CREATE TABLE client_orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    current_partner_id UUID REFERENCES partners(id)
);
CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    client_org_id UUID REFERENCES client_orgs(id) ON DELETE CASCADE
);

CREATE TABLE promoted_rules (
    rule_id TEXT PRIMARY KEY,
    pattern_signature TEXT,
    promoted_at TIMESTAMPTZ DEFAULT NOW(),
    deployment_count INTEGER DEFAULT 0,
    last_deployed_at TIMESTAMPTZ,
    notes TEXT,
    status TEXT DEFAULT 'active'
);

CREATE TABLE l1_rules (
    rule_id TEXT PRIMARY KEY REFERENCES promoted_rules(rule_id),
    runbook_id TEXT,
    confidence DOUBLE PRECISION,
    incident_pattern JSONB,
    enabled BOOLEAN DEFAULT TRUE,
    promoted_from_l2 BOOLEAN DEFAULT TRUE
);

CREATE TABLE runbooks (
    runbook_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    category TEXT,
    check_type TEXT,
    hipaa_controls TEXT[]
);

CREATE TABLE fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parameters JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE fleet_order_completions (
    id BIGSERIAL PRIMARY KEY,
    fleet_order_id UUID REFERENCES fleet_orders(id),
    status TEXT DEFAULT 'completed',
    completed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE execution_telemetry (
    id BIGSERIAL PRIMARY KEY,
    site_id TEXT,
    runbook_id TEXT,
    resolution_level TEXT,
    success BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE l1_rule_regime_events (
    id BIGSERIAL PRIMARY KEY,
    rule_id TEXT,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    window_7d_rate DOUBLE PRECISION,
    baseline_30d_rate DOUBLE PRECISION,
    delta DOUBLE PRECISION,
    severity TEXT,
    sample_size_7d INTEGER,
    sample_size_30d INTEGER,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    resolution TEXT
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        yield c
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS l1_rule_regime_events CASCADE;
            DROP TABLE IF EXISTS execution_telemetry CASCADE;
            DROP TABLE IF EXISTS fleet_order_completions CASCADE;
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS runbooks CASCADE;
            DROP TABLE IF EXISTS l1_rules CASCADE;
            DROP TABLE IF EXISTS promoted_rules CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
            DROP TABLE IF EXISTS client_orgs CASCADE;
            DROP TABLE IF EXISTS partners CASCADE;
        """)
        await c.close()


# ─── /rules SQL — the big CTE ─────────────────────────────────────


RULES_SQL = """
    WITH partner_rules AS (
        SELECT DISTINCT pr.rule_id, pr.pattern_signature, pr.promoted_at,
               pr.deployment_count, pr.last_deployed_at, pr.notes,
               l.runbook_id, l.confidence, l.incident_pattern,
               r.name AS runbook_name, r.check_type,
               r.hipaa_controls
        FROM promoted_rules pr
        JOIN l1_rules l ON l.rule_id = pr.rule_id
        LEFT JOIN runbooks r ON r.runbook_id = l.runbook_id
        WHERE pr.status = 'active'
          AND l.enabled = true
          AND EXISTS (
              SELECT 1 FROM fleet_orders fo
              JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
              WHERE fo.parameters->>'rule_id' = pr.rule_id
                AND fo.parameters->>'site_id' = ANY($1)
                AND foc.status = 'completed'
          )
    )
    SELECT pr.rule_id,
           (
             SELECT COUNT(*) FROM execution_telemetry et
             WHERE et.site_id = ANY($1)
               AND et.resolution_level = 'L1'
               AND et.runbook_id = pr.runbook_id
               AND et.created_at > NOW() - INTERVAL '30 days'
               AND et.success = true
           ) AS triggers_30d
    FROM partner_rules pr
    ORDER BY triggers_30d DESC, pr.promoted_at DESC
    LIMIT 100
"""


@pytest.mark.asyncio
async def test_rules_returns_only_partners_deployed_rules(conn):
    """A rule must appear in /rules ONLY if it's been deployed via a
    completed fleet order to one of the partner's sites."""
    pa = await conn.fetchval("INSERT INTO partners (email) VALUES ('a@p.com') RETURNING id::text")
    pb = await conn.fetchval("INSERT INTO partners (email) VALUES ('b@p.com') RETURNING id::text")
    org_a = await conn.fetchval("INSERT INTO client_orgs (current_partner_id) VALUES ($1::uuid) RETURNING id::text", pa)
    org_b = await conn.fetchval("INSERT INTO client_orgs (current_partner_id) VALUES ($1::uuid) RETURNING id::text", pb)
    await conn.execute("INSERT INTO sites VALUES ('site-a-1', $1::uuid)", org_a)
    await conn.execute("INSERT INTO sites VALUES ('site-b-1', $1::uuid)", org_b)

    # Two rules, both promoted + active + enabled
    for rid, rb in [("rule-A", "RB-X"), ("rule-B", "RB-Y")]:
        await conn.execute(
            "INSERT INTO promoted_rules (rule_id) VALUES ($1)", rid)
        await conn.execute(
            "INSERT INTO l1_rules (rule_id, runbook_id, enabled) VALUES ($1, $2, true)",
            rid, rb)
        await conn.execute(
            "INSERT INTO runbooks (runbook_id, name) VALUES ($1, $2)", rb, f"name-{rb}")

    # Deploy rule-A to site-a-1, rule-B to site-b-1 (different partner)
    fo_a = await conn.fetchval(
        "INSERT INTO fleet_orders (parameters) VALUES "
        "($1::jsonb) RETURNING id::text",
        '{"rule_id":"rule-A","site_id":"site-a-1"}')
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, status) VALUES ($1::uuid, 'completed')", fo_a)
    fo_b = await conn.fetchval(
        "INSERT INTO fleet_orders (parameters) VALUES ($1::jsonb) RETURNING id::text",
        '{"rule_id":"rule-B","site_id":"site-b-1"}')
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, status) VALUES ($1::uuid, 'completed')", fo_b)

    # Partner A's scope = ["site-a-1"]
    rows_a = await conn.fetch(RULES_SQL, ["site-a-1"])
    rule_ids_a = {r["rule_id"] for r in rows_a}
    assert rule_ids_a == {"rule-A"}, (
        f"Cross-partner rule visibility leak — partner A saw {rule_ids_a}"
    )

    rows_b = await conn.fetch(RULES_SQL, ["site-b-1"])
    rule_ids_b = {r["rule_id"] for r in rows_b}
    assert rule_ids_b == {"rule-B"}


@pytest.mark.asyncio
async def test_rules_excludes_inactive_status(conn):
    """promoted_rules.status='active' filter must hold — disabled
    rules don't show in partner UI."""
    p = await conn.fetchval("INSERT INTO partners (email) VALUES ('x@p.com') RETURNING id::text")
    org = await conn.fetchval("INSERT INTO client_orgs (current_partner_id) VALUES ($1::uuid) RETURNING id::text", p)
    await conn.execute("INSERT INTO sites VALUES ('s', $1::uuid)", org)

    await conn.execute("INSERT INTO promoted_rules (rule_id, status) VALUES ('rule-disabled', 'paused')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id, enabled) VALUES ('rule-disabled', 'RB-X', true)")
    fo = await conn.fetchval(
        "INSERT INTO fleet_orders (parameters) VALUES ($1::jsonb) RETURNING id::text",
        '{"rule_id":"rule-disabled","site_id":"s"}')
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, status) VALUES ($1::uuid, 'completed')", fo)

    rows = await conn.fetch(RULES_SQL, ["s"])
    assert rows == [], "Paused promoted_rule should be hidden from /rules"


@pytest.mark.asyncio
async def test_rules_excludes_l1_disabled(conn):
    p = await conn.fetchval("INSERT INTO partners (email) VALUES ('y@p.com') RETURNING id::text")
    org = await conn.fetchval("INSERT INTO client_orgs (current_partner_id) VALUES ($1::uuid) RETURNING id::text", p)
    await conn.execute("INSERT INTO sites VALUES ('s2', $1::uuid)", org)

    await conn.execute("INSERT INTO promoted_rules (rule_id, status) VALUES ('rule-l1off', 'active')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id, enabled) VALUES ('rule-l1off', 'RB-Y', false)")
    fo = await conn.fetchval(
        "INSERT INTO fleet_orders (parameters) VALUES ($1::jsonb) RETURNING id::text",
        '{"rule_id":"rule-l1off","site_id":"s2"}')
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, status) VALUES ($1::uuid, 'completed')", fo)

    rows = await conn.fetch(RULES_SQL, ["s2"])
    assert rows == [], "L1-disabled rule should not appear"


# ─── /summary SQL fragments ──────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_active_rules_count(conn):
    p = await conn.fetchval("INSERT INTO partners (email) VALUES ('s@p.com') RETURNING id::text")
    org = await conn.fetchval("INSERT INTO client_orgs (current_partner_id) VALUES ($1::uuid) RETURNING id::text", p)
    await conn.execute("INSERT INTO sites VALUES ('s', $1::uuid)", org)
    for rid in ["r1", "r2", "r3"]:
        await conn.execute("INSERT INTO promoted_rules (rule_id, status) VALUES ($1, 'active')", rid)
        await conn.execute("INSERT INTO l1_rules (rule_id, enabled) VALUES ($1, true)", rid)
        fo = await conn.fetchval(
            "INSERT INTO fleet_orders (parameters) VALUES ($1::jsonb) RETURNING id::text",
            f'{{"rule_id":"{rid}","site_id":"s"}}')
        await conn.execute(
            "INSERT INTO fleet_order_completions (fleet_order_id, status) VALUES ($1::uuid, 'completed')", fo)

    row = await conn.fetchrow("""
        SELECT COUNT(DISTINCT pr.rule_id) AS n
        FROM promoted_rules pr
        JOIN fleet_orders fo ON fo.parameters->>'rule_id' = pr.rule_id
        JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
        WHERE pr.status = 'active'
          AND fo.parameters->>'site_id' = ANY($1)
          AND foc.status = 'completed'
    """, ["s"])
    assert row["n"] == 3


# ─── /regime-alerts ──────────────────────────────────────────────


REGIME_SQL = """
    SELECT rce.rule_id, rce.detected_at, rce.window_7d_rate,
           rce.baseline_30d_rate, rce.delta, rce.severity,
           rce.sample_size_7d, l.runbook_id
    FROM l1_rule_regime_events rce
    JOIN l1_rules l ON l.rule_id = rce.rule_id
    WHERE rce.detected_at > NOW() - make_interval(days => $1)
      AND rce.acknowledged_at IS NULL
    ORDER BY rce.severity DESC, rce.detected_at DESC
    LIMIT 100
"""


@pytest.mark.asyncio
async def test_regime_alerts_filters_acknowledged(conn):
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('reg-A'), ('reg-B')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id) VALUES ('reg-A', 'RB-A'), ('reg-B', 'RB-B')")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events (rule_id, severity) "
        "VALUES ('reg-A', 'warning'), ('reg-B', 'critical')"
    )
    await conn.execute(
        "UPDATE l1_rule_regime_events SET acknowledged_at = NOW() WHERE rule_id = 'reg-A'"
    )

    rows = await conn.fetch(REGIME_SQL, 14)
    rule_ids = {r["rule_id"] for r in rows}
    assert rule_ids == {"reg-B"}, "Acknowledged events must be filtered out"


@pytest.mark.asyncio
async def test_regime_alerts_orders_critical_first(conn):
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('a'), ('b'), ('c')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id) VALUES ('a','X'),('b','Y'),('c','Z')")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events (rule_id, severity) "
        "VALUES ('a', 'warning'), ('b', 'critical'), ('c', 'warning')"
    )
    rows = await conn.fetch(REGIME_SQL, 14)
    assert rows[0]["rule_id"] == "b"  # critical first
    assert rows[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_regime_alerts_respects_days_filter(conn):
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('old')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id) VALUES ('old', 'RB-OLD')")
    await conn.execute(
        "INSERT INTO l1_rule_regime_events (rule_id, severity, detected_at) "
        "VALUES ('old', 'warning', NOW() - INTERVAL '60 days')"
    )
    rows = await conn.fetch(REGIME_SQL, 14)
    assert rows == [], "Events older than days filter should be excluded"


# ─── /regime-alerts/{id}/ack — atomic update ─────────────────────


@pytest.mark.asyncio
async def test_ack_regime_alert_marks_acknowledged(conn):
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('r')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id) VALUES ('r', 'X')")
    eid = await conn.fetchval(
        "INSERT INTO l1_rule_regime_events (rule_id, severity) "
        "VALUES ('r', 'warning') RETURNING id"
    )

    await conn.execute("""
        UPDATE l1_rule_regime_events
           SET acknowledged_at = NOW(),
               acknowledged_by = $2,
               resolution = COALESCE(resolution, 'still_investigating')
         WHERE id = $1 AND acknowledged_at IS NULL
    """, eid, "p@example.com")

    row = await conn.fetchrow(
        "SELECT acknowledged_at, acknowledged_by, resolution "
        "FROM l1_rule_regime_events WHERE id = $1", eid,
    )
    assert row["acknowledged_at"] is not None
    assert row["acknowledged_by"] == "p@example.com"
    assert row["resolution"] == "still_investigating"


@pytest.mark.asyncio
async def test_ack_is_idempotent_on_already_acked(conn):
    """Re-acking an already-acked event should leave the original
    timestamp untouched (WHERE acknowledged_at IS NULL guard)."""
    await conn.execute("INSERT INTO promoted_rules (rule_id) VALUES ('r2')")
    await conn.execute("INSERT INTO l1_rules (rule_id, runbook_id) VALUES ('r2', 'Y')")
    eid = await conn.fetchval(
        "INSERT INTO l1_rule_regime_events (rule_id, severity, "
        "acknowledged_at, acknowledged_by) "
        "VALUES ('r2', 'warning', NOW() - INTERVAL '1 day', 'first@p.com') "
        "RETURNING id"
    )
    original_ts = await conn.fetchval(
        "SELECT acknowledged_at FROM l1_rule_regime_events WHERE id = $1", eid)

    await conn.execute("""
        UPDATE l1_rule_regime_events
           SET acknowledged_at = NOW(),
               acknowledged_by = $2
         WHERE id = $1 AND acknowledged_at IS NULL
    """, eid, "second@p.com")

    row = await conn.fetchrow(
        "SELECT acknowledged_at, acknowledged_by FROM l1_rule_regime_events "
        "WHERE id = $1", eid)
    assert row["acknowledged_at"] == original_ts
    assert row["acknowledged_by"] == "first@p.com"


# ─── _build_rule_narrative — pure function ───────────────────────


def test_build_rule_narrative_handles_zero_triggers():
    from fleet_intelligence import _build_rule_narrative
    text = _build_rule_narrative(
        rule_id="rule-x",
        runbook_name="Disk cleanup",
        incident_type="disk_full",
        triggers_30d=0,
        promoted_at=datetime.now(timezone.utc) - timedelta(days=10),
        deployment_count=2,
        confidence=0.85,
        hipaa_controls=["164.308(a)(1)(ii)(D)"],
    )
    assert "Disk cleanup" in text
    assert "No triggers recorded in the last 30 days" in text
    assert "164.308" in text


def test_build_rule_narrative_handles_singular_vs_plural():
    from fleet_intelligence import _build_rule_narrative
    text_one = _build_rule_narrative(
        rule_id="r", runbook_name="X", incident_type="y",
        triggers_30d=1, promoted_at=None, deployment_count=1,
        confidence=0.9, hipaa_controls=None,
    )
    assert "1 time " in text_one or "1 time." in text_one
    assert "appliances 1 time" in text_one

    text_many = _build_rule_narrative(
        rule_id="r", runbook_name="X", incident_type="y",
        triggers_30d=5, promoted_at=None, deployment_count=3,
        confidence=0.9, hipaa_controls=None,
    )
    assert "5 times" in text_many
    assert "3 times" in text_many


def test_build_rule_narrative_no_hipaa_no_clause():
    from fleet_intelligence import _build_rule_narrative
    text = _build_rule_narrative(
        rule_id="r", runbook_name="X", incident_type="y",
        triggers_30d=2, promoted_at=None, deployment_count=1,
        confidence=0.9, hipaa_controls=None,
    )
    assert "HIPAA" not in text
