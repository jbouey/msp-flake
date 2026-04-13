"""End-to-end test of privileged-chain enforcement triggers
(migrations 175 + 176).

Why this exists (Phase 15 A-spec): the Session 205 migrations 175/176
shipped a plpgsql RAISE %% bug. The CREATE FUNCTION succeeded — bodies
are stored as text — and the bug only fired when the trigger was
exercised by a real INSERT, taking production down for 60 minutes.

A static "apply migrations against a fresh DB" smoke test does not
catch this class. You have to FIRE the trigger. This test does that.

Skipped automatically when PG_TEST_URL is unset, so local Pytest runs
without Postgres are unaffected. The CI workflow sets PG_TEST_URL to a
postgres:15 service container.
"""
from __future__ import annotations

import os
import uuid
import pathlib

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres chain-trigger test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PRIVILEGED_TYPES = [
    "enable_emergency_access",
    "disable_emergency_access",
    "bulk_remediation",
    "signing_key_rotation",
]


# Minimum prereq schema needed by migrations 175 + 176. We don't replay
# the full migration history (no init.sql committed for fresh-DB
# bootstrap); we recreate just the columns the triggers touch.
PREREQ_SCHEMA = """
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS sites CASCADE;

CREATE TABLE sites (site_id TEXT PRIMARY KEY);

CREATE TABLE fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type TEXT NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb,
    signed_payload BYTEA,
    signature BYTEA,
    nonce TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '1 day'
);

CREATE TABLE compliance_bundles (
    bundle_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    check_type TEXT NOT NULL
);

CREATE EXTENSION IF NOT EXISTS pgcrypto;
"""


def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


@pytest_asyncio.fixture
async def conn():
    """Per-test connection. Tears down + rebuilds the prereq schema and
    re-applies migrations 175+176 each time so each test starts from
    a clean slate."""
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        await c.execute(_read_migration("175_privileged_chain_enforcement.sql"))
        await c.execute(_read_migration("176_privileged_chain_update_guard.sql"))
        yield c
    finally:
        await c.execute("""
            DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON fleet_orders;
            DROP TRIGGER IF EXISTS trg_enforce_privileged_immutability ON fleet_orders;
            DROP FUNCTION IF EXISTS enforce_privileged_order_attestation();
            DROP FUNCTION IF EXISTS enforce_privileged_order_immutability();
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS compliance_bundles CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
        """)
        await c.close()


async def _insert_bundle(c, site_id: str, bundle_id: str | None = None) -> str:
    bundle_id = bundle_id or f"bundle-{uuid.uuid4().hex[:12]}"
    await c.execute(
        "INSERT INTO sites (site_id) VALUES ($1) ON CONFLICT DO NOTHING",
        site_id,
    )
    await c.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, $2, 'privileged_access')",
        bundle_id, site_id,
    )
    return bundle_id


# ─── Migration 175 — INSERT enforcement ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", PRIVILEGED_TYPES)
async def test_175_insert_privileged_without_bundle_id_rejected(conn, order_type):
    """Privileged INSERT with NO attestation_bundle_id in parameters
    must be rejected at the trigger layer."""
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) "
            "VALUES ($1, '{\"site_id\":\"site-x\"}'::jsonb)",
            order_type,
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)
    assert "attestation_bundle_id" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", PRIVILEGED_TYPES)
async def test_175_insert_privileged_with_unknown_bundle_id_rejected(conn, order_type):
    """Privileged INSERT pointing at a bundle that doesn't exist must
    be rejected — we can't have orphan attestation references."""
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) "
            "VALUES ($1, $2::jsonb)",
            order_type,
            '{"site_id":"site-x","attestation_bundle_id":"bundle-does-not-exist"}',
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", PRIVILEGED_TYPES)
async def test_175_insert_privileged_bundle_for_wrong_site_rejected(conn, order_type):
    """Privileged INSERT pointing at a bundle for a DIFFERENT site
    must be rejected. Without this check, a partner could attest on
    site-A and then issue a privileged order on site-B."""
    bundle_id = await _insert_bundle(conn, "site-a")
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) "
            "VALUES ($1, $2::jsonb)",
            order_type,
            f'{{"site_id":"site-b","attestation_bundle_id":"{bundle_id}"}}',
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", PRIVILEGED_TYPES)
async def test_175_insert_privileged_with_valid_bundle_succeeds(conn, order_type):
    """Privileged INSERT with a real, same-site bundle must succeed."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) "
        "VALUES ($1, $2::jsonb)",
        order_type,
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    cnt = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
    assert cnt == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", [
    "nixos_rebuild", "update_daemon", "run_drift", "restart_agent",
])
async def test_175_non_privileged_passes_through(conn, order_type):
    """Non-privileged orders must be unaffected by the trigger — no
    bundle required, no validation."""
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) "
        "VALUES ($1, '{}'::jsonb)",
        order_type,
    )
    cnt = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
    assert cnt == 1


# ─── Migration 176 — UPDATE immutability ─────────────────────────


@pytest.mark.asyncio
async def test_176_cannot_update_order_type_into_privileged(conn):
    """Cannot UPDATE order_type from non-privileged to privileged.
    If you could, the chain would have a backdated row that never
    went through the attestation flow."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('nixos_rebuild', '{}'::jsonb)"
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET order_type = 'enable_emergency_access' "
            "WHERE order_type = 'nixos_rebuild'"
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


@pytest.mark.asyncio
async def test_176_cannot_update_order_type_out_of_privileged(conn):
    """Cannot UPDATE order_type from privileged to non-privileged
    (would erase audit trail of the privileged action)."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET order_type = 'nixos_rebuild' "
            "WHERE order_type = 'enable_emergency_access'"
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


@pytest.mark.asyncio
async def test_176_cannot_update_attestation_bundle_id(conn):
    """Cannot UPDATE parameters->>'attestation_bundle_id' on a privileged
    row — the bundle reference is the chain anchor."""
    bundle_id = await _insert_bundle(conn, "site-a")
    other_bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET parameters = jsonb_set("
            "parameters, '{attestation_bundle_id}', to_jsonb($1::text)"
            ") WHERE order_type = 'enable_emergency_access'",
            other_bundle_id,
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)
    assert "attestation_bundle_id" in str(exc.value)


@pytest.mark.asyncio
async def test_176_cannot_update_site_id(conn):
    """Cannot UPDATE parameters->>'site_id' on a privileged row."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET parameters = jsonb_set("
            "parameters, '{site_id}', '\"site-b\"'::jsonb"
            ") WHERE order_type = 'enable_emergency_access'"
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)
    assert "site_id" in str(exc.value)


@pytest.mark.asyncio
async def test_176_cannot_update_signed_payload(conn):
    """signed_payload + signature + nonce are the cryptographic core.
    Can't be UPDATEd on a privileged row."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters, signed_payload) "
        "VALUES ('enable_emergency_access', $1::jsonb, '\\x01'::bytea)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET signed_payload = '\\x02'::bytea "
            "WHERE order_type = 'enable_emergency_access'"
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


@pytest.mark.asyncio
async def test_176_can_update_status_to_cancelled(conn):
    """Cancelling a privileged order via status='cancelled' must STILL
    work — that's the documented operator escape hatch. Verify the
    trigger doesn't accidentally lock all writes."""
    bundle_id = await _insert_bundle(conn, "site-a")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )
    await conn.execute(
        "UPDATE fleet_orders SET status = 'cancelled' "
        "WHERE order_type = 'enable_emergency_access'"
    )
    status = await conn.fetchval(
        "SELECT status FROM fleet_orders WHERE order_type = 'enable_emergency_access'"
    )
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_176_non_privileged_freely_updateable(conn):
    """Non-privileged orders are unaffected by the immutability trigger."""
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters, signed_payload) "
        "VALUES ('nixos_rebuild', '{}'::jsonb, '\\x01'::bytea)"
    )
    await conn.execute(
        "UPDATE fleet_orders SET parameters = '{\"changed\":true}'::jsonb, "
        "signed_payload = '\\x02'::bytea WHERE order_type = 'nixos_rebuild'"
    )
    p = await conn.fetchval("SELECT parameters->>'changed' FROM fleet_orders")
    assert p == "true"


# ─── Regression guard — the bug that took us down ─────────────────


@pytest.mark.asyncio
async def test_regression_session_205_raise_format_works(conn):
    """The Session 205 outage was caused by `%%` in plpgsql RAISE
    format strings — RAISE expects a single `%`. The CREATE FUNCTION
    succeeded but every trigger-fire produced
    "too many parameters specified for RAISE".

    This test exercises the trigger with a violation. If anyone
    re-introduces `%%`, the error message will be the parser's
    unhelpful "too many parameters specified for RAISE" instead of
    our intended PRIVILEGED_CHAIN_VIOLATION text — and we'll catch it.
    """
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) "
            "VALUES ('enable_emergency_access', '{\"site_id\":\"x\"}'::jsonb)"
        )
    msg = str(exc.value)
    assert "PRIVILEGED_CHAIN_VIOLATION" in msg, (
        f"Trigger fired but message was wrong (regression?): {msg}"
    )
    assert "too many parameters specified for RAISE" not in msg, (
        "Session 205 %% bug regressed — trigger uses literal % in RAISE format"
    )
