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


# ═══════════════════════════════════════════════════════════════════
# Phase 2 — DB-layer helpers (SHADOW mode)
#
# These bridge the pure crypto helpers above to the actual tables
# shipped in migration 184 + the existing promoted_rule_events ledger
# from the flywheel spine (migration 181).
#
# Mode is controlled by `RUNBOOK_CONSENT_MODE` env var:
#   - 'shadow'  (default): verify_consent_active LOGS result; callers
#                use `.should_block()` which ALWAYS returns False.
#   - 'enforce' (later):   `.should_block()` returns True when consent
#                is missing/expired/revoked. Phase 3 flip.
# ═══════════════════════════════════════════════════════════════════
import os as _os
import json as _json
import typing as _t
from dataclasses import dataclass as _dataclass


CONSENT_MODE_ENV = "RUNBOOK_CONSENT_MODE"


def get_consent_mode() -> str:
    mode = (_os.getenv(CONSENT_MODE_ENV) or "shadow").lower()
    return "enforce" if mode == "enforce" else "shadow"


@_dataclass
class ConsentCheckResult:
    """Outcome of `verify_consent_active()`.

    `ok` is True when a non-revoked, non-expired consent row exists
    for (site_id, class_id). `reason` is a short token the UI / logs
    can display: `ok` / `no_consent` / `expired` / `revoked` / `unknown_class`.
    """

    ok: bool
    reason: str
    consent_id: str | None = None
    expires_at: str | None = None

    def should_block(self) -> bool:
        """True iff consent is missing AND mode is enforce."""
        if self.ok:
            return False
        return get_consent_mode() == "enforce"


# Deterministic runbook_id → class_id classification. This mirrors the
# prefix conventions already used throughout the daemon's runbook
# registry (RB-AUTO-SERVICE_*, RB-WIN-PATCH-*, etc). Once the
# runbook_registry table is populated in Phase 4, we'll look up the
# class there; this fallback exists so shadow mode can start logging
# immediately without waiting for registry population.
_CLASS_PATTERNS: list[tuple[str, str]] = [
    # (substring or prefix to match, class_id)
    ("SERVICE",            "SERVICE_RESTART"),
    ("DNS",                "DNS_ROTATION"),
    ("FIREWALL",           "FIREWALL_RULE"),
    ("CERT",               "CERT_ROTATION"),
    ("BACKUP",             "BACKUP_RETRY"),
    ("PATCH",              "PATCH_INSTALL"),
    ("UPDATE",             "PATCH_INSTALL"),
    ("GPO",                "GROUP_POLICY_RESET"),
    ("GROUP_POLICY",       "GROUP_POLICY_RESET"),
    ("DEFENDER-EXCLUS",    "DEFENDER_EXCLUSION"),
    ("DEFENDER_EXCLUS",    "DEFENDER_EXCLUSION"),
    ("PERSIST",            "PERSISTENCE_CLEANUP"),
    ("ACCOUNT",            "ACCOUNT_DISABLE"),
    ("USER-DISABLE",       "ACCOUNT_DISABLE"),
    ("LOG-ARCHIVE",        "LOG_ARCHIVE"),
    ("LOG_ARCHIVE",        "LOG_ARCHIVE"),
    ("SYNC-RULE",          "CONFIG_SYNC"),
    ("SYNC_RULE",          "CONFIG_SYNC"),
    ("CONFIG",             "CONFIG_SYNC"),
    ("DRIFT",              "CONFIG_SYNC"),
]


def classify_runbook_to_class(runbook_id: str | None) -> str | None:
    """Return class_id for a runbook, or None if unclassifiable.

    Pattern match against `_CLASS_PATTERNS` (case-insensitive). First
    match wins — order matters: more specific tokens first. Returning
    None is an explicit signal that pre-execution check should treat
    this as `unknown_class` and fall through (in shadow) or block (in
    enforce, Phase 3+ — with an operator override).
    """
    if not runbook_id:
        return None
    up = runbook_id.upper()
    for token, class_id in _CLASS_PATTERNS:
        if token in up:
            return class_id
    return None


# NOTE: Type hints use `_t.Any` for the DB session param because this
# module is imported from both asyncpg-connection contexts (tenant_
# connection / admin_connection) and SQLAlchemy AsyncSession (agent_api).
# Both support `.execute(text(...))`-style calls via execute_with_retry.


async def create_consent(
    db: _t.Any,
    *,
    site_id: str,
    class_id: str,
    consented_by_email: str,
    ttl_days: int = 365,
    evidence_bundle_id: str | None = None,
) -> str:
    """Insert a fresh consent row + ledger event.

    Generates a new Ed25519 keypair per consent, signs the payload,
    writes the row, and discards the private key. Caller supplies the
    evidence_bundle_id (from compliance_bundles hash chain); if None,
    we stamp a placeholder and rely on Phase 4 to stitch real bundles.

    Returns the consent_id (UUID string).
    """
    from sqlalchemy import text as _sql_text  # local import: optional dep

    if not evidence_bundle_id:
        # Placeholder until Phase 4 wires the evidence chain write.
        evidence_bundle_id = f"CONSENT-PLACEHOLDER-{secrets.token_hex(6)}"

    now_utc = datetime.now(timezone.utc)
    payload = build_consent_payload(
        site_id=site_id,
        class_id=class_id,
        consented_by_email=consented_by_email,
        consented_at=now_utc,
        ttl_days=ttl_days,
    )
    kp = generate_consent_keypair()
    signature = sign_consent_payload(payload, kp.private_key_bytes)
    # Private key is never persisted — it goes out of scope here.

    result = await db.execute(
        _sql_text("""
            INSERT INTO runbook_class_consent
                (site_id, class_id, consented_by_email, consented_at,
                 client_signature, client_pubkey, consent_ttl_days,
                 evidence_bundle_id)
            VALUES
                (:site_id, :class_id, :email, :consented_at,
                 :signature, :pubkey, :ttl,
                 :bundle_id)
            RETURNING consent_id
        """),
        {
            "site_id": site_id,
            "class_id": class_id,
            "email": consented_by_email,
            "consented_at": now_utc,
            "signature": signature,
            "pubkey": kp.public_key_bytes,
            "ttl": ttl_days,
            "bundle_id": evidence_bundle_id,
        },
    )
    row = result.fetchone()
    consent_id = str(row[0]) if row else None
    if consent_id is None:
        raise RuntimeError("consent insert returned no consent_id")

    # Ledger event — runbook.consented
    await db.execute(
        _sql_text("""
            INSERT INTO promoted_rule_events
                (rule_id, event_type, actor, stage, outcome, reason, proof, created_at)
            VALUES
                (:rule_id, 'runbook.consented', :actor, 'consent', 'success',
                 :reason, :proof::jsonb, NOW())
        """),
        {
            "rule_id": f"CONSENT:{class_id}@{site_id}",
            "actor": consented_by_email,
            "reason": f"class-level consent granted for {class_id} ({ttl_days}d TTL)",
            "proof": _json.dumps({
                "consent_id": consent_id,
                "bundle_id": evidence_bundle_id,
                "pubkey_fingerprint": hashlib.sha256(kp.public_key_bytes).hexdigest()[:16],
            }),
        },
    )
    return consent_id


async def revoke_consent(
    db: _t.Any,
    *,
    consent_id: str,
    revoked_by_email: str,
    reason: str,
) -> None:
    """Mark a consent as revoked + write ledger event.

    Idempotent: revoking an already-revoked row is a no-op (but still
    writes a ledger event, so the audit trail reflects the attempt).
    """
    from sqlalchemy import text as _sql_text

    if len(reason) < 10:
        raise ValueError("reason must be >= 10 chars")
    if "@" not in revoked_by_email:
        raise ValueError("revoked_by_email must be valid email")

    result = await db.execute(
        _sql_text("""
            UPDATE runbook_class_consent
            SET revoked_at = NOW(),
                revocation_reason = :reason
            WHERE consent_id = :consent_id
              AND revoked_at IS NULL
            RETURNING site_id, class_id
        """),
        {"consent_id": consent_id, "reason": reason},
    )
    row = result.fetchone()
    if row is None:
        # Already revoked or doesn't exist — log-only, no raise
        site_id, class_id = "unknown", "unknown"
    else:
        site_id, class_id = row[0], row[1]

    await db.execute(
        _sql_text("""
            INSERT INTO promoted_rule_events
                (rule_id, event_type, actor, stage, outcome, reason, proof, created_at)
            VALUES
                (:rule_id, 'runbook.revoked', :actor, 'consent', 'success',
                 :reason, :proof::jsonb, NOW())
        """),
        {
            "rule_id": f"CONSENT:{class_id}@{site_id}",
            "actor": revoked_by_email,
            "reason": reason[:500],
            "proof": _json.dumps({"consent_id": consent_id}),
        },
    )


async def verify_consent_active(
    db: _t.Any,
    *,
    site_id: str,
    class_id: str | None,
) -> ConsentCheckResult:
    """Return the current consent posture for (site_id, class_id).

    Does NOT block — `result.should_block()` decides based on mode.
    """
    from sqlalchemy import text as _sql_text

    if not class_id:
        return ConsentCheckResult(ok=False, reason="unknown_class")

    result = await db.execute(
        _sql_text("""
            SELECT consent_id,
                   consented_at,
                   consent_ttl_days,
                   revoked_at,
                   (consented_at + (consent_ttl_days || ' days')::INTERVAL) AS expires_at
            FROM runbook_class_consent
            WHERE site_id = :site_id
              AND class_id = :class_id
              AND revoked_at IS NULL
            ORDER BY consented_at DESC
            LIMIT 1
        """),
        {"site_id": site_id, "class_id": class_id},
    )
    row = result.fetchone()
    if row is None:
        return ConsentCheckResult(ok=False, reason="no_consent")

    consent_id = str(row[0])
    expires_at = row[4]
    now_utc = datetime.now(timezone.utc)
    if expires_at and expires_at < now_utc:
        return ConsentCheckResult(
            ok=False,
            reason="expired",
            consent_id=consent_id,
            expires_at=expires_at.isoformat(),
        )
    return ConsentCheckResult(
        ok=True,
        reason="ok",
        consent_id=consent_id,
        expires_at=expires_at.isoformat() if expires_at else None,
    )


async def record_executed_with_consent(
    db: _t.Any,
    *,
    site_id: str,
    class_id: str,
    runbook_id: str,
    consent_id: str | None,
    incident_id: str | None = None,
) -> None:
    """Append `runbook.executed_with_consent` to the ledger.

    Callers invoke AFTER a runbook has been dispatched (order created,
    not yet completion-acked). Even in shadow mode, write the event
    whenever consent IS present — that's a legitimate audit signal.
    """
    from sqlalchemy import text as _sql_text

    await db.execute(
        _sql_text("""
            INSERT INTO promoted_rule_events
                (rule_id, event_type, actor, stage, outcome, reason, proof, created_at)
            VALUES
                (:rule_id, 'runbook.executed_with_consent', 'system', 'execution',
                 :outcome, :reason, :proof::jsonb, NOW())
        """),
        {
            "rule_id": f"CONSENT:{class_id}@{site_id}",
            "outcome": "success" if consent_id else "noop",
            "reason": f"runbook {runbook_id} dispatched; consent={consent_id or 'shadow-absent'}",
            "proof": _json.dumps({
                "consent_id": consent_id,
                "runbook_id": runbook_id,
                "incident_id": incident_id,
                "mode": get_consent_mode(),
            }),
        },
    )
