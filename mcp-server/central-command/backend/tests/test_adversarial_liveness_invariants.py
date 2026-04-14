"""
Session 206 D4: adversarial test battery for appliance liveness invariants.

These tests encode the attacker's-eye-view assumption that the system will
eventually regress. Each invariant we established in this session gets a
test that actively tries to violate it:

  * Migration 192 trigger — try a multi-row UPDATE, verify REJECT
  * Migration 191 heartbeats  — try to DELETE, verify REJECT
  * Migration 197 liveness_claims — try to DELETE, verify REJECT
  * heartbeat_hash — verify it's deterministic + stable across inserts
  * mesh ACK — verify an appliance can't ACK another's target

Gated by TEST_DATABASE_URL like the other PG integration tests. Safe to
skip locally; CI with a Postgres fixture runs them.
"""

from __future__ import annotations
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_MCP_SERVER_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_MCP_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_ROOT))


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="adversarial invariant tests require TEST_DATABASE_URL",
)


@pytest.fixture
async def pool():
    import asyncpg
    p = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


async def _seed_two_appliances(conn, site_id: str):
    await conn.execute(
        "INSERT INTO sites (site_id, clinic_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        site_id, f"adversarial-{site_id}",
    )
    for suffix in ("AA", "BB"):
        mac = f"CC:DD:EE:FF:00:{suffix}"
        aid = f"{site_id}-{mac}"
        await conn.execute(
            """
            INSERT INTO site_appliances
                (site_id, appliance_id, hostname, mac_address, ip_addresses,
                 agent_version, status, first_checkin, last_checkin)
            VALUES ($1, $2, 'osiriscare', $3, '[]'::jsonb, '0.4.1',
                    'online', NOW(), NOW())
            ON CONFLICT (appliance_id) DO UPDATE SET last_checkin = NOW()
            """,
            site_id, aid, mac,
        )


@pytest.mark.asyncio
async def test_attacker_cannot_site_wide_update_without_bulk_flag(pool):
    """Migration 192 trigger: attempting to UPDATE multiple rows per site
    without SET LOCAL app.allow_multi_row must fail."""
    from asyncpg.exceptions import RaiseError

    site_id = f"adv-bulk-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            with pytest.raises(Exception) as excinfo:
                await conn.execute(
                    """
                    UPDATE site_appliances
                    SET status = 'online'
                    WHERE site_id = $1
                    """,
                    site_id,
                )
            # Accept either RaiseError or generic exception carrying our message
            assert "Site-wide UPDATE" in str(excinfo.value) or "allow_multi_row" in str(excinfo.value), (
                f"Expected Migration 192 trigger rejection, got: {excinfo.value}"
            )


@pytest.mark.asyncio
async def test_bulk_flag_allows_intentional_site_wide_update(pool):
    """Legitimate bulk ops work when the SET LOCAL flag is present."""
    site_id = f"adv-bulk-ok-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.allow_multi_row = 'true'")
            # Must NOT raise.
            await conn.execute(
                """
                UPDATE site_appliances
                SET status = 'online'
                WHERE site_id = $1
                """,
                site_id,
            )


@pytest.mark.asyncio
async def test_heartbeats_delete_blocked(pool):
    """appliance_heartbeats is append-only. DELETE must be rejected."""
    site_id = f"adv-hb-delete-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)
        mac = "CC:DD:EE:FF:00:AA"
        aid = f"{site_id}-{mac}"
        await conn.execute(
            """
            INSERT INTO appliance_heartbeats (site_id, appliance_id, observed_at, status)
            VALUES ($1, $2, NOW(), 'online')
            """,
            site_id, aid,
        )

    async with pool.acquire() as conn:
        with pytest.raises(Exception) as excinfo:
            await conn.execute(
                """
                DELETE FROM appliance_heartbeats
                WHERE site_id = $1
                """,
                site_id,
            )
        assert "append-only" in str(excinfo.value), (
            f"Expected append-only trigger rejection, got: {excinfo.value}"
        )


@pytest.mark.asyncio
async def test_liveness_claims_delete_blocked(pool):
    """liveness_claims is append-only — evidence grade."""
    site_id = f"adv-claim-delete-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)
        mac = "CC:DD:EE:FF:00:AA"
        aid = f"{site_id}-{mac}"
        await conn.execute(
            """
            INSERT INTO liveness_claims (site_id, appliance_id, claim_type, details)
            VALUES ($1, $2, 'test', '{}'::jsonb)
            """,
            site_id, aid,
        )

    async with pool.acquire() as conn:
        with pytest.raises(Exception) as excinfo:
            await conn.execute(
                "DELETE FROM liveness_claims WHERE site_id = $1",
                site_id,
            )
        assert "append-only" in str(excinfo.value), (
            f"Expected append-only trigger rejection, got: {excinfo.value}"
        )


@pytest.mark.asyncio
async def test_heartbeat_hash_deterministic(pool):
    """Two heartbeats with the same identity (site+appliance+observed_at+status)
    produce the same heartbeat_hash. Determinism is the basis for the
    claim ledger — an auditor re-hashing from the raw inputs must get
    the same value."""
    site_id = f"adv-hash-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)
        mac = "CC:DD:EE:FF:00:AA"
        aid = f"{site_id}-{mac}"
        # Insert with an explicit observed_at so we can compare.
        observed_at = datetime.now(timezone.utc)
        id1 = await conn.fetchval(
            """
            INSERT INTO appliance_heartbeats (site_id, appliance_id, observed_at, status)
            VALUES ($1, $2, $3, 'online')
            RETURNING id
            """,
            site_id, aid, observed_at,
        )
        id2 = await conn.fetchval(
            """
            INSERT INTO appliance_heartbeats (site_id, appliance_id, observed_at, status)
            VALUES ($1, $2, $3, 'online')
            RETURNING id
            """,
            site_id, aid, observed_at,
        )
        h1 = await conn.fetchval(
            "SELECT heartbeat_hash FROM appliance_heartbeats WHERE id = $1", id1
        )
        h2 = await conn.fetchval(
            "SELECT heartbeat_hash FROM appliance_heartbeats WHERE id = $1", id2
        )
        assert h1 == h2, "heartbeat_hash must be deterministic"
        assert len(h1) == 64, "heartbeat_hash should be SHA-256 hex (64 chars)"


@pytest.mark.asyncio
async def test_appliance_cannot_ack_another_appliances_target(pool):
    """mesh_target_assignments.record_mesh_target_ack rejects ACKs from
    the wrong appliance — confirms only the assigned owner can extend TTL."""
    site_id = f"adv-ack-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_two_appliances(conn, site_id)
        aid_a = f"{site_id}-CC:DD:EE:FF:00:AA"
        aid_b = f"{site_id}-CC:DD:EE:FF:00:BB"
        await conn.execute(
            """
            INSERT INTO mesh_target_assignments
                (site_id, appliance_id, target_key, target_type)
            VALUES ($1, $2, 'target-1', 'device')
            """,
            site_id, aid_a,
        )

        # Appliance B tries to ACK a target owned by A — must return FALSE.
        acked_wrong = await conn.fetchval(
            "SELECT record_mesh_target_ack($1, $2, 'target-1', 'device')",
            site_id, aid_b,
        )
        assert acked_wrong is False, "Appliance B must not be able to ACK A's target"

        # A's legitimate ACK works.
        acked_right = await conn.fetchval(
            "SELECT record_mesh_target_ack($1, $2, 'target-1', 'device')",
            site_id, aid_a,
        )
        assert acked_right is True
