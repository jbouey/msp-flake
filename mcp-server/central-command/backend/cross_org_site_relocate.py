"""Cross-org site relocate state machine.

Round-table 21 (2026-05-05) Camila/Brian/Linda/Steve/Adam + Maya 2nd-eye
+ Patricia/Marcus/Linda Gate-1 adversarial. Closes task #21 from the
ownership/email gaps audit. Pre-ship: cross-org site relocate returned
HTTP 403 from sites.py:1938 with a "coming soon" comment. Real-world
demand: clinic acquired by hospital network, clinic switches to a new
client_org under acquisition, etc.

Three-actor state machine + 24h cooling-off + cryptographic chain
crossing the org boundary via sites.prior_client_org_id (Migration 280).

States (Migration 279):
  pending_source_release  →release→  pending_target_accept
  pending_target_accept   →accept→   pending_admin_execute
                                     (24h cooling-off countdown starts)
  pending_admin_execute   →execute→  completed
                                     (sites.client_org_id flipped,
                                      sites.prior_client_org_id set)
  any pending             →cancel→   canceled
  any pending             →expires_at passed→ expired

Each transition writes an Ed25519 attestation bundle. ALLOWED_EVENTS
holds the 6 lifecycle event_types + 1 attested-flag event_type.

Endpoints (mounted at /api/admin/cross-org-relocate/):
  POST   /initiate                   — Osiris admin only
  POST   /source-release?token=...   — source-org owner via magic link
  POST   /target-accept?token=...    — target-org owner via magic link
  POST   /{id}/execute               — Osiris admin (after cooling-off)
  POST   /{id}/cancel                — any of the 3 actors
  GET    /{id}                       — visibility (admin)
  POST   /propose-enable             — first admin: propose flag flip
  POST   /approve-enable             — second admin (must differ):
                                       approve + flip flag enabled.
                                       Reason >=40 chars carries the
                                       outside-counsel opinion identifier.

Feature-flag-gated. The flag itself is in feature_flags (Migration 281)
and toggling it requires the dual-admin propose+approve sequence
(Migration 282 — counsel governance hardening 2026-05-06; DB CHECK
enforces approver != proposer). Until counsel signs off and both
admins flip the flag, every endpoint here returns 503.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from .auth import require_admin
from .chain_attestation import emit_privileged_attestation
from .client_portal import _audit_client_action, get_client_user_from_session
from .fleet import get_pool
from .tenant_middleware import admin_connection

# Frontend URL for magic-link redemption. Mirrors the pattern in
# client_owner_transfer.py BASE_URL — same env-var, same default.
BASE_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")

logger = logging.getLogger(__name__)

cross_org_relocate_router = APIRouter(
    prefix="/admin/cross-org-relocate",
    tags=["admin", "cross-org-relocate"],
)

# Defaults. Per-source-org overrides land via the existing
# transfer_cooling_off_hours / transfer_expiry_days mechanism (mig 275).
DEFAULT_COOLING_OFF_HOURS = 24
DEFAULT_EXPIRY_DAYS = 7
MIN_REASON_LENGTH = 20
MIN_FLAG_REASON_LENGTH = 40

FLAG_NAME = "cross_org_site_relocate"


# ─────────────────────────────────────────────────────────────────
# Feature-flag gate. Patricia RT21 (2026-05-05): every endpoint
# checks this before doing anything. Until outside-counsel BAA review
# returns AND two distinct admins complete the dual-admin sequence
# (/propose-enable + /approve-enable, mig 282 governance hardening),
# the endpoints return 503 with an actionable message.
# ─────────────────────────────────────────────────────────────────


async def _feature_enabled(conn) -> bool:
    """Read feature_flags row; return True iff enabled with attestation."""
    row = await conn.fetchrow(
        "SELECT enabled FROM feature_flags WHERE flag_name = $1",
        FLAG_NAME,
    )
    return bool(row and row["enabled"])


async def _require_feature_enabled(conn) -> None:
    if not await _feature_enabled(conn):
        raise HTTPException(
            status_code=503,
            detail=(
                "Cross-org site relocate is pending outside-counsel BAA "
                "review. Endpoint disabled. To enable, two distinct "
                "admins must POST /api/admin/cross-org-relocate/"
                "propose-enable (operational trigger) followed by POST "
                "/approve-enable (legal-opinion identifier in the "
                ">=40-char reason field). DB CHECK enforces approver != "
                "proposer."
            ),
        )


# ─────────────────────────────────────────────────────────────────
# Attestation helper. Anchors at the source org's primary site_id —
# the site being moved, which is itself the natural anchor. The chain
# walks across the org boundary at the `_executed` event when
# sites.client_org_id flips.
# ─────────────────────────────────────────────────────────────────


async def _emit_attestation(
    conn,
    *,
    event_type: str,
    actor_email: str,
    actor_role: str,
    reason: str,
    site_id: str,
    relocate_id: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Write the Ed25519 attestation bundle via the canonical helper.

    Per RT32 (2026-05-05) DRY closure, all privileged-access attestations
    flow through `chain_attestation.emit_privileged_attestation` so a
    refactor of the underlying helper lands in one place. The caller
    treats `failed=True` as a hard stop — chain-of-custody is inviolable.
    """
    approvals = [{
        "stage": actor_role,
        "actor": actor_email,
        "relocate_request_id": relocate_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]
    if extra_metadata:
        approvals[0].update(extra_metadata)
    failed, bundle_id = await emit_privileged_attestation(
        conn,
        anchor_site_id=site_id,
        event_type=event_type,
        actor_email=actor_email,
        reason=reason,
        approvals=approvals,
    )
    if failed:
        # Per CLAUDE.md "Privileged-Access Chain of Custody (Session 205,
        # INVIOLABLE)": the caller MUST refuse to proceed if attestation
        # fails. Bubble up as a 500.
        logger.error(
            "cross_org_site_relocate attestation failed",
            extra={
                "event_type": event_type,
                "relocate_id": relocate_id,
                "site_id": site_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cryptographic attestation failed. The state transition "
                "has NOT been applied. Retry; if the failure persists, "
                "this is a P0 chain-of-custody incident."
            ),
        )
    return bundle_id


async def _append_bundle_id(conn, relocate_id: str, bundle_id: Optional[str]) -> None:
    if not bundle_id:
        return
    await conn.execute(
        """
        UPDATE cross_org_site_relocate_requests
           SET attestation_bundle_ids = attestation_bundle_ids || $2::jsonb
         WHERE id = $1
        """,
        relocate_id,
        json.dumps([bundle_id]),
    )


# ─────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────


class InitiateRelocateRequest(BaseModel):
    site_id: str = Field(..., min_length=1)
    target_org_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=MIN_REASON_LENGTH)


class SourceReleaseRequest(BaseModel):
    token: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=MIN_REASON_LENGTH)


class TargetAcceptRequest(BaseModel):
    token: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=MIN_REASON_LENGTH)


class CancelRequest(BaseModel):
    reason: str = Field(..., min_length=MIN_REASON_LENGTH)


class ProposeEnableRequest(BaseModel):
    """First-admin proposal to enable the cross-org-relocate feature.

    Outside-counsel adversarial review (2026-05-06) hardening: a single
    admin enabling a legally sensitive capability is the design's
    governance choke point. Two-step: this endpoint records the
    proposal; a SECOND distinct admin must call /approve-enable.

    Reason >=20 chars matches the rest of the privileged-access chain.
    The legal-opinion identifier lives on the APPROVER's >=40-char
    reason field, not here.
    """
    reason: str = Field(..., min_length=MIN_REASON_LENGTH)


class ApproveEnableRequest(BaseModel):
    """Second-admin approval of a pending proposal. Must be a DIFFERENT
    admin than the one who proposed (DB CHECK + endpoint check enforce
    both). Reason >=40 chars: this is where the outside-counsel opinion
    identifier lands."""
    reason: str = Field(..., min_length=MIN_FLAG_REASON_LENGTH)


# ─────────────────────────────────────────────────────────────────
# Steve threat-model preconditions (RT21 design + Gate 1 verification)
# ─────────────────────────────────────────────────────────────────


async def _check_no_pending_owner_transfers(
    conn, source_org_id: str, target_org_id: str
) -> None:
    """Steve mit 3: refuse if EITHER org has an in-flight owner transfer.
    A compromised source-owner who triggers a relocate could otherwise
    follow up by triggering an owner-transfer to bypass the cancel
    window. Symmetric on target side."""
    row = await conn.fetchrow(
        """
        SELECT client_org_id
          FROM client_org_owner_transfer_requests
         WHERE client_org_id IN ($1::uuid, $2::uuid)
           AND status IN (
               'pending_current_ack',
               'pending_target_accept'
           )
         LIMIT 1
        """,
        source_org_id,
        target_org_id,
    )
    if row:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Org {row['client_org_id']} has a pending owner-transfer. "
                "Cross-org relocate refuses to initiate while an owner-"
                "transfer is in flight (Steve mit 3). Cancel/expire the "
                "owner-transfer first, then re-initiate."
            ),
        )


async def _check_target_org_baa(conn, target_org_id: str) -> None:
    """Steve mit 5 + counsel approval condition #2 (2026-05-06): target
    client_org MUST have (a) `baa_on_file=true` AND (b) a non-NULL
    `baa_relocate_receipt_signature_id` (or addendum_signature_id) —
    proving that contracts-team has reviewed the BAA language and
    confirmed it expressly authorizes receipt + continuity of
    transferred site compliance records.

    Migration 283 added the receipt-authorization columns. The
    boolean `baa_on_file` (mig 124) only confirms a BAA exists; the
    new signature-id columns prove someone reviewed it for the
    cross-org-relocate-specific language counsel's approval requires.

    NOTE on §164.504(e): the legal test is whether each governing BAA
    permits the use/access pattern that occurs during and after a
    cross-org relocate, regardless of whether the vendor is the same.
    The target-org BAA's permitted-use clause is what licenses the
    receive-side activity; this check verifies that BAA is in force
    AND that contracts-team has confirmed the relevant language is
    present. See `.agent/plans/21-counsel-briefing-packet-2026-05-06.md`
    §2 Q1 + the v2.3 hardening note."""
    row = await conn.fetchrow(
        """
        SELECT baa_on_file,
               baa_relocate_receipt_signature_id,
               baa_relocate_receipt_addendum_signature_id,
               baa_relocate_receipt_authorized_at
          FROM client_orgs WHERE id = $1::uuid
        """,
        target_org_id,
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Target org {target_org_id} not found.",
        )
    if not row["baa_on_file"]:
        raise HTTPException(
            status_code=412,
            detail=(
                "Target org does not have a BAA on file. Cross-org "
                "relocate requires baa_on_file=true on the receiving "
                "org BEFORE target-accept can complete. Set the BAA "
                "via the org-management flow first."
            ),
        )
    # Counsel approval condition #2 (mig 283): receipt language must
    # be reviewed + a signature_id recorded. The signature can come
    # from the standard BAA OR from a relocate-receipt addendum;
    # either is acceptable. NULL means "BAA on file, but not reviewed
    # for receipt language" — refuse.
    has_receipt_auth = (
        row["baa_relocate_receipt_signature_id"] is not None
        or row["baa_relocate_receipt_addendum_signature_id"] is not None
    )
    if not has_receipt_auth:
        raise HTTPException(
            status_code=412,
            detail=(
                "Target org has a BAA on file but contracts-team has "
                "not yet recorded receipt-authorization for cross-org "
                "site relocate. Outside HIPAA counsel approval (2026-"
                "05-06) is contingent on the receiving org's BAA or "
                "addendum expressly authorizing receipt + continuity "
                "of transferred site compliance records. Set "
                "`baa_relocate_receipt_signature_id` or "
                "`baa_relocate_receipt_addendum_signature_id` on the "
                "client_orgs row via the org-management flow before "
                "the target-accept step can advance."
            ),
        )


# ─────────────────────────────────────────────────────────────────
# Email templates (RT21 Phase 3)
#
# Three customer-facing emails fire across the lifecycle:
#   - source_release notice → source-org owner (carries magic link)
#   - target_accept notice  → target-org owner (carries magic link)
#   - post-execute notice   → BOTH owners (no link; receipt of move)
#
# Language follows CLAUDE.md Session 199 rules: NO banned words
# (ensures/prevents/protects/guarantees/100%); use "supports audit-
# readiness", "operator visibility", "PHI scrubbed at appliance".
#
# All three are async + best-effort: an email failure logs at ERROR
# but does NOT abort the state transition. The chain-of-custody
# attestation is the load-bearing record; the email is courtesy.
# Pattern matches `client_owner_transfer._send_target_accept_email`.
# ─────────────────────────────────────────────────────────────────


#
# OPAQUE-MODE rationale (outside-counsel adversarial review 2026-05-06):
# Subject lines and body content do NOT include site_name, source_org_name,
# target_org_name, or initiator_email. Recipients click a magic-link to
# the authenticated client portal where the full context (clinic name,
# org names, requestor, reason) is visible only after authentication.
#
# Rationale per counsel:
#   - Reduces SMTP-relay disclosure surface: "the easy thing to harden."
#   - Closes the seam where the email itself reveals operational facts
#     (a specific clinic transferring across covered-entity boundaries)
#     even though clinic_name is not a §164.514 individual identifier.
#   - "Once a safer alternative is cheap, counsel has less incentive to
#     bless the riskier version." Opaque IS the cheap safer alternative.
#
# Helper signatures dropped site_name / source_org_name / target_org_name
# parameters — the helpers don't need them anymore. The portal serves
# the rich context behind authentication.
#


async def _send_source_release_email(
    *,
    source_owner_email: str,
    relocate_id: str,
    source_release_token: str,
    expires_at: datetime,
) -> None:
    """Source-org owner notice: action required, click to portal.

    Opaque mode (default). No site or organization names in the
    subject or body — the magic link redirects through portal auth
    where full request context is shown."""
    try:
        from .email_service import send_email
        release_url = (
            f"{BASE_URL}/admin/cross-org-relocate/source-release"
            f"?token={source_release_token}&id={relocate_id}"
        )
        body = (
            "Hello,\n"
            "\n"
            "An action is requested for one of your OsirisCare client "
            "organizations: a cross-organization site relocate has "
            "been initiated and your release is required for it to "
            "proceed.\n"
            "\n"
            "To review the request and take action, click here within "
            "7 days. The link redirects you through OsirisCare portal "
            "authentication, where the full request context (site, "
            "source organization, target organization, requestor, and "
            "reason) is visible:\n"
            f"  {release_url}\n"
            "\n"
            f"Link expires: {expires_at.isoformat()}\n"
            f"Reference: relocate-{relocate_id}\n"
            "\n"
            "Why this email omits identifying information:\n"
            "We minimize identifying information in unauthenticated "
            "channels (email transit, third-party SMTP relays). Full "
            "details — including the clinic name and the source / "
            "target organization names — are visible only inside the "
            "authenticated portal session.\n"
            "\n"
            "If you did not expect this email, do not click the link. "
            "Contact your OsirisCare account representative.\n"
            "\n"
            "---\n"
            "OsirisCare — substrate-level cross-organization site "
            "relocate notice"
        )
        await send_email(
            source_owner_email,
            "OsirisCare: action required — site relocate request",
            body,
        )
    except Exception:
        logger.error("source_release_email_failed", exc_info=True)


async def _send_target_accept_email(
    *,
    target_owner_email: str,
    relocate_id: str,
    target_accept_token: str,
    expires_at: datetime,
) -> None:
    """Target-org owner notice: action required, click to portal.

    Opaque mode (default). No site or organization names in the
    subject or body — the magic link redirects through portal auth
    where full request context is shown, including the BAA-on-file
    precondition and the cooling-off window detail."""
    try:
        from .email_service import send_email
        accept_url = (
            f"{BASE_URL}/admin/cross-org-relocate/target-accept"
            f"?token={target_accept_token}&id={relocate_id}"
        )
        body = (
            "Hello,\n"
            "\n"
            "An action is requested for one of your OsirisCare client "
            "organizations: a cross-organization site relocate is "
            "ready for your acceptance. The current owner of the "
            "source organization has released the site; your "
            "acceptance is required to receive it.\n"
            "\n"
            "To review the request and take action, click here within "
            "7 days. The link redirects you through OsirisCare portal "
            "authentication, where the full request context — site "
            "name, source organization, target organization (yours), "
            "BAA-on-file precondition, and cooling-off window — is "
            "visible:\n"
            f"  {accept_url}\n"
            "\n"
            f"Link expires: {expires_at.isoformat()}\n"
            f"Reference: relocate-{relocate_id}\n"
            "\n"
            "Why this email omits identifying information:\n"
            "We minimize identifying information in unauthenticated "
            "channels (email transit, third-party SMTP relays). Full "
            "details are visible only inside the authenticated portal "
            "session.\n"
            "\n"
            "If you did not expect this email, do not click the link. "
            "Contact your OsirisCare account representative.\n"
            "\n"
            "---\n"
            "OsirisCare — substrate-level cross-organization site "
            "relocate notice"
        )
        await send_email(
            target_owner_email,
            "OsirisCare: action required — site relocate accept request",
            body,
        )
    except Exception:
        logger.error("target_accept_email_failed", exc_info=True)


async def _send_post_execute_email(
    *,
    recipient_email: str,
    relocate_id: str,
    executed_at: datetime,
    attestation_bundle_id: Optional[str],
) -> None:
    """Post-execute receipt: no action; both owners get one.

    Opaque mode (default). No site or organization names in the
    subject or body — the recipient logs into the portal to see full
    context including the auditor kit URL for the moved site."""
    try:
        from .email_service import send_email
        body = (
            "Hello,\n"
            "\n"
            "A cross-organization site relocate that involved one of "
            "your OsirisCare client organizations has been completed. "
            "Log in to the OsirisCare portal to view the full context, "
            "including the auditor kit URL for the moved site.\n"
            "\n"
            f"Completed at: {executed_at.isoformat()}\n"
            f"Reference: relocate-{relocate_id}\n"
            + (
                f"Cryptographic chain anchor: {attestation_bundle_id}\n"
                if attestation_bundle_id
                else ""
            ) +
            "\n"
            f"Portal: {BASE_URL}\n"
            "\n"
            "Why this email omits identifying information:\n"
            "We minimize identifying information in unauthenticated "
            "channels. The full context — site name, organization "
            "names, executor, auditor kit URL — is visible only inside "
            "the authenticated portal session.\n"
            "\n"
            "Questions? Contact your OsirisCare account representative.\n"
            "\n"
            "---\n"
            "OsirisCare — substrate-level cross-organization site "
            "relocate completion notice"
        )
        await send_email(
            recipient_email,
            "OsirisCare: site relocate completed",
            body,
        )
    except Exception:
        logger.error("post_execute_email_failed", exc_info=True)


# ─────────────────────────────────────────────────────────────────
# Endpoint 1: initiate (Osiris admin only)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/initiate")
async def initiate_cross_org_relocate(
    body: InitiateRelocateRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Osiris admin initiates a cross-org relocate.

    Creates a pending_source_release row + magic-link tokens for both
    source-owner + target-owner. Sends source-release email; the target
    won't be contacted until source releases. Steve mit 6: ONLY Osiris
    admin can hit this endpoint — partners cannot.
    """
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()
    if not actor_email:
        raise HTTPException(403, "Admin actor email is required.")

    async with admin_connection(pool) as conn:
        await _require_feature_enabled(conn)

        # Resolve site → source_org. The site MUST exist and have a
        # client_org_id (otherwise it's an unassigned orphan; refuse).
        site = await conn.fetchrow(
            """
            SELECT site_id, client_org_id, clinic_name, status
              FROM sites
             WHERE site_id = $1
            """,
            body.site_id,
        )
        if not site:
            raise HTTPException(404, f"Site {body.site_id} not found.")
        if site["status"] == "inactive":
            raise HTTPException(
                400,
                "Site is inactive. Cross-org relocate refuses inactive sites.",
            )
        if not site["client_org_id"]:
            raise HTTPException(
                400,
                "Site has no source client_org_id. Assign before relocate.",
            )

        source_org_id = str(site["client_org_id"])
        if source_org_id == body.target_org_id:
            raise HTTPException(
                400,
                "Source and target client_org_id are identical. "
                "Cross-org relocate is for moving BETWEEN orgs.",
            )

        # Steve preconditions
        await _check_no_pending_owner_transfers(conn, source_org_id, body.target_org_id)

        # BAA precondition checked at target-accept (we don't want to
        # block initiate just because the BAA is a few hours away from
        # being signed — but the flow can't complete without it).

        # Per-source-org cooling-off + expiry
        prefs = await conn.fetchrow(
            """
            SELECT transfer_cooling_off_hours, transfer_expiry_days
              FROM client_orgs WHERE id = $1::uuid
            """,
            source_org_id,
        )
        cool_h = (prefs and prefs["transfer_cooling_off_hours"]) or DEFAULT_COOLING_OFF_HOURS
        exp_d = (prefs and prefs["transfer_expiry_days"]) or DEFAULT_EXPIRY_DAYS

        # Generate the source-release magic-link token only. The
        # target-accept token is generated at source-release time
        # (RT21 Phase 3 hardening: shorter leak window — no token sits
        # idle waiting for source to act). Same rotation pattern as
        # client_owner_transfer at the ack step.
        source_release_token = secrets.token_urlsafe(32)
        source_release_token_hash = hashlib.sha256(
            source_release_token.encode("utf-8")
        ).hexdigest()
        target_accept_token_hash = None  # filled at source-release

        expires_at = datetime.now(timezone.utc) + timedelta(days=exp_d)

        # Patricia RT21 Gate 2: pin the recipient emails at issue-time
        # so the §164.528 attribution is unambiguous even in a multi-
        # owner org. The link is ISSUED to a specific human; the
        # endpoints will verify the redeemer's email matches.
        source_owner_row = await conn.fetchrow(
            """
            SELECT email FROM client_users
             WHERE client_org_id = $1::uuid
               AND role = 'owner'
               AND deleted_at IS NULL
             ORDER BY created_at ASC
             LIMIT 1
            """,
            source_org_id,
        )
        target_owner_row = await conn.fetchrow(
            """
            SELECT email FROM client_users
             WHERE client_org_id = $1::uuid
               AND role = 'owner'
               AND deleted_at IS NULL
             ORDER BY created_at ASC
             LIMIT 1
            """,
            body.target_org_id,
        )
        if not source_owner_row:
            raise HTTPException(
                412,
                "Source org has no owner; assign one before initiating.",
            )
        if not target_owner_row:
            raise HTTPException(
                412,
                "Target org has no owner; assign one before initiating.",
            )
        expected_source_email = source_owner_row["email"].lower().strip()
        expected_target_email = target_owner_row["email"].lower().strip()

        # Note: org / site names are NOT resolved here. Counsel revision
        # 2026-05-06 made emails opaque; the helpers no longer need
        # those fields. The portal serves identifying context behind
        # authentication. The names lookup that used to live here was
        # removed.

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO cross_org_site_relocate_requests (
                    site_id, source_org_id, target_org_id,
                    initiator_email, initiator_reason,
                    source_release_token_hash, target_accept_token_hash,
                    expected_source_release_email, expected_target_accept_email,
                    expires_at
                )
                VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                body.site_id,
                source_org_id,
                body.target_org_id,
                actor_email,
                body.reason,
                source_release_token_hash,
                target_accept_token_hash,
                expected_source_email,
                expected_target_email,
                expires_at,
            )
            relocate_id = str(row["id"])

            bundle_id = await _emit_attestation(
                conn,
                event_type="cross_org_site_relocate_initiated",
                actor_email=actor_email,
                actor_role="admin",
                reason=body.reason,
                site_id=body.site_id,
                relocate_id=relocate_id,
                extra_metadata={
                    "source_org_id": source_org_id,
                    "target_org_id": body.target_org_id,
                    "expires_at": expires_at.isoformat(),
                    "cooling_off_hours": cool_h,
                },
            )
            await _append_bundle_id(conn, relocate_id, bundle_id)

            # §164.528 disclosure-accounting (Patricia RT21 #3): record
            # the relocate event in admin_audit_log so the disclosure
            # is recoverable through standard audit-trail reads. The
            # attestation bundle is the cryptographic complement.
            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5::jsonb, $6
                )
                """,
                user.get("id"),
                actor_email,
                "cross_org_site_relocate_initiated",
                f"site:{body.site_id}",
                json.dumps({
                    "relocate_id": relocate_id,
                    "source_org_id": source_org_id,
                    "target_org_id": body.target_org_id,
                    "reason": body.reason,
                    "attestation_bundle_id": bundle_id,
                }),
                (request.client.host if request.client else None),
            )

    # Patricia RT21 Gate 2: NEVER return plaintext tokens in the
    # response body. The endpoint is feature-flag-gated AND email
    # delivery is required before the flow can complete. RT21 Phase 3
    # wires the source-release email here — the plaintext token rides
    # the email channel, never the response body.
    logger.info(
        "cross_org_site_relocate initiated",
        extra={
            "relocate_id": relocate_id,
            "site_id": body.site_id,
            "source_org_id": source_org_id,
            "target_org_id": body.target_org_id,
            "actor": actor_email,
            "expected_source_email": expected_source_email,
            "expected_target_email": expected_target_email,
        },
    )

    # Phase 3: deliver the source-release magic link by email. The
    # target-accept email fires LATER, only after source-release
    # succeeds (so the target owner isn't notified of a move that
    # might never happen). The send is best-effort — failures log at
    # ERROR but the relocate row + attestation are already committed.
    await _send_source_release_email(
        source_owner_email=expected_source_email,
        relocate_id=relocate_id,
        source_release_token=source_release_token,
        expires_at=expires_at,
    )
    # Marcus RT21 P3 token-lifecycle rule: drop the plaintext local var
    # as soon as it's done being used. Defends against post-mortem stack
    # capture / accidental logging / a future code path that survives an
    # exception. The hash persists in DB; the plaintext only existed
    # long enough to ride the email channel.
    source_release_token = None  # noqa: F841

    return {
        "relocate_id": relocate_id,
        "status": "pending_source_release",
        "expires_at": expires_at.isoformat(),
        "cooling_off_hours": cool_h,
        "expected_source_release_email": expected_source_email,
        "expected_target_accept_email": expected_target_email,
    }


# ─────────────────────────────────────────────────────────────────
# Endpoint 2: source-release (source-org owner via magic link)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/source-release")
async def source_release(body: SourceReleaseRequest, request: Request):
    """Source-org owner approves the release via magic-link token."""
    pool = await get_pool()
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()

    async with admin_connection(pool) as conn:
        await _require_feature_enabled(conn)

        row = await conn.fetchrow(
            """
            SELECT id, site_id, source_org_id, target_org_id, status,
                   expires_at, expected_source_release_email
              FROM cross_org_site_relocate_requests
             WHERE source_release_token_hash = $1
               AND status = 'pending_source_release'
            """,
            token_hash,
        )
        if not row:
            raise HTTPException(404, "Invalid or already-used release link.")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(410, "Relocate request expired.")

        relocate_id = str(row["id"])
        # Patricia RT21 Gate 2: the link was ISSUED to a specific human.
        # Confirm that human is still an active owner of the source org.
        # Defense in depth across email-rename: we verify the EXPECTED
        # email is still an owner of record. If the owner was rotated
        # between initiate and click, the request needs to be canceled
        # + re-initiated to anchor on the new owner.
        expected_email = (row["expected_source_release_email"] or "").lower().strip()
        if not expected_email:
            raise HTTPException(
                500,
                "Request missing pinned source owner email — corrupted state.",
            )
        verify = await conn.fetchrow(
            """
            SELECT 1 FROM client_users
             WHERE client_org_id = $1::uuid
               AND LOWER(email) = $2
               AND role = 'owner'
               AND deleted_at IS NULL
            """,
            row["source_org_id"],
            expected_email,
        )
        if not verify:
            raise HTTPException(
                409,
                "Pinned source owner is no longer an active owner of the "
                "source org. Cancel + re-initiate to anchor on the "
                "current owner.",
            )
        actor_email = expected_email

        # Generate the target-accept token now (rotation pattern —
        # shorter leak window than issuing both tokens at initiate
        # time and letting the target token sit idle).
        target_accept_token = secrets.token_urlsafe(32)
        target_accept_token_hash = hashlib.sha256(
            target_accept_token.encode("utf-8")
        ).hexdigest()

        # Pre-fetch only what the opaque email needs: target owner
        # email + expiry. Org/site names + cooling_off_hours are NOT
        # included in the email (counsel revision 2026-05-06 — opaque
        # mode); the portal renders all identifying context after auth.
        ctx = await conn.fetchrow(
            """
            SELECT expected_target_accept_email, expires_at
              FROM cross_org_site_relocate_requests
             WHERE id = $1
            """,
            relocate_id,
        )

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE cross_org_site_relocate_requests
                   SET status = 'pending_target_accept',
                       source_release_email = $2,
                       source_release_at = NOW(),
                       source_release_reason = $3,
                       source_release_token_hash = NULL,
                       target_accept_token_hash = $4
                 WHERE id = $1
                """,
                relocate_id,
                actor_email,
                body.reason,
                target_accept_token_hash,
            )

            bundle_id = await _emit_attestation(
                conn,
                event_type="cross_org_site_relocate_source_released",
                actor_email=actor_email,
                actor_role="source_owner",
                reason=body.reason,
                site_id=row["site_id"],
                relocate_id=relocate_id,
                extra_metadata={
                    "source_org_id": str(row["source_org_id"]),
                    "target_org_id": str(row["target_org_id"]),
                },
            )
            await _append_bundle_id(conn, relocate_id, bundle_id)

    # Phase 3: deliver the target-accept magic link by email. After
    # transaction commit so a partial-success state never exists where
    # the target email links to a row that doesn't reflect "released."
    if ctx:
        await _send_target_accept_email(
            target_owner_email=(ctx["expected_target_accept_email"] or "").lower().strip(),
            relocate_id=relocate_id,
            target_accept_token=target_accept_token,
            expires_at=ctx["expires_at"],
        )
    # Marcus RT21 P3 token-lifecycle rule: drop plaintext after use.
    target_accept_token = None  # noqa: F841

    logger.info(
        "cross_org_site_relocate source-released",
        extra={"relocate_id": relocate_id, "actor": actor_email},
    )
    return {"relocate_id": relocate_id, "status": "pending_target_accept"}


# ─────────────────────────────────────────────────────────────────
# Endpoint 3: target-accept (target-org owner via magic link)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/target-accept")
async def target_accept(body: TargetAcceptRequest, request: Request):
    """Target-org owner approves the receipt via magic-link token.
    Triggers the cooling_off_until window — admin can't execute until
    it elapses (Steve mit 2)."""
    pool = await get_pool()
    token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()

    async with admin_connection(pool) as conn:
        await _require_feature_enabled(conn)

        row = await conn.fetchrow(
            """
            SELECT id, site_id, source_org_id, target_org_id, status,
                   expires_at, expected_target_accept_email
              FROM cross_org_site_relocate_requests
             WHERE target_accept_token_hash = $1
               AND status = 'pending_target_accept'
            """,
            token_hash,
        )
        if not row:
            raise HTTPException(404, "Invalid or already-used accept link.")
        if row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(410, "Relocate request expired.")

        # Steve mit 5: BAA precondition. Re-checked at execute time too,
        # but we want to fail fast at accept so the target owner sees
        # an actionable error.
        await _check_target_org_baa(conn, str(row["target_org_id"]))

        relocate_id = str(row["id"])

        # Patricia RT21 Gate 2: pinned target email — same shape as
        # source-release verification.
        expected_email = (row["expected_target_accept_email"] or "").lower().strip()
        if not expected_email:
            raise HTTPException(
                500,
                "Request missing pinned target owner email — corrupted state.",
            )
        verify = await conn.fetchrow(
            """
            SELECT 1 FROM client_users
             WHERE client_org_id = $1::uuid
               AND LOWER(email) = $2
               AND role = 'owner'
               AND deleted_at IS NULL
            """,
            row["target_org_id"],
            expected_email,
        )
        if not verify:
            raise HTTPException(
                409,
                "Pinned target owner is no longer an active owner of the "
                "target org. Cancel + re-initiate to anchor on the "
                "current owner.",
            )
        actor_email = expected_email

        # Per-source-org cooling-off (the source's preference governs;
        # the target opted into receiving so they're agreeing to the
        # source's window).
        prefs = await conn.fetchrow(
            "SELECT transfer_cooling_off_hours FROM client_orgs WHERE id = $1::uuid",
            row["source_org_id"],
        )
        cool_h = (prefs and prefs["transfer_cooling_off_hours"]) or DEFAULT_COOLING_OFF_HOURS
        cooling_off_until = datetime.now(timezone.utc) + timedelta(hours=cool_h)

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE cross_org_site_relocate_requests
                   SET status = 'pending_admin_execute',
                       target_accept_email = $2,
                       target_accept_at = NOW(),
                       target_accept_reason = $3,
                       target_accept_token_hash = NULL,
                       cooling_off_until = $4
                 WHERE id = $1
                """,
                relocate_id,
                actor_email,
                body.reason,
                cooling_off_until,
            )

            bundle_id = await _emit_attestation(
                conn,
                event_type="cross_org_site_relocate_target_accepted",
                actor_email=actor_email,
                actor_role="target_owner",
                reason=body.reason,
                site_id=row["site_id"],
                relocate_id=relocate_id,
                extra_metadata={
                    "cooling_off_until": cooling_off_until.isoformat(),
                    "cooling_off_hours": cool_h,
                },
            )
            await _append_bundle_id(conn, relocate_id, bundle_id)

    return {
        "relocate_id": relocate_id,
        "status": "pending_admin_execute",
        "cooling_off_until": cooling_off_until.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# Endpoint 4: execute (Osiris admin, after cooling-off)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/{relocate_id}/execute")
async def execute_relocate(
    relocate_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Osiris admin pulls the trigger after cooling-off elapses.
    Flips sites.client_org_id + sets sites.prior_client_org_id."""
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()

    async with admin_connection(pool) as conn:
        await _require_feature_enabled(conn)

        row = await conn.fetchrow(
            """
            SELECT id, site_id, source_org_id, target_org_id, status,
                   cooling_off_until, expires_at
              FROM cross_org_site_relocate_requests
             WHERE id = $1
            """,
            relocate_id,
        )
        if not row:
            raise HTTPException(404, "Relocate request not found.")
        if row["status"] != "pending_admin_execute":
            raise HTTPException(
                409,
                f"Relocate is in status {row['status']!r}; execute requires "
                f"pending_admin_execute.",
            )
        now = datetime.now(timezone.utc)
        if row["cooling_off_until"] and row["cooling_off_until"] > now:
            raise HTTPException(
                425,  # Too Early
                f"Cooling-off window not elapsed; until "
                f"{row['cooling_off_until'].isoformat()}.",
            )
        if row["expires_at"] < now:
            raise HTTPException(410, "Relocate request expired.")

        await _check_target_org_baa(conn, str(row["target_org_id"]))

        # Pre-fetch only the recipient emails for the opaque post-
        # execute receipt. Org/site names omitted — counsel revision
        # 2026-05-06: emails are opaque; the portal serves identifying
        # context after authentication.
        ctx = await conn.fetchrow(
            """
            SELECT expected_source_release_email,
                   expected_target_accept_email
              FROM cross_org_site_relocate_requests
             WHERE id = $1
            """,
            relocate_id,
        )

        async with conn.transaction():
            # Marcus RT21 Gate 2 fix: lock the relocate row + guard the
            # sites UPDATE with `WHERE client_org_id = source_org_id`.
            # Two simultaneous admins reading status=pending_admin_execute
            # could otherwise both flip — first wins, second muddies the
            # audit trail. The status transition gate (UPDATE ... WHERE
            # status='pending_admin_execute' RETURNING) makes the second
            # flip a no-op.
            #
            # Also LOCK the relocate row at the start of the transaction.
            await conn.execute(
                """
                SELECT 1 FROM cross_org_site_relocate_requests
                 WHERE id = $1 FOR UPDATE
                """,
                relocate_id,
            )

            # Flip sites.client_org_id + record prior. The WHERE clause
            # also filters by current client_org_id = source_org_id —
            # if a concurrent execute already flipped it, the UPDATE
            # affects 0 rows and we abort.
            flipped = await conn.execute(
                """
                UPDATE sites
                   SET prior_client_org_id = client_org_id,
                       client_org_id = $2::uuid
                 WHERE site_id = $1
                   AND client_org_id = $3::uuid
                """,
                row["site_id"],
                row["target_org_id"],
                row["source_org_id"],
            )
            # asyncpg returns "UPDATE n" string; parse the count.
            if flipped.endswith(" 0"):
                raise HTTPException(
                    409,
                    "Site ownership changed since pending_admin_execute "
                    "was set — another execute may be in flight or the "
                    "site was relocated by a different path. Refusing "
                    "to double-flip.",
                )

            # Same status guard on the relocate row — ensures only one
            # transition succeeds.
            transitioned = await conn.execute(
                """
                UPDATE cross_org_site_relocate_requests
                   SET status = 'completed',
                       executor_email = $2,
                       executed_at = NOW()
                 WHERE id = $1
                   AND status = 'pending_admin_execute'
                """,
                relocate_id,
                actor_email,
            )
            if transitioned.endswith(" 0"):
                raise HTTPException(
                    409,
                    "Relocate state transitioned by another execute. "
                    "No-op.",
                )

            bundle_id = await _emit_attestation(
                conn,
                event_type="cross_org_site_relocate_executed",
                actor_email=actor_email,
                actor_role="admin",
                reason=(
                    f"execute relocate_id={relocate_id} "
                    f"site={row['site_id']}"
                ),
                site_id=row["site_id"],
                relocate_id=relocate_id,
                extra_metadata={
                    "source_org_id": str(row["source_org_id"]),
                    "target_org_id": str(row["target_org_id"]),
                },
            )
            await _append_bundle_id(conn, relocate_id, bundle_id)

            # §164.528 disclosure-accounting on execute (Patricia RT21).
            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                )
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
                """,
                user.get("id"),
                actor_email,
                "cross_org_site_relocate_executed",
                f"site:{row['site_id']}",
                json.dumps({
                    "relocate_id": relocate_id,
                    "source_org_id": str(row["source_org_id"]),
                    "target_org_id": str(row["target_org_id"]),
                    "attestation_bundle_id": bundle_id,
                }),
                (request.client.host if request.client else None),
            )

    # Phase 3: deliver post-execute receipts to BOTH owners. Best-
    # effort — failures log at ERROR but the move + attestation are
    # already committed. The bundle_id rides the email so each owner
    # has the chain anchor in their records.
    if ctx:
        executed_at = datetime.now(timezone.utc)
        s_email = (ctx["expected_source_release_email"] or "").lower().strip()
        t_email = (ctx["expected_target_accept_email"] or "").lower().strip()
        # Opaque mode (counsel revision): same body for both owners,
        # no source/target differentiation in the email itself. The
        # portal serves the per-recipient detail after authentication.
        if s_email:
            await _send_post_execute_email(
                recipient_email=s_email,
                relocate_id=relocate_id,
                executed_at=executed_at,
                attestation_bundle_id=bundle_id,
            )
        if t_email:
            await _send_post_execute_email(
                recipient_email=t_email,
                relocate_id=relocate_id,
                executed_at=executed_at,
                attestation_bundle_id=bundle_id,
            )

    return {"relocate_id": relocate_id, "status": "completed"}


# ─────────────────────────────────────────────────────────────────
# Endpoint 5: cancel (any of 3 actors via magic-link OR admin auth)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/{relocate_id}/cancel")
async def cancel_relocate(
    relocate_id: str,
    body: CancelRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Cancel a pending relocate. v1 ships with admin-only cancel; the
    source-owner / target-owner cancel paths use the same magic-link
    tokens issued at initiate time and route through this endpoint
    after token verification."""
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()

    async with admin_connection(pool) as conn:
        await _require_feature_enabled(conn)

        row = await conn.fetchrow(
            """
            SELECT id, site_id, source_org_id, target_org_id, status
              FROM cross_org_site_relocate_requests
             WHERE id = $1
            """,
            relocate_id,
        )
        if not row:
            raise HTTPException(404, "Relocate request not found.")
        if row["status"] not in (
            "pending_source_release",
            "pending_target_accept",
            "pending_admin_execute",
        ):
            raise HTTPException(
                409,
                f"Relocate is in terminal status {row['status']!r}; "
                "cancel only valid in pending states.",
            )

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE cross_org_site_relocate_requests
                   SET status = 'canceled',
                       canceled_by_email = $2,
                       canceled_at = NOW(),
                       cancel_reason = $3,
                       source_release_token_hash = NULL,
                       target_accept_token_hash = NULL
                 WHERE id = $1
                """,
                relocate_id,
                actor_email,
                body.reason,
            )

            bundle_id = await _emit_attestation(
                conn,
                event_type="cross_org_site_relocate_canceled",
                actor_email=actor_email,
                actor_role="admin",
                reason=body.reason,
                site_id=row["site_id"],
                relocate_id=relocate_id,
            )
            await _append_bundle_id(conn, relocate_id, bundle_id)

    return {"relocate_id": relocate_id, "status": "canceled"}


# ─────────────────────────────────────────────────────────────────
# Endpoint 6: get (admin visibility)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.get("/{relocate_id}")
async def get_relocate(
    relocate_id: str,
    user: Dict[str, Any] = Depends(require_admin),
):
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT id, site_id, source_org_id, target_org_id, status,
                   initiator_email, initiator_reason,
                   source_release_email, source_release_at,
                   target_accept_email, target_accept_at,
                   executor_email, executed_at,
                   canceled_by_email, canceled_at, cancel_reason,
                   expires_at, cooling_off_until,
                   attestation_bundle_ids, created_at
              FROM cross_org_site_relocate_requests
             WHERE id = $1
            """,
            relocate_id,
        )
        if not row:
            raise HTTPException(404, "Relocate request not found.")
        return {
            k: (v.isoformat() if isinstance(v, datetime) else (str(v) if isinstance(v, UUID) else v))
            for k, v in dict(row).items()
        }


# ─────────────────────────────────────────────────────────────────
# Endpoint 7+8: dual-admin flag-flip (counsel governance hardening)
#
# Outside-counsel adversarial review (2026-05-06): a single admin
# enabling a legally sensitive capability is the design's governance
# choke point. The feature-flag toggle does not live in the
# cryptographic chain (Marcus FK finding; substrate-level event with
# no site anchor); its audit lives in (1) the append-only feature_flags
# row + (2) admin_audit_log. Counsel's hardening: TWO distinct admins.
#
# Step 1 — propose-enable: first admin records intent + reason ≥20ch.
#   Writes proposal columns; flag stays disabled.
# Step 2 — approve-enable: second admin must be DIFFERENT from
#   proposer. Reason ≥40ch — this is where the outside-counsel
#   opinion identifier lives. DB CHECK enforces approver ≠ proposer.
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/propose-enable")
async def propose_enable(
    body: ProposeEnableRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """First admin proposes enabling the cross-org-relocate feature.
    A SECOND distinct admin must follow up with /approve-enable for the
    flag to actually flip."""
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()
    if not actor_email:
        raise HTTPException(403, "Admin actor email is required.")

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT enabled FROM feature_flags WHERE flag_name = $1",
            FLAG_NAME,
        )
        if not row:
            raise HTTPException(
                500,
                f"Feature flag {FLAG_NAME!r} row missing — mig 281 not applied.",
            )
        if row["enabled"]:
            raise HTTPException(
                409,
                f"Feature {FLAG_NAME!r} already enabled.",
            )

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE feature_flags
                   SET enable_proposed_by_email = $2,
                       enable_proposed_at = NOW(),
                       enable_proposed_reason = $3
                 WHERE flag_name = $1
                """,
                FLAG_NAME,
                actor_email,
                body.reason,
            )

            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                )
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
                """,
                user.get("id"),
                actor_email,
                "propose_enable_cross_org_site_relocate",
                f"feature_flag:{FLAG_NAME}",
                json.dumps({"reason": body.reason}),
                (request.client.host if request.client else None),
            )

    return {
        "flag_name": FLAG_NAME,
        "proposed_by": actor_email,
        "next_step": (
            "A second distinct admin must POST /approve-enable with the "
            "outside-counsel opinion identifier in the reason field "
            "(>=40 chars) for the flag to flip."
        ),
    }


@cross_org_relocate_router.post("/approve-enable")
async def approve_enable(
    body: ApproveEnableRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Second admin approves a pending propose-enable. MUST be a
    different admin than the proposer (DB CHECK + endpoint check).
    Reason >=40ch — counsel-opinion identifier lives here."""
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()
    if not actor_email:
        raise HTTPException(403, "Admin actor email is required.")

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT enabled, enable_proposed_by_email, enable_proposed_at,
                   enable_proposed_reason
              FROM feature_flags
             WHERE flag_name = $1
            """,
            FLAG_NAME,
        )
        if not row:
            raise HTTPException(
                500,
                f"Feature flag {FLAG_NAME!r} row missing — mig 281 not applied.",
            )
        if row["enabled"]:
            raise HTTPException(
                409,
                f"Feature {FLAG_NAME!r} already enabled.",
            )
        if not row["enable_proposed_by_email"]:
            raise HTTPException(
                412,
                "No pending proposal. Call /propose-enable first as a "
                "different admin.",
            )
        proposer = row["enable_proposed_by_email"].lower().strip()
        if proposer == actor_email:
            # Defense in depth — same check at the DB layer (CHECK
            # constraint mig 282) so a code-path bypass still fails.
            raise HTTPException(
                403,
                f"Same-admin self-approval rejected: this admin "
                f"({actor_email}) proposed the enable. A different "
                f"admin must approve. Counsel governance rule.",
            )

        async with conn.transaction():
            try:
                await conn.execute(
                    """
                    UPDATE feature_flags
                       SET enabled = true,
                           enabled_at = NOW(),
                           enabled_by_email = $2,
                           enable_reason = $3
                     WHERE flag_name = $1
                    """,
                    FLAG_NAME,
                    actor_email,
                    body.reason,
                )
            except Exception as e:
                raise HTTPException(
                    400,
                    f"Feature-flag update rejected at DB layer: {e}. "
                    "Verify reason >=40ch and approver != proposer.",
                )

            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                )
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
                """,
                user.get("id"),
                actor_email,
                "approve_enable_cross_org_site_relocate",
                f"feature_flag:{FLAG_NAME}",
                json.dumps({
                    "approver_reason": body.reason,
                    "proposer": proposer,
                    "proposed_at": row["enable_proposed_at"].isoformat(),
                    "proposer_reason": row["enable_proposed_reason"],
                }),
                (request.client.host if request.client else None),
            )

    return {
        "flag_name": FLAG_NAME,
        "enabled": True,
        "proposer": proposer,
        "approver": actor_email,
    }


# ─────────────────────────────────────────────────────────────────
# Sweep loop: expire stale requests
# ─────────────────────────────────────────────────────────────────


SWEEP_INTERVAL_S = 60


async def cross_org_relocate_sweep_loop():
    """Background loop that expires rows past expires_at without
    progression. Each expired row writes a `cross_org_site_relocate_
    expired` attestation so the chain has a closure event (Maya P0-3
    parity from RT19/MFA sweep)."""
    pool = await get_pool()
    while True:
        try:
            async with admin_connection(pool) as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, site_id, source_org_id, target_org_id, status
                      FROM cross_org_site_relocate_requests
                     WHERE status IN (
                         'pending_source_release',
                         'pending_target_accept',
                         'pending_admin_execute'
                     )
                       AND expires_at < NOW()
                     LIMIT 50
                    """
                )
                for row in rows:
                    relocate_id = str(row["id"])
                    async with conn.transaction():
                        await conn.execute(
                            """
                            UPDATE cross_org_site_relocate_requests
                               SET status = 'expired',
                                   source_release_token_hash = NULL,
                                   target_accept_token_hash = NULL
                             WHERE id = $1
                               AND status = $2
                            """,
                            relocate_id,
                            row["status"],
                        )
                        bundle_id = await _emit_attestation(
                            conn,
                            event_type="cross_org_site_relocate_expired",
                            actor_email="system@osiriscare.io",
                            actor_role="system",
                            reason=(
                                f"expires_at passed for relocate "
                                f"{relocate_id} site={row['site_id']}"
                            ),
                            site_id=row["site_id"],
                            relocate_id=relocate_id,
                        )
                        await _append_bundle_id(conn, relocate_id, bundle_id)
        except Exception as e:
            logger.error(
                "cross_org_relocate_sweep_loop iteration failed",
                extra={"err": str(e)},
                exc_info=True,
            )
        await asyncio.sleep(SWEEP_INTERVAL_S)
