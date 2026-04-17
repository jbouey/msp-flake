"""Partner agreement e-signature — MSA + Subcontractor BAA + Reseller Addendum.

Mirrors the BAA flow in ``client_signup.py``: append-only SHA256-committed
signatures stored in ``partner_agreements`` (Migration 228). A partner cannot
invite clinics (Batch C) until all three agreement types are signed with the
current version.

Why three:
  * ``msa``                — Master Software License + Services Agreement
  * ``subcontractor_baa``  — Subcontractor BAA with the MSP (OsirisCare is
                             subcontractor to the MSP, NEVER direct-to-CE).
  * ``reseller_addendum``  — resale, margin, brand, client data portability.

Bumping a ``*_VERSION`` constant auto-invalidates all prior signatures of
that type — partners re-sign at next dashboard login.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .fleet import get_pool
from .partners import require_partner_role
from .tenant_middleware import admin_connection

try:
    from .shared import check_rate_limit
except ImportError:  # pytest-safe (mirrors client_signup.py / auth.py)
    from shared import check_rate_limit  # type: ignore[no-redef]

logger = logging.getLogger("partner_agreements")


# ─── Current agreement versions (source of truth) ────────────────────
# Bumping a version here forces re-sign at next login. Old signatures
# remain in the table bound to the hash of the text they originally saw.

MSA_VERSION = "msa-v1.0-2026-04-17"
SUBCONTRACTOR_BAA_VERSION = "baa-v1.0-2026-04-17"
RESELLER_ADDENDUM_VERSION = "reseller-v1.0-2026-04-17"

CURRENT_VERSIONS: Dict[str, str] = {
    "msa": MSA_VERSION,
    "subcontractor_baa": SUBCONTRACTOR_BAA_VERSION,
    "reseller_addendum": RESELLER_ADDENDUM_VERSION,
}

REQUIRED_TYPES = tuple(CURRENT_VERSIONS.keys())


router = APIRouter(prefix="/api/partners/agreements", tags=["partner-agreements"])


# ─── Models ──────────────────────────────────────────────────────────

class SignAgreement(BaseModel):
    agreement_type: Literal["msa", "subcontractor_baa", "reseller_addendum"]
    version: str = Field(..., min_length=1, max_length=64)
    signer_name: str = Field(..., min_length=1, max_length=255)
    text_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")

    @field_validator("version")
    @classmethod
    def _version_current(cls, v: str, info) -> str:
        atype = info.data.get("agreement_type")
        if atype and CURRENT_VERSIONS.get(atype) != v:
            raise ValueError(
                f"version '{v}' is not current for {atype}; "
                f"expected '{CURRENT_VERSIONS.get(atype)}'"
            )
        return v


# ─── Helpers ─────────────────────────────────────────────────────────

async def _get_active_agreements(conn, partner_id: str) -> Dict[str, Dict[str, Any]]:
    """Return {agreement_type → {version, signed_at, signer_name, text_sha256}}
    for the authenticated partner via the migration-228 view."""
    rows = await conn.fetch(
        """
        SELECT agreement_type, version, signed_at, signer_name, text_sha256
          FROM v_partner_active_agreements
         WHERE partner_id = $1
        """,
        partner_id,
    )
    return {
        r["agreement_type"]: {
            "version": r["version"],
            "signed_at": r["signed_at"].isoformat() if r["signed_at"] else None,
            "signer_name": r["signer_name"],
            "text_sha256": r["text_sha256"],
        }
        for r in rows
    }


def _missing_or_stale(active: Dict[str, Dict[str, Any]]) -> List[str]:
    """Returns the subset of REQUIRED_TYPES that are either unsigned or
    signed against an out-of-date version."""
    out: List[str] = []
    for atype, current_version in CURRENT_VERSIONS.items():
        row = active.get(atype)
        if not row or row["version"] != current_version:
            out.append(atype)
    return out


async def require_active_partner_agreements(
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> dict:
    """Gate for partner-privileged actions (e.g., create invites) that
    must not succeed until all three legal artifacts are current.

    Returns the partner dict on success; 428 Precondition Required with the
    list of missing agreement types otherwise. The dashboard catches 428,
    pops the sign-agreements modal, and retries the original action.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        active = await _get_active_agreements(conn, partner["id"])
    missing = _missing_or_stale(active)
    if missing:
        raise HTTPException(
            status_code=428,
            detail={
                "status": "agreements_required",
                "missing": missing,
                "current_versions": {t: CURRENT_VERSIONS[t] for t in missing},
                "error": (
                    "All partner agreements must be signed before this action. "
                    "Sign MSA + Subcontractor BAA + Reseller Addendum from the "
                    "partner dashboard."
                ),
            },
        )
    return partner


# ─── Routes ──────────────────────────────────────────────────────────

@router.post("/sign")
async def sign_agreement(
    req: SignAgreement,
    request: Request,
    partner: dict = require_partner_role("admin", "billing"),
) -> Dict[str, Any]:
    """Record a partner agreement e-signature.

    Only partners with role ``admin`` or ``billing`` may sign on behalf of
    the company. ``tech`` role is intentionally excluded — signing is a
    legal act, not a technical one.
    """
    # Rate-limit per partner. A human signing three agreements can reach
    # three calls in a minute; abuse would look like scripted replay.
    allowed, retry_after = await check_rate_limit(
        f"partner:{partner['id']}",
        "partner_sign_agreement",
        window_seconds=3600,
        max_requests=20,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many agreement attempts for this partner.",
            headers={"Retry-After": str(retry_after or 3600)},
        )

    # Signer email must match the authenticated partner user (if session).
    # Fall back to the partner company email for API-key auth.
    signer_email = partner.get("email") or partner.get("contact_email") or ""
    partner_user_id = partner.get("partner_user_id")
    signer_role = partner.get("user_role")

    agreement_id = str(uuid.uuid4())
    client_ip = request.client.host if request.client else None
    user_agent = (request.headers.get("user-agent") or "")[:500]

    pool = await get_pool()
    async with admin_connection(pool) as conn, conn.transaction():
        # If partner_user_id present, pull their email + role for the record.
        if partner_user_id:
            prow = await conn.fetchrow(
                "SELECT email, role FROM partner_users WHERE id = $1",
                uuid.UUID(partner_user_id) if isinstance(partner_user_id, str)
                else partner_user_id,
            )
            if prow:
                signer_email = prow["email"] or signer_email
                signer_role = prow["role"] or signer_role

        if not signer_email:
            raise HTTPException(
                status_code=400,
                detail="signer email could not be resolved from session",
            )

        await conn.execute(
            """
            INSERT INTO partner_agreements
                (agreement_id, partner_id, agreement_type, version,
                 text_sha256, signer_name, signer_email,
                 signer_ip, signer_user_agent, signer_role, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            """,
            agreement_id,
            partner["id"],
            req.agreement_type,
            req.version,
            req.text_sha256,
            req.signer_name,
            signer_email,
            client_ip,
            user_agent,
            signer_role,
            json.dumps({"partner_user_id": str(partner_user_id) if partner_user_id else None}),
        )

    logger.info(
        "partner_agreement_signed partner_id=%s type=%s version=%s "
        "agreement_id=%s signer=%s ip=%s",
        partner["id"], req.agreement_type, req.version,
        agreement_id, signer_email, client_ip,
    )
    return {
        "agreement_id": agreement_id,
        "agreement_type": req.agreement_type,
        "version": req.version,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/mine")
async def get_my_agreements(
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> Dict[str, Any]:
    """Return the partner's current signature status + current versions.

    Dashboard UI reads this on load to decide whether to pop the
    sign-agreements modal + which ones are stale.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        active = await _get_active_agreements(conn, partner["id"])
    missing = _missing_or_stale(active)
    return {
        "partner_id": partner["id"],
        "required_types": list(REQUIRED_TYPES),
        "current_versions": CURRENT_VERSIONS,
        "active": active,
        "missing": missing,
        "ready_to_invite": not missing,
    }
