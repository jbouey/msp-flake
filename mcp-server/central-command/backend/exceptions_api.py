"""
Compliance Exception Management API.

Endpoints for partners and clients to manage compliance exceptions.

Security: All endpoints verify ownership before allowing access.
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, Path
from pydantic import BaseModel, Field

from .fleet import get_pool
from .partners import require_partner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exceptions", tags=["Exceptions"])


# =============================================================================
# Pydantic Models
# =============================================================================

class CreateExceptionRequest(BaseModel):
    """Request to create a new exception."""
    site_id: str
    scope_type: str = Field(..., pattern="^(runbook|check|control)$")
    item_id: str  # runbook_id, check_id, or control_id
    device_filter: Optional[str] = None  # e.g., "hostname:legacy-*"

    reason: str = Field(..., min_length=10, max_length=1000)
    compensating_control: Optional[str] = None
    risk_accepted_by: str = Field(..., min_length=2)

    duration_days: Optional[int] = Field(None, ge=1, le=365)
    action: str = Field("both", pattern="^(suppress_alert|skip_remediation|both)$")
    approval_notes: Optional[str] = None


class RenewExceptionRequest(BaseModel):
    """Request to renew an exception."""
    duration_days: Optional[int] = Field(None, ge=1, le=365)
    reason: Optional[str] = None


class RevokeExceptionRequest(BaseModel):
    """Request to revoke an exception."""
    reason: str = Field(..., min_length=5)


class ExceptionResponse(BaseModel):
    """Exception response model."""
    id: str
    site_id: str
    scope_type: str
    item_id: str
    device_filter: Optional[str]
    requested_by: str
    approved_by: str
    approval_date: str
    approval_tier: str
    approval_notes: Optional[str]
    start_date: str
    expiration_date: str
    requires_renewal: bool
    reason: str
    compensating_control: Optional[str]
    risk_accepted_by: str
    action: str
    created_at: str
    updated_at: str
    is_active: bool
    is_valid: bool
    days_until_expiration: int


class ExceptionSummaryResponse(BaseModel):
    """Summary of exceptions for a site."""
    total: int
    active: int
    expired: int
    revoked: int
    expiring_soon: int
    by_scope: dict
    by_tier: dict


# =============================================================================
# Helper Functions
# =============================================================================

def get_approval_tier(partner: dict) -> str:
    """Determine approval tier based on partner role."""
    # Partners get partner tier, can upgrade via L3 escalation
    return "partner"


def get_max_duration(tier: str) -> int:
    """Get max duration in days for a tier."""
    return {
        "client_admin": 30,
        "partner": 90,
        "l3_escalation": 365,
        "central_command": 3650,
    }.get(tier, 30)


def generate_exception_id() -> str:
    """Generate a secure, non-enumerable exception ID."""
    return f"EXC-{uuid.uuid4().hex[:12].upper()}"


async def verify_site_ownership(conn, partner: dict, site_id: str) -> bool:
    """
    Verify that a partner owns or has access to a site.

    Security: Prevents IDOR attacks by ensuring partners can only
    access sites they own.
    """
    partner_id = partner.get("id")
    if not partner_id:
        return False

    # Check if site belongs to this partner
    result = await conn.fetchrow("""
        SELECT 1 FROM sites
        WHERE site_id = $1 AND partner_id = $2
    """, site_id, partner_id)

    return result is not None


async def verify_exception_ownership(conn, partner: dict, exception_id: str) -> dict:
    """
    Verify that a partner owns an exception (via site ownership).

    Returns the exception row if owned, raises 403/404 otherwise.
    Security: Prevents IDOR attacks on exception resources.
    """
    partner_id = partner.get("id")
    if not partner_id:
        raise HTTPException(status_code=403, detail="Invalid partner session")

    # Get exception and verify site ownership in one query
    row = await conn.fetchrow("""
        SELECT e.* FROM compliance_exceptions e
        JOIN sites s ON e.site_id = s.site_id
        WHERE e.id = $1 AND s.partner_id = $2
    """, exception_id, partner_id)

    if not row:
        # Check if exception exists at all (for better error messages)
        exists = await conn.fetchrow(
            "SELECT 1 FROM compliance_exceptions WHERE id = $1",
            exception_id
        )
        if exists:
            # Exception exists but partner doesn't own it
            logger.warning(
                f"IDOR attempt: partner {partner_id} tried to access exception {exception_id}"
            )
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=404, detail="Exception not found")

    return row


async def require_site_access(conn, partner: dict, site_id: str):
    """
    Verify site access or raise 403.

    Security: Use this before any operation that requires site access.
    """
    if not await verify_site_ownership(conn, partner, site_id):
        logger.warning(
            f"IDOR attempt: partner {partner.get('id')} tried to access site {site_id}"
        )
        raise HTTPException(status_code=403, detail="Access denied to this site")


# =============================================================================
# Endpoints
# =============================================================================

@router.post("", response_model=ExceptionResponse)
async def create_exception(
    request: CreateExceptionRequest,
    partner: dict = Depends(require_partner),
):
    """
    Create a new compliance exception.

    Permissions:
    - Partner admins can create site-wide exceptions (90 days max)
    - Client admins can create device-specific exceptions (30 days max)

    Security: Verifies partner owns the site before creating exception.
    """
    pool = await get_pool()

    # Determine approval tier
    tier = get_approval_tier(partner)
    max_days = get_max_duration(tier)

    # Enforce duration limits
    duration = request.duration_days or max_days
    if duration > max_days:
        duration = max_days

    now = datetime.now(timezone.utc)
    exception_id = generate_exception_id()  # Secure UUID-based ID

    partner_email = partner.get("email", partner.get("name", "unknown"))

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this site
        await require_site_access(conn, partner, request.site_id)

        # Check if similar exception already exists
        existing = await conn.fetchrow("""
            SELECT id FROM compliance_exceptions
            WHERE site_id = $1 AND scope_type = $2 AND item_id = $3
            AND is_active = true AND expiration_date > NOW()
        """, request.site_id, request.scope_type, request.item_id)

        if existing:
            raise HTTPException(
                status_code=409,
                detail="An active exception already exists for this item"
            )

        # Create the exception
        expiration = now + timedelta(days=duration)

        row = await conn.fetchrow("""
            INSERT INTO compliance_exceptions (
                id, site_id, scope_type, item_id, device_filter,
                requested_by, approved_by, approval_date, approval_tier, approval_notes,
                start_date, expiration_date, requires_renewal,
                reason, compensating_control, risk_accepted_by,
                action, created_at, updated_at, is_active
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13,
                $14, $15, $16,
                $17, $18, $19, true
            ) RETURNING *
        """,
            exception_id, request.site_id, request.scope_type, request.item_id, request.device_filter,
            partner_email, partner_email, now, tier, request.approval_notes,
            now, expiration, True,
            request.reason, request.compensating_control, request.risk_accepted_by,
            request.action, now, now,
        )

        # Log audit entry
        await conn.execute("""
            INSERT INTO exception_audit_log (exception_id, action, performed_by, performed_at, notes)
            VALUES ($1, 'created', $2, $3, $4)
        """, exception_id, partner_email, now, f"Created with {duration} day duration")

    return _row_to_response(row)


@router.get("", response_model=List[ExceptionResponse])
async def list_exceptions(
    site_id: str,
    active_only: bool = Query(True),
    partner: dict = Depends(require_partner),
):
    """
    List all exceptions for a site.

    Security: Verifies partner owns the site before returning exceptions.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this site
        await require_site_access(conn, partner, site_id)

        query = "SELECT * FROM compliance_exceptions WHERE site_id = $1"
        params = [site_id]

        if active_only:
            query += " AND is_active = true"

        query += " ORDER BY created_at DESC"

        rows = await conn.fetch(query, *params)

    return [_row_to_response(row) for row in rows]


@router.get("/summary", response_model=ExceptionSummaryResponse)
async def get_exception_summary(
    site_id: str,
    partner: dict = Depends(require_partner),
):
    """
    Get summary of exceptions for a site.

    Security: Verifies partner owns the site before returning summary.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this site
        await require_site_access(conn, partner, site_id)

        rows = await conn.fetch(
            "SELECT * FROM compliance_exceptions WHERE site_id = $1",
            site_id
        )

    now = datetime.now(timezone.utc)

    active = [r for r in rows if r["is_active"] and r["expiration_date"] > now]
    expired = [r for r in rows if r["is_active"] and r["expiration_date"] <= now]
    revoked = [r for r in rows if not r["is_active"]]

    # Expiring within 14 days
    soon = now + timedelta(days=14)
    expiring_soon = [r for r in active if r["expiration_date"] <= soon]

    return ExceptionSummaryResponse(
        total=len(rows),
        active=len(active),
        expired=len(expired),
        revoked=len(revoked),
        expiring_soon=len(expiring_soon),
        by_scope={
            "runbook": len([r for r in active if r["scope_type"] == "runbook"]),
            "check": len([r for r in active if r["scope_type"] == "check"]),
            "control": len([r for r in active if r["scope_type"] == "control"]),
        },
        by_tier={
            "client_admin": len([r for r in active if r["approval_tier"] == "client_admin"]),
            "partner": len([r for r in active if r["approval_tier"] == "partner"]),
            "l3_escalation": len([r for r in active if r["approval_tier"] == "l3_escalation"]),
        },
    )


@router.get("/expiring", response_model=List[ExceptionResponse])
async def get_expiring_exceptions(
    days: int = Query(14, ge=1, le=90),
    site_id: Optional[str] = None,
    partner: dict = Depends(require_partner),
):
    """
    Get exceptions expiring within the specified days.

    Security: Only returns exceptions for sites owned by the partner.
    """
    pool = await get_pool()
    partner_id = partner.get("id")

    cutoff = datetime.now(timezone.utc) + timedelta(days=days)

    async with pool.acquire() as conn:
        # SECURITY: Only get exceptions for sites this partner owns
        if site_id:
            # Verify partner owns this specific site
            await require_site_access(conn, partner, site_id)
            query = """
                SELECT e.* FROM compliance_exceptions e
                WHERE e.is_active = true AND e.expiration_date <= $1
                AND e.site_id = $2
                ORDER BY e.expiration_date ASC
            """
            rows = await conn.fetch(query, cutoff, site_id)
        else:
            # Get all expiring exceptions for all sites owned by this partner
            query = """
                SELECT e.* FROM compliance_exceptions e
                JOIN sites s ON e.site_id = s.site_id
                WHERE e.is_active = true AND e.expiration_date <= $1
                AND s.partner_id = $2
                ORDER BY e.expiration_date ASC
            """
            rows = await conn.fetch(query, cutoff, partner_id)

    return [_row_to_response(row) for row in rows]


@router.get("/{exception_id}", response_model=ExceptionResponse)
async def get_exception(
    exception_id: str = Path(..., description="Exception ID"),
    partner: dict = Depends(require_partner),
):
    """
    Get a specific exception by ID.

    Security: Verifies partner owns the exception's site.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this exception
        row = await verify_exception_ownership(conn, partner, exception_id)

    return _row_to_response(row)


@router.get("/{exception_id}/audit", response_model=List[dict])
async def get_exception_audit_log(
    exception_id: str = Path(..., description="Exception ID"),
    partner: dict = Depends(require_partner),
):
    """
    Get audit log for an exception.

    Security: Verifies partner owns the exception's site.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this exception
        await verify_exception_ownership(conn, partner, exception_id)

        rows = await conn.fetch("""
            SELECT action, performed_by, performed_at, notes
            FROM exception_audit_log
            WHERE exception_id = $1
            ORDER BY performed_at DESC
        """, exception_id)

    return [dict(row) for row in rows]


@router.post("/{exception_id}/renew", response_model=ExceptionResponse)
async def renew_exception(
    exception_id: str = Path(..., description="Exception ID"),
    request: RenewExceptionRequest = None,
    partner: dict = Depends(require_partner),
):
    """
    Renew an exception for another period.

    Security: Verifies partner owns the exception's site.
    """
    if request is None:
        request = RenewExceptionRequest()

    pool = await get_pool()

    tier = get_approval_tier(partner)
    max_days = get_max_duration(tier)

    duration = request.duration_days or max_days
    if duration > max_days:
        duration = max_days

    now = datetime.now(timezone.utc)
    expiration = now + timedelta(days=duration)

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this exception
        existing = await verify_exception_ownership(conn, partner, exception_id)

        if not existing["is_active"]:
            raise HTTPException(status_code=400, detail="Cannot renew revoked exception")

        # Update exception
        row = await conn.fetchrow("""
            UPDATE compliance_exceptions
            SET start_date = $1, expiration_date = $2, updated_at = $3
            WHERE id = $4
            RETURNING *
        """, now, expiration, now, exception_id)

        # Log audit entry
        await conn.execute("""
            INSERT INTO exception_audit_log (exception_id, action, performed_by, performed_at, notes)
            VALUES ($1, 'renewed', $2, $3, $4)
        """, exception_id, partner.get("email", partner.get("name", "unknown")), now, request.reason or f"Renewed for {duration} days")

    return _row_to_response(row)


@router.post("/{exception_id}/revoke", response_model=ExceptionResponse)
async def revoke_exception(
    exception_id: str = Path(..., description="Exception ID"),
    request: RevokeExceptionRequest = ...,
    partner: dict = Depends(require_partner),
):
    """
    Revoke (deactivate) an exception.

    Security: Verifies partner owns the exception's site.
    """
    pool = await get_pool()

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this exception
        existing = await verify_exception_ownership(conn, partner, exception_id)

        if not existing["is_active"]:
            raise HTTPException(status_code=400, detail="Exception already revoked")

        # Revoke exception
        row = await conn.fetchrow("""
            UPDATE compliance_exceptions
            SET is_active = false, updated_at = $1
            WHERE id = $2
            RETURNING *
        """, now, exception_id)

        # Log audit entry
        await conn.execute("""
            INSERT INTO exception_audit_log (exception_id, action, performed_by, performed_at, notes)
            VALUES ($1, 'revoked', $2, $3, $4)
        """, exception_id, partner.get("email", partner.get("name", "unknown")), now, request.reason)

    return _row_to_response(row)


@router.get("/check/{site_id}/{scope_type}/{item_id}")
async def check_exception_exists(
    site_id: str = Path(..., description="Site ID"),
    scope_type: str = Path(..., description="Scope type (runbook, check, control)"),
    item_id: str = Path(..., description="Item ID"),
    hostname: Optional[str] = None,
    partner: dict = Depends(require_partner),
):
    """
    Check if an active exception exists for a specific check/runbook.

    Used by the agent to determine if alerts should be suppressed
    or remediation should be skipped.

    Security: Verifies partner owns the site.
    """
    pool = await get_pool()

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # SECURITY: Verify partner owns this site
        await require_site_access(conn, partner, site_id)

        # Find matching active exception
        rows = await conn.fetch("""
            SELECT * FROM compliance_exceptions
            WHERE site_id = $1 AND scope_type = $2 AND item_id = $3
            AND is_active = true AND expiration_date > $4
            ORDER BY created_at DESC
        """, site_id, scope_type, item_id, now)

        for row in rows:
            # Check device filter if specified
            device_filter = row.get("device_filter")
            if device_filter and hostname:
                # Simple hostname matching
                if device_filter.startswith("hostname:"):
                    pattern = device_filter.replace("hostname:", "").replace("*", "")
                    if pattern not in hostname:
                        continue

            # Found matching exception
            return {
                "has_exception": True,
                "exception_id": row["id"],
                "action": row["action"],
                "suppress_alert": row["action"] in ("suppress_alert", "both"),
                "skip_remediation": row["action"] in ("skip_remediation", "both"),
                "reason": row["reason"],
                "expires": row["expiration_date"].isoformat(),
            }

    return {
        "has_exception": False,
        "suppress_alert": False,
        "skip_remediation": False,
    }


# =============================================================================
# Database Migration
# =============================================================================

async def create_exceptions_tables(conn):
    """Create exceptions tables if they don't exist."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS compliance_exceptions (
            id TEXT PRIMARY KEY,
            site_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            device_filter TEXT,
            requested_by TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            approval_date TIMESTAMPTZ NOT NULL,
            approval_tier TEXT NOT NULL,
            approval_notes TEXT,
            start_date TIMESTAMPTZ NOT NULL,
            expiration_date TIMESTAMPTZ NOT NULL,
            requires_renewal BOOLEAN DEFAULT true,
            reason TEXT NOT NULL,
            compensating_control TEXT,
            risk_accepted_by TEXT NOT NULL,
            action TEXT DEFAULT 'both',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            is_active BOOLEAN DEFAULT true
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exceptions_site_active
        ON compliance_exceptions(site_id, is_active)
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exceptions_item
        ON compliance_exceptions(scope_type, item_id, is_active)
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS exception_audit_log (
            id SERIAL PRIMARY KEY,
            exception_id TEXT NOT NULL,
            action TEXT NOT NULL,
            performed_by TEXT NOT NULL,
            performed_at TIMESTAMPTZ DEFAULT NOW(),
            notes TEXT
        )
    """)

    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exception_audit_log
        ON exception_audit_log(exception_id)
    """)


def _row_to_response(row) -> ExceptionResponse:
    """Convert database row to response model."""
    now = datetime.now(timezone.utc)
    exp = row["expiration_date"]
    if hasattr(exp, 'tzinfo') and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    is_valid = row["is_active"] and exp > now
    days_left = max(0, (exp - now).days) if is_valid else 0

    return ExceptionResponse(
        id=row["id"],
        site_id=row["site_id"],
        scope_type=row["scope_type"],
        item_id=row["item_id"],
        device_filter=row["device_filter"],
        requested_by=row["requested_by"],
        approved_by=row["approved_by"],
        approval_date=row["approval_date"].isoformat() if hasattr(row["approval_date"], 'isoformat') else row["approval_date"],
        approval_tier=row["approval_tier"],
        approval_notes=row["approval_notes"],
        start_date=row["start_date"].isoformat() if hasattr(row["start_date"], 'isoformat') else row["start_date"],
        expiration_date=exp.isoformat() if hasattr(exp, 'isoformat') else str(exp),
        requires_renewal=row["requires_renewal"],
        reason=row["reason"],
        compensating_control=row["compensating_control"],
        risk_accepted_by=row["risk_accepted_by"],
        action=row["action"],
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], 'isoformat') else row["created_at"],
        updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], 'isoformat') else row["updated_at"],
        is_active=row["is_active"],
        is_valid=is_valid,
        days_until_expiration=days_left,
    )
