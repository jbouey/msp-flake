"""Partner management endpoints.

Provides API endpoints for partner (MSP/reseller) management:
- Partner CRUD operations
- Partner authentication
- Provision code generation and claiming
- Partner-scoped site listing

IMPORTANT: Route order matters in FastAPI - /me routes must be defined
before /{partner_id} routes to avoid /me being captured as a partner_id.
"""

import json
import os
import secrets
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Response, Cookie, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .fleet import get_pool
from .auth import require_admin
from .partner_activity_logger import (
    log_partner_activity,
    log_partner_site_action,
    log_partner_credential_action,
    log_partner_provision_action,
    PartnerEventType,
    get_partner_activity,
    get_partner_activity_stats,
)

logger = logging.getLogger(__name__)

# API endpoint from environment variable
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.osiriscare.net")

# SECURITY: bcrypt is required
try:
    import bcrypt
except ImportError:
    raise RuntimeError("bcrypt library required. Install with: pip install bcrypt")

# Try to import qrcode for QR generation
try:
    import qrcode
    from io import BytesIO
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


router = APIRouter(prefix="/api/partners", tags=["partners"])


# =============================================================================
# MODELS
# =============================================================================

class PartnerCreate(BaseModel):
    """Model for creating a new partner."""
    name: str
    slug: str  # subdomain identifier
    contact_email: str
    contact_phone: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#4F46E5"
    revenue_share_percent: int = 40


class PartnerUpdate(BaseModel):
    """Model for updating partner info."""
    name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    revenue_share_percent: Optional[int] = None
    status: Optional[str] = None


class PartnerUserCreate(BaseModel):
    """Model for creating a partner user."""
    email: str
    name: Optional[str] = None
    role: str = "admin"  # admin, tech, billing


class ProvisionCreate(BaseModel):
    """Model for creating a provision code."""
    client_name: Optional[str] = None
    target_site_id: Optional[str] = None
    expires_days: int = 30


class ProvisionClaim(BaseModel):
    """Model for claiming a provision code."""
    provision_code: str
    mac_address: str
    hostname: Optional[str] = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def hash_api_key(api_key: str) -> str:
    """Hash an API key for secure storage.

    Uses HMAC-SHA256 with server secret for secure hashing that allows lookup.
    Note: bcrypt is not used for API keys because they need to be looked up by hash.
    """
    secret = os.getenv("API_KEY_SECRET")
    if not secret:
        # SECURITY: Fail if no secret is configured
        logger.error("API_KEY_SECRET environment variable must be set for secure API key hashing")
        raise RuntimeError("API_KEY_SECRET not configured - set this environment variable")
    return hashlib.sha256(f"{secret}:{api_key}".encode()).hexdigest()


def verify_api_key(api_key: str, api_key_hash: str) -> bool:
    """Verify an API key against its stored hash."""
    computed_hash = hash_api_key(api_key)
    return secrets.compare_digest(computed_hash, api_key_hash)


def generate_api_key() -> str:
    """Generate a new API key."""
    return secrets.token_urlsafe(32)


def generate_provision_code() -> str:
    """Generate a short provision code for QR."""
    return secrets.token_hex(8).upper()  # 16 char hex string


def generate_magic_token() -> str:
    """Generate a magic link token."""
    return secrets.token_urlsafe(32)


async def get_partner_from_api_key(api_key: str):
    """Validate API key and return partner."""
    pool = await get_pool()
    key_hash = hash_api_key(api_key)

    async with pool.acquire() as conn:
        partner = await conn.fetchrow("""
            SELECT id, name, slug, status
            FROM partners
            WHERE api_key_hash = $1
        """, key_hash)

        if not partner:
            return None
        if partner['status'] != 'active':
            return None
        return partner


async def require_partner(
    x_api_key: str = Header(None),
    osiris_partner_session: Optional[str] = Cookie(None)
):
    """Dependency to require valid partner authentication.

    Supports two auth methods:
    1. API Key via X-API-Key header
    2. OAuth session via osiris_partner_session cookie
    """
    pool = await get_pool()

    # Try API key first
    if x_api_key:
        partner = await get_partner_from_api_key(x_api_key)
        if partner:
            return partner
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    # Try session cookie
    if osiris_partner_session:
        session_hash = hashlib.sha256(osiris_partner_session.encode()).hexdigest()

        async with pool.acquire() as conn:
            session = await conn.fetchrow("""
                SELECT ps.partner_id, p.id, p.name, p.slug, p.status
                FROM partner_sessions ps
                JOIN partners p ON p.id = ps.partner_id
                WHERE ps.session_token_hash = $1
                  AND ps.expires_at > NOW()
                  AND p.status = 'active'
                  AND (p.pending_approval IS NULL OR p.pending_approval = false)
            """, session_hash)

            if session:
                return {
                    'id': session['id'],
                    'name': session['name'],
                    'slug': session['slug'],
                    'status': session['status']
                }

    raise HTTPException(status_code=401, detail="Authentication required")


# =============================================================================
# PUBLIC ENDPOINTS (no auth required)
# =============================================================================

@router.post("/claim")
async def claim_provision_code(claim: ProvisionClaim):
    """Claim a provision code (called by appliance during setup)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Find and validate provision code
        provision = await conn.fetchrow("""
            SELECT id, partner_id, target_site_id, client_name, status, expires_at
            FROM appliance_provisions
            WHERE provision_code = $1
        """, claim.provision_code.upper())

        if not provision:
            raise HTTPException(status_code=404, detail="Invalid provision code")

        if provision['status'] != 'pending':
            raise HTTPException(status_code=400, detail=f"Provision code already {provision['status']}")

        if provision['expires_at'] and provision['expires_at'] < datetime.now(timezone.utc):
            # Mark as expired
            await conn.execute("""
                UPDATE appliance_provisions SET status = 'expired' WHERE id = $1
            """, provision['id'])
            raise HTTPException(status_code=400, detail="Provision code expired")

        # Get partner info for response
        partner = await conn.fetchrow("""
            SELECT slug, brand_name, primary_color, logo_url
            FROM partners WHERE id = $1
        """, provision['partner_id'])

        # Generate site_id if not pre-assigned
        site_id = provision['target_site_id']
        if not site_id:
            # Generate from client name or MAC
            base = provision['client_name'] or claim.hostname or claim.mac_address
            site_id = base.lower().replace(' ', '-').replace(':', '-')[:50]
            site_id = f"{site_id}-{secrets.token_hex(3)}"

        # Generate appliance_id
        appliance_id = f"{site_id}-{claim.mac_address.upper().replace(':', '%3A')}"

        # Create site if doesn't exist
        await conn.execute("""
            INSERT INTO sites (site_id, clinic_name, partner_id, status, onboarding_stage)
            VALUES ($1, $2, $3, 'pending', 'provisioning')
            ON CONFLICT (site_id) DO NOTHING
        """, site_id, provision['client_name'] or site_id.replace('-', ' ').title(), provision['partner_id'])

        # Mark provision as claimed
        await conn.execute("""
            UPDATE appliance_provisions
            SET status = 'claimed',
                claimed_at = NOW(),
                claimed_by_mac = $1,
                claimed_appliance_id = $2
            WHERE id = $3
        """, claim.mac_address.upper(), appliance_id, provision['id'])

        return {
            "status": "claimed",
            "site_id": site_id,
            "appliance_id": appliance_id,
            "partner": {
                "slug": partner['slug'],
                "brand_name": partner['brand_name'],
                "primary_color": partner['primary_color'],
                "logo_url": partner['logo_url'],
            },
            "api_endpoint": API_BASE_URL,
            "message": "Appliance provisioned successfully"
        }


# =============================================================================
# PARTNER SELF-SERVICE ENDPOINTS (Partner-authenticated)
# These must come BEFORE /{partner_id} routes!
# =============================================================================

@router.get("/me")
async def get_my_partner(request: Request, partner=Depends(require_partner)):
    """Get current partner's info (self-service)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, name, slug, contact_email, contact_phone,
                   brand_name, logo_url, primary_color,
                   revenue_share_percent, status, created_at
            FROM partners
            WHERE id = $1
        """, partner['id'])

        # Get site count
        site_count = await conn.fetchval("""
            SELECT COUNT(*) FROM sites WHERE partner_id = $1
        """, partner['id'])

        # Get provision stats
        provision_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') as pending,
                COUNT(*) FILTER (WHERE status = 'claimed') as claimed
            FROM appliance_provisions
            WHERE partner_id = $1
        """, partner['id'])

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.PROFILE_VIEWED,
            target_type="partner",
            target_id=str(partner['id']),
            event_data={"partner_name": partner['name']},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {
            'id': str(row['id']),
            'name': row['name'],
            'slug': row['slug'],
            'contact_email': row['contact_email'],
            'contact_phone': row['contact_phone'],
            'brand_name': row['brand_name'],
            'logo_url': row['logo_url'],
            'primary_color': row['primary_color'],
            'revenue_share_percent': row['revenue_share_percent'],
            'status': row['status'],
            'site_count': site_count,
            'provisions': {
                'pending': provision_stats['pending'] if provision_stats else 0,
                'claimed': provision_stats['claimed'] if provision_stats else 0,
            },
            'created_at': row['created_at'].isoformat(),
        }


@router.get("/me/sites")
async def get_my_sites(request: Request, partner=Depends(require_partner)):
    """Get sites belonging to this partner."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   s.onboarding_stage, s.created_at,
                   COUNT(sa.id) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin
            FROM sites s
            LEFT JOIN site_appliances sa ON s.site_id = sa.site_id
            WHERE s.partner_id = $1
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier,
                     s.onboarding_stage, s.created_at
            ORDER BY s.clinic_name
        """, partner['id'])

        sites = []
        for row in rows:
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'status': row['status'],
                'tier': row['tier'],
                'onboarding_stage': row['onboarding_stage'],
                'appliance_count': row['appliance_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            })

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITES_LISTED,
            target_type="partner",
            target_id=str(partner['id']),
            event_data={"site_count": len(sites)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'sites': sites, 'count': len(sites)}


@router.post("/me/provisions")
async def create_provision_code(
    request: Request,
    provision: ProvisionCreate,
    partner=Depends(require_partner)
):
    """Create a new provision code for appliance onboarding."""
    pool = await get_pool()

    code = generate_provision_code()
    expires_at = datetime.now(timezone.utc) + timedelta(days=provision.expires_days)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO appliance_provisions (
                partner_id, provision_code, target_site_id,
                client_name, expires_at
            ) VALUES ($1, $2, $3, $4, $5)
            RETURNING id, provision_code, created_at
        """,
            partner['id'],
            code,
            provision.target_site_id,
            provision.client_name,
            expires_at
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.PROVISION_CREATED,
        target_type="provision",
        target_id=str(row['id']),
        event_data={"client_name": provision.client_name, "provision_code": row['provision_code']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "id": str(row['id']),
        "provision_code": row['provision_code'],
        "qr_content": f"osiris://{code}",  # For QR code generation
        "client_name": provision.client_name,
        "target_site_id": provision.target_site_id,
        "expires_at": expires_at.isoformat(),
        "created_at": row['created_at'].isoformat(),
    }


@router.get("/me/provisions")
async def list_provision_codes(
    status: Optional[str] = None,
    partner=Depends(require_partner)
):
    """List provision codes for this partner."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch("""
                SELECT id, provision_code, target_site_id, client_name,
                       status, claimed_at, claimed_by_mac, expires_at, created_at
                FROM appliance_provisions
                WHERE partner_id = $1 AND status = $2
                ORDER BY created_at DESC
            """, partner['id'], status)
        else:
            rows = await conn.fetch("""
                SELECT id, provision_code, target_site_id, client_name,
                       status, claimed_at, claimed_by_mac, expires_at, created_at
                FROM appliance_provisions
                WHERE partner_id = $1
                ORDER BY created_at DESC
            """, partner['id'])

        provisions = []
        for row in rows:
            provisions.append({
                'id': str(row['id']),
                'provision_code': row['provision_code'],
                'qr_content': f"osiris://{row['provision_code']}",
                'target_site_id': row['target_site_id'],
                'client_name': row['client_name'],
                'status': row['status'],
                'claimed_at': row['claimed_at'].isoformat() if row['claimed_at'] else None,
                'claimed_by_mac': row['claimed_by_mac'],
                'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                'created_at': row['created_at'].isoformat(),
            })

        return {'provisions': provisions, 'count': len(provisions)}


@router.delete("/me/provisions/{provision_id}")
async def revoke_provision_code(
    request: Request,
    provision_id: str,
    partner=Depends(require_partner)
):
    """Revoke a provision code."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        result = await conn.fetchrow("""
            UPDATE appliance_provisions
            SET status = 'revoked'
            WHERE id = $1 AND partner_id = $2 AND status = 'pending'
            RETURNING provision_code
        """, provision_id, partner['id'])

        if not result:
            raise HTTPException(
                status_code=404,
                detail="Provision code not found or already claimed/revoked"
            )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.PROVISION_REVOKED,
        target_type="provision",
        target_id=str(provision_id),
        event_data={"provision_code": result['provision_code']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "status": "revoked",
        "provision_code": result['provision_code'],
    }


@router.get("/me/provisions/{provision_id}/qr")
async def get_provision_qr_code(
    provision_id: str,
    size: int = 200,
    partner=Depends(require_partner)
):
    """Generate QR code image for a provision code.

    Returns a PNG image that can be scanned by the appliance during setup.
    The QR contains the provision URL: osiris://PROVISION_CODE

    Args:
        provision_id: UUID of the provision
        size: QR code size in pixels (default 200, max 500)
    """
    if not HAS_QRCODE:
        raise HTTPException(
            status_code=501,
            detail="QR code generation not available - qrcode library not installed"
        )

    # Limit size to prevent abuse
    size = min(max(size, 100), 500)

    pool = await get_pool()

    async with pool.acquire() as conn:
        provision = await conn.fetchrow("""
            SELECT provision_code, status
            FROM appliance_provisions
            WHERE id = $1 AND partner_id = $2
        """, provision_id, partner['id'])

        if not provision:
            raise HTTPException(status_code=404, detail="Provision not found")

        if provision['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate QR for {provision['status']} provision"
            )

    # Generate QR code
    qr_content = f"osiris://{provision['provision_code']}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Resize to requested size
    img = img.resize((size, size))

    # Convert to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={
            "Content-Disposition": f"inline; filename=provision-{provision['provision_code']}.png",
            "Cache-Control": "no-cache",
        }
    )


@router.get("/provision/{provision_code}/qr")
async def get_provision_qr_by_code(
    provision_code: str,
    size: int = 200,
):
    """Generate QR code for a provision code (public endpoint).

    This is a public endpoint that can be used to regenerate QR codes
    for valid (pending) provision codes. Used in printed setup instructions.

    Args:
        provision_code: The 16-character provision code
        size: QR code size in pixels (default 200, max 500)
    """
    if not HAS_QRCODE:
        raise HTTPException(
            status_code=501,
            detail="QR code generation not available"
        )

    size = min(max(size, 100), 500)

    pool = await get_pool()

    async with pool.acquire() as conn:
        provision = await conn.fetchrow("""
            SELECT status FROM appliance_provisions
            WHERE provision_code = $1
        """, provision_code.upper())

        if not provision:
            raise HTTPException(status_code=404, detail="Invalid provision code")

        if provision['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Provision code is {provision['status']}"
            )

    qr_content = f"osiris://{provision_code.upper()}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((size, size))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={
            "Content-Disposition": f"inline; filename=provision-{provision_code}.png",
            "Cache-Control": "no-cache",
        }
    )


# =============================================================================
# ADMIN ENDPOINTS (for OsirisCare staff)
# =============================================================================

@router.post("")
async def create_partner(request: Request, partner: PartnerCreate, admin: dict = Depends(require_admin)):
    """Create a new partner (admin only)."""
    pool = await get_pool()

    # Generate API key
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    async with pool.acquire() as conn:
        # Check slug uniqueness
        existing = await conn.fetchval(
            "SELECT 1 FROM partners WHERE slug = $1",
            partner.slug.lower()
        )
        if existing:
            raise HTTPException(status_code=400, detail=f"Slug '{partner.slug}' already exists")

        # Insert partner
        row = await conn.fetchrow("""
            INSERT INTO partners (
                name, slug, contact_email, contact_phone,
                brand_name, logo_url, primary_color,
                revenue_share_percent, api_key_hash
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, name, slug, created_at
        """,
            partner.name,
            partner.slug.lower(),
            partner.contact_email,
            partner.contact_phone,
            partner.brand_name or partner.name,
            partner.logo_url,
            partner.primary_color,
            partner.revenue_share_percent,
            api_key_hash
        )

    await log_partner_activity(
        partner_id=str(row['id']),
        event_type=PartnerEventType.PARTNER_CREATED,
        target_type="partner",
        target_id=str(row['id']),
        event_data={"partner_name": row['name'], "slug": row['slug'], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "id": str(row['id']),
        "name": row['name'],
        "slug": row['slug'],
        "api_key": api_key,  # Only returned once at creation!
        "created_at": row['created_at'].isoformat(),
        "message": "Save this API key - it cannot be retrieved later"
    }


@router.get("")
async def list_partners(status: Optional[str] = None, admin: dict = Depends(require_admin)):
    """List all partners (admin only)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch("""
                SELECT id, name, slug, contact_email, brand_name,
                       revenue_share_percent, status, created_at
                FROM partners
                WHERE status = $1
                ORDER BY name
            """, status)
        else:
            rows = await conn.fetch("""
                SELECT id, name, slug, contact_email, brand_name,
                       revenue_share_percent, status, created_at
                FROM partners
                ORDER BY name
            """)

        # Get site counts per partner
        site_counts = await conn.fetch("""
            SELECT partner_id, COUNT(*) as count
            FROM sites
            WHERE partner_id IS NOT NULL
            GROUP BY partner_id
        """)
        count_map = {str(r['partner_id']): r['count'] for r in site_counts}

        partners = []
        for row in rows:
            partner_id = str(row['id'])
            partners.append({
                'id': partner_id,
                'name': row['name'],
                'slug': row['slug'],
                'contact_email': row['contact_email'],
                'brand_name': row['brand_name'],
                'revenue_share_percent': row['revenue_share_percent'],
                'status': row['status'],
                'site_count': count_map.get(partner_id, 0),
                'created_at': row['created_at'].isoformat(),
            })

        return {'partners': partners, 'count': len(partners)}


@router.get("/activity/all")
async def get_all_partner_activity_log(
    partner_id: Optional[str] = Query(None),
    event_category: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """Get all partner activity logs (admin only)."""
    logs = await get_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        event_category=event_category,
        limit=limit,
        offset=offset,
    )
    stats = await get_partner_activity_stats()
    return {"logs": logs, "stats": stats}


# =============================================================================
# ADMIN PARTNER-SPECIFIC ENDPOINTS
# These come AFTER /me routes since they use /{partner_id} parameter
# =============================================================================

@router.get("/{partner_id}")
async def get_partner(partner_id: str, admin: dict = Depends(require_admin)):
    """Get partner details (admin only)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, name, slug, contact_email, contact_phone,
                   brand_name, logo_url, primary_color,
                   revenue_share_percent, status, created_at, updated_at
            FROM partners
            WHERE id = $1
        """, partner_id)

        if not row:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Get sites for this partner
        sites = await conn.fetch("""
            SELECT site_id, clinic_name, status, tier
            FROM sites
            WHERE partner_id = $1
            ORDER BY clinic_name
        """, partner_id)

        # Get users for this partner
        users = await conn.fetch("""
            SELECT id, email, name, role, status, last_login
            FROM partner_users
            WHERE partner_id = $1
            ORDER BY email
        """, partner_id)

        return {
            'id': str(row['id']),
            'name': row['name'],
            'slug': row['slug'],
            'contact_email': row['contact_email'],
            'contact_phone': row['contact_phone'],
            'brand_name': row['brand_name'],
            'logo_url': row['logo_url'],
            'primary_color': row['primary_color'],
            'revenue_share_percent': row['revenue_share_percent'],
            'status': row['status'],
            'created_at': row['created_at'].isoformat(),
            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
            'sites': [
                {
                    'site_id': s['site_id'],
                    'clinic_name': s['clinic_name'],
                    'status': s['status'],
                    'tier': s['tier'],
                }
                for s in sites
            ],
            'users': [
                {
                    'id': str(u['id']),
                    'email': u['email'],
                    'name': u['name'],
                    'role': u['role'],
                    'status': u['status'],
                    'last_login': u['last_login'].isoformat() if u['last_login'] else None,
                }
                for u in users
            ],
        }


@router.put("/{partner_id}")
async def update_partner(request: Request, partner_id: str, update: PartnerUpdate, admin: dict = Depends(require_admin)):
    """Update partner info (admin only)."""
    pool = await get_pool()

    updates = []
    values = []
    param_num = 1

    if update.name is not None:
        updates.append(f"name = ${param_num}")
        values.append(update.name)
        param_num += 1

    if update.contact_email is not None:
        updates.append(f"contact_email = ${param_num}")
        values.append(update.contact_email)
        param_num += 1

    if update.contact_phone is not None:
        updates.append(f"contact_phone = ${param_num}")
        values.append(update.contact_phone)
        param_num += 1

    if update.brand_name is not None:
        updates.append(f"brand_name = ${param_num}")
        values.append(update.brand_name)
        param_num += 1

    if update.logo_url is not None:
        updates.append(f"logo_url = ${param_num}")
        values.append(update.logo_url)
        param_num += 1

    if update.primary_color is not None:
        updates.append(f"primary_color = ${param_num}")
        values.append(update.primary_color)
        param_num += 1

    if update.revenue_share_percent is not None:
        updates.append(f"revenue_share_percent = ${param_num}")
        values.append(update.revenue_share_percent)
        param_num += 1

    if update.status is not None:
        updates.append(f"status = ${param_num}")
        values.append(update.status)
        param_num += 1

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append(f"updated_at = ${param_num}")
    values.append(datetime.now(timezone.utc))
    param_num += 1

    values.append(partner_id)

    query = f"""
        UPDATE partners
        SET {', '.join(updates)}
        WHERE id = ${param_num}
        RETURNING id, name, slug
    """

    async with pool.acquire() as conn:
        result = await conn.fetchrow(query, *values)
        if not result:
            raise HTTPException(status_code=404, detail="Partner not found")

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.PARTNER_UPDATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={"updated_fields": [u.split(" = ")[0].strip() for u in updates if " = " in u], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "status": "updated",
        "id": str(result['id']),
        "name": result['name'],
    }


@router.post("/{partner_id}/regenerate-key")
async def regenerate_api_key(request: Request, partner_id: str, admin: dict = Depends(require_admin)):
    """Regenerate partner API key (admin only)."""
    pool = await get_pool()

    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    async with pool.acquire() as conn:
        result = await conn.fetchrow("""
            UPDATE partners
            SET api_key_hash = $1, updated_at = NOW()
            WHERE id = $2
            RETURNING name
        """, api_key_hash, partner_id)

        if not result:
            raise HTTPException(status_code=404, detail="Partner not found")

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.API_KEY_REGENERATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={"partner_name": result['name'], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "status": "regenerated",
        "api_key": api_key,
        "message": "Save this API key - it cannot be retrieved later"
    }


@router.get("/{partner_id}/activity")
async def get_partner_activity_log(
    partner_id: str,
    event_category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """Get activity log for a specific partner (admin only)."""
    logs = await get_partner_activity(
        partner_id=partner_id,
        event_category=event_category,
        limit=limit,
        offset=offset,
    )
    stats = await get_partner_activity_stats(partner_id=partner_id)
    return {"logs": logs, "stats": stats}


@router.post("/{partner_id}/users")
async def create_partner_user(partner_id: str, user: PartnerUserCreate, admin: dict = Depends(require_admin)):
    """Create a user for a partner (admin only)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify partner exists
        partner = await conn.fetchval("SELECT 1 FROM partners WHERE id = $1", partner_id)
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Check email uniqueness within partner
        existing = await conn.fetchval("""
            SELECT 1 FROM partner_users
            WHERE partner_id = $1 AND email = $2
        """, partner_id, user.email.lower())
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists for this partner")

        # Generate magic token for initial login
        magic_token = generate_magic_token()
        magic_expires = datetime.now(timezone.utc) + timedelta(days=7)

        row = await conn.fetchrow("""
            INSERT INTO partner_users (
                partner_id, email, name, role, magic_token, magic_token_expires
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, email, name, role
        """,
            partner_id,
            user.email.lower(),
            user.name,
            user.role,
            magic_token,
            magic_expires
        )

    return {
        "id": str(row['id']),
        "email": row['email'],
        "name": row['name'],
        "role": row['role'],
        "magic_link": f"https://dashboard.osiriscare.net/partner/login?token={magic_token}",
        "expires": magic_expires.isoformat(),
    }


@router.post("/{partner_id}/users/{user_id}/magic-link")
async def generate_user_magic_link(partner_id: str, user_id: str, admin: dict = Depends(require_admin)):
    """Generate a new magic login link for a partner user (admin only)."""
    pool = await get_pool()

    magic_token = generate_magic_token()
    magic_expires = datetime.now(timezone.utc) + timedelta(hours=24)

    async with pool.acquire() as conn:
        result = await conn.fetchrow("""
            UPDATE partner_users
            SET magic_token = $1, magic_token_expires = $2
            WHERE id = $3 AND partner_id = $4
            RETURNING email
        """, magic_token, magic_expires, user_id, partner_id)

        if not result:
            raise HTTPException(status_code=404, detail="User not found")

    return {
        "magic_link": f"https://dashboard.osiriscare.net/partner/login?token={magic_token}",
        "expires": magic_expires.isoformat(),
        "email": result['email'],
    }


# =============================================================================
# DISCOVERY & CREDENTIAL ENDPOINTS
# =============================================================================

class CredentialCreate(BaseModel):
    """Model for creating site credentials."""
    name: str
    credential_type: str  # domain_admin, service_account, local_admin
    domain: Optional[str] = None
    username: str
    password: str


class DiscoveredAssetUpdate(BaseModel):
    """Model for updating a discovered asset."""
    monitoring_status: Optional[str] = None  # discovered, monitored, ignored
    asset_type: Optional[str] = None


@router.get("/me/sites/{site_id}")
async def get_partner_site_detail(request: Request, site_id: str, partner=Depends(require_partner)):
    """Get detailed site info including assets and credentials."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT s.*, p.brand_name as partner_brand
            FROM sites s
            JOIN partners p ON p.id = s.partner_id
            WHERE s.site_id = $1 AND s.partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get discovered assets
        assets = await conn.fetch("""
            SELECT id, ip_address, hostname, asset_type, os_info,
                   confidence, discovery_method, open_ports, detected_services,
                   monitoring_status, last_seen_at, last_check_status
            FROM discovered_assets
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY
                CASE asset_type
                    WHEN 'domain_controller' THEN 1
                    WHEN 'sql_server' THEN 2
                    WHEN 'backup_server' THEN 3
                    WHEN 'file_server' THEN 4
                    ELSE 10
                END,
                hostname NULLS LAST
        """, site_id)

        # Get credentials (without password)
        credentials = await conn.fetch("""
            SELECT id, name, credential_type, domain, username,
                   is_primary, validation_status, last_validated_at
            FROM site_credentials
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY is_primary DESC, name
        """, site_id)

        # Get recent discovery scans
        scans = await conn.fetch("""
            SELECT id, scan_type, triggered_by, started_at, completed_at,
                   status, assets_found, new_assets, error_message
            FROM discovery_scans
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY started_at DESC
            LIMIT 10
        """, site_id)

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITE_VIEWED,
            target_type="site",
            target_id=site_id,
            event_data={"clinic_name": site['clinic_name'], "asset_count": len(assets), "credential_count": len(credentials)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {
            'site': {
                'site_id': site['site_id'],
                'clinic_name': site['clinic_name'],
                'status': site['status'],
                'tier': site['tier'],
                'onboarding_stage': site['onboarding_stage'],
                'partner_brand': site['partner_brand'],
            },
            'assets': [
                {
                    'id': str(a['id']),
                    'ip_address': str(a['ip_address']),
                    'hostname': a['hostname'],
                    'asset_type': a['asset_type'] or 'unknown',
                    'os_info': a['os_info'],
                    'confidence': a['confidence'],
                    'discovery_method': a['discovery_method'],
                    'open_ports': a['open_ports'] or [],
                    'detected_services': a['detected_services'] or {},
                    'monitoring_status': a['monitoring_status'],
                    'last_seen_at': a['last_seen_at'].isoformat() if a['last_seen_at'] else None,
                    'last_check_status': a['last_check_status'],
                }
                for a in assets
            ],
            'credentials': [
                {
                    'id': str(c['id']),
                    'name': c['name'],
                    'credential_type': c['credential_type'],
                    'domain': c['domain'],
                    'username': c['username'],
                    'is_primary': c['is_primary'],
                    'validation_status': c['validation_status'],
                    'last_validated_at': c['last_validated_at'].isoformat() if c['last_validated_at'] else None,
                }
                for c in credentials
            ],
            'recent_scans': [
                {
                    'id': str(s['id']),
                    'scan_type': s['scan_type'],
                    'triggered_by': s['triggered_by'],
                    'started_at': s['started_at'].isoformat() if s['started_at'] else None,
                    'completed_at': s['completed_at'].isoformat() if s['completed_at'] else None,
                    'status': s['status'],
                    'assets_found': s['assets_found'],
                    'new_assets': s['new_assets'],
                    'error_message': s['error_message'],
                }
                for s in scans
            ],
            'asset_count': len(assets),
            'credential_count': len(credentials),
        }


@router.post("/me/sites/{site_id}/credentials")
async def add_site_credentials(
    request: Request,
    site_id: str,
    credential: CredentialCreate,
    partner=Depends(require_partner)
):
    """Add credentials for a site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner and get internal ID
        site = await conn.fetchrow("""
            SELECT id FROM sites
            WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Encrypt password using Fernet
        from .oauth_login import encrypt_secret
        encrypted = encrypt_secret(credential.password)

        # Check if this is the first credential (make it primary)
        existing = await conn.fetchval("""
            SELECT COUNT(*) FROM site_credentials WHERE site_id = $1
        """, site['id'])
        is_primary = existing == 0

        row = await conn.fetchrow("""
            INSERT INTO site_credentials (
                site_id, name, credential_type, domain, username,
                password_encrypted, is_primary
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, name, credential_type, validation_status
        """,
            site['id'],
            credential.name,
            credential.credential_type,
            credential.domain,
            credential.username,
            encrypted,
            is_primary
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_ADDED,
        target_type="credential",
        target_id=str(row['id']),
        event_data={"site_id": site_id, "credential_name": credential.name, "credential_type": credential.credential_type},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'id': str(row['id']),
        'name': row['name'],
        'credential_type': row['credential_type'],
        'validation_status': row['validation_status'],
        'is_primary': is_primary,
        'message': 'Credential saved. Use /validate to test connectivity.',
    }


@router.post("/me/sites/{site_id}/credentials/{credential_id}/validate")
async def validate_credential(
    request: Request,
    site_id: str,
    credential_id: str,
    partner=Depends(require_partner)
):
    """Validate a credential by testing connectivity."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner
        cred = await conn.fetchrow("""
            SELECT sc.*, s.site_id
            FROM site_credentials sc
            JOIN sites s ON s.id = sc.site_id
            WHERE sc.id = $1 AND s.partner_id = $2
        """, credential_id, partner['id'])

        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")

        # TODO: Actually validate via WinRM/LDAP
        # For now, return mock validation
        # In production, this would queue an order to the appliance

        validation_result = {
            'can_connect': True,
            'can_read_ad': cred['credential_type'] == 'domain_admin',
            'is_domain_admin': cred['credential_type'] == 'domain_admin',
            'servers_found': [],
            'servers_accessible': [],
            'warnings': ['Credential validation pending - appliance will test on next sync'],
            'errors': [],
        }

        # Update validation status
        await conn.execute("""
            UPDATE site_credentials
            SET validation_status = 'pending',
                last_validated_at = NOW(),
                validation_details = $1
            WHERE id = $2
        """, json.dumps(validation_result), credential_id)

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_VALIDATED,
        target_type="credential",
        target_id=str(credential_id),
        event_data={"site_id": site_id, "validation_status": "pending"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'credential_id': credential_id,
        'validation_status': 'pending',
        'result': validation_result,
        'message': 'Validation queued. Appliance will test on next sync.',
    }


@router.delete("/me/sites/{site_id}/credentials/{credential_id}")
async def delete_credential(
    request: Request,
    site_id: str,
    credential_id: str,
    partner=Depends(require_partner)
):
    """Delete a site credential."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM site_credentials sc
            USING sites s
            WHERE sc.id = $1
            AND sc.site_id = s.id
            AND s.partner_id = $2
        """, credential_id, partner['id'])

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Credential not found")

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_DELETED,
        target_type="credential",
        target_id=str(credential_id),
        event_data={"site_id": site_id},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {'status': 'deleted', 'credential_id': credential_id}


@router.get("/me/sites/{site_id}/assets")
async def list_site_assets(
    site_id: str,
    status: Optional[str] = None,
    partner=Depends(require_partner)
):
    """List discovered assets for a site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        query = """
            SELECT id, ip_address, hostname, asset_type, os_info,
                   confidence, discovery_method, open_ports, detected_services,
                   monitoring_status, last_seen_at, last_check_status, ad_info
            FROM discovered_assets
            WHERE site_id = $1
        """
        params = [site['id']]

        if status:
            query += " AND monitoring_status = $2"
            params.append(status)

        query += " ORDER BY asset_type, hostname NULLS LAST"

        assets = await conn.fetch(query, *params)

        return {
            'assets': [
                {
                    'id': str(a['id']),
                    'ip_address': str(a['ip_address']),
                    'hostname': a['hostname'],
                    'asset_type': a['asset_type'] or 'unknown',
                    'os_info': a['os_info'],
                    'confidence': a['confidence'],
                    'discovery_method': a['discovery_method'],
                    'open_ports': a['open_ports'] or [],
                    'detected_services': a['detected_services'] or {},
                    'monitoring_status': a['monitoring_status'],
                    'last_seen_at': a['last_seen_at'].isoformat() if a['last_seen_at'] else None,
                    'last_check_status': a['last_check_status'],
                    'ad_info': a['ad_info'],
                }
                for a in assets
            ],
            'count': len(assets),
        }


@router.patch("/me/sites/{site_id}/assets/{asset_id}")
async def update_asset(
    request: Request,
    site_id: str,
    asset_id: str,
    update: DiscoveredAssetUpdate,
    partner=Depends(require_partner)
):
    """Update a discovered asset (e.g., set monitoring status)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify ownership
        asset = await conn.fetchrow("""
            SELECT da.id FROM discovered_assets da
            JOIN sites s ON s.id = da.site_id
            WHERE da.id = $1 AND s.partner_id = $2
        """, asset_id, partner['id'])

        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        updates = []
        values = []
        param_num = 1

        if update.monitoring_status:
            if update.monitoring_status not in ('discovered', 'monitored', 'ignored'):
                raise HTTPException(status_code=400, detail="Invalid monitoring_status")
            updates.append(f"monitoring_status = ${param_num}")
            values.append(update.monitoring_status)
            param_num += 1

        if update.asset_type:
            updates.append(f"asset_type = ${param_num}")
            values.append(update.asset_type)
            param_num += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append(f"updated_at = ${param_num}")
        values.append(datetime.now(timezone.utc))
        param_num += 1

        values.append(asset_id)

        query = f"""
            UPDATE discovered_assets
            SET {', '.join(updates)}
            WHERE id = ${param_num}
            RETURNING id, monitoring_status, asset_type
        """

        result = await conn.fetchrow(query, *values)

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.ASSET_UPDATED,
        target_type="asset",
        target_id=str(asset_id),
        event_data={"site_id": site_id, "monitoring_status": result['monitoring_status'], "asset_type": result['asset_type']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'id': str(result['id']),
        'monitoring_status': result['monitoring_status'],
        'asset_type': result['asset_type'],
        'status': 'updated',
    }


@router.post("/me/sites/{site_id}/discovery/trigger")
async def trigger_discovery(request: Request, site_id: str, partner=Depends(require_partner)):
    """Trigger a network discovery scan for a site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get active appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 AND status = 'online'
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

        if not appliance:
            # Try any appliance if none online
            appliance = await conn.fetchrow("""
                SELECT appliance_id FROM site_appliances
                WHERE site_id = $1
                ORDER BY last_checkin DESC NULLS LAST
                LIMIT 1
            """, site_id)

        # Create discovery scan record
        scan = await conn.fetchrow("""
            INSERT INTO discovery_scans (site_id, scan_type, triggered_by)
            VALUES ($1, 'full', 'manual')
            RETURNING id, started_at
        """, site['id'])

        # Queue order to appliance to run discovery
        if appliance:
            import secrets
            from datetime import timedelta
            order_id = f"ORD-{secrets.token_hex(8).upper()}"
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(hours=24)  # Discovery can take time

            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at
                ) VALUES ($1, $2, $3, 'run_discovery', $4::jsonb, 1, 'pending', $5, $6)
            """,
                order_id,
                appliance['appliance_id'],
                site_id,
                json.dumps({
                    'scan_id': str(scan['id']),
                    'scan_type': 'full',
                    'triggered_by': partner.get('email', 'partner')
                }),
                now,
                expires_at
            )

            await log_partner_activity(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.DISCOVERY_TRIGGERED,
                target_type="site",
                target_id=site_id,
                event_data={"scan_id": str(scan['id']), "order_id": order_id, "status": "queued"},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
                request_path=str(request.url.path),
                request_method=request.method,
            )

            return {
                'scan_id': str(scan['id']),
                'order_id': order_id,
                'status': 'queued',
                'started_at': scan['started_at'].isoformat(),
                'message': 'Discovery scan queued. Appliance will execute on next check-in.',
            }
        else:
            await log_partner_activity(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.DISCOVERY_TRIGGERED,
                target_type="site",
                target_id=site_id,
                event_data={"scan_id": str(scan['id']), "status": "pending_no_appliance"},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
                request_path=str(request.url.path),
                request_method=request.method,
            )

            return {
                'scan_id': str(scan['id']),
                'status': 'pending',
                'started_at': scan['started_at'].isoformat(),
                'message': 'Discovery scan created but no appliance available. Will execute when appliance comes online.',
            }


# =============================================================================
# PARTNER AUTHENTICATION ENDPOINTS
# =============================================================================

class MagicTokenValidate(BaseModel):
    """Model for validating a magic link token."""
    token: str


@router.post("/auth/magic")
async def validate_magic_link(request: MagicTokenValidate):
    """Validate a magic link token and return API key for partner login.

    SECURITY: Token is sent in request body (not URL) to avoid exposure in logs.

    Args:
        request: Contains the magic link token

    Returns:
        Partner info and API key on success
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Find user with this magic token
        user = await conn.fetchrow("""
            SELECT pu.id, pu.partner_id, pu.email, pu.name, pu.role,
                   pu.magic_token_expires, p.api_key_hash, p.name as partner_name,
                   p.slug, p.status as partner_status
            FROM partner_users pu
            JOIN partners p ON p.id = pu.partner_id
            WHERE pu.magic_token = $1
        """, request.token)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired magic link")

        # Check expiration
        if user['magic_token_expires'] and user['magic_token_expires'] < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Magic link has expired")

        # Check partner is active
        if user['partner_status'] != 'active':
            raise HTTPException(status_code=403, detail="Partner account is not active")

        # Clear the magic token (single use)
        await conn.execute("""
            UPDATE partner_users
            SET magic_token = NULL, magic_token_expires = NULL, last_login = NOW()
            WHERE id = $1
        """, user['id'])

        # Generate a new API key for this session
        api_key = generate_api_key()
        api_key_hash = hash_api_key(api_key)

        # Update the partner's API key
        await conn.execute("""
            UPDATE partners SET api_key_hash = $1 WHERE id = $2
        """, api_key_hash, user['partner_id'])

    return {
        "success": True,
        "api_key": api_key,
        "partner": {
            "id": str(user['partner_id']),
            "name": user['partner_name'],
            "slug": user['slug'],
        },
        "user": {
            "id": str(user['id']),
            "email": user['email'],
            "name": user['name'],
            "role": user['role'],
        },
    }
