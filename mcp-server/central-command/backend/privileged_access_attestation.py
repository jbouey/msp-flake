"""Privileged-access attestation (Phase 14 — Session 205).

Every privileged-access event (enable_emergency_access,
disable_emergency_access, and future additions) is written as a
Ed25519-signed, hash-chained, OTS-anchored compliance_bundle —
the same evidence substrate our platform uses for drift + remediation
attestation. No separate "privileged access audit log" to review
quarterly. The event IS the audit trail.

Why this shape:
  - Cryptographically verifiable by any auditor on any laptop
  - Bound to the site's existing evidence chain (prev_hash linkage)
  - OpenTimestamps-anchored via the existing Merkle batch worker
  - Automatically published in the customer portal, auditor-kit ZIP,
    and public /recovery verify UI — no extra plumbing
  - 7-year retention, WORM lock, same as every other compliance event

Policy (enforced by callers, not this module):
  - Fleet-order creation of privileged access order MUST call
    create_privileged_access_attestation FIRST. If the attestation
    fails, the order must NOT be signed/inserted.
  - Attestation requires: named actor, reason ≥ 20 chars, target site

This module only HANDLES the writing. Enforcement lives in fleet_cli.py
and the (future) partner-portal approval flow.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


SIGNING_KEY_PATH = os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")


# ─── Privileged event catalog ─────────────────────────────────────
# MUST stay in lockstep with:
#   fleet_cli.PRIVILEGED_ORDER_TYPES
#   migration 175 v_privileged_types
# Enforced by scripts/check_privileged_chain_lockstep.py.
# See CLAUDE.md § "Privileged-Access Chain of Custody".
ALLOWED_EVENTS = {
    "enable_emergency_access",
    "disable_emergency_access",
    "signing_key_rotation",
    "bulk_remediation",
    # Session 207 Phase W0 — watchdog order catalog. Kept in lockstep
    # with fleet_cli.PRIVILEGED_ORDER_TYPES + migration 218
    # v_privileged_types. CI enforces via
    # scripts/check_privileged_chain_lockstep.py.
    "watchdog_restart_daemon",
    "watchdog_refetch_config",
    "watchdog_reset_pin_store",
    "watchdog_reset_api_key",
    "watchdog_redeploy_daemon",
    "watchdog_collect_diagnostics",
    # Session 207 Phase S escape hatch — see fleet_cli comment.
    "enable_recovery_shell_24h",
    # Session 207 R+S non-blocking follow-up — break-glass passphrase
    # retrievals flow into the attestation chain + auditor kit. Not a
    # fleet_order type (retrieval is an admin API call, not a queued
    # order), so intentionally absent from fleet_cli.PRIVILEGED_ORDER_
    # TYPES + v_privileged_types. The lockstep checker requires
    # ALLOWED_EVENTS ⊇ the other two; the reverse is not required.
    "break_glass_passphrase_retrieval",
    # v36 (Session 207 post-t740 round-table) — appliance physical-move
    # compliance chain. Detection half is written automatically via
    # appliance_relocation.detect_and_record_relocation (system-signed,
    # not a privileged_access attestation). Acknowledgment half uses
    # this privileged path: admin records reason, chain links to the
    # detection bundle. HIPAA §164.310(d)(1).
    # Not a fleet_order — admin API call only, like break_glass_passphrase_retrieval.
    "appliance_relocation_acknowledged",
    # #74 closure 2026-05-02 (sub-followup of #64 P0 kill-switch).
    # Fleet-wide healing pause/resume is admin API call (no fleet
    # order issued — server-side flag flip), so absent from
    # PRIVILEGED_ORDER_TYPES + v_privileged_types. ALLOWED_EVENTS
    # ⊇ those two so this asymmetry is permitted by lockstep checker.
    # Endpoints in main.py fan out per-site attestations (each site
    # gets its own chain-linked bundle) so HIPAA §164.312(b) integrity
    # controls have crypto evidence the operator paused healing.
    "fleet_healing_global_pause",
    "fleet_healing_global_resume",
    # #72 closure 2026-05-02 (sub-followup of #67 admin billing UI).
    # Destructive Stripe ops by an admin acting on a customer's
    # billing relationship. NOT fleet orders (Stripe API direct).
    # Each action audit-logs to admin_audit_log + writes Ed25519
    # attestation to the customer's site_id chain.
    "customer_subscription_cancel",
    "customer_subscription_refund",
    # Punch-list item #8 closure 2026-05-04 — round-table-approved
    # owner-transfer state machine. Two-step + 24h cooling-off + any-
    # admin-cancel + 1-owner-min DB trigger. ALL six events are admin-
    # API class (state machine endpoints in client_portal.py), NOT
    # fleet_orders — kept out of PRIVILEGED_ORDER_TYPES + v_privileged_
    # types per the asymmetry rule (lockstep checker permits ALLOWED_
    # EVENTS ⊇ those two; reverse not required). Migration 273 ships
    # the data layer + the 1-owner-min trigger that makes the org-
    # locked-forever class impossible at the DB level.
    "client_org_owner_transfer_initiated",
    "client_org_owner_transfer_acked",
    "client_org_owner_transfer_accepted",
    "client_org_owner_transfer_completed",
    "client_org_owner_transfer_canceled",
    "client_org_owner_transfer_expired",
    # Round-table 2026-05-04 (Maya cross-cutting parity finding —
    # partner-portal consistency audit). User role changes are
    # privileged actions; both portals attest. NOT in
    # PRIVILEGED_ORDER_TYPES (admin-API class). Closes the gap where
    # _audit_*_action wrote audit_log but the cryptographic chain
    # didn't reflect the role change.
    "client_user_role_changed",
    "partner_user_created",
    # Round-table 2026-05-04 item B — partner-admin transfer state
    # machine (mig 274). Maya's simpler shape: 2-state (pending →
    # completed/canceled/expired). 4 events vs client-side 6.
    # Anchor namespace: partner_org:<partner_id>.
    "partner_admin_transfer_initiated",
    "partner_admin_transfer_completed",
    "partner_admin_transfer_canceled",
    "partner_admin_transfer_expired",
}


class PrivilegedAccessAttestationError(Exception):
    """Raised when an attestation cannot be written. The caller must
    refuse to proceed with the downstream privileged action."""


def _load_signing_key():
    """Load the server's Ed25519 signing key. Fail fast — callers
    should treat this as an abort condition for the downstream action."""
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError as e:
        raise PrivilegedAccessAttestationError(
            f"PyNaCl not available; cannot sign attestation: {e}"
        )
    try:
        key_hex = pathlib.Path(SIGNING_KEY_PATH).read_bytes().strip()
    except Exception as e:
        raise PrivilegedAccessAttestationError(
            f"Signing key unreadable at {SIGNING_KEY_PATH}: {e}"
        )
    return SigningKey(key_hex, encoder=HexEncoder)


def _canonical(payload: Dict[str, Any]) -> str:
    """Deterministic JSON for hashing + signing. sort_keys=True, compact
    separators so Python and Go both reproduce byte-exact."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def _get_prev_bundle(conn: asyncpg.Connection, site_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent bundle for this site (for hash-chain linkage)."""
    row = await conn.fetchrow(
        "SELECT bundle_id, bundle_hash, chain_position, chain_hash "
        "FROM compliance_bundles "
        "WHERE site_id = $1 "
        "ORDER BY checked_at DESC LIMIT 1",
        site_id,
    )
    if not row:
        return None
    return dict(row)


async def create_privileged_access_attestation(
    conn: asyncpg.Connection,
    site_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    fleet_order_id: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    approvals: Optional[List[Dict[str, Any]]] = None,
    origin_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a signed + hash-chained compliance_bundle for a privileged-
    access event. Enqueues for OTS anchoring via the existing batch
    worker (ots_status='batching').

    Returns dict with keys:
      bundle_id, bundle_hash, chain_position, chain_hash, signature

    Raises PrivilegedAccessAttestationError on any failure. Caller MUST
    refuse to proceed with the downstream action on exception.

    Policy checks (enforced here for uniformity across callers):
      - actor_email non-empty (no anonymous)
      - reason ≥ 20 chars (no "testing" / "just doing it")
      - event_type in allowed set
    """
    if event_type not in ALLOWED_EVENTS:
        raise PrivilegedAccessAttestationError(
            f"event_type {event_type!r} not in allowed set {sorted(ALLOWED_EVENTS)}"
        )
    if not actor_email or "@" not in actor_email:
        raise PrivilegedAccessAttestationError(
            "actor_email required (must be a valid email — no anonymous attestations)"
        )
    if not reason or len(reason.strip()) < 20:
        raise PrivilegedAccessAttestationError(
            "reason required (min 20 chars — describe the incident or change)"
        )

    # Phase B: signing_backend abstraction. In file/shadow mode the
    # signature is byte-identical to the old sk.sign() path (same key,
    # same data). In vault mode it comes from Transit. Failure here
    # must abort — callers check for PrivilegedAccessAttestationError.
    # Try relative import (production package path) first, fall back
    # to top-level (pytest path where backend/ is on sys.path).
    try:
        from .signing_backend import get_signing_backend, SigningBackendError
    except ImportError:
        from signing_backend import get_signing_backend, SigningBackendError
    try:
        _signer = get_signing_backend()
    except SigningBackendError as e:
        raise PrivilegedAccessAttestationError(f"signing backend unavailable: {e}")
    now = datetime.now(timezone.utc)

    # Build canonical event record
    event: Dict[str, Any] = {
        "kind": "privileged_access",
        "event_type": event_type,
        "site_id": site_id,
        "actor_email": actor_email,
        "reason": reason.strip(),
        "timestamp": now.isoformat(),
    }
    if fleet_order_id:
        event["fleet_order_id"] = str(fleet_order_id)
    if duration_minutes is not None:
        event["duration_minutes"] = int(duration_minutes)
    if approvals:
        event["approvals"] = approvals
    if origin_ip:
        event["origin_ip"] = origin_ip

    checks_payload = [event]
    summary_payload = {
        "event_type": event_type,
        "actor": actor_email,
        "evidence_class": "privileged_access",
        "count": 1,
    }

    # Chain linkage — SAME site's prior bundle regardless of check_type.
    # This keeps the evidence chain single-threaded per site so any
    # gap is detectable.
    prev = await _get_prev_bundle(conn, site_id)
    prev_bundle_id = prev["bundle_id"] if prev else None
    prev_hash = prev["bundle_hash"] if prev else "0" * 64
    chain_position = (prev["chain_position"] + 1) if prev else 0

    # Canonical-JSON over (checks + summary + metadata) for hashing
    canonical = _canonical({
        "site_id": site_id,
        "checked_at": now.isoformat(),
        "check_type": "privileged_access",
        "checks": checks_payload,
        "summary": summary_payload,
        "prev_hash": prev_hash,
        "chain_position": chain_position,
    })
    bundle_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    chain_hash = hashlib.sha256(
        (prev_hash + bundle_hash).encode("utf-8")
    ).hexdigest()

    # Sign the bundle_hash with server's Ed25519 key (file or Vault
    # per SIGNING_BACKEND env).
    try:
        sig_result = _signer.sign(bundle_hash.encode("utf-8"))
    except SigningBackendError as e:
        raise PrivilegedAccessAttestationError(f"signing failed: {e}")
    signature_bytes = sig_result.signature
    signature_hex = signature_bytes.hex()  # 128 hex chars

    bundle_id = f"PA-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"

    try:
        await conn.execute("""
            INSERT INTO compliance_bundles (
                site_id, bundle_id, bundle_hash, check_type, check_result,
                checked_at, checks, summary,
                agent_signature, signed_data, signature_valid,
                prev_bundle_id, prev_hash, chain_position, chain_hash,
                signature, signed_by, ots_status
            ) VALUES (
                $1, $2, $3, 'privileged_access', 'recorded',
                $4, $5::jsonb, $6::jsonb,
                NULL, $7, true,
                $8, $9, $10, $11,
                $12, 'central-command-server', 'batching'
            )
        """,
            site_id, bundle_id, bundle_hash,
            now,
            json.dumps(checks_payload), json.dumps(summary_payload),
            canonical,
            prev_bundle_id, prev_hash, chain_position, chain_hash,
            signature_hex,
        )
    except Exception as e:
        logger.exception(
            f"Failed to write privileged-access attestation for {site_id}/{event_type}"
        )
        raise PrivilegedAccessAttestationError(
            f"attestation write failed: {e}"
        )

    # Admin audit log mirror (for the existing admin UI; the canonical
    # record lives in compliance_bundles).
    try:
        await conn.execute("""
            INSERT INTO admin_audit_log (username, action, target, details, created_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
        """,
            actor_email,
            f"PRIVILEGED_ACCESS_{event_type.upper()}",
            f"site:{site_id}",
            json.dumps({
                "bundle_id": bundle_id,
                "bundle_hash": bundle_hash,
                "chain_position": chain_position,
                "fleet_order_id": fleet_order_id,
                "duration_minutes": duration_minutes,
                "reason": reason.strip(),
            }),
        )
    except Exception as e:
        # Audit-log mirror is secondary; the attestation bundle IS the
        # canonical record. Log but don't fail the attestation.
        logger.warning(f"admin_audit_log mirror failed for {bundle_id}: {e}")

    logger.info(
        "privileged_access_attestation_written",
        extra={
            "site_id": site_id,
            "bundle_id": bundle_id,
            "event_type": event_type,
            "actor_email": actor_email,
            "chain_position": chain_position,
        },
    )

    return {
        "bundle_id": bundle_id,
        "bundle_hash": bundle_hash,
        "chain_position": chain_position,
        "chain_hash": chain_hash,
        "signature": signature_hex,
    }


async def count_recent_privileged_events(
    conn: asyncpg.Connection,
    site_id: str,
    days: int = 7,
    event_type: Optional[str] = None,
) -> int:
    """Rate-limiting helper. Counts recent attestations matching the
    event_type on this site. Caller uses the count to enforce a weekly
    cap (default 3 per site per week per event_type)."""
    if event_type:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM compliance_bundles "
            "WHERE site_id = $1 "
            "  AND check_type = 'privileged_access' "
            "  AND checked_at > NOW() - make_interval(days => $2) "
            "  AND summary->>'event_type' = $3",
            site_id, days, event_type,
        )
    else:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM compliance_bundles "
            "WHERE site_id = $1 "
            "  AND check_type = 'privileged_access' "
            "  AND checked_at > NOW() - make_interval(days => $2)",
            site_id, days,
        )
    return int((row["n"] if row else 0) or 0)
