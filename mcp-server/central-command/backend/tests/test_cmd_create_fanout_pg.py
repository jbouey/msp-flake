"""PG-fixture integration test for #118 fleet_cli.cmd_create fan-out
behavior — #126 closure (per audit/coach-126-cmd-create-fanout-pg-
gate-a-2026-05-16.md, APPROVE-WITH-FIXES, 4 narrowed tests).

Scope per Gate A:
  A — enumeration SQL (soft-delete filter + ORDER BY stability)
  C — mig 175 trigger ALLOWS N orders citing 1 bundle (1-bundle:N-
      orders shape that the fan-out exploits)
  D — mig 175 trigger REJECTS missing-bundle reference (canonical
      negative control)
  F — mig 175 trigger REJECTS cross-site bundle re-use (site-binding
      half of the chain-of-custody guarantee)

DEFERRED — B/E/G/H per Gate A "wrong test class" (covered by AST
gates / pure Python sys.exit / Postgres internals / pure Python).

Anti-scope (per Gate A bindings): does NOT invoke cmd_create;
does NOT mock signing key / attestation module / nonce prompt.
Tests the SQL + trigger contracts directly.

Loads the LATEST mig 175 function body via mig 305 (CREATE OR
REPLACE — last-write-wins per `CREATE OR REPLACE FUNCTION
enforce_privileged_order_attestation` chain: 175→218→223→305).

Skipped when PG_TEST_URL is unset (matches sibling pg-fixture tests).
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason=(
        "PG_TEST_URL not set — skipping live-Postgres cmd_create "
        "fan-out integration test"
    ),
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def _read_mig(filename: str) -> str:
    """Read a migration file and strip BEGIN/COMMIT wrappers (fixture
    runs in autocommit). Per Gate A: load from disk, NEVER inline-copy
    (copy = drift; the additive-only rule for trigger function body
    means in-test copies silently miss future mig updates)."""
    body = (_MIGRATIONS / filename).read_text(encoding="utf-8")
    # Strip top-level BEGIN; / COMMIT; — fixture uses autocommit conn.
    body = re.sub(r"^\s*BEGIN\s*;\s*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"^\s*COMMIT\s*;\s*$", "", body, flags=re.MULTILINE)
    return body


# Per Session 220 #77 PREREQ_SCHEMA DROP/CREATE pairing rule: every
# CREATE must have a matching DROP IF EXISTS earlier in the same
# string. Test #2 in a sweep run would otherwise hit DuplicateTable /
# DuplicateFunction.
PREREQ_SCHEMA = """
DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON fleet_orders CASCADE;
DROP FUNCTION IF EXISTS enforce_privileged_order_attestation() CASCADE;
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS site_appliances CASCADE;

CREATE TABLE site_appliances (
    appliance_id TEXT PRIMARY KEY,
    site_id      TEXT NOT NULL,
    mac_address  TEXT,
    hostname     TEXT,
    status       TEXT,
    last_checkin TIMESTAMPTZ,
    deleted_at   TIMESTAMPTZ
);

CREATE TABLE compliance_bundles (
    bundle_id  TEXT PRIMARY KEY,
    site_id    TEXT NOT NULL,
    check_type TEXT NOT NULL DEFAULT 'compliance'
);

CREATE TABLE fleet_orders (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type     TEXT NOT NULL,
    parameters     JSONB NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ,
    created_by     TEXT,
    nonce          TEXT,
    signature      TEXT,
    signed_payload TEXT,
    skip_version   TEXT,
    signing_method TEXT NOT NULL DEFAULT 'file'
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        # Install the LATEST trigger function body (mig 175 →...→ 305).
        # CREATE OR REPLACE chain means last-write-wins; loading 305
        # gives us the current production trigger behavior including
        # delegate_signing_key in v_privileged_types.
        for filename in (
            "175_privileged_chain_enforcement.sql",
            "218_privileged_types_watchdog.sql",
            "223_enable_recovery_shell_order_type.sql",
            "305_delegate_signing_key_privileged.sql",
        ):
            await c.execute(_read_mig(filename))
        yield c
    finally:
        await c.execute(
            "DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON "
            "fleet_orders CASCADE"
        )
        await c.execute(
            "DROP FUNCTION IF EXISTS enforce_privileged_order_attestation() "
            "CASCADE"
        )
        await c.execute("DROP TABLE IF EXISTS fleet_orders CASCADE")
        await c.execute("DROP TABLE IF EXISTS compliance_bundles CASCADE")
        await c.execute("DROP TABLE IF EXISTS site_appliances CASCADE")
        await c.close()


# ── Test A: enumeration SQL ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_enumeration_filters_soft_deleted_and_orders_by_appliance_id(
    conn,
):
    """Gate A test A: --all-at-site enumeration. Seeds 5 site_appliances
    rows (1 soft-deleted) → query returns 4 in stable appliance_id order.

    Catches: deleted_at predicate drop, ORDER BY drop, column rename."""
    site_id = "site-enum-test"
    appliance_ids = [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
        "55555555-5555-5555-5555-555555555555",
    ]
    for i, aid in enumerate(appliance_ids):
        deleted_at = "now()" if i == 2 else "NULL"
        await conn.execute(
            f"INSERT INTO site_appliances "
            f"(appliance_id, site_id, status, deleted_at) "
            f"VALUES ($1, $2, 'online', {deleted_at})",
            aid, site_id,
        )

    rows = await conn.fetch(
        """
        SELECT appliance_id, site_id, mac_address, hostname,
               status, last_checkin
          FROM site_appliances
         WHERE site_id = $1
           AND deleted_at IS NULL
         ORDER BY appliance_id
        """,
        site_id,
    )

    returned_ids = [r["appliance_id"] for r in rows]
    expected_ids = [appliance_ids[i] for i in (0, 1, 3, 4)]
    assert returned_ids == expected_ids, (
        f"Enumeration returned {returned_ids!r}; "
        f"expected {expected_ids!r} (soft-deleted excluded, "
        f"order by appliance_id stable)."
    )


# ── Test C: mig 175 ALLOWS 1-bundle:N-orders ─────────────────────


@pytest.mark.asyncio
async def test_c_trigger_allows_n_orders_citing_one_bundle(conn):
    """Gate A test C: write 1 privileged_access bundle for site-X,
    INSERT 3 fleet_orders each with distinct target_appliance_id in
    params, all citing the SAME attestation_bundle_id. Assert all 3
    land — the 1-bundle:N-orders shape that --all-at-site fan-out
    exploits via mig 175's EXISTS satisfiability."""
    site_id = "site-fan-out"
    bundle_id = "bundle-fan-out-test-001"

    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, $2, 'privileged_access')",
        bundle_id, site_id,
    )

    targets = [
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "cccccccc-cccc-cccc-cccc-cccccccccccc",
    ]
    for target in targets:
        await conn.execute(
            """
            INSERT INTO fleet_orders
              (order_type, parameters, status, expires_at, created_by,
               nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', now() + INTERVAL '24h',
                    'test@example.com', 'n', 's', 'p')
            """,
            "enable_emergency_access",
            (
                f'{{"site_id":"{site_id}",'
                f'"attestation_bundle_id":"{bundle_id}",'
                f'"target_appliance_id":"{target}"}}'
            ),
        )

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM fleet_orders "
        "WHERE parameters->>'attestation_bundle_id' = $1",
        bundle_id,
    )
    assert count == 3, (
        f"Expected 3 fleet_orders to share bundle_id {bundle_id!r}; "
        f"got {count}. mig 175 EXISTS check should allow 1-bundle:N-"
        f"orders. A future UNIQUE(attestation_bundle_id) regression "
        f"would silently break --all-at-site fan-out."
    )


# ── Test D: mig 175 REJECTS missing-bundle reference ─────────────


@pytest.mark.asyncio
async def test_d_trigger_rejects_missing_bundle(conn):
    """Gate A test D: privileged fleet_order citing a non-existent
    bundle_id is REJECTED with PRIVILEGED_CHAIN_VIOLATION. Canonical
    negative control for the chain-of-custody guarantee."""
    site_id = "site-no-bundle"
    nonexistent_bundle = "bundle-does-not-exist-999"

    with pytest.raises(asyncpg.RaiseError) as excinfo:
        await conn.execute(
            """
            INSERT INTO fleet_orders
              (order_type, parameters, status, expires_at, created_by,
               nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', now() + INTERVAL '24h',
                    'test@example.com', 'n', 's', 'p')
            """,
            "enable_emergency_access",
            (
                f'{{"site_id":"{site_id}",'
                f'"attestation_bundle_id":"{nonexistent_bundle}",'
                f'"target_appliance_id":"aaaaaaaa-aaaa-aaaa-aaaa-'
                f'aaaaaaaaaaaa"}}'
            ),
        )

    assert "PRIVILEGED_CHAIN_VIOLATION" in str(excinfo.value), (
        f"Expected error to contain literal 'PRIVILEGED_CHAIN_VIOLATION' "
        f"substring; got: {excinfo.value!r}. Per Gate A binding #4: "
        f"the literal-substring check pins the contract — a re-worded "
        f"error message would silently weaken auditor-tooling that "
        f"greps for the substring."
    )


@pytest.mark.asyncio
async def test_d2_trigger_rejects_missing_bundle_for_delegate_signing_key(
    conn,
):
    """Gate B P1-A: test D variant for `delegate_signing_key` order_type
    (added to v_privileged_types in mig 305). Defends against the
    most likely future regression class: someone re-extracts mig 305's
    function body but accidentally drops the delegate_signing_key
    entry from v_privileged_types. Mig 305 was the Session 220 fix
    for the zero-auth /delegate-key endpoint — losing privileged-chain
    gating on this order_type would re-open the original CVE class."""
    site_id = "site-no-bundle-d2"
    nonexistent_bundle = "bundle-does-not-exist-d2-999"

    with pytest.raises(asyncpg.RaiseError) as excinfo:
        await conn.execute(
            """
            INSERT INTO fleet_orders
              (order_type, parameters, status, expires_at, created_by,
               nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', now() + INTERVAL '24h',
                    'test@example.com', 'n', 's', 'p')
            """,
            "delegate_signing_key",
            (
                f'{{"site_id":"{site_id}",'
                f'"attestation_bundle_id":"{nonexistent_bundle}",'
                f'"target_appliance_id":"aaaaaaaa-aaaa-aaaa-aaaa-'
                f'aaaaaaaaaaaa"}}'
            ),
        )

    assert "PRIVILEGED_CHAIN_VIOLATION" in str(excinfo.value), (
        f"Expected delegate_signing_key (mig 305 addition) to also "
        f"raise PRIVILEGED_CHAIN_VIOLATION when citing a missing "
        f"bundle; got: {excinfo.value!r}. If this test fails after "
        f"a future mig that re-extracts the trigger function body, "
        f"the regression class is: delegate_signing_key dropped from "
        f"v_privileged_types — see Session 220 #4 lesson "
        f"(additive-only trigger body rule)."
    )


# ── Test F: mig 175 REJECTS cross-site bundle re-use ──────────────


@pytest.mark.asyncio
async def test_f_trigger_rejects_cross_site_bundle_reuse(conn):
    """Gate A test F: privileged fleet_order whose parameters.site_id
    differs from the bundle's site_id is REJECTED. Pins the
    site-binding half of mig 175's EXISTS check — the most subtle
    weakening path (drop AND site_id=$2 from the EXISTS subquery)."""
    site_a = "site-A-source"
    site_b = "site-B-attacker"
    bundle_id = "bundle-site-A-only"

    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, $2, 'privileged_access')",
        bundle_id, site_a,
    )

    with pytest.raises(asyncpg.RaiseError) as excinfo:
        await conn.execute(
            """
            INSERT INTO fleet_orders
              (order_type, parameters, status, expires_at, created_by,
               nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', now() + INTERVAL '24h',
                    'test@example.com', 'n', 's', 'p')
            """,
            "enable_emergency_access",
            (
                f'{{"site_id":"{site_b}",'
                f'"attestation_bundle_id":"{bundle_id}",'
                f'"target_appliance_id":"aaaaaaaa-aaaa-aaaa-aaaa-'
                f'aaaaaaaaaaaa"}}'
            ),
        )

    assert "PRIVILEGED_CHAIN_VIOLATION" in str(excinfo.value), (
        f"Expected cross-site bundle re-use to raise "
        f"PRIVILEGED_CHAIN_VIOLATION; got: {excinfo.value!r}. The "
        f"site-binding half of mig 175's EXISTS check is the most "
        f"subtle weakening path — a future migration dropping "
        f"`AND site_id = v_site_id` from the EXISTS subquery would "
        f"silently allow cross-site bundle re-use."
    )
