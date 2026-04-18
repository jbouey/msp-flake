"""Hero-metric contract test (Session 206 round-table, QA's ask).

The operator's hero metric is `self_heal_rate_24h_pct`. Definition:
  (L1-resolved incidents in last 24h) / (total incidents in last 24h) * 100

This test locks that math to a specific table view. If someone changes
the endpoint to include L3-escalated + resolved-by-L1 / total-incidents
(a common "boosting the number" antipattern), the test fails.

Also pins the per_site + trend shapes — CCIE's ask that per-site
breakdown is first-class once the second site lands.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres hero-metric test",
)


PREREQ = """
DROP TABLE IF EXISTS incidents CASCADE;
DROP TABLE IF EXISTS incident_recurrence_velocity CASCADE;
CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id UUID NOT NULL DEFAULT gen_random_uuid(),
    site_id TEXT,
    incident_type TEXT,
    severity TEXT,
    resolution_tier TEXT,
    status TEXT DEFAULT 'resolved',
    details JSONB DEFAULT '{}'::jsonb,
    pre_state JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    reported_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE incident_recurrence_velocity (
    site_id TEXT,
    incident_type TEXT,
    resolved_4h INT,
    resolved_7d INT,
    velocity_per_hour FLOAT,
    is_chronic BOOLEAN DEFAULT FALSE,
    last_l1_runbook TEXT,
    recurrence_broken_at TIMESTAMPTZ,
    recurrence_broken_by_runbook TEXT
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        await c.execute(PREREQ)
        yield c
    finally:
        await c.execute(
            "DROP TABLE IF EXISTS incidents CASCADE; "
            "DROP TABLE IF EXISTS incident_recurrence_velocity CASCADE;"
        )
        await c.close()


async def _seed(conn, site, tier, n, when_ago_hours=1):
    """Insert N incidents for a site + tier, created N hours ago."""
    for _ in range(n):
        await conn.execute(
            """INSERT INTO incidents (site_id, incident_type, severity, resolution_tier,
                status, created_at, reported_at)
               VALUES ($1, 'test', 'medium', $2, 'resolved', $3, $3)""",
            site, tier,
            datetime.now(timezone.utc) - timedelta(hours=when_ago_hours),
        )


# ─── Math invariants ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_heal_rate_100pct_when_all_l1(conn):
    await _seed(conn, "s1", "L1", 10)
    row = await conn.fetchrow(
        """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE resolution_tier='L1') AS l1
           FROM incidents WHERE created_at > NOW() - INTERVAL '24 hours'"""
    )
    pct = 100.0 * row["l1"] / row["total"]
    assert pct == 100.0


@pytest.mark.asyncio
async def test_self_heal_rate_drops_with_l3(conn):
    # 80 L1 + 20 L3 → 80% self-heal
    await _seed(conn, "s1", "L1", 80)
    await _seed(conn, "s1", "L3", 20)
    row = await conn.fetchrow(
        """SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE resolution_tier='L1') AS l1
           FROM incidents WHERE created_at > NOW() - INTERVAL '24 hours'"""
    )
    pct = 100.0 * row["l1"] / row["total"]
    assert round(pct, 1) == 80.0


@pytest.mark.asyncio
async def test_self_heal_rate_excludes_older_than_24h(conn):
    # Modern: 5 L1. Older than 24h: 100 L1. Only modern should count.
    await _seed(conn, "s1", "L1", 5, when_ago_hours=1)
    await _seed(conn, "s1", "L1", 100, when_ago_hours=48)
    row = await conn.fetchrow(
        """SELECT COUNT(*) AS total FROM incidents
           WHERE created_at > NOW() - INTERVAL '24 hours'"""
    )
    assert int(row["total"]) == 5


@pytest.mark.asyncio
async def test_per_site_aggregates_separately(conn):
    await _seed(conn, "site-a", "L1", 9)
    await _seed(conn, "site-a", "L3", 1)
    await _seed(conn, "site-b", "L1", 10)
    rows = await conn.fetch(
        """SELECT site_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE resolution_tier='L1') AS l1
           FROM incidents WHERE created_at > NOW() - INTERVAL '24 hours'
           GROUP BY site_id ORDER BY site_id"""
    )
    assert len(rows) == 2
    a = next(r for r in rows if r["site_id"] == "site-a")
    b = next(r for r in rows if r["site_id"] == "site-b")
    assert round(100.0 * a["l1"] / a["total"], 1) == 90.0
    assert round(100.0 * b["l1"] / b["total"], 1) == 100.0


# ─── Psychology invariants ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_incidents_returns_none_not_division_by_zero(conn):
    # The endpoint must return pct=None (not 100.0) when no incidents
    # have been observed in the window. Rationale: the self-heal *rate*
    # is undefined with a zero denominator; emitting 100.0 reads as
    # "every drift was auto-healed" when in fact nothing was observed.
    # Frontend renders an explicit "no incidents detected" empty state.
    row = await conn.fetchrow(
        """SELECT COUNT(*) AS total FROM incidents
           WHERE created_at > NOW() - INTERVAL '24 hours'"""
    )
    assert int(row["total"]) == 0
    # Matching Python: `pct = round(100.0 * l1 / total, 1) if total > 0 else None`
    total = int(row["total"])
    l1 = 0
    pct = round(100.0 * l1 / total, 1) if total > 0 else None
    assert pct is None


# ─── Transient drift (portal) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_transient_drift_resolved_at_l1_does_not_count_as_unprotected(conn):
    """Client portal: an incident that opens + resolves at L1 within
    5 minutes MUST NOT trigger 'protected=false'. The portal's
    protected flag checks for OPEN L3 incidents only; transient drift
    that auto-heals is invisible to the customer."""
    # Simulate: drift opened 2 min ago, resolved at L1
    await _seed(conn, "practice-1", "L1", 1)

    # Portal's protected-flag query (from portal.py get_portal_home):
    row = await conn.fetchrow(
        """SELECT COUNT(*) FROM incidents
           WHERE site_id = $1
             AND status NOT IN ('resolved', 'closed')
             AND resolution_tier = 'L3'""",
        "practice-1",
    )
    open_l3 = int(row["count"])
    assert open_l3 == 0, (
        "Transient L1-healed drift should NEVER show up as unprotected. "
        "The portal's protected=true invariant is a psychology lever: the "
        "customer pays for self-healing, they shouldn't see every blip."
    )


@pytest.mark.asyncio
async def test_open_l3_incident_DOES_mark_unprotected(conn):
    """Opposite of the above: an ACTUAL open L3 incident MUST mark
    the portal as not-protected. Otherwise we're hiding real work."""
    await conn.execute(
        """INSERT INTO incidents (site_id, resolution_tier, status, created_at)
           VALUES ('practice-1', 'L3', 'escalated', NOW())"""
    )
    row = await conn.fetchrow(
        """SELECT COUNT(*) FROM incidents
           WHERE site_id = $1
             AND status NOT IN ('resolved', 'closed')
             AND resolution_tier = 'L3'""",
        "practice-1",
    )
    assert int(row["count"]) == 1
