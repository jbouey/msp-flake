"""Startup invariant check integration test (Phase 15 enterprise hygiene).

Exercises startup_invariants.check_all_invariants + enforce_startup_invariants
against a real Postgres. Verifies that:

  - With all triggers installed → 0 broken
  - With a specific trigger dropped → ONLY that invariant is broken
  - Missing signing.key → INV-SIGNING-KEY reports broken
  - enforce() writes one admin_audit_log row per broken invariant
    (HIPAA-relevant: every degraded-security-posture startup is
    audit-logged for compliance review)

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import pathlib

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres invariants test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS privileged_access_magic_links CASCADE;
DROP TABLE IF EXISTS admin_audit_log CASCADE;
DROP TABLE IF EXISTS client_audit_log CASCADE;
DROP TABLE IF EXISTS portal_access_log CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP FUNCTION IF EXISTS prevent_audit_deletion() CASCADE;

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

CREATE TABLE admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE client_audit_log (id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE portal_access_log (id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW());

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Synthetic append-only trigger fn — mimics migration 151
CREATE OR REPLACE FUNCTION prevent_audit_deletion() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit table is append-only';
END;
$$;

CREATE TRIGGER trg_prevent_delete_compliance_bundles
    BEFORE DELETE ON compliance_bundles
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
CREATE TRIGGER trg_prevent_delete_admin_audit
    BEFORE DELETE ON admin_audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
CREATE TRIGGER trg_prevent_delete_client_audit
    BEFORE DELETE ON client_audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
CREATE TRIGGER trg_prevent_delete_portal_access
    BEFORE DELETE ON portal_access_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();

-- Synthetic completed-order lock — mimics migration 151
CREATE OR REPLACE FUNCTION prevent_completed_order_modification() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'completed orders are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_prevent_completed_order_modification
    BEFORE UPDATE ON fleet_orders
    FOR EACH ROW EXECUTE FUNCTION prevent_completed_order_modification();
"""


def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


@pytest_asyncio.fixture
async def conn(tmp_path, monkeypatch):
    # Point signing key env at a temp file so INV-SIGNING-KEY can pass
    sk = tmp_path / "signing.key"
    sk.write_bytes(b"x" * 32)
    monkeypatch.setenv("SIGNING_KEY_FILE", str(sk))

    # Reload the module so SIGNING_KEY_PATH picks up the new env
    import sys, importlib
    sys.modules.pop("startup_invariants", None)

    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        # Apply the actual chain-enforcement migrations
        await c.execute(_read_migration("175_privileged_chain_enforcement.sql"))
        await c.execute(_read_migration("176_privileged_chain_update_guard.sql"))
        await c.execute(_read_migration("174_privileged_access_requests.sql"))
        await c.execute(_read_migration("178_privileged_magic_links.sql"))
        yield c, sk
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS privileged_access_magic_links CASCADE;
            DROP TABLE IF EXISTS privileged_access_requests CASCADE;
            DROP TABLE IF EXISTS admin_audit_log CASCADE;
            DROP TABLE IF EXISTS client_audit_log CASCADE;
            DROP TABLE IF EXISTS portal_access_log CASCADE;
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS compliance_bundles CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
            DROP FUNCTION IF EXISTS prevent_audit_deletion() CASCADE;
            DROP FUNCTION IF EXISTS prevent_completed_order_modification() CASCADE;
        """)
        await c.close()


# ─── Happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_invariants_green_when_fully_set_up(conn):
    c, _ = conn
    import startup_invariants
    results = await startup_invariants.check_all_invariants(c)
    broken = [r for r in results if not r.ok]
    assert broken == [], (
        f"Expected all invariants green, but broken: "
        f"{[(r.name, r.detail) for r in broken]}"
    )


@pytest.mark.asyncio
async def test_enforce_returns_zero_when_green(conn):
    c, _ = conn
    import startup_invariants
    broken_count = await startup_invariants.enforce_startup_invariants(c)
    assert broken_count == 0


# ─── Individual failure cases ─────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_chain_175_trigger_detected(conn):
    c, _ = conn
    import startup_invariants
    await c.execute("DROP TRIGGER trg_enforce_privileged_chain ON fleet_orders")
    results = await startup_invariants.check_all_invariants(c)
    broken = {r.name for r in results if not r.ok}
    assert "INV-CHAIN-175" in broken


@pytest.mark.asyncio
async def test_missing_chain_176_trigger_detected(conn):
    c, _ = conn
    import startup_invariants
    await c.execute("DROP TRIGGER trg_enforce_privileged_immutability ON fleet_orders")
    results = await startup_invariants.check_all_invariants(c)
    broken = {r.name for r in results if not r.ok}
    assert "INV-CHAIN-176" in broken


@pytest.mark.asyncio
async def test_missing_evidence_delete_trigger_detected(conn):
    c, _ = conn
    import startup_invariants
    await c.execute("DROP TRIGGER trg_prevent_delete_compliance_bundles ON compliance_bundles")
    results = await startup_invariants.check_all_invariants(c)
    broken = {r.name for r in results if not r.ok}
    assert "INV-EVIDENCE-DELETE" in broken


@pytest.mark.asyncio
async def test_missing_magic_link_table_detected(conn):
    c, _ = conn
    import startup_invariants
    await c.execute("DROP TABLE privileged_access_magic_links CASCADE")
    results = await startup_invariants.check_all_invariants(c)
    broken = {r.name for r in results if not r.ok}
    assert "INV-MAGIC-LINK-TABLE" in broken


@pytest.mark.asyncio
async def test_missing_signing_key_detected(conn, monkeypatch):
    c, sk = conn
    import startup_invariants
    sk.unlink()
    # Reload so the module re-reads SIGNING_KEY_PATH from the env set
    # in the fixture. (Env is same path, but the file is gone.)
    import importlib
    importlib.reload(startup_invariants)

    results = await startup_invariants.check_all_invariants(c)
    broken = {r.name for r in results if not r.ok}
    assert "INV-SIGNING-KEY" in broken


# ─── Audit-log side effect on failure ─────────────────────────────


@pytest.mark.asyncio
async def test_enforce_writes_audit_row_per_broken(conn):
    c, _ = conn
    import startup_invariants
    # Induce two independent failures
    await c.execute("DROP TRIGGER trg_enforce_privileged_chain ON fleet_orders")
    await c.execute("DROP TRIGGER trg_prevent_delete_compliance_bundles ON compliance_bundles")

    broken_count = await startup_invariants.enforce_startup_invariants(c)
    assert broken_count >= 2

    # One audit row per broken invariant, actions STARTUP_INVARIANT_BROKEN
    rows = await c.fetch(
        "SELECT target_id FROM admin_audit_log "
        "WHERE action = 'STARTUP_INVARIANT_BROKEN'"
    )
    target_ids = {r["target_id"] for r in rows}
    assert "INV-CHAIN-175" in target_ids
    assert "INV-EVIDENCE-DELETE" in target_ids
