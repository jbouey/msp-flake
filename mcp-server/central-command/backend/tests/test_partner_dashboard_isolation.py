"""Cross-partner isolation contract — Session 206 partner round-table, QA P0.

The single most trust-ending mistake we could make is showing Partner A
a site belonging to Partner B. This test pins the invariant at the SQL
layer: the dashboard query MUST filter by partner_id at every stage
(attention_list, activity_24h, book_of_business, trend_7d).

Replicates the partners.py `/me/dashboard` queries against a minimal
fixture with two partners' worth of data, then asserts:
  - partner_A sees ONLY partner_A's sites
  - partner_B sees ONLY partner_B's sites
  - no cross-pollution in activity feeds, risk scores, or trend

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres partner isolation test",
)


PREREQ = """
DROP TABLE IF EXISTS incidents CASCADE;
DROP TABLE IF EXISTS incident_recurrence_velocity CASCADE;
DROP TABLE IF EXISTS promoted_rules CASCADE;
DROP TABLE IF EXISTS site_appliances CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP TABLE IF EXISTS partners CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE partners (id UUID PRIMARY KEY, name TEXT);

CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    clinic_name TEXT,
    partner_id UUID REFERENCES partners(id),
    status TEXT DEFAULT 'active'
);

CREATE TABLE site_appliances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id TEXT REFERENCES sites(site_id),
    last_checkin TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id UUID NOT NULL DEFAULT gen_random_uuid(),
    site_id TEXT,
    incident_type TEXT, severity TEXT DEFAULT 'medium',
    resolution_tier TEXT, status TEXT DEFAULT 'resolved',
    details JSONB DEFAULT '{}'::jsonb,
    pre_state JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE incident_recurrence_velocity (
    site_id TEXT, incident_type TEXT,
    resolved_4h INT, resolved_7d INT,
    velocity_per_hour FLOAT, is_chronic BOOLEAN DEFAULT FALSE
);

CREATE TABLE promoted_rules (
    rule_id TEXT PRIMARY KEY, site_id TEXT,
    operator_ack_required BOOLEAN DEFAULT FALSE,
    operator_ack_at TIMESTAMPTZ
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ)
        yield c
    finally:
        await c.execute(
            "DROP TABLE IF EXISTS incidents, incident_recurrence_velocity, "
            "promoted_rules, site_appliances, sites, partners CASCADE;"
        )
        await c.close()


async def _two_partners(conn):
    pA = uuid4(); pB = uuid4()
    await conn.execute("INSERT INTO partners(id,name) VALUES ($1,'Acme IT'),($2,'Other IT')", pA, pB)
    # Partner A: 2 sites with various states
    await conn.execute("INSERT INTO sites(site_id,clinic_name,partner_id) VALUES "
                       "('a-clinic-1','Drakes Dental',$1),"
                       "('a-clinic-2','Scranton Pediatrics',$1)", pA)
    # Partner B: 2 sites (should be invisible to A)
    await conn.execute("INSERT INTO sites(site_id,clinic_name,partner_id) VALUES "
                       "('b-clinic-1','Other Clinic X',$1),"
                       "('b-clinic-2','Other Clinic Y',$1)", pB)
    return pA, pB


# ─── Isolation assertions ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_attention_list_partner_scope(conn):
    pA, pB = await _two_partners(conn)

    # Seed chronic + L3 on BOTH partners' sites
    await conn.execute(
        "INSERT INTO incident_recurrence_velocity(site_id,incident_type,is_chronic) VALUES "
        "('a-clinic-1','x',true),('b-clinic-1','y',true)"
    )
    await conn.execute(
        "INSERT INTO incidents(site_id,status,resolution_tier) VALUES "
        "('a-clinic-2','escalated','L3'),"
        "('b-clinic-2','escalated','L3')"
    )

    # Run the EXACT query from partners.get_partner_dashboard attention_list
    # for partner A
    rows = await conn.fetch(
        """
        WITH site_scope AS (
            SELECT site_id, clinic_name FROM sites
            WHERE partner_id = $1 AND status != 'inactive'
        ),
        risk_agg AS (
            SELECT ss.site_id, ss.clinic_name,
                   COALESCE((SELECT COUNT(*) FROM incident_recurrence_velocity v
                             WHERE v.site_id = ss.site_id AND v.is_chronic), 0) AS chronic,
                   COALESCE((SELECT COUNT(*) FROM incidents i
                             WHERE i.site_id = ss.site_id
                               AND i.status NOT IN ('resolved','closed')
                               AND i.resolution_tier = 'L3'), 0) AS open_l3,
                   COALESCE((SELECT COUNT(*) FROM promoted_rules pr
                             WHERE pr.site_id = ss.site_id
                               AND pr.operator_ack_required
                               AND pr.operator_ack_at IS NULL), 0) AS ack_pending,
                   0::bigint AS offline_appliances
            FROM site_scope ss
        )
        SELECT site_id, clinic_name, chronic, open_l3,
               (chronic*3 + open_l3*5 + ack_pending*2 + offline_appliances*4) AS risk_score
        FROM risk_agg
        WHERE (chronic + open_l3 + ack_pending + offline_appliances) > 0
        ORDER BY risk_score DESC
        """, pA,
    )
    seen_sites = {r["site_id"] for r in rows}
    assert seen_sites == {"a-clinic-1", "a-clinic-2"}, (
        f"Partner A should see ONLY a-clinic-*; saw {seen_sites}"
    )
    for r in rows:
        assert not r["site_id"].startswith("b-"), (
            "Cross-partner LEAK: partner A saw partner B's site"
        )


@pytest.mark.asyncio
async def test_activity_feed_partner_scope(conn):
    pA, pB = await _two_partners(conn)
    now = datetime.now(timezone.utc)
    # Partner A: 3 incidents in last 24h
    for i in range(3):
        await conn.execute(
            "INSERT INTO incidents(site_id,resolution_tier,created_at) VALUES ($1,'L1',$2)",
            "a-clinic-1", now - timedelta(hours=i+1),
        )
    # Partner B: 50 incidents in last 24h (loud! should NOT appear in A's feed)
    for i in range(50):
        await conn.execute(
            "INSERT INTO incidents(site_id,resolution_tier,created_at) VALUES ($1,'L3',$2)",
            "b-clinic-1", now - timedelta(hours=i % 24),
        )

    rows = await conn.fetch(
        """SELECT i.site_id, i.resolution_tier FROM incidents i
           JOIN sites s ON s.site_id = i.site_id
           WHERE s.partner_id = $1
             AND i.created_at > NOW() - INTERVAL '24 hours'
           ORDER BY i.created_at DESC LIMIT 30""",
        pA,
    )
    assert len(rows) == 3, f"Partner A should see exactly 3 events; saw {len(rows)}"
    for r in rows:
        assert r["site_id"].startswith("a-"), (
            f"Cross-partner LEAK in activity feed: partner A saw {r['site_id']}"
        )
        assert r["resolution_tier"] != "L3", (
            "Leaked partner B's L3 escalations into A's activity feed"
        )


@pytest.mark.asyncio
async def test_book_of_business_partner_scope(conn):
    pA, pB = await _two_partners(conn)
    # Seed 10 incidents for A (9 L1, 1 L3) and 100 for B (all L3)
    for _ in range(9):
        await conn.execute(
            "INSERT INTO incidents(site_id,resolution_tier) VALUES ('a-clinic-1','L1')"
        )
    await conn.execute(
        "INSERT INTO incidents(site_id,resolution_tier) VALUES ('a-clinic-2','L3')"
    )
    for _ in range(100):
        await conn.execute(
            "INSERT INTO incidents(site_id,resolution_tier) VALUES ('b-clinic-1','L3')"
        )

    row = await conn.fetchrow(
        """SELECT COUNT(DISTINCT s.site_id) AS total_clients,
                  COUNT(i.id) AS total,
                  COUNT(i.id) FILTER (WHERE i.resolution_tier='L1') AS l1
           FROM sites s
           LEFT JOIN incidents i ON i.site_id = s.site_id
                                AND i.created_at > NOW() - INTERVAL '24 hours'
           WHERE s.partner_id = $1 AND s.status != 'inactive'""",
        pA,
    )
    # Partner A rollup: 2 clients, 10 incidents, 9 L1
    assert int(row["total_clients"]) == 2
    assert int(row["total"]) == 10
    assert int(row["l1"]) == 9
    # Partner A self-heal should be 90%, NOT polluted by B's 100 L3s
    pct = 100.0 * int(row["l1"]) / int(row["total"])
    assert pct == 90.0, (
        f"Partner A self-heal wrongly computed: {pct}%. Cross-partner incidents leaked."
    )


@pytest.mark.asyncio
async def test_trend_does_not_aggregate_foreign_partner(conn):
    pA, pB = await _two_partners(conn)
    # Seed across 3 days
    for day_ago in [1, 2, 3]:
        # partner A: 10 L1s on each day
        for _ in range(10):
            await conn.execute(
                "INSERT INTO incidents(site_id,resolution_tier,created_at) "
                "VALUES ('a-clinic-1','L1', NOW() - ($1 || ' days')::INTERVAL)",
                str(day_ago),
            )
        # partner B: 1000 L3s on each day (would totally distort trend if leaked)
        await conn.execute(
            """INSERT INTO incidents(site_id,resolution_tier,created_at)
               SELECT 'b-clinic-1','L3', NOW() - ($1 || ' days')::INTERVAL
               FROM generate_series(1,1000)""",
            str(day_ago),
        )
    rows = await conn.fetch(
        """SELECT DATE_TRUNC('day', i.created_at) AS day,
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE i.resolution_tier='L1') AS l1
           FROM incidents i
           JOIN sites s ON s.site_id = i.site_id
           WHERE s.partner_id = $1
             AND i.created_at > NOW() - INTERVAL '7 days'
           GROUP BY 1 ORDER BY 1""",
        pA,
    )
    for r in rows:
        # Each day: exactly 10 total, all L1, 100% self-heal
        assert int(r["total"]) == 10, (
            f"Trend for partner A polluted: got {r['total']} incidents on {r['day']}"
        )
        assert int(r["l1"]) == 10
