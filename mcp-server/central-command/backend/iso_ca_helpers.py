"""Pure-function helpers for iso_ca.

Split out from iso_ca.py so the unit tests don't drag in the DB
import chain (fleet → tenant_middleware → asyncpg pool). Anything
that needs to run without a server should live here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_cert(payload: dict) -> bytes:
    """Same canonicalization the mint script uses to produce the cert
    signature. Drift here breaks every claim cert ever issued."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_csr(
    *,
    site_id: str,
    mac_address: str,
    agent_pubkey_hex: str,
    hardware_id: str | None,
    nonce: str,
    timestamp: str,
    claim_cert_payload: dict,
) -> bytes:
    """Canonical bytes for the CSR signature.

    The signature proves the requester possesses the agent_pubkey AND
    that the request is bound to THIS specific claim cert (an attacker
    can't replay a stolen cert under a different pubkey)."""
    parts = [
        site_id,
        mac_address.upper(),
        agent_pubkey_hex.lower(),
        hardware_id or "",
        nonce.lower(),
        timestamp,
        json.dumps(claim_cert_payload, sort_keys=True, separators=(",", ":")),
    ]
    return ("\n".join(parts)).encode("utf-8")


def b64url_decode_padless(b64: str) -> bytes:
    """Restore padding then decode base64url."""
    pad = "=" * ((4 - len(b64) % 4) % 4)
    return base64.urlsafe_b64decode(b64 + pad)


def fingerprint(pub_hex: str) -> str:
    """16 lowercase hex chars from sha256(raw_pubkey_bytes). Same
    derivation as signature_auth and identity.go."""
    if not pub_hex or len(pub_hex) != 64:
        return ""
    try:
        raw = bytes.fromhex(pub_hex)
    except ValueError:
        return ""
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class CAValidation:
    ok: bool
    reason: str = ""
    detail: str = ""


def validate_cert_signature(
    *,
    cert_payload: dict,
    cert_signature_b64: str,
    expected_ca_pubkey_hex: str,
) -> CAValidation:
    """Signature-only validation. Caller supplies the registered CA
    pubkey (looked up from the DB) and we check the embedded sig
    against it. Validity windows + revocation are caller's job."""
    if cert_payload.get("ca_pubkey_hex") != expected_ca_pubkey_hex:
        return CAValidation(False, "ca_pubkey_mismatch",
                            "cert claims a different pubkey than the registered CA")
    try:
        sig_raw = b64url_decode_padless(cert_signature_b64)
    except (binascii.Error, ValueError) as e:
        return CAValidation(False, "bad_cert_signature_format", str(e))
    if len(sig_raw) != 64:
        return CAValidation(False, "bad_cert_signature_format",
                            f"ed25519 sig length {len(sig_raw)} != 64")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(expected_ca_pubkey_hex))
    except (ValueError, TypeError) as e:
        return CAValidation(False, "ca_pubkey_decode_failed", str(e))
    try:
        pub.verify(sig_raw, canonical_cert(cert_payload))
    except InvalidSignature:
        return CAValidation(False, "cert_signature_invalid",
                            "claim cert signature did not verify against registered CA")
    return CAValidation(True)


def verify_csr_signature(
    *,
    site_id: str,
    mac_address: str,
    agent_pubkey_hex: str,
    hardware_id: str | None,
    nonce: str,
    timestamp: str,
    claim_cert_payload: dict,
    csr_signature_b64: str,
) -> bool:
    """Verify the CSR signature using the agent_pubkey from the
    payload itself. This proves the daemon holds the matching
    private key — without this, anyone could submit any pubkey."""
    try:
        sig_raw = b64url_decode_padless(csr_signature_b64)
    except (binascii.Error, ValueError):
        return False
    if len(sig_raw) != 64:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(agent_pubkey_hex))
    except (ValueError, TypeError):
        return False
    canonical = canonical_csr(
        site_id=site_id, mac_address=mac_address,
        agent_pubkey_hex=agent_pubkey_hex,
        hardware_id=hardware_id, nonce=nonce, timestamp=timestamp,
        claim_cert_payload=claim_cert_payload,
    )
    try:
        pub.verify(sig_raw, canonical)
        return True
    except InvalidSignature:
        return False
