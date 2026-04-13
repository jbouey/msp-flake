"""Magic-link token end-to-end test (Phase 14 T2.1 / Phase 15 A-spec).

Covers the privileged_magic_link.py module:
  - mint_token writes the tracking row
  - verify_and_consume validates HMAC + expiry + single-use
  - tampered tokens, mismatched actions, mismatched session emails
    are all rejected
  - second consume of the same token is rejected (single-use atomic)
  - separate MAGIC_LINK_HMAC_KEY_FILE secret produces tokens that
    are NOT verifiable against the signing.key derivation (defense
    in depth: a leak of one secret does not compromise the other)

Skips when PG_TEST_URL is unset (same pattern as the chain-trigger
test). CI workflow provides a postgres:15 service container.
"""
from __future__ import annotations

import os
import pathlib
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres magic-link test",
)


MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"
BACKEND_DIR = pathlib.Path(__file__).parent.parent


# Minimum schema required by migrations 174 + 178.
PREREQ_SCHEMA = """
DROP TABLE IF EXISTS privileged_access_magic_links CASCADE;
DROP TABLE IF EXISTS privileged_access_requests CASCADE;
DROP TABLE IF EXISTS sites CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sites (site_id VARCHAR(100) PRIMARY KEY);
"""


def _read_migration(filename: str) -> str:
    return (MIGRATIONS_DIR / filename).read_text()


@pytest_asyncio.fixture
async def setup_env(tmp_path, monkeypatch):
    """Create a temp signing.key file + point the magic-link module at
    it. Resets MAGIC_LINK_HMAC_KEY_FILE to the default-derivation path."""
    signing_key = tmp_path / "signing.key"
    signing_key.write_bytes(secrets.token_bytes(32))
    monkeypatch.setenv("SIGNING_KEY_FILE", str(signing_key))
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", "")
    # Re-import so module-level constants pick up the new env
    import importlib
    import sys
    sys.modules.pop("privileged_magic_link", None)
    import privileged_magic_link
    importlib.reload(privileged_magic_link)
    return privileged_magic_link, signing_key


@pytest_asyncio.fixture
async def conn(setup_env):
    """Per-test connection. Rebuilds prereq schema + applies migrations
    174 (request queue) + 178 (magic-link table)."""
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        await c.execute(_read_migration("174_privileged_access_requests.sql"))
        await c.execute(_read_migration("178_privileged_magic_links.sql"))
        yield c
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS privileged_access_magic_links CASCADE;
            DROP TABLE IF EXISTS privileged_access_requests CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
        """)
        await c.close()


async def _seed_request(c) -> str:
    """Insert a sites row + a pending privileged-access request and
    return its id. Magic-link FKs require both."""
    site_id = f"site-{uuid.uuid4().hex[:8]}"
    await c.execute("INSERT INTO sites (site_id) VALUES ($1)", site_id)
    rid = await c.fetchval(
        "INSERT INTO privileged_access_requests "
        "(site_id, event_type, initiator_email, initiator_role, reason, expires_at) "
        "VALUES ($1, 'enable_emergency_access', 'tech@partner.example', "
        "'partner_tech', 'integration test', NOW() + INTERVAL '1 hour') "
        "RETURNING id::text",
        site_id,
    )
    return rid


# ─── mint_token ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mint_token_writes_tracking_row(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "client@example.com")

    assert tok.count(".") == 2, f"expected dotted token format, got {tok}"
    token_id = tok.split(".")[0]
    row = await conn.fetchrow(
        "SELECT action, target_user_email, consumed_at FROM "
        "privileged_access_magic_links WHERE token_id = $1",
        token_id,
    )
    assert row is not None
    assert row["action"] == "approve"
    assert row["target_user_email"] == "client@example.com"
    assert row["consumed_at"] is None


@pytest.mark.asyncio
async def test_mint_token_rejects_invalid_action(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    with pytest.raises(pml.MagicLinkError, match="action"):
        await pml.mint_token(conn, rid, "bogus", "client@example.com")


@pytest.mark.asyncio
async def test_mint_token_rejects_invalid_email(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    with pytest.raises(pml.MagicLinkError, match="email"):
        await pml.mint_token(conn, rid, "approve", "not-an-email")


@pytest.mark.asyncio
async def test_mint_token_rejects_ttl_out_of_range(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    with pytest.raises(pml.MagicLinkError, match="ttl_seconds"):
        await pml.mint_token(conn, rid, "approve", "x@y.com", ttl_seconds=10)
    with pytest.raises(pml.MagicLinkError, match="ttl_seconds"):
        await pml.mint_token(conn, rid, "approve", "x@y.com", ttl_seconds=99999)


# ─── verify_and_consume — happy path + single-use ─────────────────


@pytest.mark.asyncio
async def test_verify_and_consume_happy_path(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "client@example.com")

    verified = await pml.verify_and_consume(
        conn, tok,
        expected_action="approve",
        session_user_email="client@example.com",
        client_ip="10.0.0.5",
        user_agent="pytest/1.0",
    )
    assert verified.action == "approve"
    assert verified.target_user_email == "client@example.com"
    assert verified.request_id == rid

    # Side effect: row marked consumed with audit fields populated
    consumed = await conn.fetchrow(
        "SELECT consumed_at, consumed_by_ip, consumed_by_ua FROM "
        "privileged_access_magic_links WHERE token_id = $1",
        tok.split(".")[0],
    )
    assert consumed["consumed_at"] is not None
    assert consumed["consumed_by_ip"] == "10.0.0.5"
    assert consumed["consumed_by_ua"] == "pytest/1.0"


@pytest.mark.asyncio
async def test_second_consume_rejected(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com")

    await pml.verify_and_consume(conn, tok, "approve", "x@y.com")
    with pytest.raises(pml.MagicLinkError, match="consumed"):
        await pml.verify_and_consume(conn, tok, "approve", "x@y.com")


# ─── verify_and_consume — rejection cases ─────────────────────────


@pytest.mark.asyncio
async def test_tampered_token_hmac_rejected(conn, setup_env):
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com")

    # Flip one hex char in the HMAC component
    token_id, mac, exp = tok.split(".")
    bad_mac = ("0" if mac[0] != "0" else "1") + mac[1:]
    bad_tok = f"{token_id}.{bad_mac}.{exp}"

    with pytest.raises(pml.MagicLinkError, match="HMAC"):
        await pml.verify_and_consume(conn, bad_tok, "approve", "x@y.com")


@pytest.mark.asyncio
async def test_tampered_exp_rejected(conn, setup_env):
    """Extending exp_unix in the URL must invalidate the HMAC."""
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com", ttl_seconds=120)

    token_id, mac, exp = tok.split(".")
    bad_tok = f"{token_id}.{mac}.{int(exp) + 999999}"

    with pytest.raises(pml.MagicLinkError, match="HMAC"):
        await pml.verify_and_consume(conn, bad_tok, "approve", "x@y.com")


@pytest.mark.asyncio
async def test_action_mismatch_rejected(conn, setup_env):
    """A token minted for 'approve' must not be consumable as 'reject'."""
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com")

    with pytest.raises(pml.MagicLinkError, match="action mismatch"):
        await pml.verify_and_consume(conn, tok, "reject", "x@y.com")


@pytest.mark.asyncio
async def test_session_email_mismatch_rejected(conn, setup_env):
    """A token minted for client-A must not be consumable while logged
    in as client-B — even if client-B has admin rights elsewhere."""
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "alice@example.com")

    with pytest.raises(pml.MagicLinkError, match="session user"):
        await pml.verify_and_consume(conn, tok, "approve", "bob@example.com")


@pytest.mark.asyncio
async def test_expired_token_rejected(conn, setup_env):
    """A token past its expires_at must be rejected even on first use."""
    pml, _ = setup_env
    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com", ttl_seconds=60)

    # Backdate the row directly
    token_id = tok.split(".")[0]
    await conn.execute(
        "UPDATE privileged_access_magic_links "
        "SET expires_at = NOW() - INTERVAL '1 minute' WHERE token_id = $1",
        token_id,
    )

    # The token's exp_unix in the dotted form is also in the future,
    # but the DB-stored expires_at takes precedence. Forge a new
    # exp_unix in the past to exercise that path: the token's exp
    # check fires first.
    parts = tok.split(".")
    past_exp = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp())
    forged = f"{parts[0]}.{parts[1]}.{past_exp}"
    with pytest.raises(pml.MagicLinkError, match="expired"):
        await pml.verify_and_consume(conn, forged, "approve", "x@y.com")


@pytest.mark.asyncio
async def test_unknown_token_id_rejected(conn, setup_env):
    """A well-formed token whose token_id is not in the DB is rejected
    (defense against a forged token where the attacker happens to have
    a valid HMAC but never minted the row)."""
    pml, _ = setup_env

    # Mint a real token to learn the format, then craft one with a
    # different token_id but the same HMAC (will fail HMAC check OR
    # not-found check — both are correct rejections).
    rid = await _seed_request(conn)
    real_tok = await pml.mint_token(conn, rid, "approve", "x@y.com")
    _, mac, exp = real_tok.split(".")
    fake_id = secrets.token_hex(16)
    fake_tok = f"{fake_id}.{mac}.{exp}"

    with pytest.raises(pml.MagicLinkError):
        await pml.verify_and_consume(conn, fake_tok, "approve", "x@y.com")


# ─── Phase 15: separate magic-link HMAC secret (defense in depth) ──


@pytest.mark.asyncio
async def test_separate_hmac_key_breaks_signing_key_forgery(conn, tmp_path, monkeypatch):
    """When MAGIC_LINK_HMAC_KEY_FILE is set, tokens minted under that
    key must NOT verify under the signing.key-derived key. This
    proves that a leak of signing.key alone does not allow magic-link
    forgery — defense in depth.
    """
    import importlib
    import sys

    # Setup #1: signing.key derivation (default mode)
    signing = tmp_path / "signing.key"
    signing.write_bytes(secrets.token_bytes(32))
    monkeypatch.setenv("SIGNING_KEY_FILE", str(signing))
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", "")
    sys.modules.pop("privileged_magic_link", None)
    import privileged_magic_link as pml_v1
    importlib.reload(pml_v1)

    # Schema reset because the setup_env fixture isn't in play here
    await conn.execute(PREREQ_SCHEMA)
    await conn.execute(_read_migration("174_privileged_access_requests.sql"))
    await conn.execute(_read_migration("178_privileged_magic_links.sql"))

    rid = await _seed_request(conn)
    tok_v1 = await pml_v1.mint_token(conn, rid, "approve", "x@y.com")

    # Setup #2: now provision a SEPARATE magic-link HMAC key. Tokens
    # minted under v1 must fail to verify under v2's key derivation.
    mlk = tmp_path / "magic-link.key"
    mlk.write_bytes(secrets.token_bytes(32))
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", str(mlk))
    sys.modules.pop("privileged_magic_link", None)
    import privileged_magic_link as pml_v2
    importlib.reload(pml_v2)

    # The v1 token's HMAC was computed with key-derived-from-signing.
    # v2 derives from the separate file. Verify must fail with HMAC
    # mismatch — proving the secrets are isolated.
    with pytest.raises(pml_v2.MagicLinkError, match="HMAC"):
        await pml_v2.verify_and_consume(conn, tok_v1, "approve", "x@y.com")


@pytest.mark.asyncio
async def test_separate_hmac_key_self_consistent(conn, tmp_path, monkeypatch):
    """Tokens minted with the separate key DO verify with the same
    separate key (sanity: we didn't break the round-trip)."""
    import importlib
    import sys

    signing = tmp_path / "signing.key"
    signing.write_bytes(secrets.token_bytes(32))
    mlk = tmp_path / "magic-link.key"
    mlk.write_bytes(secrets.token_bytes(32))
    monkeypatch.setenv("SIGNING_KEY_FILE", str(signing))
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", str(mlk))
    sys.modules.pop("privileged_magic_link", None)
    import privileged_magic_link as pml
    importlib.reload(pml)

    await conn.execute(PREREQ_SCHEMA)
    await conn.execute(_read_migration("174_privileged_access_requests.sql"))
    await conn.execute(_read_migration("178_privileged_magic_links.sql"))

    rid = await _seed_request(conn)
    tok = await pml.mint_token(conn, rid, "approve", "x@y.com")

    verified = await pml.verify_and_consume(conn, tok, "approve", "x@y.com")
    assert verified.action == "approve"


@pytest.mark.asyncio
async def test_empty_magic_link_key_file_raises(tmp_path, monkeypatch):
    """A configured-but-empty magic-link key file must fail loud (operator
    error: the env var was set but the file is empty)."""
    import importlib
    import sys

    signing = tmp_path / "signing.key"
    signing.write_bytes(secrets.token_bytes(32))
    empty = tmp_path / "empty.key"
    empty.write_bytes(b"")
    monkeypatch.setenv("SIGNING_KEY_FILE", str(signing))
    monkeypatch.setenv("MAGIC_LINK_HMAC_KEY_FILE", str(empty))
    sys.modules.pop("privileged_magic_link", None)
    import privileged_magic_link as pml
    importlib.reload(pml)

    with pytest.raises(pml.MagicLinkError, match="empty"):
        pml._hmac_key()
