"""
Session 206 M4: chaos phantom test.

Simulates the exact failure mode that made 2 appliances look online for 3
days, then verifies phantom_detector_loop (H4) catches it. If this test
ever regresses, someone has broken the orthogonal-verification layer and
we're back to trusting our own lying columns.

Scenario:
  1. Create a site with 1 genuinely-alive appliance (heartbeats + fresh
     last_checkin) and 1 phantom appliance (fresh last_checkin, NO
     heartbeats in the last 3+ min).
  2. Run one iteration of phantom_detector_loop.
  3. Assert that an APPLIANCE_LIVENESS_LIE audit log row was written for
     the phantom but NOT for the real appliance.
  4. Run a second iteration. Assert suppression kicks in — no duplicate
     alert within the suppression window.
"""

from __future__ import annotations
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Resolve mcp-server/ root so `dashboard_api` imports work when the test
# runner is invoked from backend/.
_MCP_SERVER_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_MCP_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_ROOT))


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="phantom_detector PG test requires TEST_DATABASE_URL",
)


@pytest.fixture
async def pool():
    """Connection pool to a test Postgres with the relevant schema loaded."""
    import asyncpg
    p = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


async def _seed_site_with_phantom(conn, site_id: str, now: datetime):
    """Create a site with:
      * REAL appliance  — fresh last_checkin + fresh heartbeat
      * PHANTOM appliance — fresh last_checkin + NO heartbeat (lies about liveness)
    """
    real_mac = "AA:BB:CC:DD:EE:01"
    phantom_mac = "AA:BB:CC:DD:EE:02"
    real_id = f"{site_id}-{real_mac}"
    phantom_id = f"{site_id}-{phantom_mac}"

    await conn.execute(
        """
        INSERT INTO sites (site_id, clinic_name) VALUES ($1, $2)
        ON CONFLICT (site_id) DO NOTHING
        """,
        site_id,
        f"test-{site_id}",
    )

    for aid, mac in [(real_id, real_mac), (phantom_id, phantom_mac)]:
        await conn.execute(
            """
            INSERT INTO site_appliances
                (site_id, appliance_id, hostname, mac_address, ip_addresses,
                 agent_version, status, first_checkin, last_checkin)
            VALUES ($1, $2, 'osiriscare', $3, '[]'::jsonb, '0.4.1',
                    'online', $4, $4)
            ON CONFLICT (appliance_id) DO UPDATE SET last_checkin = $4
            """,
            site_id,
            aid,
            mac,
            now,
        )

    # Only the real appliance gets a fresh heartbeat. The phantom doesn't.
    await conn.execute(
        """
        INSERT INTO appliance_heartbeats
            (site_id, appliance_id, observed_at, status, agent_version)
        VALUES ($1, $2, $3, 'online', '0.4.1')
        """,
        site_id,
        real_id,
        now,
    )
    return real_id, phantom_id


@pytest.mark.asyncio
async def test_phantom_detector_raises_liveness_lie_for_phantom_only(pool):
    """The real appliance (with a heartbeat) must NOT be flagged. The
    phantom (fresh last_checkin, no heartbeat) MUST be flagged."""
    from dashboard_api.background_tasks import phantom_detector_loop
    from dashboard_api.tenant_middleware import admin_connection

    site_id = f"test-phantom-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async with admin_connection(pool) as conn:
        real_id, phantom_id = await _seed_site_with_phantom(conn, site_id, now)

    # Run one iteration. Patch asyncio.sleep so we don't actually wait
    # 5 minutes between ticks; we only want the first iteration.
    sleep_calls = []

    async def _fake_sleep(n):
        sleep_calls.append(n)
        if len(sleep_calls) >= 2:  # initial + after-tick
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", _fake_sleep), \
         patch("dashboard_api.background_tasks._hb", MagicMock()), \
         patch("dashboard_api.email_alerts.send_critical_alert", MagicMock()):
        with pytest.raises(asyncio.CancelledError):
            await phantom_detector_loop()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT target FROM admin_audit_log
            WHERE action = 'APPLIANCE_LIVENESS_LIE'
              AND target IN ($1, $2)
              AND created_at > NOW() - INTERVAL '1 minute'
            """,
            real_id,
            phantom_id,
        )
    targets = {r["target"] for r in rows}
    assert phantom_id in targets, (
        "phantom_detector must raise APPLIANCE_LIVENESS_LIE for the phantom"
    )
    assert real_id not in targets, (
        "phantom_detector MUST NOT flag the real appliance — false positive"
    )


@pytest.mark.asyncio
async def test_phantom_detector_suppresses_duplicate_alerts(pool):
    """Within the 1-hour suppression window, a second iteration should
    NOT emit another alert for the same appliance. Prevents alert storms
    on persistent drift."""
    from dashboard_api.background_tasks import phantom_detector_loop
    from dashboard_api.tenant_middleware import admin_connection

    site_id = f"test-suppress-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async with admin_connection(pool) as conn:
        _, phantom_id = await _seed_site_with_phantom(conn, site_id, now)

    sleep_calls = []

    async def _fake_sleep(n):
        sleep_calls.append(n)
        if len(sleep_calls) >= 3:  # initial + 2 iterations
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", _fake_sleep), \
         patch("dashboard_api.background_tasks._hb", MagicMock()), \
         patch("dashboard_api.email_alerts.send_critical_alert", MagicMock()):
        with pytest.raises(asyncio.CancelledError):
            await phantom_detector_loop()

    async with admin_connection(pool) as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(*) FROM admin_audit_log
            WHERE action = 'APPLIANCE_LIVENESS_LIE'
              AND target = $1
              AND created_at > NOW() - INTERVAL '1 minute'
            """,
            phantom_id,
        )
    # Exactly ONE alert — suppression must prevent a second.
    assert n == 1, f"expected 1 alert (suppressed), got {n}"
