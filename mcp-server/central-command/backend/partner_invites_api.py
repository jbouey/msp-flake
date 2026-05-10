"""Partner→clinic invite flow (Migration 229).

A distribution bottleneck fix: the audit found partners could not onboard
a clinic without an OsirisCare intervention. This module lets a fully-signed
partner (MSA + Subcontractor BAA + Reseller Addendum all current, see
``partner_agreements_api.require_active_partner_agreements``) mint a signed,
single-use invite token, send it to a clinic via their own channel, and have
the resulting signup auto-attach to that partner's book of business.

Endpoints:

    POST   /api/partners/invites/create              — partner-authenticated
    GET    /api/partners/invites/mine                — partner-authenticated
    POST   /api/partners/invites/{invite_id}/revoke  — partner-authenticated
    GET    /api/partner-invites/{token}/validate     — PUBLIC (clinic-facing)

Webhook hook:

    consume_invite_for_signup(invite_token, signup_id) — called from the
    Stripe webhook handler when ``metadata.partner_invite_token`` is present
    on the checkout session. Atomic UPDATE with ``WHERE consumed_at IS NULL``
    + Migration 229 trigger guarantees single-use even under duplicate
    webhook delivery.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from .fleet import get_pool
from .partner_agreements_api import require_active_partner_agreements
from .partners import require_partner_role
from .tenant_middleware import admin_connection, admin_transaction

try:
    from .shared import check_rate_limit
except ImportError:  # pytest-safe
    from shared import check_rate_limit  # type: ignore[no-redef]

logger = logging.getLogger("partner_invites")


VALID_PLANS = ("pilot", "essentials", "professional", "enterprise")
TOKEN_BYTES = 32          # 256 bits
DEFAULT_TTL_DAYS = 14
MAX_TTL_DAYS = 60


# Two routers: one under /api/partners (authenticated), one under
# /api/partner-invites (public clinic-facing). Both register in main.py.
partner_router = APIRouter(prefix="/api/partners/invites", tags=["partner-invites"])
public_router = APIRouter(prefix="/api/partner-invites", tags=["partner-invites-public"])


# ─── Models ──────────────────────────────────────────────────────────

class CreateInvite(BaseModel):
    plan: str = Field(..., min_length=1, max_length=32)
    clinic_email: Optional[EmailStr] = None
    clinic_name: Optional[str] = Field(None, min_length=1, max_length=255)
    partner_brand: Optional[str] = Field(None, min_length=1, max_length=255)
    ttl_days: int = Field(DEFAULT_TTL_DAYS, ge=1, le=MAX_TTL_DAYS)

    @field_validator("plan")
    @classmethod
    def _plan_allowed(cls, v: str) -> str:
        if v not in VALID_PLANS:
            raise ValueError(f"plan must be one of {VALID_PLANS}")
        return v


class RevokeInvite(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ─── Helpers ─────────────────────────────────────────────────────────

def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _redact_token(token: str) -> str:
    """Log-safe token prefix — enough to trace, too short to replay."""
    return token[:8] + "…" if len(token) > 8 else "…"


# ─── Authenticated endpoints (partner) ──────────────────────────────

@partner_router.post("/create")
async def create_invite(
    req: CreateInvite,
    request: Request,
    partner: dict = Depends(require_active_partner_agreements),
) -> Dict[str, Any]:
    """Mint a single-use invite token. Plaintext returned ONCE.

    The partner is responsible for delivering the URL to the clinic. We do
    not email it — that would put OsirisCare back into the MSP's client
    relationship, which violates the non-operator posture.
    """
    # Abuse ceiling: 50 invites/hour/partner is fine for a real rollout,
    # cheap enough to block scripted token harvesting.
    allowed, retry_after = await check_rate_limit(
        f"partner:{partner['id']}",
        "partner_invite_create",
        window_seconds=3600,
        max_requests=50,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many invites created. Try again later.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    token = secrets.token_urlsafe(TOKEN_BYTES)         # ~43 chars URL-safe
    token_sha256 = _sha256_hex(token)
    invite_id = str(uuid.uuid4())
    partner_user_id = partner.get("partner_user_id")

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO partner_invites
                    (invite_id, partner_id, token_sha256,
                     clinic_email, clinic_name, plan,
                     partner_brand, created_by_user_id,
                     expires_at, metadata)
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    NOW() + make_interval(days => $9),
                    $10::jsonb
                )
             RETURNING expires_at
                """,
                invite_id,
                partner["id"],
                token_sha256,
                req.clinic_email,
                req.clinic_name,
                req.plan,
                req.partner_brand,
                uuid.UUID(partner_user_id) if isinstance(partner_user_id, str) else partner_user_id,
                req.ttl_days,
                json.dumps({}),
            )
        except asyncpg.UniqueViolationError:
            # token_sha256 collision (32-byte entropy → vanishingly unlikely)
            logger.error(
                "partner_invite token collision partner_id=%s — retry client-side",
                partner["id"],
            )
            raise HTTPException(status_code=503, detail="token collision, please retry")

    logger.info(
        "partner_invite_created partner_id=%s invite_id=%s plan=%s "
        "token=%s ttl_days=%d",
        partner["id"], invite_id, req.plan, _redact_token(token), req.ttl_days,
    )
    # Plaintext token returned ONCE. Subsequent fetches only see the SHA256.
    return {
        "invite_id": invite_id,
        "token": token,
        "invite_url": f"/signup?invite={token}",
        "plan": req.plan,
        "clinic_email": req.clinic_email,
        "clinic_name": req.clinic_name,
        "partner_brand": req.partner_brand,
        "expires_at": row["expires_at"].isoformat(),
        "ttl_days": req.ttl_days,
    }


@partner_router.get("/mine")
async def list_my_invites(
    partner: dict = require_partner_role("admin", "tech", "billing"),
    include_consumed: bool = False,
) -> Dict[str, Any]:
    """List a partner's invites (metadata only — no plaintext tokens)."""
    pool = await get_pool()
    # admin_transaction (wave-37): list_my_invites issues 2 admin
    # reads (filtered list — with/without consumed).
    async with admin_transaction(pool) as conn:
        if include_consumed:
            rows = await conn.fetch(
                """
                SELECT invite_id, plan, clinic_email, clinic_name, partner_brand,
                       created_at, expires_at, consumed_at, consumed_signup_id,
                       revoked_at, revoke_reason
                  FROM partner_invites
                 WHERE partner_id = $1
              ORDER BY created_at DESC
                 LIMIT 500
                """,
                partner["id"],
            )
        else:
            rows = await conn.fetch(
                """
                SELECT invite_id, plan, clinic_email, clinic_name, partner_brand,
                       created_at, expires_at, consumed_at, consumed_signup_id,
                       revoked_at, revoke_reason
                  FROM partner_invites
                 WHERE partner_id = $1
                   AND consumed_at IS NULL
                   AND revoked_at IS NULL
                   AND expires_at > NOW()
              ORDER BY created_at DESC
                 LIMIT 500
                """,
                partner["id"],
            )
    return {
        "partner_id": partner["id"],
        "invites": [
            {
                "invite_id": r["invite_id"],
                "plan": r["plan"],
                "clinic_email": r["clinic_email"],
                "clinic_name": r["clinic_name"],
                "partner_brand": r["partner_brand"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "consumed_at": r["consumed_at"].isoformat() if r["consumed_at"] else None,
                "consumed_signup_id": r["consumed_signup_id"],
                "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
                "revoke_reason": r["revoke_reason"],
                "status": (
                    "consumed" if r["consumed_at"]
                    else "revoked" if r["revoked_at"]
                    else "expired" if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc)
                    else "active"
                ),
            }
            for r in rows
        ],
    }


@partner_router.post("/{invite_id}/revoke")
async def revoke_invite(
    invite_id: str,
    req: RevokeInvite,
    partner: dict = require_partner_role("admin", "billing"),
) -> Dict[str, Any]:
    """Revoke an unconsumed invite. Consumed invites cannot be revoked —
    the Migration 229 trigger blocks the transition."""
    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE partner_invites
               SET revoked_at = NOW(),
                   revoke_reason = $3
             WHERE invite_id = $1
               AND partner_id = $2
               AND consumed_at IS NULL
               AND revoked_at IS NULL
         RETURNING invite_id, revoked_at
            """,
            invite_id, partner["id"], req.reason,
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="invite not found, already consumed, or already revoked",
        )
    logger.info(
        "partner_invite_revoked partner_id=%s invite_id=%s reason=%s",
        partner["id"], invite_id, req.reason,
    )
    return {"invite_id": row["invite_id"], "revoked_at": row["revoked_at"].isoformat()}


# ─── Public endpoint (clinic-facing) ────────────────────────────────

@public_router.get("/{token}/validate")
async def validate_invite(token: str, request: Request) -> Dict[str, Any]:
    """Validate a partner invite token WITHOUT consuming it.

    Returns the branded landing payload: partner name, plan, optional
    partner brand. The token itself stays plaintext in the URL query —
    a leaked URL + the token lookup is enough for a bad actor to hit
    the signup page early, but consumption is single-use and requires
    completing the signup flow + paying (pilot=$299, essentials=$499).
    Not a credential-grade risk.
    """
    # Rate-limit by client IP — scan for live tokens is the abuse shape.
    ip = _client_ip(request) or "unknown"
    allowed, retry_after = await check_rate_limit(
        f"ip:{ip}", "invite_validate", window_seconds=3600, max_requests=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many invite lookups. Try again later.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    if not token or len(token) > 256:
        raise HTTPException(status_code=400, detail="invalid token format")

    token_sha256 = _sha256_hex(token)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT pi.invite_id, pi.plan, pi.clinic_email, pi.clinic_name,
                   pi.partner_brand, pi.expires_at, pi.consumed_at,
                   pi.revoked_at,
                   p.name AS partner_name, p.slug AS partner_slug
              FROM partner_invites pi
              JOIN partners p ON p.id = pi.partner_id
             WHERE pi.token_sha256 = $1
            """,
            token_sha256,
        )
    if not row:
        raise HTTPException(status_code=404, detail="invite not found")
    if row["consumed_at"]:
        raise HTTPException(status_code=409, detail="invite already used")
    if row["revoked_at"]:
        raise HTTPException(status_code=410, detail="invite revoked")
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="invite expired")

    return {
        "invite_id": row["invite_id"],
        "partner_name": row["partner_name"],
        "partner_slug": row["partner_slug"],
        "partner_brand": row["partner_brand"],
        "plan": row["plan"],
        "clinic_email": row["clinic_email"],
        "clinic_name": row["clinic_name"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


# ─── Consumption helper (called from Stripe webhook) ─────────────────

async def consume_invite_for_signup(
    token: str,
    signup_id: str,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mark an invite consumed by a completed signup.

    Called from the Stripe webhook handler after ``checkout.session.completed``
    when ``metadata.partner_invite_token`` is present. Atomic UPDATE with the
    single-use guard in Migration 229 trigger + ``WHERE consumed_at IS NULL``
    predicate — duplicate webhook delivery is idempotent.

    Returns the consumed invite row on success, None if the token is not
    found / already consumed / expired. Caller is responsible for logging
    a warning in the None case.
    """
    if not token:
        return None

    token_sha256 = _sha256_hex(token)
    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            UPDATE partner_invites
               SET consumed_at = NOW(),
                   consumed_signup_id = $2,
                   consumed_ip = $3,
                   consumed_user_agent = $4
             WHERE token_sha256 = $1
               AND consumed_at IS NULL
               AND revoked_at IS NULL
               AND expires_at > NOW()
         RETURNING invite_id, partner_id, plan, clinic_email, clinic_name
            """,
            token_sha256, signup_id, client_ip, (user_agent or "")[:500],
        )
    if not row:
        logger.warning(
            "partner_invite consume skipped — token=%s signup_id=%s "
            "(not found, consumed, revoked, or expired)",
            _redact_token(token), signup_id,
        )
        return None

    logger.info(
        "partner_invite_consumed invite_id=%s partner_id=%s signup_id=%s plan=%s",
        row["invite_id"], row["partner_id"], signup_id, row["plan"],
    )
    return {
        "invite_id": row["invite_id"],
        "partner_id": str(row["partner_id"]),
        "plan": row["plan"],
        "clinic_email": row["clinic_email"],
        "clinic_name": row["clinic_name"],
    }
