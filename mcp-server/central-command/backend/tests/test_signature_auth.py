"""Unit tests for signature_auth.

Covers:
  * Canonical input determinism
  * Fingerprint stability
  * Missing / malformed header rejection
  * Timestamp skew window
  * Nonce replay rejection
  * Valid signature acceptance
  * Invalid signature rejection
  * Unknown-pubkey rejection
  * Body-hash mismatch detection

The Postgres-touching paths (pubkey lookup, nonce persistence) are
tested with a fake connection that records calls, so this module is
hermetic and runs in CI without a DB.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

import signature_auth  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_hex = pub_bytes.hex()
    return priv, pub_hex


def _sign(priv, canonical: bytes) -> str:
    raw = priv.sign(canonical)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fake_request(method: str, path: str, headers: dict):
    # Minimal shim — signature_auth only uses .method, .url.path, .headers.
    req = MagicMock()
    req.method = method
    req.url = MagicMock()
    req.url.path = path
    # headers.get is case-insensitive in Starlette; use a dict with
    # lowercase keys and case-insensitive .get.
    lower = {k.lower(): v for k, v in headers.items()}

    class CaseInsensitiveHeaders(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    req.headers = CaseInsensitiveHeaders(lower)
    return req


def _fake_conn(pubkey_hex: str, fingerprint: str, *, nonce_seen=False):
    """Fake asyncpg-ish connection with the two queries we need."""
    conn = AsyncMock()
    current_calls = {"record_nonce": 0}

    async def fetchrow(query, *args):
        q = query.strip().split()[0].lower()
        if "v_current_appliance_identity" in query:
            if pubkey_hex:
                return {
                    "agent_pubkey_hex": pubkey_hex,
                    "agent_pubkey_fingerprint": fingerprint,
                }
            return None
        if "site_appliances" in query:
            # Legacy fallback — signal "no row" so the view path is the
            # only one tested.
            return None
        if "FROM nonces" in query or "from nonces" in query.lower():
            return {"x": 1} if nonce_seen else None
        return None

    async def execute(query, *args):
        if "INSERT INTO nonces" in query:
            current_calls["record_nonce"] += 1
        return ""

    conn.fetchrow = fetchrow
    conn.execute = execute
    conn._record_nonce_calls = current_calls
    return conn


# ---------------------------------------------------------------------------
# Canonical input
# ---------------------------------------------------------------------------

def test_canonical_input_is_deterministic():
    a = signature_auth._canonical_input(
        "POST", "/api/appliances/checkin",
        hashlib.sha256(b'{"site_id":"x"}').hexdigest(),
        "2026-04-15T03:45:23Z",
        "aa" * 16,
    )
    b = signature_auth._canonical_input(
        "post", "/api/appliances/checkin",  # method uppercased
        hashlib.sha256(b'{"site_id":"x"}').hexdigest().upper(),  # hash lowercased
        "2026-04-15T03:45:23Z",
        ("aa" * 16).upper(),  # nonce lowercased
    )
    assert a == b


def test_canonical_input_uses_lf_separator_only():
    out = signature_auth._canonical_input(
        "POST", "/x", "a" * 64, "2026-04-15T03:45:23Z", "b" * 32,
    )
    # Must contain exactly 4 newlines (joining 5 parts) and no \r.
    assert out.count(b"\n") == 4
    assert b"\r" not in out
    assert not out.endswith(b"\n")


def test_empty_body_hash_constant():
    assert signature_auth.EMPTY_SHA256 == hashlib.sha256(b"").hexdigest()


def test_fingerprint_is_stable_and_16_chars():
    _, pk_hex = _make_keypair()
    fp1 = signature_auth._fingerprint(pk_hex)
    fp2 = signature_auth._fingerprint(pk_hex)
    assert fp1 == fp2
    assert len(fp1) == 16
    assert all(c in "0123456789abcdef" for c in fp1)


def test_fingerprint_rejects_garbage():
    assert signature_auth._fingerprint("not hex") == ""
    assert signature_auth._fingerprint("") == ""


# ---------------------------------------------------------------------------
# Verifier — negative paths (no DB touches required)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_headers_reports_present_false():
    req = _fake_request("POST", "/api/appliances/checkin", {})
    result = await signature_auth.verify_appliance_signature(
        req, conn=None, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.present is False
    assert result.valid is False
    assert result.reason == "no_headers"


@pytest.mark.asyncio
async def test_partial_headers_also_reports_no_headers():
    req = _fake_request("POST", "/x", {"X-Appliance-Signature": "abc"})
    result = await signature_auth.verify_appliance_signature(
        req, conn=None, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.present is False
    assert result.reason == "no_headers"


@pytest.mark.asyncio
async def test_bad_timestamp_format():
    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": "abc",
            "X-Appliance-Timestamp": "2026/04/15 03:45:23",  # wrong format
            "X-Appliance-Nonce": "a" * 32,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=None, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.present is True
    assert result.valid is False
    assert result.reason == "bad_timestamp"


@pytest.mark.asyncio
async def test_clock_skew_rejected():
    # Timestamp 10 minutes in the past.
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": "abc",
            "X-Appliance-Timestamp": old,
            "X-Appliance-Nonce": "a" * 32,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=None, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.reason == "clock_skew"


@pytest.mark.asyncio
async def test_bad_nonce_rejected():
    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": "abc",
            "X-Appliance-Timestamp": _ts_now(),
            "X-Appliance-Nonce": "not-hex",
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=None, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.reason == "bad_nonce"


@pytest.mark.asyncio
async def test_unknown_pubkey_rejected():
    conn = _fake_conn(pubkey_hex="", fingerprint="")
    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": "a" * 86,
            "X-Appliance-Timestamp": _ts_now(),
            "X-Appliance-Nonce": "a" * 32,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.reason == "unknown_pubkey"


# ---------------------------------------------------------------------------
# Verifier — positive + signature-level negatives (real crypto)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_signature_round_trip():
    priv, pub_hex = _make_keypair()
    fp = signature_auth._fingerprint(pub_hex)
    conn = _fake_conn(pubkey_hex=pub_hex, fingerprint=fp)

    method = "POST"
    path = "/api/appliances/checkin"
    body = b'{"site_id":"s","mac":"AA:BB"}'
    ts = _ts_now()
    nonce = "aa" * 16

    canonical = signature_auth._canonical_input(
        method, path, hashlib.sha256(body).hexdigest(), ts, nonce,
    )
    sig_b64 = _sign(priv, canonical)

    req = _fake_request(
        method, path,
        {
            "X-Appliance-Signature": sig_b64,
            "X-Appliance-Timestamp": ts,
            "X-Appliance-Nonce": nonce,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=body,
    )
    assert result.present is True
    assert result.valid is True
    assert result.reason == ""
    assert result.pubkey_fingerprint == fp
    # Nonce persisted for future replay detection.
    assert conn._record_nonce_calls["record_nonce"] == 1


@pytest.mark.asyncio
async def test_invalid_signature_rejected():
    priv1, pub1_hex = _make_keypair()  # server expects this one
    priv2, _ = _make_keypair()          # but we sign with a different key
    fp = signature_auth._fingerprint(pub1_hex)
    conn = _fake_conn(pubkey_hex=pub1_hex, fingerprint=fp)

    method, path = "POST", "/x"
    body = b"hello"
    ts = _ts_now()
    nonce = "ab" * 16

    canonical = signature_auth._canonical_input(
        method, path, hashlib.sha256(body).hexdigest(), ts, nonce,
    )
    sig_b64 = _sign(priv2, canonical)  # wrong private key

    req = _fake_request(
        method, path,
        {
            "X-Appliance-Signature": sig_b64,
            "X-Appliance-Timestamp": ts,
            "X-Appliance-Nonce": nonce,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=body,
    )
    assert result.valid is False
    assert result.reason == "invalid_signature"
    # Nonce NOT recorded for a failed sig.
    assert conn._record_nonce_calls["record_nonce"] == 0


@pytest.mark.asyncio
async def test_body_tamper_breaks_signature():
    priv, pub_hex = _make_keypair()
    fp = signature_auth._fingerprint(pub_hex)
    conn = _fake_conn(pubkey_hex=pub_hex, fingerprint=fp)

    method, path = "POST", "/x"
    signed_body = b'{"amount": 100}'
    ts = _ts_now()
    nonce = "cd" * 16

    canonical = signature_auth._canonical_input(
        method, path, hashlib.sha256(signed_body).hexdigest(), ts, nonce,
    )
    sig_b64 = _sign(priv, canonical)

    # Client signed amount=100; attacker swaps to amount=9999 in transit.
    tampered_body = b'{"amount": 9999}'
    req = _fake_request(
        method, path,
        {
            "X-Appliance-Signature": sig_b64,
            "X-Appliance-Timestamp": ts,
            "X-Appliance-Nonce": nonce,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=tampered_body,
    )
    assert result.valid is False
    assert result.reason == "invalid_signature"


@pytest.mark.asyncio
async def test_nonce_replay_rejected():
    priv, pub_hex = _make_keypair()
    fp = signature_auth._fingerprint(pub_hex)
    conn = _fake_conn(pubkey_hex=pub_hex, fingerprint=fp, nonce_seen=True)

    method, path = "POST", "/x"
    body = b""
    ts = _ts_now()
    nonce = "ef" * 16

    canonical = signature_auth._canonical_input(
        method, path, hashlib.sha256(body).hexdigest(), ts, nonce,
    )
    sig_b64 = _sign(priv, canonical)

    req = _fake_request(
        method, path,
        {
            "X-Appliance-Signature": sig_b64,
            "X-Appliance-Timestamp": ts,
            "X-Appliance-Nonce": nonce,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=body,
    )
    assert result.valid is False
    assert result.reason == "nonce_replay"


@pytest.mark.asyncio
async def test_malformed_signature_base64_rejected():
    _, pub_hex = _make_keypair()
    fp = signature_auth._fingerprint(pub_hex)
    conn = _fake_conn(pubkey_hex=pub_hex, fingerprint=fp)

    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": "!!!!not-base64!!!!",
            "X-Appliance-Timestamp": _ts_now(),
            "X-Appliance-Nonce": "ff" * 16,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.valid is False
    assert result.reason == "bad_signature_format"


@pytest.mark.asyncio
async def test_signature_wrong_length_rejected():
    _, pub_hex = _make_keypair()
    fp = signature_auth._fingerprint(pub_hex)
    conn = _fake_conn(pubkey_hex=pub_hex, fingerprint=fp)

    # 32-byte blob encoded as base64url — cryptographically too short.
    short = base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode()

    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": short,
            "X-Appliance-Timestamp": _ts_now(),
            "X-Appliance-Nonce": "0f" * 16,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.valid is False
    assert result.reason == "bad_signature_format"


# ---------------------------------------------------------------------------
# Legacy-fallback removal — regression guard for #179 (Session 211)
# ---------------------------------------------------------------------------
#
# `_resolve_pubkey` USED to read `site_appliances.agent_public_key` as a
# fallback when `v_current_appliance_identity` had no row. That column
# holds the EVIDENCE-bundle signing key, not the IDENTITY signing key
# sigauth needs (CLAUDE.md Session 196 — different key files on the
# daemon). The fallback returned the wrong key whenever it was hit;
# sigauth then failed with `invalid_signature` even on legitimate
# traffic. Substrate `signature_verification_failures` correctly
# flagged 100% fail on north-valley-branch-2 because of this.
#
# These tests lock in the post-removal behavior: when the identity
# view is empty, `_resolve_pubkey` returns None even if
# `site_appliances` has a populated `agent_public_key`. Sigauth then
# reports `unknown_pubkey` (the honest reason), not `invalid_signature`.


def _fake_conn_no_identity(view_pubkey_hex: Optional[str] = None,
                            view_fingerprint: Optional[str] = None):
    """Fake conn modeling: site_appliances has NO agent_identity_public_key
    populated (column is NULL → query's WHERE clause filters out the
    row, fetchrow returns None). v_current_appliance_identity may or
    may not have a row.
    Used to test the resolution chain without the new identity-key
    hot path firing."""
    conn = AsyncMock()

    async def fetchrow(query, *args):
        if "agent_identity_public_key" in query:
            # Identity-key column NULL → WHERE clause filters out.
            return None
        if "v_current_appliance_identity" in query:
            if view_pubkey_hex:
                return {
                    "agent_pubkey_hex": view_pubkey_hex,
                    "agent_pubkey_fingerprint": view_fingerprint,
                }
            return None
        return None

    conn.fetchrow = fetchrow
    return conn


def _fake_conn_with_identity(identity_pubkey_hex: str):
    """Fake conn where the new agent_identity_public_key column is
    populated (Commit A wrote it). v_current_appliance_identity
    irrelevant — identity column wins as priority 1."""
    conn = AsyncMock()

    async def fetchrow(query, *args):
        if "agent_identity_public_key" in query:
            return {"agent_identity_public_key": identity_pubkey_hex}
        if "v_current_appliance_identity" in query:
            # If reached, the identity-key fast path didn't return —
            # tests using this fake fail loudly.
            raise AssertionError(
                "v_current_appliance_identity queried — should NOT "
                "happen when agent_identity_public_key column is set"
            )
        return None

    conn.fetchrow = fetchrow
    return conn


# Need Optional import for the new helper.
from typing import Optional  # noqa: E402


@pytest.mark.asyncio
async def test_resolve_pubkey_uses_identity_column_first():
    """Commit C priority: when site_appliances.agent_identity_public_key
    is populated, _resolve_pubkey returns it WITHOUT querying the
    legacy v_current_appliance_identity view. Locks the new ordering."""
    pk = "a" * 64
    conn = _fake_conn_with_identity(pk)
    result = await signature_auth._resolve_pubkey(
        conn, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
    )
    assert result is not None
    pubkey, fp = result
    assert pubkey == pk
    assert fp == signature_auth._fingerprint(pk)


@pytest.mark.asyncio
async def test_resolve_pubkey_falls_back_to_view_when_identity_null():
    """Commit C fallback: identity column NULL but v_current view has
    a row → return the view's pubkey. Covers daemons that have a
    claim event but haven't yet checked in with v0.4.13+."""
    view_pk = "b" * 64
    view_fp = signature_auth._fingerprint(view_pk)
    conn = _fake_conn_no_identity(view_pubkey_hex=view_pk, view_fingerprint=view_fp)
    result = await signature_auth._resolve_pubkey(
        conn, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
    )
    assert result == (view_pk, view_fp)


@pytest.mark.asyncio
async def test_resolve_pubkey_returns_none_when_neither_source_has_data():
    """Commit C: no identity column, no view row → None
    (= unknown_pubkey for the caller). Replaces the post-Step-3 test
    of the same intent — semantics preserved, only the fake's shape
    changed because the column queried is different."""
    conn = _fake_conn_no_identity(view_pubkey_hex=None)
    result = await signature_auth._resolve_pubkey(
        conn, site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
    )
    assert result is None, (
        "Expected None when neither agent_identity_public_key nor "
        "v_current_appliance_identity has a row for this MAC. If this "
        "fires, an unsound fallback may have been re-added — see #179."
    )


@pytest.mark.asyncio
async def test_verify_returns_unknown_pubkey_when_no_sources_have_data():
    """End-to-end: a MAC with neither identity column nor claim event
    returns reason=unknown_pubkey, NOT reason=invalid_signature.
    The substrate's sigauth_crypto_failures invariant depends on
    this distinction — real crypto fails should be the priority
    signal, not enrollment debt masquerading as crypto fails."""
    conn = _fake_conn_no_identity()
    sig = base64.urlsafe_b64encode(b"x" * 64).rstrip(b"=").decode()
    req = _fake_request(
        "POST", "/x",
        {
            "X-Appliance-Signature": sig,
            "X-Appliance-Timestamp": _ts_now(),
            "X-Appliance-Nonce": "0f" * 16,
        },
    )
    result = await signature_auth.verify_appliance_signature(
        req, conn=conn, site_id="s", mac_address="AA:BB", body_bytes=b"",
    )
    assert result.valid is False
    assert result.reason == "unknown_pubkey"
