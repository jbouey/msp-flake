"""Contract tests for Migration 184 Phase 1 — runbook consent sign/verify.

Six tests from docs/migration-184-runbook-attestation-spec.md §Contract tests.
These pin the cryptographic + structural invariants BEFORE the schema
lands in production, so any regression in the payload format or the
sign/verify roundtrip fails CI immediately.

Tests 1–4 are pure Python (no DB). Tests 5–6 need a file on disk to
hash. All run under the existing pytest config.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup — tests import via `dashboard_api.X` which resolves through
# the `mcp-server/dashboard_api -> central-command/backend` symlink.
# CI working-directory is backend/, so we add mcp-server/ to sys.path.
# See test_evidence_dedup.py for the canonical version of this dance.
# ---------------------------------------------------------------------------

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_mcp_server_dir = os.path.dirname(os.path.dirname(_backend_dir))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _mcp_server_dir not in sys.path:
    sys.path.insert(0, _mcp_server_dir)

from dashboard_api.runbook_consent import (  # noqa: E402
    build_consent_payload,
    sign_consent_payload,
    verify_consent_signature,
    generate_consent_keypair,
    compute_script_sha256,
    CONSENT_SEPARATOR,
)


# ─── Helpers ─────────────────────────────────────────────────────

FIXED_TIME = datetime(2026, 4, 14, 0, 0, 0, tzinfo=timezone.utc)


def _standard_payload(**overrides):
    base = {
        "site_id": "drakes-dental",
        "class_id": "DNS_ROTATION",
        "consented_by_email": "manager@drakes-dental.com",
        "consented_at": FIXED_TIME,
        "ttl_days": 365,
    }
    base.update(overrides)
    return build_consent_payload(**base)


# ─── Test 1 — sign/verify roundtrip ──────────────────────────────

def test_consent_sign_verify_roundtrip():
    """A freshly-signed consent must verify against its own pubkey."""
    payload = _standard_payload()
    kp = generate_consent_keypair()
    sig = sign_consent_payload(payload, kp.private_key_bytes)
    assert len(sig) == 64, "Ed25519 signatures are always 64 bytes"
    assert len(kp.public_key_bytes) == 32, "Ed25519 pubkeys are 32 bytes"
    assert verify_consent_signature(payload, sig, kp.public_key_bytes) is True


# ─── Test 2 — payload is deterministic ───────────────────────────

def test_payload_bytes_deterministic():
    """Building two payloads with identical inputs must yield byte-
    identical representations. If this ever drifts, historical
    signatures stop verifying — catastrophic for audit trail."""
    p1 = _standard_payload()
    p2 = _standard_payload()
    assert p1.to_bytes() == p2.to_bytes()
    # Confirm the separator is where we expect so downstream tooling
    # (docs, audit viewers) can parse the payload if needed.
    assert p1.to_bytes().decode().count(CONSENT_SEPARATOR) == 4


# ─── Test 3 — signature mutation fails verification ──────────────

def test_signature_mutation_fails():
    """Any single-byte edit to either the signature or the payload
    must cause verification to fail. This is the whole point of
    cryptographic consent."""
    payload = _standard_payload()
    kp = generate_consent_keypair()
    sig = sign_consent_payload(payload, kp.private_key_bytes)

    # Flip the last byte of the signature
    bad_sig = sig[:-1] + bytes([sig[-1] ^ 0x01])
    assert verify_consent_signature(payload, bad_sig, kp.public_key_bytes) is False

    # Mutate the payload (change the email by 1 char)
    mutated = _standard_payload(consented_by_email="manager+x@drakes-dental.com")
    assert verify_consent_signature(mutated, sig, kp.public_key_bytes) is False


# ─── Test 4 — wrong pubkey fails verification ────────────────────

def test_different_key_fails_verify():
    """Signing with key A and verifying with key B must fail, even
    when the payload is identical."""
    payload = _standard_payload()
    kp_a = generate_consent_keypair()
    kp_b = generate_consent_keypair()
    assert kp_a.public_key_bytes != kp_b.public_key_bytes

    sig = sign_consent_payload(payload, kp_a.private_key_bytes)
    assert verify_consent_signature(payload, sig, kp_b.public_key_bytes) is False


# ─── Test 5 — script SHA matches disk ────────────────────────────

def test_script_sha_matches_disk(tmp_path: Path):
    """The registry's stored SHA must equal what's computed at
    execution time over the raw file bytes. No normalization."""
    script = tmp_path / "rb.sh"
    body = b"#!/bin/bash\necho hello world\n"
    script.write_bytes(body)

    # Manual hash for cross-check
    import hashlib
    expected = hashlib.sha256(body).hexdigest()
    assert compute_script_sha256(script) == expected
    assert len(expected) == 64


# ─── Test 6 — SHA mismatches after script edit ───────────────────

def test_script_sha_mismatches_after_edit(tmp_path: Path):
    """Any byte-level change to the script invalidates the SHA.
    Execution must block in Phase 2 when this happens (SCRIPT_DRIFT)."""
    script = tmp_path / "rb.sh"
    script.write_bytes(b"#!/bin/bash\necho v1\n")
    sha_before = compute_script_sha256(script)

    # Simulate a subtle edit — add one byte
    script.write_bytes(b"#!/bin/bash\necho v1 \n")  # added trailing space
    sha_after = compute_script_sha256(script)

    assert sha_before != sha_after


# ─── Negative input validation ───────────────────────────────────

def test_ttl_out_of_range_rejected():
    with pytest.raises(ValueError, match="ttl_days"):
        _standard_payload(ttl_days=0)
    with pytest.raises(ValueError, match="ttl_days"):
        _standard_payload(ttl_days=10_000)


def test_bad_email_rejected():
    with pytest.raises(ValueError, match="email"):
        _standard_payload(consented_by_email="not-an-email")


def test_separator_in_input_rejected():
    with pytest.raises(ValueError, match=CONSENT_SEPARATOR):
        _standard_payload(site_id="dangerous|site")
    with pytest.raises(ValueError, match=CONSENT_SEPARATOR):
        _standard_payload(class_id=f"DNS{CONSENT_SEPARATOR}ROTATION")
