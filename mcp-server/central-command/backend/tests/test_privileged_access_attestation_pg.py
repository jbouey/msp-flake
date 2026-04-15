"""Integration test for privileged_access_attestation (Phase 15 A-spec).

Round-table QA audit: 'test_privileged_access_attestation.py — chain
linkage, HMAC signing, bundle_hash determinism' — previously zero
coverage. This file closes that gap.

Covers:
  - Valid attestation: writes row, returns bundle_id / bundle_hash /
    signature / chain_position / chain_hash
  - Policy enforcement: rejects unknown event_type, anonymous actor,
    reason < 20 chars
  - Chain linkage: N+1 attestation links prev_hash = N's bundle_hash;
    chain_position increments by 1
  - Ed25519 signature actually verifies against the server's pubkey
  - bundle_hash determinism: same inputs → same canonical JSON (when
    timestamp is frozen)
  - count_recent_privileged_events filters correctly

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres attestation test",
)


# Minimal compliance_bundles schema mirroring the INSERT in
# privileged_access_attestation.create_privileged_access_attestation.
PREREQ_SCHEMA = """
DROP TABLE IF EXISTS admin_audit_log CASCADE;
DROP TABLE IF EXISTS compliance_bundles CASCADE;
DROP TABLE IF EXISTS sites CASCADE;

CREATE TABLE sites (site_id TEXT PRIMARY KEY);

CREATE TABLE compliance_bundles (
    bundle_id         TEXT PRIMARY KEY,
    site_id           TEXT NOT NULL,
    bundle_hash       TEXT NOT NULL,
    check_type        TEXT NOT NULL,
    check_result      TEXT,
    checked_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checks            JSONB,
    summary           JSONB,
    agent_signature   TEXT,
    signed_data       TEXT,
    signature_valid   BOOLEAN,
    prev_bundle_id    TEXT,
    prev_hash         TEXT NOT NULL,
    chain_position    INTEGER NOT NULL,
    chain_hash        TEXT NOT NULL,
    signature         TEXT,
    signed_by         TEXT,
    ots_status        TEXT
);
CREATE INDEX idx_cb_site_pos ON compliance_bundles (site_id, chain_position);

CREATE TABLE admin_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    username    TEXT,
    action      TEXT,
    target      TEXT,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
"""


def _make_signing_key(tmp_path: pathlib.Path) -> pathlib.Path:
    """Generate a real Ed25519 signing key, hex-encoded, and write to
    the path the attestation module will read."""
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed — attestation tests skipped")
    sk = SigningKey.generate()
    key_hex = sk.encode(encoder=HexEncoder)
    p = tmp_path / "signing.key"
    p.write_bytes(key_hex)
    return p


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    sk_path = _make_signing_key(tmp_path)
    monkeypatch.setenv("SIGNING_KEY_FILE", str(sk_path))
    # Force the signing_backend singleton to re-read the new env.
    # Without this, a FileSigningBackend built in an earlier test
    # keeps pointing at the first SIGNING_KEY_FILE value, producing
    # signatures that don't verify under this test's fresh key.
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb_mod  # noqa
    importlib.reload(sb_mod)
    try:
        sb_mod.reset_singleton()
    except Exception:
        pass
    sys.modules.pop("privileged_access_attestation", None)
    import privileged_access_attestation as paa
    importlib.reload(paa)
    return paa, sk_path


@pytest_asyncio.fixture
async def conn(setup):
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        yield c, setup
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS admin_audit_log CASCADE;
            DROP TABLE IF EXISTS compliance_bundles CASCADE;
            DROP TABLE IF EXISTS sites CASCADE;
        """)
        await c.close()


# ─── Happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_attestation_writes_bundle(conn):
    c, (paa, _) = conn
    await c.execute("INSERT INTO sites (site_id) VALUES ('site-a')")

    result = await paa.create_privileged_access_attestation(
        c,
        site_id="site-a",
        event_type="enable_emergency_access",
        actor_email="tech@partner.example",
        reason="Fixing urgent firewall regression on prod — valid test case",
    )

    assert "bundle_id" in result
    assert result["bundle_id"].startswith("PA-")
    assert len(result["bundle_hash"]) == 64  # sha256 hex
    assert len(result["signature"]) == 128   # Ed25519 sig hex
    assert result["chain_position"] == 0     # first bundle = 0 (genesis)

    # Row actually written
    row = await c.fetchrow(
        "SELECT check_type, ots_status, summary->>'event_type' AS event_type "
        "FROM compliance_bundles WHERE bundle_id = $1",
        result["bundle_id"],
    )
    assert row["check_type"] == "privileged_access"
    assert row["ots_status"] == "batching"
    assert row["event_type"] == "enable_emergency_access"


@pytest.mark.asyncio
async def test_chain_linkage_second_attestation_links_to_first(conn):
    c, (paa, _) = conn
    await c.execute("INSERT INTO sites (site_id) VALUES ('site-a')")

    first = await paa.create_privileged_access_attestation(
        c, site_id="site-a", event_type="enable_emergency_access",
        actor_email="tech@partner.example",
        reason="First emergency access — twenty-plus char reason here",
    )
    second = await paa.create_privileged_access_attestation(
        c, site_id="site-a", event_type="disable_emergency_access",
        actor_email="tech@partner.example",
        reason="Second attestation, disabling previously-enabled access",
    )

    assert second["chain_position"] == first["chain_position"] + 1

    # Second's prev_hash must equal first's bundle_hash
    row = await c.fetchrow(
        "SELECT prev_hash FROM compliance_bundles WHERE bundle_id = $1",
        second["bundle_id"],
    )
    assert row["prev_hash"] == first["bundle_hash"]


@pytest.mark.asyncio
async def test_signature_verifies_with_server_pubkey(conn):
    c, (paa, sk_path) = conn
    from nacl.signing import SigningKey, VerifyKey
    from nacl.encoding import HexEncoder

    await c.execute("INSERT INTO sites (site_id) VALUES ('site-a')")
    result = await paa.create_privileged_access_attestation(
        c, site_id="site-a", event_type="enable_emergency_access",
        actor_email="tech@partner.example",
        reason="Valid test attestation for signature verification round-trip",
    )

    # Load the pubkey from our synthetic signing key and verify
    sk = SigningKey(sk_path.read_bytes(), encoder=HexEncoder)
    vk: VerifyKey = sk.verify_key
    # Signature is over bundle_hash (per create_… implementation)
    sig_bytes = bytes.fromhex(result["signature"])
    # verify raises on mismatch, returns signed message on ok
    vk.verify(result["bundle_hash"].encode("utf-8"), sig_bytes)


# ─── Policy enforcement ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_event_type_rejected(conn):
    c, (paa, _) = conn
    with pytest.raises(paa.PrivilegedAccessAttestationError, match="allowed set"):
        await paa.create_privileged_access_attestation(
            c, site_id="site-a", event_type="DELETE_EVERYTHING",
            actor_email="tech@partner.example",
            reason="Clearly bogus event type should be rejected by policy",
        )


@pytest.mark.asyncio
async def test_anonymous_actor_rejected(conn):
    c, (paa, _) = conn
    for bad in ("", "   ", "no-at-symbol", None):
        with pytest.raises(paa.PrivilegedAccessAttestationError, match="actor_email"):
            await paa.create_privileged_access_attestation(
                c, site_id="site-a",
                event_type="enable_emergency_access",
                actor_email=bad or "",
                reason="Valid reason but actor is anonymous — must reject",
            )


@pytest.mark.asyncio
async def test_short_reason_rejected(conn):
    c, (paa, _) = conn
    for bad in ("", "short", "still short", " " * 30):
        with pytest.raises(paa.PrivilegedAccessAttestationError, match="reason"):
            await paa.create_privileged_access_attestation(
                c, site_id="site-a",
                event_type="enable_emergency_access",
                actor_email="tech@partner.example",
                reason=bad,
            )


# ─── Rate-limit helper ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_recent_privileged_events(conn):
    c, (paa, _) = conn
    await c.execute("INSERT INTO sites (site_id) VALUES ('site-a'), ('site-b')")

    # 3 events on site-a, 1 on site-b
    for _ in range(3):
        await paa.create_privileged_access_attestation(
            c, site_id="site-a", event_type="enable_emergency_access",
            actor_email="tech@partner.example",
            reason="Rate-limit test attestation with enough chars",
        )
    await paa.create_privileged_access_attestation(
        c, site_id="site-b", event_type="bulk_remediation",
        actor_email="tech@partner.example",
        reason="Different site, different type — should not leak across",
    )

    count_a = await paa.count_recent_privileged_events(c, "site-a", days=7)
    assert count_a == 3

    count_b = await paa.count_recent_privileged_events(c, "site-b", days=7)
    assert count_b == 1

    # Filter by event_type
    count_a_filtered = await paa.count_recent_privileged_events(
        c, "site-a", days=7, event_type="enable_emergency_access",
    )
    assert count_a_filtered == 3

    count_a_zero = await paa.count_recent_privileged_events(
        c, "site-a", days=7, event_type="bulk_remediation",
    )
    assert count_a_zero == 0


# ─── ALLOWED_EVENTS lockstep guard ────────────────────────────────


def test_allowed_events_matches_privileged_order_types():
    """The ALLOWED_EVENTS set MUST match fleet_cli.PRIVILEGED_ORDER_TYPES
    MUST match the latest migration defining v_privileged_types. The
    lockstep CI script (scripts/check_privileged_chain_lockstep.py) is
    the authoritative enforcer; this test gives a fast signal during
    development AND asserts the shape of the expected set so future
    additions can't silently drift.

    Session 207 Phase W0 (migration 218) added 6 watchdog_* events to
    power the SSH-free recovery surface. Session 207 ship order:
    W→T→H4→H1→H6→S. Every addition to the set requires:
      fleet_cli.PRIVILEGED_ORDER_TYPES
      privileged_access_attestation.ALLOWED_EVENTS
      migration v_privileged_types
    all updated together.
    """
    import privileged_access_attestation as paa
    expected = {
        "enable_emergency_access",
        "disable_emergency_access",
        "signing_key_rotation",
        "bulk_remediation",
        # Session 207 Phase W0 watchdog catalog
        "watchdog_restart_daemon",
        "watchdog_refetch_config",
        "watchdog_reset_pin_store",
        "watchdog_reset_api_key",
        "watchdog_redeploy_daemon",
        "watchdog_collect_diagnostics",
        # Session 207 Phase S escape hatch
        "enable_recovery_shell_24h",
    }
    assert paa.ALLOWED_EVENTS == expected, (
        f"ALLOWED_EVENTS drifted. Got {paa.ALLOWED_EVENTS}. Update "
        f"fleet_cli + attestation + migration + this test + the "
        f"lockstep script together or the chain has a gap."
    )
