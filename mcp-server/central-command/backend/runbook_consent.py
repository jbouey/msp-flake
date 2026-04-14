"""Runbook consent attestation (Migration 184 Phase 1).

Pure helpers — NO wire-up yet. Phase 2 will plug `verify_consent_active()`
into the L1/L2 engines; Phase 4 adds UI + registry population.

Design:
  * Consent payload is deterministic bytes over:
        sha256(site_id || '|' || class_id || '|' || email || '|' || consented_at_iso || '|' || ttl_days)
    Pipe separators make the concatenation unambiguous when any field
    contains the others' delimiters.
  * Signature is Ed25519 over those bytes.
  * Pubkey is stored alongside the signature so verifiers don't need
    out-of-band key lookup.
  * The signing PRIVATE key is never persisted; the server generates a
    fresh keypair per consent when the customer approves, returns the
    consent_id to the customer, and discards the private key after
    signing. This makes the consent non-repudiable by its pubkey alone.
  * Script SHA is computed over raw file bytes — no newline normalization,
    no trimming, to match exactly what systemd-run executes on disk.

Reuses the existing `compliance_bundles` hash chain + OTS anchoring by
writing a `check_type='runbook_consent'` row for every consent / amend
/ revoke transition (that happens at the caller, not here).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

# Ed25519 primitives — we already use pynacl elsewhere for evidence
# signing, so no new dep.
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError


__all__ = [
    "ConsentPayload",
    "ConsentKeyPair",
    "build_consent_payload",
    "sign_consent_payload",
    "verify_consent_signature",
    "generate_consent_keypair",
    "compute_script_sha256",
    "CONSENT_SEPARATOR",
]


# Pipe chosen because it's rare in emails and never used in the other
# fields we concatenate. Using a non-printable would save one byte but
# makes test fixtures harder to read.
CONSENT_SEPARATOR = "|"


class ConsentPayload(NamedTuple):
    """The bytes that get signed. Keep this struct stable forever —
    changing it will break verification of every historical consent."""

    site_id: str
    class_id: str
    consented_by_email: str
    consented_at_iso: str  # RFC3339, UTC
    ttl_days: int

    def to_bytes(self) -> bytes:
        return CONSENT_SEPARATOR.join([
            self.site_id,
            self.class_id,
            self.consented_by_email,
            self.consented_at_iso,
            str(self.ttl_days),
        ]).encode("utf-8")


class ConsentKeyPair(NamedTuple):
    """Server-generated Ed25519 keypair used for one consent."""

    private_key_bytes: bytes  # 32 bytes — DISCARD after signing
    public_key_bytes: bytes   # 32 bytes — store on the row


def build_consent_payload(
    *,
    site_id: str,
    class_id: str,
    consented_by_email: str,
    consented_at: datetime | None = None,
    ttl_days: int = 365,
) -> ConsentPayload:
    """Assemble a deterministic consent payload.

    `consented_at` defaults to NOW() in UTC, but callers SHOULD pass the
    exact timestamp they intend to persist on the `runbook_class_consent`
    row — otherwise the bytes that get signed won't match what the row
    stores.
    """
    if consented_at is None:
        consented_at = datetime.now(timezone.utc)
    if consented_at.tzinfo is None:
        raise ValueError("consented_at must be timezone-aware")
    if ttl_days < 1 or ttl_days > 3650:
        raise ValueError("ttl_days must be between 1 and 3650")
    if not site_id or CONSENT_SEPARATOR in site_id:
        raise ValueError("site_id must be non-empty and contain no '|'")
    if not class_id or CONSENT_SEPARATOR in class_id:
        raise ValueError("class_id must be non-empty and contain no '|'")
    if "@" not in consented_by_email or CONSENT_SEPARATOR in consented_by_email:
        raise ValueError("consented_by_email must be a valid email without '|'")

    return ConsentPayload(
        site_id=site_id,
        class_id=class_id,
        consented_by_email=consented_by_email,
        consented_at_iso=consented_at.isoformat(),
        ttl_days=ttl_days,
    )


def generate_consent_keypair() -> ConsentKeyPair:
    """Fresh Ed25519 keypair for a single consent. Caller discards the
    private key after signing — only the pubkey + signature persist."""
    sk = SigningKey.generate()
    return ConsentKeyPair(
        private_key_bytes=bytes(sk),
        public_key_bytes=bytes(sk.verify_key),
    )


def sign_consent_payload(payload: ConsentPayload, private_key_bytes: bytes) -> bytes:
    """Return a 64-byte Ed25519 signature over payload.to_bytes()."""
    if len(private_key_bytes) != 32:
        raise ValueError("private_key_bytes must be 32 bytes (Ed25519 seed)")
    sk = SigningKey(private_key_bytes)
    signed = sk.sign(payload.to_bytes())
    # .signature is 64 bytes; .message is the original payload.
    return bytes(signed.signature)


def verify_consent_signature(
    payload: ConsentPayload,
    signature: bytes,
    public_key_bytes: bytes,
) -> bool:
    """Return True iff the signature verifies for payload under pubkey."""
    if len(public_key_bytes) != 32:
        return False
    if len(signature) != 64:
        return False
    try:
        VerifyKey(public_key_bytes).verify(payload.to_bytes(), signature)
    except (BadSignatureError, Exception):  # noqa: BLE001 — intentionally broad
        return False
    return True


def compute_script_sha256(script_path: str | Path) -> str:
    """SHA-256 over raw file bytes. Matches what `systemd-run` executes.

    No newline normalization, no trimming — runbooks check this at
    execution time; any mismatch blocks the run (`SCRIPT_DRIFT`).
    """
    p = Path(script_path)
    if not p.is_file():
        raise FileNotFoundError(f"script not found: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── Test-only helper (exported for test_runbook_consent.py) ──────

def _random_seed_for_tests() -> bytes:
    """Deterministic-enough randomness for tests. Do NOT use in prod."""
    return secrets.token_bytes(32)
