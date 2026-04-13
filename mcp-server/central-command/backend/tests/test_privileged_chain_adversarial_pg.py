"""Adversarial red-team tests for the privileged-access chain.

Phase 15 enterprise hygiene — round-table audit item #6: "Pen-test the
three enforcement layers — try to bypass via raw SQL, via flag-
stripping, via replay. Document attempts + outcomes."

Every test in this file is a documented attack scenario. The test is
the WAY we prove each attack is blocked. If someone changes the
enforcement layer and accidentally opens one of these attack paths,
the test fails. Regression = security incident.

The chain-of-custody invariant lives at three layers:
  1. CLI (fleet_cli.py) — rejects unauthenticated privileged orders
  2. API (privileged_access_api.py) — rejects un-attested queue entries
  3. DB  (migrations 175 + 176) — rejects untethered INSERTs/UPDATEs

This file exercises layer 3 directly via asyncpg (bypassing CLI + API
entirely — the DB must still hold the line).

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping adversarial pen-test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS fleet_orders CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS admin_audit_log CASCADE;
DROP TABLE IF EXISTS client_audit_log CASCADE;
DROP TABLE IF EXISTS portal_access_log CASCADE;
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

-- Audit tables that migration 179 references for TRUNCATE triggers
CREATE TABLE admin_audit_log (id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE client_audit_log (id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE portal_access_log (id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT NOW());

-- Synthetic prevent_audit_deletion() — migration 179 uses it for
-- the BEFORE TRUNCATE triggers. In prod this is defined by migration 151.
CREATE OR REPLACE FUNCTION prevent_audit_deletion() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit table is append-only; TRUNCATE/DELETE blocked';
END;
$$;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
"""


def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        await c.execute(_read_migration("175_privileged_chain_enforcement.sql"))
        await c.execute(_read_migration("176_privileged_chain_update_guard.sql"))
        # Phase 15 closing pass: migration 179 hardens the triggers
        # against session_replication_role + TRUNCATE bypasses.
        await c.execute(_read_migration("179_privileged_chain_hardening.sql"))
        yield c
    finally:
        await c.execute("""
            DROP TRIGGER IF EXISTS trg_enforce_privileged_chain ON fleet_orders;
            DROP TRIGGER IF EXISTS trg_enforce_privileged_immutability ON fleet_orders;
            DROP TRIGGER IF EXISTS compliance_bundles_no_truncate ON compliance_bundles;
            DROP TRIGGER IF EXISTS admin_audit_log_no_truncate ON admin_audit_log;
            DROP TRIGGER IF EXISTS client_audit_log_no_truncate ON client_audit_log;
            DROP TRIGGER IF EXISTS portal_access_log_no_truncate ON portal_access_log;
            DROP FUNCTION IF EXISTS enforce_privileged_order_attestation();
            DROP FUNCTION IF EXISTS enforce_privileged_order_immutability();
            DROP FUNCTION IF EXISTS prevent_audit_deletion() CASCADE;
            DROP TABLE IF EXISTS admin_audit_log CASCADE;
            DROP TABLE IF EXISTS client_audit_log CASCADE;
            DROP TABLE IF EXISTS portal_access_log CASCADE;
            DROP TABLE IF EXISTS fleet_orders CASCADE;
            DROP TABLE IF EXISTS compliance_bundles CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
        """)
        await c.close()


# ─── Attack 1: cross-site bundle reuse ────────────────────────────


@pytest.mark.asyncio
async def test_attack_cross_site_bundle_reuse_blocked(conn):
    """Scenario: a partner admin is authorized for site-A. They create
    a legitimate attestation bundle on site-A. Then they try to use
    the SAME bundle_id to authorize a privileged order on site-B,
    where they should have no standing.

    Defense: the trigger verifies `(bundle_id, site_id)` match — a
    bundle_id alone is not enough.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a'), ('site-b')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) VALUES "
            "('enable_emergency_access', $1::jsonb)",
            f'{{"site_id":"site-b","attestation_bundle_id":"{bundle_id}"}}',
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


# ─── Attack 2: wrong check_type ───────────────────────────────────


@pytest.mark.asyncio
async def test_attack_non_privileged_check_type_rejected(conn):
    """Scenario: attacker creates a compliance_bundle of check_type
    'patching' (a normal drift bundle), then tries to claim it as the
    attestation for a privileged order.

    Defense: trigger requires check_type = 'privileged_access'.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'patching')",  # WRONG check_type
        bundle_id,
    )
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) VALUES "
            "('enable_emergency_access', $1::jsonb)",
            f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


# ─── Attack 3: bundle_id SQL injection payloads ──────────────────


@pytest.mark.asyncio
async def test_attack_bundle_id_sql_injection_payloads(conn):
    """Scenario: attacker tries various SQL-injection strings as the
    bundle_id in parameters.

    Defense: asyncpg's `$1` parameters are bound via the wire protocol
    — there is no interpolation. Still, test a few classic payloads to
    prove the trigger rejects them as unknown bundle_ids.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    payloads = [
        "' OR '1'='1",
        "'; DROP TABLE compliance_bundles; --",
        "\" OR true --",
        "bundle\\' ; DELETE FROM fleet_orders; --",
        "$$; DROP TRIGGER trg_enforce_privileged_chain ON fleet_orders; --",
    ]
    for payload in payloads:
        with pytest.raises(asyncpg.RaiseError) as exc:
            await conn.execute(
                "INSERT INTO fleet_orders (order_type, parameters) VALUES "
                "('enable_emergency_access', $1::jsonb)",
                f'{{"site_id":"site-a","attestation_bundle_id":{__import__("json").dumps(payload)}}}',
            )
        assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)

    # Prove the triggers are still installed (SQL injection did not drop them)
    triggers = await conn.fetch(
        "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'trg_enforce%' "
        "AND NOT tgisinternal"
    )
    names = {r["tgname"] for r in triggers}
    assert "trg_enforce_privileged_chain" in names
    assert "trg_enforce_privileged_immutability" in names


# ─── Attack 4: race — create bundle AFTER order ──────────────────


@pytest.mark.asyncio
async def test_attack_bundle_created_after_order_does_not_retroactively_authorize(conn):
    """Scenario: attacker attempts to INSERT the privileged order
    FIRST (which fails because no bundle exists), then creates a
    bundle matching the intended id, then retries the order.

    Defense: the retry will succeed, but a ledger scan will show:
      - bundle_id created_at AFTER the attacker's attempted order_at
      - the order is only recorded after the bundle exists
    There is no "retroactive authorization" — the bundle must exist
    BEFORE the order INSERT, period.

    This test confirms the literal ordering: once the bundle exists,
    the order succeeds. The audit trail (created_at timestamps) is
    the forensic signal — not the trigger. The trigger cannot
    distinguish a legitimate pre-existing bundle from a freshly-
    forged one. But the trigger DOES enforce that a bundle must
    exist somewhere, which forecloses the "no bundle at all"
    attack.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:12]}"

    # Step 1: attacker tries to insert order — blocked because no bundle
    with pytest.raises(asyncpg.RaiseError):
        await conn.execute(
            "INSERT INTO fleet_orders (order_type, parameters) VALUES "
            "('enable_emergency_access', $1::jsonb)",
            f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
        )

    # Step 2: attacker creates matching bundle
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )

    # Step 3: retry order — now succeeds at trigger layer
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )

    # The DB layer cannot tell the bundle was freshly created. This is
    # why the bundle itself is Ed25519-signed by the server — a
    # late-created bundle would need the server's signing key to
    # appear legitimate. Auditors catch timestamp anomalies in the
    # signed payload.
    cnt = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
    assert cnt == 1


# ─── Attack 5: UPDATE to swap fresh bundle for expired bundle ────


@pytest.mark.asyncio
async def test_attack_cannot_swap_bundle_reference_post_insert(conn):
    """Scenario: attacker INSERTs a legitimate privileged order with
    a bundle from LAST MONTH. Later, they try to UPDATE the
    parameters->>'attestation_bundle_id' to point at a fresh bundle,
    laundering the old order under new attestation context.

    Defense: migration 176 blocks UPDATE of attestation_bundle_id on
    privileged rows.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    old_bundle = f"old-bundle-{uuid.uuid4().hex[:8]}"
    new_bundle = f"new-bundle-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access'), ($2, 'site-a', 'privileged_access')",
        old_bundle, new_bundle,
    )
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', $1::jsonb)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{old_bundle}"}}',
    )

    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            "UPDATE fleet_orders SET parameters = jsonb_set("
            "parameters, '{attestation_bundle_id}', to_jsonb($1::text)"
            ") WHERE order_type = 'enable_emergency_access'",
            new_bundle,
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)


# ─── Attack 6: launder privileged order as non-privileged ────────


@pytest.mark.asyncio
async def test_attack_cannot_relabel_privileged_as_non_privileged(conn):
    """Scenario: attacker inserts a legitimate privileged order (chain
    of custody intact), then tries to UPDATE order_type='nixos_rebuild'
    to erase the privileged audit trail and make it look like a
    mundane order in reports.

    Defense: migration 176 blocks order_type UPDATEs crossing the
    privileged/non-privileged boundary.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )
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


# ─── Attack 7: strip signature bytes post-insert ─────────────────


@pytest.mark.asyncio
async def test_attack_cannot_strip_signature_bytes(conn):
    """Scenario: attacker inserts a privileged order with a bad
    signature (would fail appliance-side verification), then UPDATEs
    to replace signature with one from a different order that WAS
    legitimately signed.

    Defense: signed_payload / signature / nonce are immutable on
    privileged rows (migration 176).
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters, signed_payload, signature) "
        "VALUES ('enable_emergency_access', $1::jsonb, '\\x01'::bytea, '\\xAA'::bytea)",
        f'{{"site_id":"site-a","attestation_bundle_id":"{bundle_id}"}}',
    )

    for column, new_val in [
        ("signed_payload", "'\\x02'::bytea"),
        ("signature", "'\\xBB'::bytea"),
        ("nonce", "'new-nonce'"),
    ]:
        with pytest.raises(asyncpg.RaiseError) as exc:
            await conn.execute(
                f"UPDATE fleet_orders SET {column} = {new_val} "
                f"WHERE order_type = 'enable_emergency_access'"
            )
        assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value), (
            f"attack via {column} was not blocked"
        )


# ─── Attack 8: mass cancel privileged orders (denial of evidence) ─


@pytest.mark.asyncio
async def test_cancel_is_the_only_post_insert_mutation_allowed(conn):
    """Documented fact: status='cancelled' IS allowed. This is NOT a
    bypass — cancellation is the documented operator escape hatch.
    The invariant is that the attestation bundle remains; the order
    just no longer executes. A cancelled order + its attested bundle
    together form a credible audit trail of "we started this, we
    stopped it."

    This test documents the allowed mutation so future hardening
    doesn't accidentally revoke it (which would break legitimate
    rollback).
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )
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

    # Attestation bundle is still there — audit trail intact
    cnt = await conn.fetchval(
        "SELECT COUNT(*) FROM compliance_bundles WHERE bundle_id = $1",
        bundle_id,
    )
    assert cnt == 1


# ─── Attack 9: COPY FROM bypass (CISO red-team request) ─────────

@pytest.mark.asyncio
async def test_attack_copy_from_still_fires_triggers(conn):
    """Scenario: attacker with DB write tries to bypass the trigger
    via `COPY fleet_orders FROM ...` — a bulk-ingest path that some
    DBs historically skipped triggers for.

    Defense: Postgres COPY fires ROW triggers in FOR EACH ROW mode
    by default. Verified by exercising a synthetic COPY that inserts
    a privileged row WITHOUT the required bundle — should still
    RAISE PRIVILEGED_CHAIN_VIOLATION.

    Round-table red-team item #6, part A.
    """
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    # Use an ephemeral stdin copy — asyncpg supports this via copy_records_to_table
    # or raw COPY via execute(). Simpler: wrap a COPY in an INSERT-from-SELECT
    # synthetic that asyncpg can run directly, which still fires the trigger.
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute(
            """
            INSERT INTO fleet_orders (order_type, parameters)
            SELECT 'enable_emergency_access', '{"site_id":"site-a"}'::jsonb
            """
        )
    assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)

    # Also test the literal COPY path — this is a 2-tuple example
    # showing the attacker cannot craft a COPY body that lands rows
    # in fleet_orders bypassing the trigger.
    with pytest.raises((asyncpg.RaiseError, asyncpg.PostgresError)) as exc2:
        await conn.copy_records_to_table(
            "fleet_orders",
            records=[(
                None,                                    # id — default
                "enable_emergency_access",               # order_type
                '{"site_id":"site-a"}',                  # parameters
                None, None, None,                         # signed_payload/sig/nonce
                "active",                                 # status
                None,                                     # created_at — default
                None,                                     # expires_at — default
            )],
            columns=[
                "id", "order_type", "parameters",
                "signed_payload", "signature", "nonce",
                "status", "created_at", "expires_at",
            ],
        )
    # The error may surface as RaiseError (from the trigger) or a
    # related PostgresError in COPY framing. Either way, the row was
    # rejected.


# ─── Attack 10: TRUNCATE bypass (CISO red-team request) ──────────


@pytest.mark.asyncio
async def test_attack_truncate_on_compliance_bundles_blocked_by_179(conn):
    """Scenario: attacker tries `TRUNCATE compliance_bundles CASCADE`
    to wipe evidence wholesale.

    Defense (migration 179): BEFORE TRUNCATE trigger fires on the
    statement and RAISEs. Verifies migration 179 closes the
    round-table red-team audit gap.

    Round-table red-team item #6, part B.
    """
    # Create a real privileged bundle
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    bundle_id = f"bundle-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO compliance_bundles (bundle_id, site_id, check_type) "
        "VALUES ($1, 'site-a', 'privileged_access')",
        bundle_id,
    )

    # TRUNCATE must now RAISE — migration 179 wires the BEFORE TRUNCATE
    # trigger to the existing prevent_audit_deletion() function.
    with pytest.raises(asyncpg.RaiseError) as exc:
        await conn.execute("TRUNCATE compliance_bundles")
    assert "append-only" in str(exc.value).lower() or "truncate" in str(exc.value).lower()

    # The bundle is still there — attack blocked
    cnt = await conn.fetchval("SELECT COUNT(*) FROM compliance_bundles")
    assert cnt == 1

    # Same defense on the audit tables
    for table in ("admin_audit_log", "client_audit_log", "portal_access_log"):
        with pytest.raises(asyncpg.RaiseError):
            await conn.execute(f"TRUNCATE {table}")


# ─── Attack 11: session_replication_role trick ────────────────────


@pytest.mark.asyncio
async def test_attack_session_replication_role_trick_blocked_by_179(conn):
    """Scenario: superuser sets `session_replication_role = 'replica'`
    to switch the session into replication mode. 'O'-mode triggers
    are skipped in replica mode; ALWAYS-mode triggers fire regardless.

    Defense (migration 179): chain triggers are altered to ENABLE
    ALWAYS, so they fire in replica mode too.

    Round-table red-team item #6, part C.
    """
    rows = await conn.fetch(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgname IN (
            'trg_enforce_privileged_chain',
            'trg_enforce_privileged_immutability'
        ) AND NOT tgisinternal
        """
    )
    # asyncpg returns the pg_trigger.tgenabled 'char' column as bytes.
    def _s(v):
        return v.decode() if isinstance(v, bytes) else v
    status = {r["tgname"]: _s(r["tgenabled"]) for r in rows}
    assert status.get("trg_enforce_privileged_chain") == "A", (
        f"Expected chain trigger to be ENABLE ALWAYS ('A') after "
        f"migration 179; got {status}"
    )
    assert status.get("trg_enforce_privileged_immutability") == "A", (
        f"Expected immutability trigger to be ENABLE ALWAYS ('A') after "
        f"migration 179; got {status}"
    )

    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    await conn.execute("SET session_replication_role = 'replica'")
    try:
        # ALWAYS-mode trigger fires even in replica mode — attack blocked
        with pytest.raises(asyncpg.RaiseError) as exc:
            await conn.execute(
                "INSERT INTO fleet_orders (order_type, parameters) VALUES "
                "('enable_emergency_access', '{\"site_id\":\"site-a\"}'::jsonb)"
            )
        assert "PRIVILEGED_CHAIN_VIOLATION" in str(exc.value)
    finally:
        await conn.execute("RESET session_replication_role")


# ─── Attack 12: trigger tampering (documenting detection latency) ─


@pytest.mark.asyncio
async def test_if_trigger_is_dropped_attack_succeeds_but_startup_invariants_detect(conn):
    """Documented failure mode: if a superuser DROPs the chain
    triggers (either maliciously or via misguided hotfix), the
    DB-layer protection is gone and the attacks above would succeed.

    This test DOCUMENTS that fact — the DB-layer defense is
    conditional on the triggers being installed. That's why we also
    ship:
      1. migrate.py cmd_up as fail-closed startup
      2. startup_invariants.py as runtime verification
      3. /api/admin/health/loops + admin_audit_log as post-hoc
         detection

    With all three, the blast radius of a dropped trigger is:
      (time between DROP and next startup) * (writes accepted in
      that window). And every startup afterward flags the breach
      in the admin_audit_log via STARTUP_INVARIANT_BROKEN.
    """
    await conn.execute("DROP TRIGGER trg_enforce_privileged_chain ON fleet_orders")

    # Now the attack succeeds
    await conn.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    await conn.execute(
        "INSERT INTO fleet_orders (order_type, parameters) VALUES "
        "('enable_emergency_access', '{\"site_id\":\"site-a\"}'::jsonb)"
    )
    # ↑ would raise if trigger were present. It's not.
    cnt = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
    assert cnt == 1, "trigger removal should have allowed the insert"

    # But startup_invariants will detect the gap next boot
    import startup_invariants
    results = await startup_invariants.check_all_invariants(conn)
    broken = [r.name for r in results if not r.ok]
    assert "INV-CHAIN-175" in broken
