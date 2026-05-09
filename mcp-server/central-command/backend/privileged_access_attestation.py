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
    # PartnerUsersScreen v2 (Session 217 follow-up to task #18 phase 3):
    # self-scoped role-change + deactivate endpoints. Same attestation
    # shape as partner_user_created — anchor at partner_org:<partner_id>,
    # NOT in fleet_cli.PRIVILEGED_ORDER_TYPES (admin-API events).
    "partner_user_role_changed",
    "partner_user_deactivated",
    # Round-table 31 + Maya final sweep (Session 217): reactivate is a
    # semantically distinct event from create — auditors reading the
    # chain need to distinguish "new user invited" from "deactivated
    # user re-enabled." Pre-fix self_create_partner_user emitted
    # partner_user_created for both branches.
    "partner_user_reactivated",
    # Round-table 2026-05-04 item B — partner-admin transfer state
    # machine (mig 274). Maya's simpler shape: 2-state (pending →
    # completed/canceled/expired). 4 events vs client-side 6.
    # Anchor namespace: partner_org:<partner_id>.
    "partner_admin_transfer_initiated",
    "partner_admin_transfer_completed",
    "partner_admin_transfer_canceled",
    "partner_admin_transfer_expired",
    # Maya P1-1 closure 2026-05-04 — final-pass adversarial review
    # found 4 operator-alert hooks claiming "cryptographically
    # attested" semantics without an Ed25519 chain. Promoted to full
    # chain per the enterprise-grade default. Anchor: client-org
    # events use org's primary site_id; partner-org events use
    # partner_org:<id> synthetic namespace. NOT in
    # PRIVILEGED_ORDER_TYPES — admin-API class.
    "org_deprovisioned",
    "org_reprovisioned",
    "partner_api_key_regenerated",
    "partner_org_deleted",
    # Followup task #20 closure 2026-05-04 — per-org configurable
    # cooling-off / expiry on owner+admin transfers (mig 275).
    # Changes to friction-level configuration are themselves privileged
    # actions: weakening cooling-off from 24h to 0h reduces the attack
    # window operators have to notice + cancel a malicious transfer.
    # Same chain pattern as the underlying transfer events.
    "client_org_transfer_prefs_changed",
    "partner_transfer_prefs_changed",
    # Task #19 closure 2026-05-05 — MFA admin overrides (mig 276).
    # 3 sub-features × 2 portals = 6 events; plus 2 reversal events
    # for Steve P3 mitigation B (24h reversible-link revoke). Each
    # is admin-API class, NOT a fleet_order. Anchor: client-side
    # events use org's primary site_id; partner-side use
    # partner_org:<id>.
    "client_org_mfa_policy_changed",
    "partner_mfa_policy_changed",
    "client_user_mfa_reset",
    "partner_user_mfa_reset",
    "client_user_mfa_revoked",
    "partner_user_mfa_revoked",
    "client_user_mfa_revocation_reversed",
    "partner_user_mfa_revocation_reversed",
    # Maya P0-3 (round-table 2026-05-05): the sweep loop silently
    # expiring a revocation without an attestation row is a chain-of-
    # custody gap — auditor downloading the kit would see the revoke
    # event with no closure event when the user didn't restore in 24h.
    # Sweep emits these per-row.
    "client_user_mfa_revocation_expired",
    "partner_user_mfa_revocation_expired",
    # Task #23 closure 2026-05-05 — client_user email rename (mig 277).
    # Three actor classes converge to email rename: self-service (with
    # magic-link confirm to NEW), partner-admin (operator class, ≥20ch
    # reason), substrate (admin_users, ≥40ch reason, P0 partner-alert).
    # Reversed event reserved for future reversal-flow; v1 ships
    # without a reversal window per Maya P1 round-table verdict —
    # re-running rename IS the undo path. ALLOWED_EVENTS: 49.
    "client_user_email_changed_by_self",
    "client_user_email_changed_by_partner",
    "client_user_email_changed_by_substrate",
    "client_user_email_change_reversed",
    # Task #21 closure 2026-05-05 — cross-org site relocate (Migrations
    # 279 + 280 + 281). Three-actor state machine + cryptographic chain
    # crossing the org boundary via sites.prior_client_org_id +
    # site_canonical_aliases. Anchor: source org's primary site_id (the
    # site being moved). NOT in PRIVILEGED_ORDER_TYPES — admin-API
    # class (DB state mutation, not fleet_order). The 7th event is the
    # feature-flag enable itself: flag flip is a privileged-class
    # action with its own attestation chain entry, per Patricia
    # (2026-05-05 RT21 adversarial). ALLOWED_EVENTS: 53.
    "cross_org_site_relocate_initiated",
    "cross_org_site_relocate_source_released",
    "cross_org_site_relocate_target_accepted",
    "cross_org_site_relocate_executed",
    "cross_org_site_relocate_canceled",
    "cross_org_site_relocate_expired",
    # NOTE: `enable_cross_org_site_relocate` (the feature-flag flip) was
    # considered for the privileged-access chain but DROPPED at RT21
    # Gate 2 (Marcus): compliance_bundles.site_id FKs to sites(site_id),
    # and the flag flip is a substrate-level event with no natural site
    # anchor. Synthetic anchors fail the FK; fan-out per-site (the
    # fleet_healing_global_pause pattern) is heavy for a rare event.
    # The flag-flip's audit trail lives in two places instead:
    #   1. feature_flags table is append-only (DELETE trigger) and
    #      records actor_email + reason ≥40ch + enabled_at + disable
    #      timestamps + reasons. This IS the historical record.
    #   2. admin_audit_log row written on every toggle.
    # Both are forensically recoverable; the asymmetry vs
    # ALLOWED_EVENTS is intentional and documented here.
    # F2 closure 2026-05-06 — Privacy Officer designation flow
    # (round-table 2026-05-06 customer-iterated spec). The
    # Compliance Attestation Letter (F1) pulls the Privacy Officer
    # name from a SIGNED ACCEPTANCE attestation, not a profile
    # field. §164.308(a)(2) requires identifying the security
    # official; a chain-anchored signed acceptance is the
    # auditor-respected evidence shape. Anchor: org's primary
    # site_id (per Anchor-namespace convention, Session 216). NOT
    # in PRIVILEGED_ORDER_TYPES — admin-API class (settings page,
    # not fleet_order). 2 events: designation + revocation
    # (revocation = replacement; new designation must follow).
    "client_org_privacy_officer_designated",
    "client_org_privacy_officer_revoked",
    # P-F6 closure 2026-05-08 — partner BAA roster (mig 290).
    # Tony-the-MSP-HIPAA-lead's three-party BAA chain:
    # CE → MSP-as-BA → OsirisCare-as-Subcontractor. Each
    # MSP→clinic BAA in the roster generates a chain-anchored
    # attestation; revocation does the same. Anchor: synthetic
    # partner_org:<partner_id> namespace (matches Session 216
    # convention for partner-org events). NOT in
    # PRIVILEGED_ORDER_TYPES — admin-API class.
    "partner_baa_roster_added",
    "partner_baa_roster_revoked",
    # Sprint-N+2 closure 2026-05-08 — partner per-site drill-down
    # cross-portal magic link (mig 293). Lisa-the-MSP-MD's
    # "open this clinic's portal as the practice owner"
    # workflow. Each mint is a partner-action privileged event
    # (admin-API class — NOT a fleet_order, NOT in
    # PRIVILEGED_ORDER_TYPES + v_privileged_types). Anchor:
    # partner_org:<partner_id> (Session 216 convention for
    # partner-org events). Round-table .agent/plans/37-partner-
    # per-site-drill-down-roundtable-2026-05-08.md D4 RESOLVED.
    "partner_client_portal_link_minted",
    # 2026-05-09 cold-onboarding adversarial-walkthrough closure (P0 #1
    # + P1-5). Self-serve cold-path: customer pays via Stripe →
    # signup webhook materializes a `client_orgs` row (status=pending
    # until BAA confirmed) and an owner `client_users` row. The
    # creation event is chain-anchored at the synthetic
    # `client_org:<id>` namespace (Session 216 anchor convention)
    # because no site exists yet. The BAA signature event closes the
    # chain — its presence is what flips client_orgs.status pending →
    # active. Both are admin-API class (Stripe webhook handler), NOT
    # fleet_orders → ALLOWED_EVENTS-only (asymmetry permitted by
    # lockstep checker: ALLOWED_EVENTS ⊇ PRIVILEGED_ORDER_TYPES).
    "client_org_created",
    "baa_signed",
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
    """Return the most recent bundle for this site (for hash-chain linkage).

    Race-hardened (audit F-P2-3, 2026-05-08): two concurrent privileged
    attestations on the same site previously read the same prev_hash
    and could produce two bundles with identical (chain_position,
    prev_hash). compliance_bundles.PRIMARY KEY is (id, created_at) on
    the partitioned table, NOT (site_id, chain_position) — so the
    duplicate slipped past the DB and was only detected on chain-walk
    verify (auditor-visible "verify.sh diverges" credibility hit).

    Fix: take a per-site advisory transaction lock at the START of the
    chain-mutation flow. The lock is released automatically at COMMIT
    or ROLLBACK. Two concurrent attestations on the same site now
    serialize through this lock; reads on different sites are
    unaffected.

    `pg_advisory_xact_lock` requires being inside an explicit
    transaction. The caller passes a `conn` already inside an admin
    transaction (per the cross-org/owner-transfer/privileged-access
    flows that all use admin_transaction). Acquiring the lock
    OUTSIDE a transaction returns immediately with no serialization
    semantics, which would defeat the purpose; the assertion below
    catches that misuse loudly.
    """
    # P1-1 fix from 2026-05-09 15-commit audit: the docstring above
    # claims an assertion catches caller-not-in-transaction. The
    # assertion was missing. asyncpg.Connection exposes
    # `is_in_transaction()` since 0.27 — explicit assert here makes
    # the misuse fail loudly at the line of misuse instead of letting
    # `pg_advisory_xact_lock` no-op silently outside a transaction
    # (the lock auto-releases at statement end if not in a txn,
    # which is functionally equivalent to "no lock at all").
    assert conn.is_in_transaction(), (
        "_get_prev_bundle() requires the caller to be inside an "
        "explicit transaction (admin_transaction or admin_connection "
        "wrapped in conn.transaction()). pg_advisory_xact_lock has no "
        "serialization semantics outside a transaction — the lock "
        "would release at statement end, defeating the race-fix."
    )

    # `hashtext()` returns int4 natively; the two-arg
    # `pg_advisory_xact_lock(int, int)` overload takes int4+int4
    # (NOT int8+int8 — that signature does not exist). The second
    # key (`hashtext('attest')`) namespaces the lock so it cannot
    # collide with other advisory-lock callers using the same
    # site_id for an unrelated purpose.
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext('attest'))",
        site_id,
    )

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
        # canonical record. Log at ERROR (with exc_info) per the
        # "no silent write failures" inviolable rule (CLAUDE.md), but
        # don't fail the attestation. Operators tail ERROR; WARNING
        # gets filtered out of incident reviews.
        logger.error(
            "admin_audit_log_mirror_failed",
            extra={"bundle_id": bundle_id, "exception_class": type(e).__name__},
            exc_info=True,
        )

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
