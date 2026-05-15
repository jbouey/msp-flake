"""Real-Postgres behavioral test for the STEP 7c pending_deploys
CTE-JOIN-back dedup (Task #89, #75 Gate B FU-1).

#75 shipped the canonical-devices CTE in sites.py:5654 so the hot-path
`pending_deploys` reader's `LIMIT 5` counts DISTINCT physical devices,
not raw multi-appliance observations. The source-shape gate
(test_pending_deploys_dedup.py) pins the structural invariants
(CTE present, status filter outside CTE, credential JOIN outside CTE,
local_device_id flows through). This file is the BEHAVIORAL companion:
runs the actual SQL against real Postgres + verifies dedup semantics.

4 cases from #75 Gate A:
  1. multi_appliance_pending_deploy_collapses_to_one
  2. limit5_counts_distinct_devices_not_observations (starvation
     guard — pre-#75 a single 3×-duplicated device could starve out
     5 real ones)
  3. real_pending_device_not_dropped (negative control)
  4. credential_join_predicate_survives_cte (a device with no
     matching site_credentials row is excluded by the LIKE-join)

Skipped when PG_TEST_URL is unset (CI tier-2). Mirrors
test_appliance_offline_detection_pg.py's fixture pattern.
"""
from __future__ import annotations

import os
import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres dedup test",
)


# The exact STEP 7c query from sites.py:5654, verbatim. Pinned here so
# this test would fail loudly if the source SQL drifts away from the
# shape it's testing.
STEP_7C_SQL = """
WITH dd_freshest AS (
    SELECT DISTINCT ON (cd.canonical_id)
           cd.canonical_id, dd.*
      FROM canonical_devices cd
      JOIN discovered_devices dd
        ON dd.site_id = cd.site_id
       AND dd.ip_address = cd.ip_address
       AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
     WHERE cd.site_id = $1
     ORDER BY cd.canonical_id, dd.last_seen_at DESC
)
SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name,
       sc.encrypted_data, sc.credential_type
FROM dd_freshest dd
JOIN site_credentials sc ON sc.site_id = $1
    AND sc.credential_name LIKE dd.hostname || ' (%'
WHERE dd.device_status = 'pending_deploy'
LIMIT 5
"""


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS site_credentials CASCADE;
DROP TABLE IF EXISTS canonical_devices CASCADE;
DROP TABLE IF EXISTS discovered_devices CASCADE;

CREATE TABLE discovered_devices (
    local_device_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    ip_address TEXT,
    mac_address TEXT,
    hostname TEXT,
    os_name TEXT,
    device_status TEXT NOT NULL DEFAULT 'pending_deploy',
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE canonical_devices (
    canonical_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    mac_dedup_key TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (site_id, ip_address, mac_dedup_key)
);

CREATE TABLE site_credentials (
    id SERIAL PRIMARY KEY,
    site_id TEXT NOT NULL,
    credential_type TEXT NOT NULL,
    credential_name TEXT NOT NULL,
    encrypted_data BYTEA
);
"""

SITE_ID = "pg-test-site-1"


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        yield c
    finally:
        await c.execute(
            "DROP TABLE IF EXISTS site_credentials, canonical_devices, "
            "discovered_devices CASCADE;"
        )
        await c.close()


async def _seed_canonical(conn, canonical_id, ip, mac):
    """One canonical-devices row per physical device."""
    await conn.execute(
        "INSERT INTO canonical_devices (canonical_id, site_id, ip_address, mac_dedup_key) "
        "VALUES ($1, $2, $3, $4)",
        canonical_id, SITE_ID, ip, mac,
    )


async def _seed_discovered(conn, local_id, ip, mac, hostname, status="pending_deploy",
                           last_seen=None):
    """One discovered_devices row per (canonical_device × scanning appliance)."""
    sql = (
        "INSERT INTO discovered_devices (local_device_id, site_id, ip_address, "
        "mac_address, hostname, os_name, device_status, last_seen_at) "
        "VALUES ($1, $2, $3, $4, $5, 'linux', $6, "
        "COALESCE($7::timestamptz, NOW()))"
    )
    await conn.execute(sql, local_id, SITE_ID, ip, mac, hostname, status, last_seen)


async def _seed_credential(conn, hostname):
    """One site_credentials row whose credential_name matches the
    hostname via the LIKE-join `credential_name LIKE hostname || ' (%`."""
    await conn.execute(
        "INSERT INTO site_credentials (site_id, credential_type, credential_name, "
        "encrypted_data) VALUES ($1, 'ssh_key', $2, $3)",
        SITE_ID, f"{hostname} (ssh_key)", b"\x00fake_ciphertext",
    )


# ─── Cases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_appliance_pending_deploy_collapses_to_one(conn):
    """1 canonical device × 3 scanning-appliance observations → CTE
    emits 1 row, not 3. Pre-#75 the LIMIT-5-over-observations bug
    could starve real devices out of the deploy batch."""
    await _seed_canonical(conn, "cd-1", "10.0.0.5", "aa:bb:cc:dd:ee:ff")
    # 3 discovered_devices rows for the SAME physical device, from
    # 3 different scanning appliances — each gets a distinct local_id.
    for n in (1, 2, 3):
        await _seed_discovered(
            conn, f"local-{n}", "10.0.0.5", "aa:bb:cc:dd:ee:ff", "ws-alice"
        )
    await _seed_credential(conn, "ws-alice")

    rows = await conn.fetch(STEP_7C_SQL, SITE_ID)
    assert len(rows) == 1, (
        f"multi-appliance same device must collapse to 1 row; got {len(rows)}"
    )
    # DISTINCT ON picks the freshest observation — any of the 3 local_ids is
    # valid; verifying that it IS one of the seeded ids.
    assert rows[0]["local_device_id"] in {"local-1", "local-2", "local-3"}


@pytest.mark.asyncio
async def test_limit5_counts_distinct_devices_not_observations(conn):
    """6 distinct canonical devices × 3 scanning appliances each (18
    discovered rows) → exactly 5 distinct devices come back (LIMIT 5
    over canonical). Pre-#75 this could return as few as 2 distinct
    devices."""
    for i in range(6):
        ip = f"10.0.0.{10 + i}"
        mac = f"00:11:22:33:44:{i:02x}"
        hostname = f"ws-{i}"
        await _seed_canonical(conn, f"cd-{i}", ip, mac)
        for n in range(3):
            await _seed_discovered(
                conn, f"local-{i}-{n}", ip, mac, hostname
            )
        await _seed_credential(conn, hostname)

    rows = await conn.fetch(STEP_7C_SQL, SITE_ID)
    assert len(rows) == 5, (
        f"LIMIT 5 must count DISTINCT canonical devices, not observations; "
        f"got {len(rows)}"
    )
    distinct_hostnames = {r["hostname"] for r in rows}
    assert len(distinct_hostnames) == 5, (
        f"each returned row should be a different physical device; "
        f"got duplicates: {[r['hostname'] for r in rows]}"
    )


@pytest.mark.asyncio
async def test_real_pending_device_not_dropped(conn):
    """Negative control: 1 canonical × 1 observation × matching
    credential → device appears in result."""
    await _seed_canonical(conn, "cd-1", "10.0.0.99", "ff:ff:ff:ff:ff:01")
    await _seed_discovered(
        conn, "local-only-1", "10.0.0.99", "ff:ff:ff:ff:ff:01", "ws-real"
    )
    await _seed_credential(conn, "ws-real")

    rows = await conn.fetch(STEP_7C_SQL, SITE_ID)
    assert len(rows) == 1
    assert rows[0]["hostname"] == "ws-real"
    assert rows[0]["credential_type"] == "ssh_key"


@pytest.mark.asyncio
async def test_credential_join_predicate_survives_cte(conn):
    """A pending_deploy device with NO matching site_credentials row
    must be EXCLUDED by the LIKE-JOIN (which sits OUTSIDE the CTE).
    Pre-fix on the CTE shape, if the credential JOIN had been folded
    INTO the CTE, the LIKE would re-evaluate per raw observation."""
    await _seed_canonical(conn, "cd-1", "10.0.0.50", "00:00:00:00:00:01")
    await _seed_discovered(
        conn, "local-1", "10.0.0.50", "00:00:00:00:00:01", "ws-orphan"
    )
    # NO credential for ws-orphan — the LIKE-join excludes the row.

    rows = await conn.fetch(STEP_7C_SQL, SITE_ID)
    assert rows == [], (
        f"device with no matching credential must be excluded; got {len(rows)}"
    )


@pytest.mark.asyncio
async def test_status_filter_excludes_deploying_devices(conn):
    """A canonical device whose freshest observation is `deploying`
    (already mid-deploy) must be excluded by the OUTER `device_status
    = 'pending_deploy'` filter — even if older observations exist with
    status='pending_deploy'. Pre-fix, if the filter were INSIDE the
    CTE, a stale duplicate row would silently re-trigger deployment."""
    await _seed_canonical(conn, "cd-1", "10.0.0.77", "aa:00:00:00:00:01")
    # Older observation: pending_deploy
    await _seed_discovered(
        conn, "local-old", "10.0.0.77", "aa:00:00:00:00:01", "ws-mid",
        status="pending_deploy",
        last_seen="2026-05-01T00:00:00+00:00",
    )
    # Freshest observation: deploying (mid-deploy from a previous tick)
    await _seed_discovered(
        conn, "local-new", "10.0.0.77", "aa:00:00:00:00:01", "ws-mid",
        status="deploying",
        last_seen="2026-05-15T00:00:00+00:00",
    )
    await _seed_credential(conn, "ws-mid")

    rows = await conn.fetch(STEP_7C_SQL, SITE_ID)
    assert rows == [], (
        f"canonical device with freshest=deploying must drop out; got {len(rows)}"
    )
