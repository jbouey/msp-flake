"""Pytest configuration for Central Command backend tests."""

import os
import secrets
import sys
import uuid

# Add the backend directory to the Python path so backend modules are
# importable from tests/ without relying on package layout.
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import asyncpg  # noqa: E402 — after sys.path mutation
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures for the substrate-action guardrail suite (Task 7).
#
# Individual substrate test files (test_substrate_actions_*.py) define their
# own same-named fixtures; pytest precedence means the local fixture wins,
# so these conftest versions only serve the new guardrail tests that
# parametrize across every handler in the SUBSTRATE_ACTIONS registry.
#
# NOT xdist-safe: fixed MAC + email prefix would collide across parallel
# workers. Run this suite with `-p no:xdist` or `-n 0` if distributed.
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

_GUARDRAIL_MAC = "aa:bb:cc:dd:ee:77"
_GUARDRAIL_SITE_ID = "test-substrate-guardrail"


@pytest_asyncio.fixture
async def pool():
    """Short-lived asyncpg pool for substrate guardrail tests."""
    if not _TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    p = await asyncpg.create_pool(_TEST_DB_URL, min_size=1, max_size=2)
    try:
        yield p
    finally:
        await p.close()


@pytest_asyncio.fixture
async def seed_stale_install_session(pool):
    """Insert one stale install_sessions row, yield target_ref dict, then clean up."""
    from tenant_middleware import admin_connection

    mac = _GUARDRAIL_MAC
    site_id = _GUARDRAIL_SITE_ID

    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            mac,
        )
        await conn.execute(
            "INSERT INTO install_sessions "
            "(session_id, site_id, mac_address, install_stage, checkin_count, "
            " first_seen, last_seen) "
            "VALUES ($1, $2, $3, $4, $5, "
            " NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour')",
            f"{site_id}:{mac}",
            site_id,
            mac,
            "live_usb",
            5,
        )

    yield {"mac": mac, "site_id": site_id}

    async with admin_connection(pool) as conn:
        try:
            await conn.execute(
                "DELETE FROM install_sessions WHERE mac_address = $1",
                mac,
            )
        except (asyncpg.InterfaceError, asyncpg.ClosedPoolError):
            pass  # pool may already be closing


@pytest_asyncio.fixture
async def seed_locked_partner(pool):
    """Insert a partner row with failed_login_attempts=5 and locked_until set."""
    from tenant_middleware import admin_connection

    email = f"substrate-guardrail-{secrets.token_hex(4)}@example.invalid"
    slug = f"guardrail-{secrets.token_hex(6)}"
    api_key_hash = secrets.token_hex(16)

    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM partners WHERE contact_email = $1", email,
        )
        await conn.execute(
            "INSERT INTO partners "
            "(name, slug, contact_email, api_key_hash, "
            " failed_login_attempts, locked_until) "
            "VALUES ($1, $2, $3, $4, $5, NOW() + INTERVAL '15 minutes')",
            "Substrate Guardrail Test Partner",
            slug,
            email,
            api_key_hash,
            5,
        )

    yield {"email": email}

    async with admin_connection(pool) as conn:
        try:
            await conn.execute(
                "DELETE FROM partners WHERE contact_email = $1", email,
            )
        except (asyncpg.InterfaceError, asyncpg.ClosedPoolError):
            pass


@pytest_asyncio.fixture
async def seed_active_fleet_order(pool):
    """Insert an active fleet_order of a non-privileged type for guardrail tests."""
    from tenant_middleware import admin_connection

    order_id = str(uuid.uuid4())

    async with admin_connection(pool) as conn:
        await conn.execute(
            "INSERT INTO fleet_orders "
            "(id, order_type, parameters, status, created_at, expires_at, created_by) "
            "VALUES ($1::uuid, $2, $3::jsonb, 'active', NOW(), "
            "        NOW() + INTERVAL '7 days', $4)",
            order_id,
            "run_drift",
            "{}",
            "substrate-guardrail-test-seed",
        )

    yield {
        "order_id": order_id,
        "order_type": "run_drift",
        "site_id": _GUARDRAIL_SITE_ID,
    }

    async with admin_connection(pool) as conn:
        try:
            await conn.execute(
                "DELETE FROM fleet_orders WHERE id = $1::uuid",
                order_id,
            )
        except (asyncpg.InterfaceError, asyncpg.ClosedPoolError):
            pass


@pytest.fixture
def happy_path_target_ref_for(
    seed_stale_install_session, seed_locked_partner, seed_active_fleet_order,
):
    """Maps action_key → happy-path target_ref dict for parametrized guardrail tests."""
    mapping = {
        "cleanup_install_session": {"mac": seed_stale_install_session["mac"]},
        "unlock_platform_account": {
            "table": "partners", "email": seed_locked_partner["email"],
        },
        "reconcile_fleet_order": {
            "order_id": seed_active_fleet_order["order_id"],
        },
    }
    return lambda action_key: mapping[action_key]
