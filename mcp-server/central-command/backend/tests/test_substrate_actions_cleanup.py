"""Tests for cleanup_install_session handler (Task 3).

Verifies:
  (a) successful delete of a seeded install_sessions row
  (b) TargetNotFound raised when no matching row exists
  (c) TargetRefInvalid raised when mac is missing or malformed

Gated by TEST_DATABASE_URL — safe to skip locally; CI with a Postgres
fixture runs them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from substrate_actions import (
    _handle_cleanup_install_session,
    TargetNotFound,
    TargetRefInvalid,
)

TEST_MAC = "11:22:33:44:55:66"
TEST_SITE_ID = "test-substrate-cleanup"

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="cleanup_install_session integration tests require TEST_DATABASE_URL",
)


@pytest_asyncio.fixture
async def pool():
    """Create a short-lived asyncpg pool for the test session."""
    p = await asyncpg.create_pool(_TEST_DB_URL, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def seed_stale_install_session(pool):
    """Insert one stale install_sessions row, yield target_ref dict, then clean up.

    Uses a deterministic test MAC that is unique enough to avoid collision
    with real appliance data. Pre-DELETEs by mac_address before INSERT so
    the fixture is idempotent when a prior test run left debris.
    """
    from tenant_middleware import admin_connection

    async with admin_connection(pool) as conn:
        # Idempotent pre-clean so the INSERT below never hits a PK collision.
        await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            TEST_MAC,
        )
        await conn.execute(
            "INSERT INTO install_sessions "
            "(session_id, site_id, mac_address, install_stage, checkin_count, "
            " first_seen, last_seen) "
            "VALUES ($1, $2, $3, $4, $5, "
            " NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour')",
            f"{TEST_SITE_ID}:{TEST_MAC}",
            TEST_SITE_ID,
            TEST_MAC,
            "live_usb",
            5,
        )

    yield {"mac": TEST_MAC, "site_id": TEST_SITE_ID}

    # Teardown — DELETE even if the test itself deleted the row (idempotent).
    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            TEST_MAC,
        )


@pytest.mark.asyncio
async def test_cleanup_install_session_deletes_one_row(pool, seed_stale_install_session):
    """Handler returns deleted=1, correct mac/stage, and the row is gone."""
    from tenant_middleware import admin_connection

    mac = seed_stale_install_session["mac"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            result = await _handle_cleanup_install_session(
                conn, {"mac": mac, "stage": "live_usb"}, reason=""
            )

    assert result["deleted"] == 1
    assert result["mac"] == mac
    assert result["stage"] == "live_usb"
    assert result["checkin_count"] == 5
    assert "first_seen" in result  # ISO string — existence check sufficient

    # Verify the row is truly gone.
    async with admin_connection(pool) as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM install_sessions WHERE mac_address = $1",
            mac,
        )
    assert n == 0


@pytest.mark.asyncio
async def test_cleanup_install_session_missing_row_raises_notfound(pool):
    """TargetNotFound raised when no row matches the given mac."""
    from tenant_middleware import admin_connection

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotFound):
                await _handle_cleanup_install_session(
                    conn, {"mac": "00:00:00:00:00:00", "stage": "live_usb"}, reason=""
                )


@pytest.mark.asyncio
async def test_cleanup_install_session_rejects_missing_mac(pool):
    """TargetRefInvalid raised when mac key is absent from target_ref."""
    from tenant_middleware import admin_connection

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetRefInvalid):
                await _handle_cleanup_install_session(conn, {}, reason="")
