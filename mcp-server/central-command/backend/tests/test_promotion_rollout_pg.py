"""Phase 15 closing — flywheel rollout end-to-end test.

Round-table audit found the 43 historical promoted_rules all show
deployment_count=0 because two of the three promotion paths
(learning_api admin-bulk + client_portal client-approve) bypassed
issue_sync_promoted_rule_orders. With this commit they call it.

This test proves the WHOLE feedback loop:

  1. Create a promoted_rule
  2. Call issue_sync_promoted_rule_orders → fleet_order INSERTed
  3. Insert fleet_order_completion with status='completed'
  4. Migration 163 trigger fires
  5. promoted_rules.deployment_count increments

If any step in that chain breaks, the flywheel goes blind. Tested
end-to-end against real Postgres.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres rollout test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS fleet_order_completions CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS promoted_rules CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP FUNCTION IF EXISTS track_promoted_rule_deployment() CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sites (site_id TEXT PRIMARY KEY);

CREATE TABLE promoted_rules (
    rule_id TEXT PRIMARY KEY,
    pattern_signature TEXT,
    site_id TEXT,
    partner_id TEXT,
    rule_yaml TEXT,
    rule_json TEXT,
    notes TEXT,
    promoted_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'active',
    deployment_count INTEGER DEFAULT 0,
    last_deployed_at TIMESTAMPTZ
);

CREATE TABLE fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type TEXT NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '1 day',
    created_by TEXT,
    nonce TEXT,
    signature TEXT,
    signed_payload TEXT
);

CREATE TABLE fleet_order_completions (
    fleet_order_id UUID NOT NULL REFERENCES fleet_orders(id) ON DELETE CASCADE,
    appliance_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fleet_order_id, appliance_id)
);
"""


def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


@pytest_asyncio.fixture
async def setup_signing(tmp_path, monkeypatch):
    """Real Ed25519 key on disk so sign_fleet_order can run."""
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed")
    sk = SigningKey.generate()
    p = tmp_path / "signing.key"
    p.write_bytes(sk.encode(encoder=HexEncoder))
    monkeypatch.setenv("SIGNING_KEY_FILE", str(p))
    monkeypatch.setenv("SIGNING_BACKEND", "file")
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", "")

    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend
    importlib.reload(signing_backend)
    signing_backend.reset_singleton()

    # main.py's sign_data is what order_signing reaches for. Patch it
    # to use the file backend directly so we don't need the full main
    # module (heavy FastAPI imports).
    import order_signing
    def _sign(data: str) -> str:
        result = signing_backend.get_signing_backend().sign(data.encode())
        return result.signature.hex()
    monkeypatch.setattr(order_signing, "_sign_order", lambda *a, **kw: (
        secrets.token_hex(16), _sign("test-payload-" + str(a[0])), "test-payload"
    ))


@pytest_asyncio.fixture
async def conn(setup_signing):
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        # Apply migration 163 — the trigger that bumps deployment_count
        await c.execute(_read_migration("163_promotion_deployment_trigger.sql"))
        yield c
    finally:
        await c.execute("""
            DROP TRIGGER IF EXISTS trg_track_promoted_rule_deployment ON fleet_order_completions;
            DROP FUNCTION IF EXISTS track_promoted_rule_deployment();
            DROP TABLE IF EXISTS fleet_order_completions CASCADE;
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS promoted_rules CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
        """)
        await c.close()


# ─── End-to-end: rollout creates orders, completion bumps counter ──


@pytest.mark.asyncio
async def test_full_rollout_loop(conn):
    """Promotion → order issuance → completion ack → counter increment."""
    from flywheel_promote import issue_sync_promoted_rule_orders

    # Seed: a site + a promoted_rules row at deployment_count=0
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-1')")
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id, rule_yaml) "
        "VALUES ('rule-test-1', 'site-1', 'id: rule-test-1\nrunbook_id: RB-X')"
    )

    # Step 1: issue the rollout order
    n = await issue_sync_promoted_rule_orders(
        conn,
        rule_id="rule-test-1",
        runbook_id="RB-X",
        rule_yaml="id: rule-test-1\nrunbook_id: RB-X",
        site_id="site-1",
        scope="site",
    )
    assert n == 1, f"expected 1 order created, got {n}"

    # Step 2: fleet_order row exists with correct parameters
    order = await conn.fetchrow(
        "SELECT id::text AS id, order_type, parameters FROM fleet_orders "
        "WHERE order_type = 'sync_promoted_rule'"
    )
    assert order is not None
    assert order["order_type"] == "sync_promoted_rule"
    params = json.loads(order["parameters"])
    assert params["rule_id"] == "rule-test-1"
    assert params["site_id"] == "site-1"
    assert params["runbook_id"] == "RB-X"

    # Step 3: deployment_count is still 0 (no completion yet)
    pre = await conn.fetchval(
        "SELECT deployment_count FROM promoted_rules WHERE rule_id = 'rule-test-1'"
    )
    assert pre == 0

    # Step 4: insert fleet_order_completion → trigger should fire
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status) "
        "VALUES ($1::uuid, $2, 'completed')",
        order["id"], "appliance-A",
    )

    # Step 5: deployment_count incremented + last_deployed_at populated
    post = await conn.fetchrow(
        "SELECT deployment_count, last_deployed_at FROM promoted_rules "
        "WHERE rule_id = 'rule-test-1'"
    )
    assert post["deployment_count"] == 1, (
        f"trigger didn't fire: deployment_count = {post['deployment_count']}"
    )
    assert post["last_deployed_at"] is not None


@pytest.mark.asyncio
async def test_failed_completion_does_not_increment(conn):
    """Trigger only fires for status='completed', NOT 'failed' or 'skipped'."""
    from flywheel_promote import issue_sync_promoted_rule_orders

    await conn.execute("INSERT INTO sites (site_id) VALUES ('s2')")
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id, rule_yaml) "
        "VALUES ('rule-fail', 's2', 'yaml')"
    )
    await issue_sync_promoted_rule_orders(
        conn, rule_id="rule-fail", runbook_id="RB-Y",
        rule_yaml="yaml", site_id="s2", scope="site",
    )
    order_id = await conn.fetchval(
        "SELECT id::text FROM fleet_orders WHERE order_type='sync_promoted_rule'"
    )
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status) "
        "VALUES ($1::uuid, 'app', 'failed')",
        order_id,
    )
    cnt = await conn.fetchval(
        "SELECT deployment_count FROM promoted_rules WHERE rule_id='rule-fail'"
    )
    assert cnt == 0, "failed completion should NOT increment deployment_count"


@pytest.mark.asyncio
async def test_non_sync_promoted_rule_orders_dont_increment(conn):
    """Trigger only fires for order_type='sync_promoted_rule', NOT
    other types like nixos_rebuild or update_daemon."""
    await conn.execute("INSERT INTO sites (site_id) VALUES ('s3')")
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id) VALUES ('rule-other', 's3')"
    )
    other_order_id = await conn.fetchval(
        "INSERT INTO fleet_orders (order_type, parameters) "
        "VALUES ('nixos_rebuild', '{\"rule_id\":\"rule-other\"}'::jsonb) "
        "RETURNING id::text"
    )
    await conn.execute(
        "INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status) "
        "VALUES ($1::uuid, 'app', 'completed')",
        other_order_id,
    )
    cnt = await conn.fetchval(
        "SELECT deployment_count FROM promoted_rules WHERE rule_id='rule-other'"
    )
    assert cnt == 0, "non-sync_promoted_rule order should NOT increment counter"


@pytest.mark.asyncio
async def test_multiple_completions_each_increment(conn):
    """Two appliances each ack the same fleet_order → counter goes to 2."""
    from flywheel_promote import issue_sync_promoted_rule_orders

    await conn.execute("INSERT INTO sites (site_id) VALUES ('s4')")
    await conn.execute(
        "INSERT INTO promoted_rules (rule_id, site_id, rule_yaml) "
        "VALUES ('rule-multi', 's4', 'y')"
    )
    await issue_sync_promoted_rule_orders(
        conn, rule_id="rule-multi", runbook_id="RB-M",
        rule_yaml="y", site_id="s4", scope="site",
    )
    order_id = await conn.fetchval(
        "SELECT id::text FROM fleet_orders WHERE order_type='sync_promoted_rule'"
    )
    for app in ("app-1", "app-2", "app-3"):
        await conn.execute(
            "INSERT INTO fleet_order_completions (fleet_order_id, appliance_id, status) "
            "VALUES ($1::uuid, $2, 'completed')",
            order_id, app,
        )
    cnt = await conn.fetchval(
        "SELECT deployment_count FROM promoted_rules WHERE rule_id='rule-multi'"
    )
    assert cnt == 3, f"expected 3 deployments (one per appliance), got {cnt}"
