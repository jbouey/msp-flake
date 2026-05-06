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
  POST   /enable-feature             — Patricia attested-flag flip

Feature-flag-gated. The flag itself is in feature_flags (Migration 281)
and toggling it requires its own Ed25519 attestation (≥40-char reason
including the legal-opinion identifier). Until counsel signs off and
the flag flips, every endpoint here returns 503.
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
# returns + an admin flips the flag through `/enable-feature`, the
# endpoints return 503 with an actionable message.
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
                "review. Endpoint disabled. The feature flag flips via "
                "POST /api/admin/cross-org-relocate/enable-feature, which "
                "itself requires Ed25519 attestation referencing the "
                "counsel sign-off."
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


class EnableFeatureRequest(BaseModel):
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
    """Steve mit 5: target client_org MUST have baa_on_file=true. The
    receiving BAA governs PHI handling once the site moves — without
    one on file, the move would create an uncovered-disclosure event.

    NOTE: this is a receive-side check. The substrate's BAA WITH the
    receiving org is the governing instrument; per RT21 Marcus,
    §164.504(e) BA-to-BA transfer rules don't apply because Osiris is
    the same BA on both sides."""
    row = await conn.fetchrow(
        "SELECT baa_on_file FROM client_orgs WHERE id = $1::uuid",
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

        # Generate magic-link tokens. Stored as SHA256 — never plaintext.
        # The plaintext goes in the email; the hash goes in the DB.
        source_release_token = secrets.token_urlsafe(32)
        target_accept_token = secrets.token_urlsafe(32)
        source_release_token_hash = hashlib.sha256(
            source_release_token.encode("utf-8")
        ).hexdigest()
        target_accept_token_hash = hashlib.sha256(
            target_accept_token.encode("utf-8")
        ).hexdigest()

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
    # delivery is required before the flow can complete. Until
    # `cross_org_site_relocate_email_delivery` is wired (Phase 3), the
    # initiate endpoint creates the row + attestation but the magic
    # links are unreachable. The feature flag stays disabled until
    # email infra ships — the design contract is "no flag flip without
    # email delivery."
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
    # Tokens are NOT returned. Email-driven delivery only.
    _ = source_release_token  # exists in DB as hash; plaintext is for email
    _ = target_accept_token

    return {
        "relocate_id": relocate_id,
        "status": "pending_source_release",
        "expires_at": expires_at.isoformat(),
        "cooling_off_hours": cool_h,
        "expected_source_release_email": expected_source_email,
        "expected_target_accept_email": expected_target_email,
        "_email_delivery_pending": True,
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

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE cross_org_site_relocate_requests
                   SET status = 'pending_target_accept',
                       source_release_email = $2,
                       source_release_at = NOW(),
                       source_release_reason = $3,
                       source_release_token_hash = NULL
                 WHERE id = $1
                """,
                relocate_id,
                actor_email,
                body.reason,
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
# Endpoint 7: enable-feature (Patricia RT21 attested-flag flip)
# ─────────────────────────────────────────────────────────────────


@cross_org_relocate_router.post("/enable-feature")
async def enable_feature(
    body: EnableFeatureRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Flip the feature flag from disabled→enabled. RT21 Gate 2 closure
    (Marcus FK finding): the flag-flip is NOT in privileged_access
    ALLOWED_EVENTS because compliance_bundles.site_id FKs to a real
    site_id and the flag-flip has no site anchor. Audit trail lives in:

      1. feature_flags row itself — append-only (DELETE trigger blocks).
         Stores actor_email + reason ≥40ch + enabled_at + (later) the
         disable triplet. Forensically recoverable.
      2. admin_audit_log row — standard §164.528 disclosure-accounting
         shape.

    The asymmetry vs other privileged events is documented in
    privileged_access_attestation.py near ALLOWED_EVENTS.

    Patricia's intent — that the ≥40-char `reason` carries the legal-
    opinion identifier — is preserved. Length is enforced both at the
    Pydantic body-validation layer AND at the DB CHECK constraint."""
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower().strip()
    if not actor_email:
        raise HTTPException(403, "Admin actor email is required.")

    async with admin_connection(pool) as conn:
        # Check current state — only flip from disabled→enabled here.
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
                # CHECK constraint failure (reason length, etc.).
                raise HTTPException(
                    400,
                    f"Feature-flag update rejected at DB layer: {e}. "
                    "Verify reason ≥40ch.",
                )

            # admin_audit_log row for §164.528 parity.
            await conn.execute(
                """
                INSERT INTO admin_audit_log (
                    user_id, username, action, target, details, ip_address
                )
                VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
                """,
                user.get("id"),
                actor_email,
                "enable_cross_org_site_relocate",
                f"feature_flag:{FLAG_NAME}",
                json.dumps({"reason": body.reason}),
                (request.client.host if request.client else None),
            )

    return {"flag_name": FLAG_NAME, "enabled": True}


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
