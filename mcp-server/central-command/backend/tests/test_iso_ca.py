"""Unit tests for iso_ca_helpers — pure-function half of the iso_ca
endpoint module. Hermetic: no DB, no FastAPI, just the byte layouts
and signature verification that have to stay locked in step with
the daemon + the mint script.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import iso_ca_helpers as h  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub.hex()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _make_cert_payload(ca_pub_hex: str, *, iso_release_sha: str = "a" * 40,
                      valid_days: int = 90) -> dict:
    issued = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "iso_release_sha": iso_release_sha,
        "ca_pubkey_hex": ca_pub_hex,
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_until": (issued + timedelta(days=valid_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
    }


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def test_canonical_cert_is_sort_key_compact():
    out = h.canonical_cert({"b": 2, "a": 1})
    assert out == b'{"a":1,"b":2}'


def test_canonical_cert_round_trips_real_payload():
    payload = _make_cert_payload("a" * 64)
    out = h.canonical_cert(payload)
    decoded = json.loads(out)
    assert decoded == payload


def test_canonical_csr_matches_documented_layout():
    cert_payload = _make_cert_payload("a" * 64)
    out = h.canonical_csr(
        site_id="site-x",
        mac_address="aa:bb:cc:dd:ee:ff",  # daemon may send mixed case
        agent_pubkey_hex="0" * 64,
        hardware_id="HW-A",
        nonce="ABCDEF" + "00" * 13,  # mixed case nonce
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
    )
    parts = out.decode().split("\n")
    assert parts[0] == "site-x"
    assert parts[1] == "AA:BB:CC:DD:EE:FF"  # uppercased
    assert parts[2] == "0" * 64  # lowercased
    assert parts[3] == "HW-A"
    assert parts[4] == ("abcdef" + "00" * 13)  # lowercased
    assert parts[5] == "2026-04-15T03:45:23Z"
    assert json.loads(parts[6]) == cert_payload


def test_canonical_csr_handles_no_hardware_id():
    out = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="0" * 64,
        hardware_id=None,
        nonce="a" * 32, timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=_make_cert_payload("a" * 64),
    )
    # Empty string in the position of hardware_id.
    parts = out.decode().split("\n")
    assert parts[3] == ""


# ---------------------------------------------------------------------------
# Fingerprint + base64url
# ---------------------------------------------------------------------------


def test_fingerprint_matches_python_helper_value():
    _, pub_hex = _make_keypair()
    expected = hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16]
    assert h.fingerprint(pub_hex) == expected


def test_fingerprint_rejects_garbage():
    assert h.fingerprint("") == ""
    assert h.fingerprint("not hex") == ""
    assert h.fingerprint("a" * 63) == ""


def test_b64url_decode_padless_round_trip():
    raw = b"the quick brown fox jumps over the lazy dog 1234567890!@#$"
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert h.b64url_decode_padless(b64) == raw


# ---------------------------------------------------------------------------
# Cert signature validation
# ---------------------------------------------------------------------------


def test_validate_cert_signature_happy_path():
    priv, pub_hex = _make_keypair()
    payload = _make_cert_payload(pub_hex)
    sig = _b64url(priv.sign(h.canonical_cert(payload)))
    res = h.validate_cert_signature(
        cert_payload=payload, cert_signature_b64=sig,
        expected_ca_pubkey_hex=pub_hex,
    )
    assert res.ok is True


def test_validate_cert_signature_rejects_pubkey_drift():
    priv1, pub1_hex = _make_keypair()
    _, pub2_hex = _make_keypair()
    payload = _make_cert_payload(pub1_hex)  # cert claims pub1
    sig = _b64url(priv1.sign(h.canonical_cert(payload)))
    res = h.validate_cert_signature(
        cert_payload=payload, cert_signature_b64=sig,
        expected_ca_pubkey_hex=pub2_hex,  # but registered CA is pub2
    )
    assert res.ok is False
    assert res.reason == "ca_pubkey_mismatch"


def test_validate_cert_signature_rejects_tampered_signature():
    priv, pub_hex = _make_keypair()
    payload = _make_cert_payload(pub_hex)
    sig_bytes = bytearray(priv.sign(h.canonical_cert(payload)))
    sig_bytes[0] ^= 0xFF
    res = h.validate_cert_signature(
        cert_payload=payload,
        cert_signature_b64=_b64url(bytes(sig_bytes)),
        expected_ca_pubkey_hex=pub_hex,
    )
    assert res.reason == "cert_signature_invalid"


def test_validate_cert_signature_rejects_short_signature():
    _, pub_hex = _make_keypair()
    res = h.validate_cert_signature(
        cert_payload=_make_cert_payload(pub_hex),
        cert_signature_b64=_b64url(b"x" * 32),
        expected_ca_pubkey_hex=pub_hex,
    )
    assert res.reason == "bad_cert_signature_format"


def test_validate_cert_signature_rejects_garbage_b64():
    _, pub_hex = _make_keypair()
    res = h.validate_cert_signature(
        cert_payload=_make_cert_payload(pub_hex),
        cert_signature_b64="!!!not-base64!!!",
        expected_ca_pubkey_hex=pub_hex,
    )
    assert res.reason == "bad_cert_signature_format"


# ---------------------------------------------------------------------------
# CSR signature verification
# ---------------------------------------------------------------------------


def test_verify_csr_signature_happy_path():
    csr_priv, csr_pub_hex = _make_keypair()
    cert_payload = _make_cert_payload("a" * 64)

    canonical = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
    )
    sig = _b64url(csr_priv.sign(canonical))
    assert h.verify_csr_signature(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
        csr_signature_b64=sig,
    ) is True


def test_verify_csr_signature_rejects_wrong_key():
    _, claimed_pub_hex = _make_keypair()
    actual_priv, _ = _make_keypair()  # signing with the wrong private key

    cert_payload = _make_cert_payload("a" * 64)
    canonical = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=claimed_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
    )
    sig = _b64url(actual_priv.sign(canonical))
    assert h.verify_csr_signature(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=claimed_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
        csr_signature_b64=sig,
    ) is False


def test_verify_csr_signature_detects_payload_tamper():
    csr_priv, csr_pub_hex = _make_keypair()
    cert_payload = _make_cert_payload("a" * 64)

    # Sign for site=s.
    canonical = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
    )
    sig = _b64url(csr_priv.sign(canonical))

    # Verify under a DIFFERENT site_id — should fail.
    assert h.verify_csr_signature(
        site_id="different-site",
        mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
        csr_signature_b64=sig,
    ) is False


def test_verify_csr_signature_detects_cert_swap():
    """An attacker who steals a CSR and swaps the embedded cert
    contents must not be able to bypass verification."""
    csr_priv, csr_pub_hex = _make_keypair()
    cert_payload_a = _make_cert_payload("a" * 64)
    cert_payload_b = _make_cert_payload("b" * 64)

    canonical = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload_a,
    )
    sig = _b64url(csr_priv.sign(canonical))

    # Swap the cert payload — verifier rebuilds canonical with B,
    # signature was over A → mismatch.
    assert h.verify_csr_signature(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=csr_pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload_b,
        csr_signature_b64=sig,
    ) is False


def test_verify_csr_signature_handles_garbage_sig_gracefully():
    _, pub_hex = _make_keypair()
    assert h.verify_csr_signature(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex=pub_hex,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=_make_cert_payload("a" * 64),
        csr_signature_b64="!!!not-base64!!!",
    ) is False


def test_verify_csr_signature_rejects_garbage_pubkey():
    csr_priv, _ = _make_keypair()
    cert_payload = _make_cert_payload("a" * 64)
    canonical = h.canonical_csr(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="zz" * 32,  # not hex
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
    )
    sig = _b64url(csr_priv.sign(canonical))
    assert h.verify_csr_signature(
        site_id="s", mac_address="AA:BB:CC:DD:EE:FF",
        agent_pubkey_hex="zz" * 32,
        hardware_id="HW", nonce="ab" * 16,
        timestamp="2026-04-15T03:45:23Z",
        claim_cert_payload=cert_payload,
        csr_signature_b64=sig,
    ) is False
