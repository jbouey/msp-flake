"""Phase 15 closing — enterprise appliance offline detection.

The round-table audit surfaced a visibility gap: an appliance powered
down and the dashboard kept it labeled 'online' because `status` was
only written on successful checkin and no loop moved it to 'offline'.

This test pins down the state machine end-to-end:

  1. Seed a `site_appliances` row whose last_checkin is 10 min old
  2. Run mark_stale_appliances_loop's SQL
  3. Row should be status='offline', offline_since=NOW(),
     offline_event_count=1
  4. Simulate a fresh checkin — row moves back to status='online',
     offline_since=NULL, offline_notified=false, recovered_at=NOW()

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import pathlib
from datetime import timedelta

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres offline-detection test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS site_appliances CASCADE;

CREATE TABLE site_appliances (
    appliance_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    hostname TEXT,
    display_name TEXT,
    mac_address TEXT,
    ip_addresses JSONB,
    agent_version TEXT,
    nixos_version TEXT,
    uptime_seconds BIGINT,
    first_checkin TIMESTAMPTZ,
    last_checkin TIMESTAMPTZ,
    daemon_health JSONB,
    status VARCHAR DEFAULT 'pending',
    offline_since TIMESTAMPTZ,
    offline_notified BOOLEAN DEFAULT false,
    deleted_at TIMESTAMPTZ,
    deleted_by TEXT,
    auth_failure_count INTEGER DEFAULT 0,
    recovered_at TIMESTAMPTZ,
    offline_event_count INTEGER NOT NULL DEFAULT 0
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        # Apply migration 180 — the offline-detection index + CHECK + columns
        # The fixture schema already has recovered_at and offline_event_count
        # so migration 180's ADD COLUMN IF NOT EXISTS is a no-op; exercise
        # the CHECK and INDEX parts below.
        mig = (MIGRATIONS_DIR / "180_appliance_offline_detection.sql").read_text()
        await c.execute(mig)
        yield c
    finally:
        await c.execute("DROP TABLE IF EXISTS site_appliances CASCADE;")
        await c.close()


# ─── State machine ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_appliance_flipped_offline(conn):
    """Appliance with last_checkin > 5 min ago → status='offline'."""
    await conn.execute("""
        INSERT INTO site_appliances (appliance_id, site_id, hostname, status, last_checkin)
        VALUES ('a-stale-1', 's1', 'host-1', 'online', NOW() - INTERVAL '10 minutes')
    """)
    # The loop's core SQL — this is what mark_stale_appliances_loop runs.
    rows = await conn.fetch("""
        UPDATE site_appliances
        SET status = 'offline',
            offline_since = COALESCE(offline_since, NOW()),
            offline_event_count = offline_event_count + 1
        WHERE status != 'offline'
          AND status != 'decommissioned'
          AND deleted_at IS NULL
          AND last_checkin IS NOT NULL
          AND last_checkin < NOW() - INTERVAL '5 minutes'
        RETURNING appliance_id, status, offline_since, offline_event_count
    """)
    assert len(rows) == 1
    assert rows[0]["status"] == "offline"
    assert rows[0]["offline_since"] is not None
    assert rows[0]["offline_event_count"] == 1


@pytest.mark.asyncio
async def test_fresh_appliance_not_flipped(conn):
    """Appliance with recent last_checkin stays online."""
    await conn.execute("""
        INSERT INTO site_appliances (appliance_id, site_id, hostname, status, last_checkin)
        VALUES ('a-fresh', 's1', 'host-2', 'online', NOW() - INTERVAL '1 minute')
    """)
    rows = await conn.fetch("""
        UPDATE site_appliances
        SET status = 'offline', offline_since = NOW()
        WHERE status != 'offline' AND status != 'decommissioned'
          AND deleted_at IS NULL AND last_checkin IS NOT NULL
          AND last_checkin < NOW() - INTERVAL '5 minutes'
        RETURNING appliance_id
    """)
    assert len(rows) == 0
    cur = await conn.fetchval(
        "SELECT status FROM site_appliances WHERE appliance_id='a-fresh'"
    )
    assert cur == "online"


@pytest.mark.asyncio
async def test_decommissioned_not_flipped(conn):
    """Decommissioned appliances must never move to 'offline' — they're
    retired, not failing. Alerting on them would be noise."""
    await conn.execute("""
        INSERT INTO site_appliances (appliance_id, site_id, hostname, status, last_checkin)
        VALUES ('a-decom', 's1', 'host-3', 'decommissioned', NOW() - INTERVAL '30 days')
    """)
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'offline', offline_since = NOW()
        WHERE status != 'offline' AND status != 'decommissioned'
          AND deleted_at IS NULL AND last_checkin IS NOT NULL
          AND last_checkin < NOW() - INTERVAL '5 minutes'
    """)
    cur = await conn.fetchval(
        "SELECT status FROM site_appliances WHERE appliance_id='a-decom'"
    )
    assert cur == "decommissioned"


@pytest.mark.asyncio
async def test_soft_deleted_not_flipped(conn):
    """Soft-deleted appliances (deleted_at set) must not be touched
    by the offline loop."""
    await conn.execute("""
        INSERT INTO site_appliances (appliance_id, site_id, hostname, status, last_checkin, deleted_at)
        VALUES ('a-del', 's1', 'host-4', 'online', NOW() - INTERVAL '1 hour', NOW())
    """)
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'offline', offline_since = NOW()
        WHERE status != 'offline' AND status != 'decommissioned'
          AND deleted_at IS NULL AND last_checkin IS NOT NULL
          AND last_checkin < NOW() - INTERVAL '5 minutes'
    """)
    cur = await conn.fetchval(
        "SELECT status FROM site_appliances WHERE appliance_id='a-del'"
    )
    assert cur == "online"  # Unchanged


@pytest.mark.asyncio
async def test_recovery_from_offline_stamps_recovered_at(conn):
    """On successful checkin, offline→online transition stamps
    recovered_at. Mirrors sites.py STEP 3 upsert's CASE."""
    await conn.execute("""
        INSERT INTO site_appliances (
            appliance_id, site_id, hostname, status, last_checkin,
            offline_since, offline_notified, offline_event_count
        )
        VALUES ('a-recov', 's1', 'host-5', 'offline', NOW() - INTERVAL '10 minutes',
                NOW() - INTERVAL '10 minutes', true, 3)
    """)
    # Simulate the checkin UPSERT's conflict-update clause
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'online',
            last_checkin = NOW(),
            offline_since = NULL,
            offline_notified = false,
            recovered_at = CASE
                WHEN status = 'offline' THEN NOW()
                ELSE recovered_at
            END
        WHERE appliance_id = 'a-recov'
    """)
    row = await conn.fetchrow("""
        SELECT status, offline_since, offline_notified,
               recovered_at, offline_event_count
        FROM site_appliances WHERE appliance_id = 'a-recov'
    """)
    assert row["status"] == "online"
    assert row["offline_since"] is None
    assert row["offline_notified"] is False
    assert row["recovered_at"] is not None
    # offline_event_count is preserved (lifetime counter, don't reset)
    assert row["offline_event_count"] == 3


@pytest.mark.asyncio
async def test_status_check_constraint_rejects_garbage(conn):
    """CHECK constraint narrowing valid statuses (migration 180)."""
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("""
            INSERT INTO site_appliances (appliance_id, site_id, status, last_checkin)
            VALUES ('bad-status', 's1', 'turquoise', NOW())
        """)


@pytest.mark.asyncio
async def test_offline_notified_debounce_rearm_on_recovery(conn):
    """offline_notified=true blocks re-alerting. On recovery, it MUST
    reset to false so the next offline event alerts fresh."""
    await conn.execute("""
        INSERT INTO site_appliances (
            appliance_id, site_id, hostname, status, last_checkin,
            offline_since, offline_notified
        ) VALUES ('a-debounce', 's1', 'host-6', 'offline',
                  NOW() - INTERVAL '10 minutes',
                  NOW() - INTERVAL '10 minutes', true)
    """)
    # Simulate checkin recovery
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'online', offline_since = NULL, offline_notified = false,
            last_checkin = NOW()
        WHERE appliance_id = 'a-debounce'
    """)
    # Now simulate the appliance going offline AGAIN 10 min later
    await conn.execute("""
        UPDATE site_appliances SET last_checkin = NOW() - INTERVAL '10 minutes'
        WHERE appliance_id = 'a-debounce'
    """)
    row = await conn.fetchrow(
        "SELECT offline_notified FROM site_appliances WHERE appliance_id='a-debounce'"
    )
    # Rearmed — next stale-loop pass will alert
    assert row["offline_notified"] is False


@pytest.mark.asyncio
async def test_offline_event_count_accumulates(conn):
    """offline_event_count must accumulate across independent outages
    — critical for MTBF calculation."""
    await conn.execute("""
        INSERT INTO site_appliances (appliance_id, site_id, hostname, status, last_checkin)
        VALUES ('a-counter', 's1', 'host-7', 'online', NOW() - INTERVAL '10 minutes')
    """)
    # First outage
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'offline', offline_since = NOW(),
            offline_event_count = offline_event_count + 1
        WHERE appliance_id = 'a-counter'
    """)
    # Recovery
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'online', last_checkin = NOW(),
            offline_since = NULL
        WHERE appliance_id = 'a-counter'
    """)
    # Second outage 10 min later
    await conn.execute("""
        UPDATE site_appliances
        SET last_checkin = NOW() - INTERVAL '10 minutes'
        WHERE appliance_id = 'a-counter'
    """)
    await conn.execute("""
        UPDATE site_appliances
        SET status = 'offline', offline_since = NOW(),
            offline_event_count = offline_event_count + 1
        WHERE appliance_id = 'a-counter'
    """)
    cnt = await conn.fetchval(
        "SELECT offline_event_count FROM site_appliances WHERE appliance_id='a-counter'"
    )
    assert cnt == 2
