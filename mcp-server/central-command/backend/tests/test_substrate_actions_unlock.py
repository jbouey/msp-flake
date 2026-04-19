"""Tests for unlock_platform_account handler (Task 4).

Verifies:
  (a) partners row with failed_login_attempts=5 + locked_until gets reset
  (b) client_users row with failed_login_attempts=5 + locked_until gets reset
  (c) TargetRefInvalid raised when table is not in allowlist
  (d) TargetNotActionable raised when account is not currently locked

Gated by TEST_DATABASE_URL — safe to skip locally; CI with a Postgres
fixture runs them.
"""
from __future__ import annotations

import os
import secrets
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
    _handle_unlock_platform_account,
    TargetNotActionable,
    TargetRefInvalid,
)

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="unlock_platform_account integration tests require TEST_DATABASE_URL",
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
async def seed_locked_partner(pool):
    """Insert a partner row with failed_login_attempts=5 and locked_until set.

    Uses .invalid TLD so this email can never collide with real data.
    Slug and api_key_hash are randomised so parallel runs don't collide.
    """
    from tenant_middleware import admin_connection

    email = "substrate-unlock-test-partner@example.invalid"
    slug = f"sub-unlock-test-{secrets.token_hex(6)}"
    api_key_hash = secrets.token_hex(16)

    async with admin_connection(pool) as conn:
        # Pre-clean so fixture is idempotent if a prior run left debris.
        await conn.execute(
            "DELETE FROM partners WHERE contact_email = $1", email
        )
        await conn.execute(
            "INSERT INTO partners "
            "(name, slug, contact_email, api_key_hash, "
            " failed_login_attempts, locked_until) "
            "VALUES ($1, $2, $3, $4, $5, NOW() + INTERVAL '15 minutes')",
            "Substrate Unlock Test Partner",
            slug,
            email,
            api_key_hash,
            5,
        )

    yield {"email": email}

    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM partners WHERE contact_email = $1", email
        )


@pytest_asyncio.fixture
async def seed_unlocked_partner(pool):
    """Insert a partner row with failed_login_attempts=0 and locked_until=NULL."""
    from tenant_middleware import admin_connection

    email = "substrate-unlock-test-partner-ok@example.invalid"
    slug = f"sub-unlock-ok-{secrets.token_hex(6)}"
    api_key_hash = secrets.token_hex(16)

    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM partners WHERE contact_email = $1", email
        )
        await conn.execute(
            "INSERT INTO partners "
            "(name, slug, contact_email, api_key_hash, "
            " failed_login_attempts, locked_until) "
            "VALUES ($1, $2, $3, $4, $5, NULL)",
            "Substrate Unlock Test Partner OK",
            slug,
            email,
            api_key_hash,
            0,
        )

    yield {"email": email}

    async with admin_connection(pool) as conn:
        await conn.execute(
            "DELETE FROM partners WHERE contact_email = $1", email
        )


@pytest_asyncio.fixture
async def seed_locked_client_user(pool):
    """Insert a client_orgs row + a client_users row with locked account.

    client_users has a FK on client_org_id so we create the org first and
    tear it down (CASCADE deletes the user) on teardown.
    """
    from tenant_middleware import admin_connection

    email = "substrate-unlock-test-client@example.invalid"
    org_name = f"Substrate Unlock Test Org {secrets.token_hex(4)}"
    org_email = f"org-{secrets.token_hex(6)}@example.invalid"

    async with admin_connection(pool) as conn:
        # Pre-clean user row (org may not exist yet, ignore failure).
        await conn.execute(
            "DELETE FROM client_users WHERE email = $1", email
        )
        # Create org.
        org_id = await conn.fetchval(
            "INSERT INTO client_orgs (name, primary_email) "
            "VALUES ($1, $2) RETURNING id",
            org_name,
            org_email,
        )
        # Create locked user.
        await conn.execute(
            "INSERT INTO client_users "
            "(client_org_id, email, role, "
            " failed_login_attempts, locked_until) "
            "VALUES ($1, $2, 'viewer', $3, NOW() + INTERVAL '15 minutes')",
            org_id,
            email,
            5,
        )

    yield {"email": email, "org_id": org_id}

    async with admin_connection(pool) as conn:
        # CASCADE deletes client_users rows too.
        await conn.execute(
            "DELETE FROM client_orgs WHERE id = $1", org_id
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_partner_resets_counters(pool, seed_locked_partner):
    """Unlocking a locked partner resets both failed_login_attempts and locked_until."""
    from tenant_middleware import admin_connection

    email = seed_locked_partner["email"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            result = await _handle_unlock_platform_account(
                conn,
                {"table": "partners", "email": email},
                reason="Integration test: reset locked partner account",
            )

    assert result["table"] == "partners"
    assert result["email"] == email
    assert result["previous_failed_count"] == 5
    assert result["previous_locked_until"] is not None  # was set

    # Verify DB state was actually updated.
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT failed_login_attempts, locked_until "
            "FROM partners WHERE contact_email = $1",
            email,
        )
    assert row["failed_login_attempts"] == 0
    assert row["locked_until"] is None


@pytest.mark.asyncio
async def test_unlock_client_user_resets_counters(pool, seed_locked_client_user):
    """Unlocking a locked client_user resets both failed_login_attempts and locked_until."""
    from tenant_middleware import admin_connection

    email = seed_locked_client_user["email"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            result = await _handle_unlock_platform_account(
                conn,
                {"table": "client_users", "email": email},
                reason="Integration test: reset locked client user account",
            )

    assert result["table"] == "client_users"
    assert result["email"] == email
    assert result["previous_failed_count"] == 5
    assert result["previous_locked_until"] is not None

    # Verify DB state.
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT failed_login_attempts, locked_until "
            "FROM client_users WHERE email = $1",
            email,
        )
    assert row["failed_login_attempts"] == 0
    assert row["locked_until"] is None


@pytest.mark.asyncio
async def test_unlock_rejects_invalid_table(pool):
    """TargetRefInvalid raised when table is not in ALLOWED_UNLOCK_TABLES."""
    from tenant_middleware import admin_connection

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetRefInvalid):
                await _handle_unlock_platform_account(
                    conn,
                    {"table": "sites", "email": "someone@example.invalid"},
                    reason="Integration test: reject invalid table",
                )


@pytest.mark.asyncio
async def test_unlock_not_actionable_if_not_locked(pool, seed_unlocked_partner):
    """TargetNotActionable raised when the account is not currently locked."""
    from tenant_middleware import admin_connection

    email = seed_unlocked_partner["email"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotActionable):
                await _handle_unlock_platform_account(
                    conn,
                    {"table": "partners", "email": email},
                    reason="Integration test: expect not-actionable",
                )
