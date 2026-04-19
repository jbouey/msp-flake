"""Tests for reconcile_fleet_order handler (Task 5).

Verifies:
  (a) active fleet_order of a non-privileged type is marked completed
  (b) already-completed fleet_order raises TargetNotActionable
  (c) privileged order_type raises TargetRefInvalid (unit-mock test — no
      real INSERT needed; seeding watchdog/emergency types in the test DB
      is blocked by migration 175 for the 4 types in its v_privileged_types
      array, but the fleet_cli.PRIVILEGED_ORDER_TYPES set has 9 members.
      We use a mock conn instead of a real INSERT for maximum portability.)
  (d) non-existent order_id raises TargetNotFound

Schema note:
  fleet_orders has NO site_id column (migration 049 — fleet-wide table).
  No completed_at or result column. Only status is written by this handler.
  Per-appliance ack rows live in fleet_order_completions.

Migration 175 note:
  trg_enforce_privileged_chain fires BEFORE INSERT on fleet_orders and
  blocks the 4 types in its v_privileged_types array
  ('enable_emergency_access', 'disable_emergency_access',
  'bulk_remediation', 'signing_key_rotation'). The 9-member
  fleet_cli.PRIVILEGED_ORDER_TYPES set also includes watchdog_* types
  and 'enable_recovery_shell_24h' which are NOT in migration 175's list
  (migration 218 extended the lockstep — the test uses the larger set).
  The privileged-type test (c) uses a mock conn to avoid any INSERT
  ceremony regardless of which migration version is active.

Gated by TEST_DATABASE_URL — safe to skip locally; CI with a Postgres
fixture runs them.
"""
from __future__ import annotations

import os
import secrets
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from substrate_actions import (
    _handle_reconcile_fleet_order,
    TargetNotActionable,
    TargetNotFound,
    TargetRefInvalid,
)

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="reconcile_fleet_order integration tests require TEST_DATABASE_URL",
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
async def seed_active_order(pool):
    """Insert an active fleet_order of a non-privileged type (run_drift).

    Uses a far-future expires_at so the row is always 'active' during the
    test window.  Cleaned up in teardown.
    """
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
            "substrate-reconcile-test-seed",
        )

    try:
        yield {"order_id": order_id, "order_type": "run_drift"}
    finally:
        async with admin_connection(pool) as conn:
            try:
                # Skip if already completed by the test — trigger blocks
                # further UPDATE; DELETE is fine (no DELETE trigger on
                # fleet_orders in migration 151, only fleet_order_completions
                # has a no-delete trigger).
                await conn.execute(
                    "DELETE FROM fleet_orders WHERE id = $1::uuid",
                    order_id,
                )
            except Exception:
                pass  # pool may already be closing


@pytest_asyncio.fixture
async def seed_completed_order(pool):
    """Insert an already-completed fleet_order."""
    from tenant_middleware import admin_connection

    order_id = str(uuid.uuid4())

    async with admin_connection(pool) as conn:
        await conn.execute(
            "INSERT INTO fleet_orders "
            "(id, order_type, parameters, status, created_at, expires_at, created_by) "
            "VALUES ($1::uuid, $2, $3::jsonb, 'completed', NOW(), "
            "        NOW() + INTERVAL '7 days', $4)",
            order_id,
            "force_checkin",
            "{}",
            "substrate-reconcile-test-seed",
        )

    try:
        yield {"order_id": order_id}
    finally:
        async with admin_connection(pool) as conn:
            try:
                await conn.execute(
                    "DELETE FROM fleet_orders WHERE id = $1::uuid",
                    order_id,
                )
            except Exception:
                pass  # pool may already be closing


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_marks_active_order_completed(pool, seed_active_order):
    """Active fleet_order is marked completed; return dict is correct."""
    from tenant_middleware import admin_connection

    order_id = seed_active_order["order_id"]
    order_type = seed_active_order["order_type"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            result = await _handle_reconcile_fleet_order(
                conn,
                {"order_id": order_id},
                reason="Integration test: reconcile stalled active order",
            )

    assert result["order_id"] == order_id
    assert result["order_type"] == order_type
    assert result["prev_status"] == "active"

    # Verify DB state was actually updated.
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM fleet_orders WHERE id = $1::uuid",
            order_id,
        )
    assert row is not None
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_rejects_completed_order(pool, seed_completed_order):
    """TargetNotActionable raised when fleet_order is already completed."""
    from tenant_middleware import admin_connection

    order_id = seed_completed_order["order_id"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotActionable, match="already completed"):
                await _handle_reconcile_fleet_order(
                    conn,
                    {"order_id": order_id},
                    reason="Integration test: expect not-actionable for completed order",
                )


# ---------------------------------------------------------------------------
# Test (c): privileged order type — UNIT TEST (no real DB INSERT)
#
# We use a mock conn instead of a real INSERT because:
#   - migration 175's trg_enforce_privileged_chain blocks 4 of the 9
#     privileged types unless a matching compliance_bundles attestation
#     row exists (impossible to satisfy cheaply in a test).
#   - The other 5 watchdog types ARE insertable, but using them would
#     require knowing exactly which migration version is active on the test
#     DB.  A mock conn is simpler, portable, and still exercises the
#     handler logic (the privileged-type check happens in Python, not SQL).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_rejects_privileged_order_type():
    """TargetRefInvalid raised when order_type is in PRIVILEGED_ORDER_TYPES.

    Uses a mock asyncpg connection — no real INSERT into fleet_orders.
    The privileged-type guard is a Python-level check in the handler,
    so no DB is needed to exercise it.
    """
    privileged_type = "enable_emergency_access"  # member of PRIVILEGED_ORDER_TYPES

    mock_row = asyncpg.Record  # just for type reference — won't be called
    fake_order_id = str(uuid.uuid4())

    # Build a minimal mock connection whose fetchrow returns a row with the
    # privileged order_type.
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(
        return_value={
            "id": uuid.UUID(fake_order_id),
            "order_type": privileged_type,
            "status": "active",
        }
    )

    with pytest.raises(TargetRefInvalid, match="privileged"):
        await _handle_reconcile_fleet_order(
            mock_conn,
            {"order_id": fake_order_id},
            reason="Integration test: expect refusal for privileged order type",
        )

    # execute should never be called — handler must bail before the UPDATE.
    mock_conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_not_found_raises_notfound(pool):
    """TargetNotFound raised for a well-formed UUID with no matching row."""
    from tenant_middleware import admin_connection

    nonexistent_id = str(uuid.uuid4())

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            with pytest.raises(TargetNotFound, match=nonexistent_id):
                await _handle_reconcile_fleet_order(
                    conn,
                    {"order_id": nonexistent_id},
                    reason="Integration test: expect not-found for missing order",
                )
