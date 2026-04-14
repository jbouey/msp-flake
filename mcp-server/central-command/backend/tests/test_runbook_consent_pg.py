"""Migration 184 Phase 2 — PG integration tests for consent DB helpers.

Covers the grant → verify → revoke → verify-again lifecycle, plus
the runbook classifier and ledger writes. Runs against a real
Postgres instance when `PG_TEST_URL` is set; skips otherwise.

These tests pin:
  * `create_consent()` inserts the row AND writes a ledger event
  * `verify_consent_active()` distinguishes ok / no_consent / expired / revoked
  * `revoke_consent()` marks the row AND writes a ledger event
  * `classify_runbook_to_class()` maps known prefixes deterministically
  * `record_executed_with_consent()` writes a ledger event with the
    current mode stamped in the proof
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

# Path setup — same pattern as test_runbook_consent.py
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_mcp_server_dir = os.path.dirname(os.path.dirname(_backend_dir))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _mcp_server_dir not in sys.path:
    sys.path.insert(0, _mcp_server_dir)

PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres consent tests",
)


# ─── Fixtures ────────────────────────────────────────────────────

# The module imports SQLAlchemy text() — we need a minimal shim that
# accepts the same call pattern as AsyncSession.execute() so we can
# reuse the helper code against asyncpg directly. Easiest path: use
# an actual SQLAlchemy session.

@pytest.fixture
def _signing_key(tmp_path, monkeypatch):
    """Ed25519 signing key file for `_write_consent_bundle`.

    Phase-2.5 close — consent grants/revokes now write to
    compliance_bundles through the same file-backed Ed25519 signer
    that evidence uses. Tests need a real key file; the backend
    singleton is reset so it re-reads the env.
    """
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed")
    sk = SigningKey.generate()
    key_hex = sk.encode(encoder=HexEncoder)
    p = tmp_path / "signing.key"
    p.write_bytes(key_hex)
    monkeypatch.setenv("SIGNING_KEY_FILE", str(p))
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb_mod  # noqa
    importlib.reload(sb_mod)
    try:
        sb_mod.reset_singleton()
    except Exception:
        pass
    return p


@pytest.fixture
async def db_session(_signing_key):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    # The helpers use asyncpg driver via SQLAlchemy AsyncSession
    url = PG_TEST_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, echo=False)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
    session = SessionMaker()

    # Bootstrap the schema — the PG_TEST_URL DB is ephemeral so
    # migrations may not have run. Apply migration 184 inline so the
    # test is self-contained.
    from sqlalchemy import text
    await session.execute(text("""
        DROP TABLE IF EXISTS consent_amendments CASCADE;
        DROP TABLE IF EXISTS runbook_class_consent CASCADE;
        DROP TABLE IF EXISTS runbook_registry CASCADE;
        DROP TABLE IF EXISTS runbook_classes CASCADE;
        DROP TABLE IF EXISTS promoted_rule_events CASCADE;
        DROP TABLE IF EXISTS compliance_bundles CASCADE;
        DROP TABLE IF EXISTS sites CASCADE;

        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        CREATE TABLE sites (site_id TEXT PRIMARY KEY, status TEXT DEFAULT 'active');
        INSERT INTO sites(site_id) VALUES ('drakes-dental');

        -- Minimal compliance_bundles schema sufficient for
        -- _write_consent_bundle inserts. Not partitioned in test
        -- fixture; that's a deliberate simplification.
        CREATE TABLE compliance_bundles (
            id SERIAL PRIMARY KEY,
            site_id TEXT NOT NULL,
            bundle_id TEXT NOT NULL,
            bundle_hash TEXT NOT NULL,
            check_type TEXT NOT NULL,
            check_result TEXT,
            checked_at TIMESTAMPTZ,
            checks JSONB, summary JSONB,
            agent_signature TEXT, signed_data TEXT,
            signature_valid BOOLEAN,
            prev_bundle_id TEXT, prev_hash TEXT,
            chain_position INT, chain_hash TEXT,
            signature TEXT, signed_by TEXT,
            ots_status TEXT
        );

        CREATE TABLE runbook_classes (
            class_id TEXT PRIMARY KEY, display_name TEXT, description TEXT,
            risk_level TEXT, hipaa_controls TEXT[] DEFAULT '{}',
            example_actions JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        INSERT INTO runbook_classes (class_id, display_name, description, risk_level)
        VALUES
          ('DNS_ROTATION','DNS','dns','medium'),
          ('SERVICE_RESTART','Svc','svc','low'),
          ('PATCH_INSTALL','Patch','patch','high');

        CREATE TABLE runbook_class_consent (
            consent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            site_id TEXT REFERENCES sites(site_id),
            class_id TEXT REFERENCES runbook_classes(class_id),
            consented_by_email TEXT NOT NULL,
            consented_at TIMESTAMPTZ DEFAULT NOW(),
            client_signature BYTEA NOT NULL,
            client_pubkey BYTEA NOT NULL,
            consent_ttl_days INT DEFAULT 365,
            revoked_at TIMESTAMPTZ,
            revocation_reason TEXT,
            evidence_bundle_id TEXT NOT NULL,
            UNIQUE (site_id, class_id, revoked_at)
        );

        CREATE TABLE promoted_rule_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id TEXT, event_type TEXT, actor TEXT, stage TEXT,
            outcome TEXT, reason TEXT, proof JSONB DEFAULT '{}'::jsonb,
            from_state TEXT, to_state TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))
    await session.commit()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


# ─── Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_runbook_to_class_happy_path():
    """Deterministic mapping for common runbook_id prefixes."""
    from dashboard_api.runbook_consent import classify_runbook_to_class

    cases = [
        ("RB-AUTO-SERVICE_RESTART", "SERVICE_RESTART"),
        ("RB-DNS-ROTATE-001", "DNS_ROTATION"),
        ("RB-WIN-PATCH-001", "PATCH_INSTALL"),
        ("RB-WIN-FIREWALL-001", "FIREWALL_RULE"),
        ("LIN-CERT-001", "CERT_ROTATION"),
        ("RB-WIN-BACKUP-001", "BACKUP_RETRY"),
        ("RB-DRIFT-001", "CONFIG_SYNC"),
        ("RB-LOG-ARCHIVE-001", "LOG_ARCHIVE"),
    ]
    for runbook, expected in cases:
        assert classify_runbook_to_class(runbook) == expected, runbook
    assert classify_runbook_to_class("RB-UNKNOWN-999") is None
    assert classify_runbook_to_class(None) is None
    assert classify_runbook_to_class("") is None


@pytest.mark.asyncio
async def test_create_and_verify_consent(db_session):
    """grant → verify should return ok."""
    from dashboard_api.runbook_consent import (
        create_consent, verify_consent_active,
    )

    cid = await create_consent(
        db_session,
        site_id="drakes-dental",
        class_id="DNS_ROTATION",
        consented_by_email="manager@drakes-dental.com",
        ttl_days=365,
    )
    await db_session.commit()
    assert cid and len(cid) == 36  # UUID

    result = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="DNS_ROTATION",
    )
    assert result.ok is True, f"expected ok, got {result}"
    assert result.reason == "ok"
    assert result.consent_id == cid
    assert result.expires_at is not None


@pytest.mark.asyncio
async def test_verify_missing_consent_returns_no_consent(db_session):
    """no grant → verify returns no_consent, not ok."""
    from dashboard_api.runbook_consent import verify_consent_active

    result = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="SERVICE_RESTART",
    )
    assert result.ok is False
    assert result.reason == "no_consent"


@pytest.mark.asyncio
async def test_unknown_class_returns_unknown_class(db_session):
    """`None` class_id (from classifier fallthrough) → unknown_class."""
    from dashboard_api.runbook_consent import verify_consent_active

    result = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id=None,
    )
    assert result.ok is False
    assert result.reason == "unknown_class"


@pytest.mark.asyncio
async def test_revoke_makes_consent_inactive(db_session):
    """grant → revoke → verify returns no_consent (revoked row excluded)."""
    from dashboard_api.runbook_consent import (
        create_consent, revoke_consent, verify_consent_active,
    )

    cid = await create_consent(
        db_session,
        site_id="drakes-dental",
        class_id="PATCH_INSTALL",
        consented_by_email="manager@drakes-dental.com",
        ttl_days=365,
    )
    await db_session.commit()

    await revoke_consent(
        db_session,
        consent_id=cid,
        revoked_by_email="manager@drakes-dental.com",
        reason="rotating to annual review schedule",
    )
    await db_session.commit()

    result = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="PATCH_INSTALL",
    )
    assert result.ok is False
    assert result.reason == "no_consent"


@pytest.mark.asyncio
async def test_ledger_events_written_for_grant_and_revoke(db_session):
    """Each grant and revoke must append to promoted_rule_events."""
    from sqlalchemy import text
    from dashboard_api.runbook_consent import create_consent, revoke_consent

    cid = await create_consent(
        db_session,
        site_id="drakes-dental",
        class_id="DNS_ROTATION",
        consented_by_email="manager@drakes-dental.com",
        ttl_days=365,
    )
    await revoke_consent(
        db_session,
        consent_id=cid,
        revoked_by_email="manager@drakes-dental.com",
        reason="audit-time consent refresh",
    )
    await db_session.commit()

    rows = (await db_session.execute(text("""
        SELECT event_type, outcome FROM promoted_rule_events
        ORDER BY created_at ASC
    """))).fetchall()
    event_types = [r[0] for r in rows]
    assert "runbook.consented" in event_types
    assert "runbook.revoked" in event_types


@pytest.mark.asyncio
async def test_executed_with_consent_ledger_write(db_session):
    """record_executed_with_consent writes a ledger row with mode."""
    from sqlalchemy import text
    from dashboard_api.runbook_consent import record_executed_with_consent

    await record_executed_with_consent(
        db_session,
        site_id="drakes-dental",
        class_id="SERVICE_RESTART",
        runbook_id="RB-AUTO-SERVICE_RESTART",
        consent_id=None,  # shadow-mode: simulate execution without consent
        incident_id="incident-xyz",
    )
    await db_session.commit()

    row = (await db_session.execute(text("""
        SELECT event_type, outcome, proof FROM promoted_rule_events
        WHERE event_type = 'runbook.executed_with_consent'
        ORDER BY created_at DESC LIMIT 1
    """))).fetchone()
    assert row is not None
    assert row[0] == "runbook.executed_with_consent"
    # consent_id=None → outcome='noop' (shadow-mode log signal)
    assert row[1] == "noop"
    # proof should name-stamp the mode + runbook_id
    assert "runbook_id" in row[2]
    assert "mode" in row[2]


@pytest.mark.asyncio
async def test_should_block_is_false_when_class_not_enforced(db_session, monkeypatch):
    """Phase 2 default — no class is enforced; should_block = False for all."""
    from dashboard_api.runbook_consent import verify_consent_active

    monkeypatch.delenv("RUNBOOK_CONSENT_ENFORCE_CLASSES", raising=False)
    result = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="SERVICE_RESTART",
    )
    assert result.ok is False
    assert result.should_block() is False  # shadow — never blocks


@pytest.mark.asyncio
async def test_should_block_is_true_only_for_enforced_class(db_session, monkeypatch):
    """Phase 3 — ONE class is enforced; others remain shadow."""
    from dashboard_api.runbook_consent import verify_consent_active

    monkeypatch.setenv("RUNBOOK_CONSENT_ENFORCE_CLASSES", "LOG_ARCHIVE")

    # LOG_ARCHIVE (enforced) → blocks
    enforced = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="LOG_ARCHIVE",
    )
    assert enforced.should_block() is True

    # SERVICE_RESTART (NOT enforced) → still shadow
    shadow = await verify_consent_active(
        db_session, site_id="drakes-dental", class_id="SERVICE_RESTART",
    )
    assert shadow.should_block() is False


@pytest.mark.asyncio
async def test_wildcard_enforce_blocks_any_missing(db_session, monkeypatch):
    """`*` sentinel enforces every class."""
    from dashboard_api.runbook_consent import verify_consent_active

    monkeypatch.setenv("RUNBOOK_CONSENT_ENFORCE_CLASSES", "*")
    for cls in ("SERVICE_RESTART", "DNS_ROTATION", "PATCH_INSTALL"):
        r = await verify_consent_active(
            db_session, site_id="drakes-dental", class_id=cls,
        )
        assert r.should_block() is True, cls


@pytest.mark.asyncio
async def test_create_consent_writes_compliance_bundle(db_session):
    """Round-table close — every consent mutation writes a
    signed + hash-chained compliance_bundles row."""
    from sqlalchemy import text
    from dashboard_api.runbook_consent import create_consent

    cid = await create_consent(
        db_session,
        site_id="drakes-dental",
        class_id="LOG_ARCHIVE",
        consented_by_email="manager@drakes-dental.com",
        ttl_days=365,
    )
    await db_session.commit()

    row = (await db_session.execute(text("""
        SELECT bundle_id, bundle_hash, check_type, signature, ots_status,
               chain_hash, chain_position
        FROM compliance_bundles
        WHERE site_id = 'drakes-dental'
        ORDER BY id DESC LIMIT 1
    """))).fetchone()
    assert row is not None, "no compliance_bundles row written"
    bundle_id, bundle_hash, check_type, signature, ots_status, chain_hash, chain_position = row
    assert bundle_id.startswith("RC-"), bundle_id
    assert check_type == "runbook_consent"
    assert len(bundle_hash) == 64  # sha256 hex
    assert len(signature) == 128   # ed25519 hex
    assert ots_status == "batching"  # enqueued for anchor
    assert chain_position == 0  # first bundle for this site
    assert len(chain_hash) == 64

    # And the consent row should reference the bundle_id we just wrote
    consent_row = (await db_session.execute(text("""
        SELECT evidence_bundle_id FROM runbook_class_consent
        WHERE consent_id = :cid
    """), {"cid": cid})).fetchone()
    assert consent_row[0] == bundle_id, \
        f"consent row referenced wrong bundle: {consent_row[0]} vs {bundle_id}"
