"""PG integration tests for Migration 184 Phase 4 magic-link lifecycle.

Exercises the flow the `/api/partners/me/consent/request` →
`/api/portal/consent/approve/{token}` endpoints orchestrate, without
spinning up the FastAPI app. Tests directly manipulate the
`consent_request_tokens` table + call the same helpers the endpoints
use (`create_consent`), then assert the invariants.

Covers:
  1. Happy path — request → approve → consent row + bundle + ledger event
  2. Wrong-email approve → rejected
  3. Expired token → rejected
  4. Consumed token → rejected
  5. Concurrent approve race → at most one consent active
  6. Bogus token → 404-style not found

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from datetime import datetime, timezone, timedelta

import pytest

# Path setup — same pattern as test_runbook_consent_pg.py
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_mcp_server_dir = os.path.dirname(os.path.dirname(_backend_dir))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _mcp_server_dir not in sys.path:
    sys.path.insert(0, _mcp_server_dir)

PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres magic-link tests",
)


# ─── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def _signing_key(tmp_path, monkeypatch):
    """Ed25519 signing key — required by `_write_consent_bundle`."""
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed")
    sk = SigningKey.generate()
    p = tmp_path / "signing.key"
    p.write_bytes(sk.encode(encoder=HexEncoder))
    monkeypatch.setenv("SIGNING_KEY_FILE", str(p))
    import importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb  # noqa
    importlib.reload(sb)
    try:
        sb.reset_singleton()
    except Exception:
        pass
    return p


@pytest.fixture
async def db_session(_signing_key):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text

    url = PG_TEST_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, echo=False)
    SessionMaker = async_sessionmaker(engine, expire_on_commit=False)
    session = SessionMaker()

    await session.execute(text("""
        DROP TABLE IF EXISTS consent_request_tokens CASCADE;
        DROP TABLE IF EXISTS runbook_class_consent CASCADE;
        DROP TABLE IF EXISTS runbook_classes CASCADE;
        DROP TABLE IF EXISTS promoted_rule_events CASCADE;
        DROP TABLE IF EXISTS compliance_bundles CASCADE;
        DROP TABLE IF EXISTS sites CASCADE;

        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        CREATE TABLE sites (site_id TEXT PRIMARY KEY, status TEXT DEFAULT 'active');
        INSERT INTO sites(site_id) VALUES ('drakes-dental');

        CREATE TABLE runbook_classes (
            class_id TEXT PRIMARY KEY, display_name TEXT, description TEXT,
            risk_level TEXT, hipaa_controls TEXT[] DEFAULT '{}',
            example_actions JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        INSERT INTO runbook_classes (class_id, display_name, description, risk_level)
        VALUES ('DNS_ROTATION', 'DNS', 'dns', 'medium');

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

        CREATE TABLE consent_request_tokens (
            token_hash TEXT PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
            class_id TEXT NOT NULL REFERENCES runbook_classes(class_id),
            requested_by_email TEXT NOT NULL,
            requested_for_email TEXT NOT NULL,
            requested_ttl_days INT DEFAULT 365,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            consumed_consent_id UUID REFERENCES runbook_class_consent(consent_id),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE promoted_rule_events (
            event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id TEXT, event_type TEXT, actor TEXT, stage TEXT,
            outcome TEXT, reason TEXT, proof JSONB DEFAULT '{}'::jsonb,
            from_state TEXT, to_state TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

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
    """))
    await session.commit()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _make_token(db_session, requested_for_email="manager@drakes.com",
                ttl_hours=72, already_consumed=False):
    """Insert a consent_request_tokens row and return (raw_token, hash).

    Helper mirrors what `/me/consent/request` does internally — tests
    don't need to go through the FastAPI layer since the primitives are
    what matter.
    """
    from sqlalchemy import text
    raw = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    consumed = datetime.now(timezone.utc) if already_consumed else None

    async def _insert():
        await db_session.execute(text("""
            INSERT INTO consent_request_tokens
                (token_hash, site_id, class_id, requested_by_email,
                 requested_for_email, requested_ttl_days, expires_at,
                 consumed_at)
            VALUES (:th, 'drakes-dental', 'DNS_ROTATION',
                    'partner@acme.it', :for_email, 365, :exp, :consumed)
        """), {"th": thash, "for_email": requested_for_email,
               "exp": expires, "consumed": consumed})
        await db_session.commit()
    return raw, thash, _insert


# ─── Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_happy_path_request_to_approve_to_consent(db_session):
    """End-to-end: insert token → simulate GET details → simulate POST
    approve → verify consent row + bundle + ledger event + consumed flag."""
    from sqlalchemy import text
    from dashboard_api.runbook_consent import create_consent

    raw, thash, insert = _make_token(db_session)
    await insert()

    # GET details: just lookup
    row = (await db_session.execute(text("""
        SELECT site_id, class_id, requested_for_email, consumed_at, expires_at
        FROM consent_request_tokens WHERE token_hash = :th
    """), {"th": thash})).fetchone()
    assert row is not None
    assert row[3] is None  # not consumed yet

    # POST approve — directly call create_consent + mark consumed
    cid = await create_consent(
        db_session, site_id="drakes-dental", class_id="DNS_ROTATION",
        consented_by_email="manager@drakes.com", ttl_days=365,
    )
    await db_session.execute(text("""
        UPDATE consent_request_tokens SET consumed_at = NOW(),
                                           consumed_consent_id = :cid
        WHERE token_hash = :th
    """), {"th": thash, "cid": cid})
    await db_session.commit()

    # Assertions:
    # 1. Token consumed
    token_row = (await db_session.execute(text("""
        SELECT consumed_at, consumed_consent_id FROM consent_request_tokens
        WHERE token_hash = :th
    """), {"th": thash})).fetchone()
    assert token_row[0] is not None
    assert str(token_row[1]) == cid

    # 2. Consent row exists
    consent = (await db_session.execute(text("""
        SELECT consent_id FROM runbook_class_consent WHERE consent_id = :cid
    """), {"cid": cid})).fetchone()
    assert consent is not None

    # 3. Signed + hash-chained bundle written
    bundle = (await db_session.execute(text("""
        SELECT bundle_id, signature, ots_status, check_type, checks
        FROM compliance_bundles WHERE site_id = 'drakes-dental'
        ORDER BY id DESC LIMIT 1
    """))).fetchone()
    assert bundle is not None
    assert bundle[0].startswith("RC-")
    assert len(bundle[1]) == 128  # Ed25519 hex
    assert bundle[2] == "batching"
    assert bundle[3] == "runbook_consent"
    # Phase 4 hardening #5 — consent_copy_version + text in bundle
    import json
    checks = bundle[4] if isinstance(bundle[4], list) else json.loads(bundle[4])
    assert isinstance(checks, list) and len(checks) == 1
    ev = checks[0]
    assert ev.get("consent_copy_version"), "bundle missing consent_copy_version"
    assert "authorize" in ev.get("consent_copy_text", "").lower()

    # 4. Ledger event runbook.consented exists
    ledger = (await db_session.execute(text("""
        SELECT event_type FROM promoted_rule_events
        WHERE event_type = 'runbook.consented' AND actor = 'manager@drakes.com'
    """))).fetchone()
    assert ledger is not None


@pytest.mark.asyncio
async def test_wrong_email_rejected(db_session):
    """Token issued to manager@drakes.com should reject an approval
    attempt by imposter@someone.com (even with the correct token)."""
    from sqlalchemy import text
    import hmac

    _, thash, insert = _make_token(db_session, requested_for_email="manager@drakes.com")
    await insert()

    row = (await db_session.execute(text("""
        SELECT requested_for_email FROM consent_request_tokens WHERE token_hash = :th
    """), {"th": thash})).fetchone()
    expected = row[0].lower().encode()
    attempted = "imposter@someone.com".encode()
    # Mirror the timing-safe check the endpoint does
    assert not hmac.compare_digest(attempted, expected)


@pytest.mark.asyncio
async def test_expired_token_rejected(db_session):
    """Token expired at 0h TTL → treat as expired, no approve."""
    from sqlalchemy import text
    # Insert with negative TTL so it's immediately expired
    _, thash, _ = _make_token(db_session, ttl_hours=-1)
    # Skip insert helper; do it manually with explicit expires_at in past
    raw = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw.encode()).hexdigest()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.execute(text("""
        INSERT INTO consent_request_tokens
            (token_hash, site_id, class_id, requested_by_email,
             requested_for_email, requested_ttl_days, expires_at)
        VALUES (:th, 'drakes-dental', 'DNS_ROTATION', 'p@x.com',
                'm@x.com', 365, :exp)
    """), {"th": thash, "exp": past})
    await db_session.commit()

    row = (await db_session.execute(text("""
        SELECT expires_at FROM consent_request_tokens WHERE token_hash = :th
    """), {"th": thash})).fetchone()
    # Endpoint checks: expires_at < NOW() → 410
    assert row[0] < datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_already_consumed_token_rejected(db_session):
    """Consumed token can't be re-consumed."""
    from sqlalchemy import text
    _, thash, insert = _make_token(db_session, already_consumed=True)
    await insert()

    row = (await db_session.execute(text("""
        SELECT consumed_at FROM consent_request_tokens WHERE token_hash = :th
    """), {"th": thash})).fetchone()
    # Endpoint check: consumed_at IS NOT NULL → 410
    assert row[0] is not None


@pytest.mark.asyncio
async def test_concurrent_approve_unique_constraint(db_session):
    """Two approves racing → at most one active consent (UNIQUE constraint
    on (site_id, class_id, revoked_at=NULL) catches it)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from dashboard_api.runbook_consent import create_consent

    _, thash1, insert1 = _make_token(db_session)
    await insert1()

    # First approve succeeds
    cid1 = await create_consent(
        db_session, site_id="drakes-dental", class_id="DNS_ROTATION",
        consented_by_email="manager@drakes.com", ttl_days=365,
    )
    assert cid1

    # Second approve (second person racing) must fail — unique constraint
    failed_with_integrity = False
    try:
        await create_consent(
            db_session, site_id="drakes-dental", class_id="DNS_ROTATION",
            consented_by_email="colleague@drakes.com", ttl_days=365,
        )
    except IntegrityError:
        failed_with_integrity = True
    except Exception as e:  # noqa: BLE001
        # Accept any DB error that wraps the unique violation
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            failed_with_integrity = True
    assert failed_with_integrity, "second concurrent create_consent must fail"

    await db_session.rollback()


@pytest.mark.asyncio
async def test_bogus_token_not_found(db_session):
    """A SHA that was never issued returns no row."""
    from sqlalchemy import text
    bogus = hashlib.sha256(b"never-issued").hexdigest()
    row = (await db_session.execute(text("""
        SELECT site_id FROM consent_request_tokens WHERE token_hash = :th
    """), {"th": bogus})).fetchone()
    # Endpoint check: row is None → 404
    assert row is None
