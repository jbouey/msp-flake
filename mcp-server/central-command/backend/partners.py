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
import re
import html
import secrets
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Response, Cookie, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import uuid as _uuid

from .fleet import get_pool
from .auth import require_admin
from .tenant_middleware import tenant_connection, admin_connection
from .partner_auth import hash_session_token
from .db_utils import _uid
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

# Valid values for client_alert_mode on orgs and sites
VALID_ALERT_MODES = {"self_service", "informed", "silent"}


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


class BrandingUpdate(BaseModel):
    """Model for updating partner white-label branding."""
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    tagline: Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_STRIP_HTML_RE = re.compile(r'<[^>]+>')


def _validate_hex_color(value: str) -> str:
    """Validate that a string is a valid #XXXXXX hex color."""
    if not _HEX_COLOR_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid hex color format: {value}")
    return value


def _sanitize_text(value: str) -> str:
    """Strip HTML tags and decode entities from user-provided text."""
    return html.unescape(_STRIP_HTML_RE.sub('', value)).strip()

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

    async with admin_connection(pool) as conn:
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
            result = dict(partner)
            result["user_role"] = "admin"
            return result
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    # Try session cookie
    if osiris_partner_session:
        session_hash = hash_session_token(osiris_partner_session)

        async with admin_connection(pool) as conn:
            session = await conn.fetchrow("""
                SELECT ps.partner_id, p.id, p.name, p.slug, p.status,
                       pu.role AS user_role, pu.id AS partner_user_id
                FROM partner_sessions ps
                JOIN partners p ON p.id = ps.partner_id
                LEFT JOIN partner_users pu ON pu.id = ps.partner_user_id
                WHERE ps.session_token_hash = $1
                  AND ps.expires_at > NOW()
                  AND p.status = 'active'
                  AND (p.pending_approval IS NULL OR p.pending_approval = false)
            """, session_hash)

            if session:
                result = {
                    'id': session['id'],
                    'name': session['name'],
                    'slug': session['slug'],
                    'status': session['status'],
                }
                result["user_role"] = session.get("user_role") or "admin"  # NULL = legacy session = admin
                result["partner_user_id"] = str(session["partner_user_id"]) if session.get("partner_user_id") else None
                return result

    raise HTTPException(status_code=401, detail="Authentication required")


def require_partner_role(*allowed_roles):
    """Dependency factory that checks partner_users.role against allowed roles.
    Returns 403 for unauthorized roles."""
    async def _check(partner: dict = Depends(require_partner)):
        if partner.get("user_role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions for this action",
            )
        return partner
    return Depends(_check)


# =============================================================================
# PUBLIC ENDPOINTS (no auth required)
# =============================================================================

@router.post("/claim")
async def claim_provision_code(claim: ProvisionClaim):
    """Claim a provision code (called by appliance during setup)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
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
async def get_my_partner(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get current partner's info (self-service)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
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
async def get_my_sites(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get sites belonging to this partner."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   s.onboarding_stage, s.created_at,
                   COUNT(DISTINCT sa.id) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin,
                   COALESCE(gas.total_agents, 0) as agent_count,
                   COALESCE(gas.overall_compliance_rate, 0) as agent_compliance_rate,
                   gas.last_event as agent_last_event
            FROM sites s
            LEFT JOIN site_appliances sa ON s.site_id = sa.site_id
            LEFT JOIN site_go_agent_summaries gas ON s.site_id = gas.site_id
            WHERE s.partner_id = $1
              AND s.status != 'inactive'
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier,
                     s.onboarding_stage, s.created_at,
                     gas.total_agents, gas.overall_compliance_rate, gas.last_event
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
                'agent_count': row['agent_count'],
                'agent_compliance_rate': float(row['agent_compliance_rate']),
                'agent_last_event': row['agent_last_event'].isoformat() if row['agent_last_event'] else None,
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


@router.get("/me/orgs")
async def get_my_orgs(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get organizations managed by this partner with consolidated health."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT
                co.id, co.name, co.primary_email, co.practice_type,
                co.provider_count, co.status, co.created_at,
                COUNT(DISTINCT s.site_id) as site_count,
                COUNT(DISTINCT sa.appliance_id) as appliance_count,
                MAX(sa.last_checkin) as last_checkin,
                COUNT(DISTINCT sa.id) FILTER (
                    WHERE sa.last_checkin > NOW() - INTERVAL '15 minutes'
                ) as online_count,
                COALESCE(SUM(gas.total_agents), 0) as total_agents,
                CASE WHEN COUNT(gas.site_id) > 0
                     THEN ROUND(AVG(gas.overall_compliance_rate)::numeric, 1)
                     ELSE 0 END as avg_agent_compliance
            FROM client_orgs co
            LEFT JOIN sites s ON s.client_org_id = co.id
                AND s.partner_id = $1 AND s.status != 'inactive'
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            LEFT JOIN site_go_agent_summaries gas ON gas.site_id = s.site_id
            WHERE co.current_partner_id = $1
            GROUP BY co.id
            ORDER BY co.name
        """, partner['id'])

        orgs = []
        for row in rows:
            orgs.append({
                'id': str(row['id']),
                'name': row['name'],
                'primary_email': row['primary_email'],
                'practice_type': row['practice_type'],
                'provider_count': row['provider_count'],
                'status': row['status'],
                'site_count': row['site_count'],
                'appliance_count': row['appliance_count'],
                'online_count': row['online_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'total_agents': row['total_agents'],
                'avg_agent_compliance': float(row['avg_agent_compliance']),
            })

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITES_LISTED,
            target_type="organizations",
            target_id=str(partner['id']),
            event_data={"org_count": len(orgs)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'organizations': orgs, 'count': len(orgs)}


@router.get("/me/orgs/{org_id}/drift-config")
async def get_partner_org_drift_config(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech", "billing")
):
    """Get drift config for all sites in an org (org-level view)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get all site drift configs in this org
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, sdc.disabled_checks
            FROM sites s
            LEFT JOIN site_drift_config sdc ON sdc.site_id = s.site_id
            WHERE s.client_org_id = $1 AND s.partner_id = $2
            ORDER BY s.clinic_name
        """, org_id, partner['id'])

        sites = []
        for row in rows:
            disabled = row['disabled_checks'] or []
            if isinstance(disabled, str):
                import json
                disabled = json.loads(disabled)
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'disabled_checks': disabled,
            })

        return {'org_id': org_id, 'sites': sites}


@router.put("/me/orgs/{org_id}/drift-config")
async def update_partner_org_drift_config(
    org_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech")
):
    """Apply drift config to ALL sites in an org (bulk operation)."""
    pool = await get_pool()
    body = await request.json()
    disabled_checks = body.get("disabled_checks", [])

    if not isinstance(disabled_checks, list):
        raise HTTPException(status_code=400, detail="disabled_checks must be a list")

    # Safety bounds: prevent disabling critical compliance checks
    from .routes import CRITICAL_DRIFT_CHECKS
    blocked = [c for c in disabled_checks if c in CRITICAL_DRIFT_CHECKS]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot disable critical compliance checks: {', '.join(sorted(blocked))}. "
            f"These checks are required for HIPAA compliance monitoring.",
        )

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get org's sites
        site_rows = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )

        import json
        updated = 0
        async with conn.transaction():
            for row in site_rows:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, disabled_checks)
                    VALUES ($1, $2)
                    ON CONFLICT (site_id) DO UPDATE SET
                        disabled_checks = $2,
                        updated_at = NOW()
                """, row['site_id'], json.dumps(disabled_checks))
                updated += 1

        await log_partner_site_action(
            partner_id=str(partner['id']),
            site_id=org_id,
            event_type=PartnerEventType.ASSET_UPDATED,
            event_data={
                "action": "bulk_drift_config",
                "disabled_checks": disabled_checks,
                "sites_updated": updated,
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'status': 'updated', 'sites_updated': updated}


# =============================================================================
# ORG / SITE ALERT CONFIG (partner-managed client notification settings)
# =============================================================================

@router.get("/me/orgs/{org_id}/alert-config")
async def get_partner_org_alert_config(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Return alert email config and per-site overrides for a partner org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            """SELECT alert_email, cc_email, client_alert_mode
               FROM client_orgs
               WHERE id = $1 AND current_partner_id = $2""",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_rows = await conn.fetch(
            """SELECT s.site_id, s.clinic_name AS name, s.client_alert_mode
               FROM sites s
               WHERE s.client_org_id = $1
                 AND s.partner_id = $2
                 AND s.client_alert_mode IS NOT NULL""",
            org_id, partner['id']
        )

    return {
        "alert_email": org["alert_email"],
        "cc_email": org["cc_email"],
        "client_alert_mode": org["client_alert_mode"],
        "site_overrides": [
            {
                "site_id": r["site_id"],
                "name": r["name"],
                "client_alert_mode": r["client_alert_mode"],
            }
            for r in site_rows
        ],
    }


@router.put("/me/orgs/{org_id}/alert-config")
async def update_partner_org_alert_config(
    org_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Update alert email and/or client_alert_mode for a partner org."""
    body = await request.json()

    # Validate mode if provided
    mode = body.get("client_alert_mode")
    if mode is not None and mode not in VALID_ALERT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"client_alert_mode must be one of: {', '.join(sorted(VALID_ALERT_MODES))}",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Build dynamic UPDATE for only the provided fields
        allowed_fields = {"alert_email", "cc_email", "client_alert_mode"}
        updates = {k: v for k, v in body.items() if k in allowed_fields}
        if updates:
            set_clauses = ", ".join(
                f"{col} = ${i + 1}" for i, col in enumerate(updates)
            )
            values = list(updates.values())
            values.append(org_id)
            await conn.execute(
                f"UPDATE client_orgs SET {set_clauses}, updated_at = NOW() WHERE id = ${len(values)}",
                *values,
            )

    return {"status": "updated"}


@router.put("/me/sites/{site_id}/alert-config")
async def update_partner_site_alert_config(
    site_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Set per-site client_alert_mode override (null = inherit from org)."""
    body = await request.json()
    mode = body.get("client_alert_mode")

    # null is explicitly allowed (clears the override)
    if mode is not None and mode not in VALID_ALERT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"client_alert_mode must be one of: {', '.join(sorted(VALID_ALERT_MODES))} or null",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner['id']
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        await conn.execute(
            "UPDATE sites SET client_alert_mode = $1, updated_at = NOW() WHERE site_id = $2",
            mode, site_id,
        )

    return {"status": "updated", "client_alert_mode": mode}


# =============================================================================
# ORG-LEVEL INVENTORY (partner view — multi-appliance aggregation)
# =============================================================================

@router.get("/me/orgs/{org_id}/devices")
async def get_partner_org_devices(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Aggregate device inventory across all sites in a partner's org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"devices": [], "summary": {"total": 0, "compliant": 0, "drifted": 0, "unknown": 0, "compliance_rate": 0}, "total": 0}

        devices = await conn.fetch("""
            SELECT d.id, d.site_id, s.clinic_name, d.hostname, d.ip_address,
                   d.device_type, d.os_name, d.compliance_status, d.device_status,
                   d.last_seen_at
            FROM discovered_devices d
            JOIN sites s ON d.site_id = s.site_id
            WHERE d.site_id = ANY($1)
            ORDER BY d.ip_address
            LIMIT $2 OFFSET $3
        """, site_ids, limit, offset)

        total = await conn.fetchval(
            "SELECT count(*) FROM discovered_devices WHERE site_id = ANY($1)", site_ids
        )
        summary = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status IS NULL OR compliance_status = 'unknown') as unknown
            FROM discovered_devices WHERE site_id = ANY($1)
        """, site_ids)

    return {
        "devices": [
            {
                "id": str(d['id']), "site_id": d['site_id'], "clinic_name": d['clinic_name'],
                "hostname": d['hostname'], "ip_address": d['ip_address'],
                "device_type": d['device_type'], "os_name": d['os_name'],
                "compliance_status": d['compliance_status'] or 'unknown',
                "device_status": d['device_status'],
                "last_seen": d['last_seen_at'].isoformat() if d['last_seen_at'] else None,
            }
            for d in devices
        ],
        "summary": {
            "total": summary['total'], "compliant": summary['compliant'],
            "drifted": summary['drifted'], "unknown": summary['unknown'],
            "compliance_rate": round(summary['compliant'] / summary['total'] * 100, 1) if summary['total'] > 0 else 0,
        },
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/me/orgs/{org_id}/workstations")
async def get_partner_org_workstations(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Aggregate workstation compliance across all sites in a partner's org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"workstations": [], "summary": None}

        ws_rows = await conn.fetch("""
            SELECT w.id, w.site_id, s.clinic_name, w.hostname, w.ip_address,
                   w.os_name, w.online, w.compliance_status, w.compliance_percentage, w.last_seen
            FROM workstations w
            JOIN sites s ON w.site_id = s.site_id
            WHERE w.site_id = ANY($1) ORDER BY w.hostname
        """, site_ids)

        summary = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE online) as online,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status = 'error') as error,
                count(*) FILTER (WHERE compliance_status IS NULL OR compliance_status = 'unknown') as unknown
            FROM workstations WHERE site_id = ANY($1)
        """, site_ids)

    total = summary['total'] or 0
    return {
        "workstations": [
            {
                "id": str(ws['id']), "site_id": ws['site_id'], "clinic_name": ws['clinic_name'],
                "hostname": ws['hostname'], "ip_address": ws['ip_address'], "os_name": ws['os_name'],
                "online": ws['online'], "compliance_status": ws['compliance_status'] or 'unknown',
                "compliance_percentage": float(ws['compliance_percentage'] or 0),
                "last_seen": ws['last_seen'].isoformat() if ws['last_seen'] else None,
            }
            for ws in ws_rows
        ],
        "summary": {
            "total_workstations": total,
            "online_workstations": summary['online'] or 0,
            "compliant_workstations": summary['compliant'] or 0,
            "drifted_workstations": summary['drifted'] or 0,
            "unknown_workstations": summary['unknown'] or 0,
            "overall_compliance_rate": round((summary['compliant'] or 0) / total * 100, 1) if total > 0 else 0,
        },
    }


@router.get("/me/orgs/{org_id}/agents")
async def get_partner_org_agents(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Aggregate Go agent status across all sites in a partner's org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"agents": [], "summary": {"total": 0, "active": 0}}

        now = datetime.now(timezone.utc)
        rows = await conn.fetch("""
            SELECT g.agent_id, g.hostname, g.ip_address, g.site_id, s.clinic_name,
                   COALESCE(NULLIF(g.os_version, ''), g.os_name) AS os_version,
                   g.agent_version, g.last_heartbeat, g.compliance_percentage
            FROM go_agents g
            JOIN sites s ON g.site_id = s.site_id
            WHERE g.site_id = ANY($1)
            ORDER BY g.last_heartbeat DESC NULLS LAST
        """, site_ids)

    agents = []
    summary = {"active": 0, "stale": 0, "offline": 0, "never": 0}
    for r in rows:
        hb = r['last_heartbeat']
        if hb is None:
            derived = "never"
        else:
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            age = now - hb
            derived = "active" if age < timedelta(minutes=5) else "stale" if age < timedelta(hours=1) else "offline"
        summary[derived] += 1
        agents.append({
            "agent_id": r['agent_id'], "hostname": r['hostname'], "site_id": r['site_id'],
            "clinic_name": r['clinic_name'], "os_version": r['os_version'],
            "agent_version": r['agent_version'], "derived_status": derived,
            "last_heartbeat": r['last_heartbeat'].isoformat() if r['last_heartbeat'] else None,
            "compliance_percentage": float(r['compliance_percentage'] or 0),
        })

    return {"agents": agents, "summary": {**summary, "total": len(agents)}}


@router.get("/me/orgs/{org_id}/evidence-witnesses")
async def get_partner_org_witnesses(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Witness attestation stats for a partner's org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]

        total_att = await conn.fetchval("""
            SELECT count(*) FROM witness_attestations wa
            WHERE wa.bundle_id IN (SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1))
        """, site_ids) or 0

        recent = await conn.fetchval("""
            SELECT count(*) FROM witness_attestations wa
            WHERE wa.created_at > NOW() - interval '24h'
            AND wa.bundle_id IN (SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1))
        """, site_ids) or 0

        total_bundles = await conn.fetchval("""
            SELECT count(DISTINCT bundle_id) FROM compliance_bundles
            WHERE site_id = ANY($1) AND checked_at > NOW() - interval '24h'
        """, site_ids) or 0

    return {
        "total_attestations": total_att,
        "attestations_24h": recent,
        "coverage_pct": round(recent / total_bundles * 100, 1) if total_bundles > 0 else 0,
        "total_bundles_24h": total_bundles,
    }


@router.post("/me/provisions")
async def create_provision_code(
    request: Request,
    provision: ProvisionCreate,
    partner: dict = require_partner_role("admin")
):
    """Create a new provision code for appliance onboarding."""
    pool = await get_pool()

    code = generate_provision_code()
    expires_at = datetime.now(timezone.utc) + timedelta(days=provision.expires_days)

    async with admin_connection(pool) as conn:
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
    partner: dict = require_partner_role("admin", "tech")
):
    """List provision codes for this partner."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
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
    partner: dict = require_partner_role("admin")
):
    """Revoke a provision code."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE appliance_provisions
            SET status = 'revoked'
            WHERE id = $1 AND partner_id = $2 AND status = 'pending'
            RETURNING provision_code
        """, _uid(provision_id), partner['id'])

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
    partner: dict = require_partner_role("admin", "tech")
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

    async with admin_connection(pool) as conn:
        provision = await conn.fetchrow("""
            SELECT provision_code, status
            FROM appliance_provisions
            WHERE id = $1 AND partner_id = $2
        """, _uid(provision_id), partner['id'])

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

    async with admin_connection(pool) as conn:
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
# PARTNER BRANDING ENDPOINTS (Partner-authenticated)
# =============================================================================

@router.get("/me/branding")
async def get_my_branding(partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get own branding config."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT brand_name, logo_url, primary_color, secondary_color,
                   tagline, support_email, support_phone, slug
            FROM partners WHERE id = $1
        """, partner['id'])

    if not row:
        raise HTTPException(status_code=404, detail="Partner not found")

    return {
        "brand_name": row["brand_name"] or "OsirisCare",
        "logo_url": row["logo_url"],
        "primary_color": row["primary_color"] or "#0D9488",
        "secondary_color": row["secondary_color"] or "#6366F1",
        "tagline": row["tagline"],
        "support_email": row["support_email"],
        "support_phone": row["support_phone"],
        "partner_slug": row["slug"],
    }


@router.put("/me/branding")
async def update_my_branding(
    request: Request,
    body: BrandingUpdate,
    partner: dict = require_partner_role("admin"),
):
    """Update branding. Admin only."""
    updates = {}

    # Validate colors
    if body.primary_color is not None:
        _validate_hex_color(body.primary_color)
        updates["primary_color"] = body.primary_color
    if body.secondary_color is not None:
        _validate_hex_color(body.secondary_color)
        updates["secondary_color"] = body.secondary_color

    # Validate logo_url must be HTTPS if provided
    if body.logo_url is not None:
        if body.logo_url and not body.logo_url.startswith("https://"):
            raise HTTPException(
                status_code=400,
                detail="logo_url must use HTTPS",
            )
        updates["logo_url"] = body.logo_url

    # Sanitize text fields — strip HTML
    if body.brand_name is not None:
        sanitized = _sanitize_text(body.brand_name)
        if not sanitized:
            raise HTTPException(status_code=400, detail="brand_name cannot be empty")
        updates["brand_name"] = sanitized[:255]
    if body.tagline is not None:
        updates["tagline"] = _sanitize_text(body.tagline)[:500]

    # Support contact fields (light validation)
    if body.support_email is not None:
        updates["support_email"] = body.support_email[:255] if body.support_email else None
    if body.support_phone is not None:
        updates["support_phone"] = body.support_phone[:50] if body.support_phone else None

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build dynamic UPDATE
    set_clauses = []
    params = []
    for i, (col, val) in enumerate(updates.items(), start=1):
        set_clauses.append(f"{col} = ${i}")
        params.append(val)
    params.append(partner['id'])
    set_sql = ", ".join(set_clauses)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await conn.execute(
            f"UPDATE partners SET {set_sql}, updated_at = NOW() WHERE id = ${len(params)}",
            *params,
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.BRANDING_UPDATED,
        target_type="partner",
        target_id=str(partner['id']),
        event_data={"updated_fields": list(updates.keys())},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {"status": "updated", "updated_fields": list(updates.keys())}


# =============================================================================
# PUBLIC BRANDING ENDPOINT (no auth — for login page rendering)
# =============================================================================

# Separate router for the public branding endpoint (no partner auth prefix)
branding_public_router = APIRouter(tags=["portal-branding"])


@branding_public_router.get("/api/portal/branding/{partner_slug}")
async def get_portal_branding(partner_slug: str):
    """Public endpoint — returns partner branding for login page rendering."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT brand_name, logo_url, primary_color, secondary_color,
                   tagline, support_email, support_phone, slug
            FROM partners WHERE slug = $1 AND status = 'active'
        """, partner_slug)

    if not row:
        # Return OsirisCare defaults — don't reveal partner doesn't exist
        return {
            "brand_name": "OsirisCare",
            "logo_url": None,
            "primary_color": "#0D9488",
            "secondary_color": "#6366F1",
            "tagline": "HIPAA Compliance Simplified",
            "support_email": None,
            "support_phone": None,
            "partner_slug": partner_slug,
        }

    return {
        "brand_name": row["brand_name"] or "OsirisCare",
        "logo_url": row["logo_url"],
        "primary_color": row["primary_color"] or "#0D9488",
        "secondary_color": row["secondary_color"] or "#6366F1",
        "tagline": row["tagline"],
        "support_email": row["support_email"],
        "support_phone": row["support_phone"],
        "partner_slug": row["slug"],
    }


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

    async with admin_connection(pool) as conn:
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
async def list_partners(
    status: Optional[str] = None,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("name", regex="^(name|created_at|status|site_count|revenue_share_percent)$"),
    sort_dir: str = Query("asc", regex="^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """List partners with pagination, search, and sorting (admin only)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Build WHERE clause
        conditions = []
        params: list = []
        idx = 1

        ALLOWED_STATUSES = {"active", "suspended", "inactive", "pending"}
        if status:
            if status not in ALLOWED_STATUSES:
                raise HTTPException(status_code=400, detail=f"Invalid status filter: {status}")
            conditions.append(f"p.status = ${idx}")
            params.append(status)
            idx += 1

        if search:
            conditions.append(
                f"(p.name ILIKE ${idx} OR p.slug ILIKE ${idx} OR p.contact_email ILIKE ${idx} OR p.brand_name ILIKE ${idx})"
            )
            params.append(f"%{search}%")
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Sort mapping (site_count is computed, needs special handling)
        sort_col = {
            "name": "p.name",
            "created_at": "p.created_at",
            "status": "p.status",
            "revenue_share_percent": "p.revenue_share_percent",
            "site_count": "site_count",
        }.get(sort_by, "p.name")
        order = f"{sort_col} {'DESC' if sort_dir == 'desc' else 'ASC'}"

        # Get total count for pagination
        total_row = await conn.fetchrow(
            f"SELECT COUNT(*) as total FROM partners p {where}", *params
        )
        total_count = total_row['total'] if total_row else 0

        # Get aggregate stats (all partners, ignoring filters)
        stats_row = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'active') as active,
                COUNT(*) FILTER (WHERE status = 'suspended') as suspended,
                COUNT(*) FILTER (WHERE status = 'inactive') as inactive
            FROM partners
        """)

        total_sites_row = await conn.fetchrow(
            "SELECT COUNT(*) as total FROM sites WHERE partner_id IS NOT NULL"
        )

        # Fetch page with site counts joined
        params_page = list(params)
        params_page.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT p.id, p.name, p.slug, p.contact_email, p.brand_name,
                   p.revenue_share_percent, p.status, p.created_at,
                   COALESCE(sc.cnt, 0) as site_count
            FROM partners p
            LEFT JOIN (
                SELECT partner_id, COUNT(*) as cnt
                FROM sites
                WHERE partner_id IS NOT NULL
                GROUP BY partner_id
            ) sc ON sc.partner_id = p.id
            {where}
            ORDER BY {order}
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params_page)

        partners = []
        for row in rows:
            partners.append({
                'id': str(row['id']),
                'name': row['name'],
                'slug': row['slug'],
                'contact_email': row['contact_email'],
                'brand_name': row['brand_name'],
                'revenue_share_percent': row['revenue_share_percent'],
                'status': row['status'],
                'site_count': row['site_count'],
                'created_at': row['created_at'].isoformat(),
            })

        return {
            'partners': partners,
            'count': len(partners),
            'total': total_count,
            'limit': limit,
            'offset': offset,
            'stats': {
                'total': stats_row['total'] if stats_row else 0,
                'active': stats_row['active'] if stats_row else 0,
                'suspended': stats_row['suspended'] if stats_row else 0,
                'inactive': stats_row['inactive'] if stats_row else 0,
                'total_sites': total_sites_row['total'] if total_sites_row else 0,
            },
        }


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

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT id, name, slug, contact_email, contact_phone,
                   brand_name, logo_url, primary_color,
                   revenue_share_percent, status, created_at, updated_at
            FROM partners
            WHERE id = $1
        """, _uid(partner_id))

        if not row:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Get sites for this partner
        sites = await conn.fetch("""
            SELECT site_id, clinic_name, status, tier
            FROM sites
            WHERE partner_id = $1
            ORDER BY clinic_name
        """, _uid(partner_id))

        # Get users for this partner
        users = await conn.fetch("""
            SELECT id, email, name, role, status, last_login
            FROM partner_users
            WHERE partner_id = $1
            ORDER BY email
        """, _uid(partner_id))

        # Get appliance stats per site
        appliance_stats = await conn.fetch("""
            SELECT sa.site_id, COUNT(*) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin
            FROM site_appliances sa
            JOIN sites s ON s.site_id = sa.site_id
            WHERE s.partner_id = $1
            GROUP BY sa.site_id
        """, _uid(partner_id))
        app_map = {r['site_id']: {'count': r['appliance_count'], 'last_checkin': r['last_checkin']} for r in appliance_stats}

        # Get incident counts (best-effort — incidents may not link cleanly to partner sites)
        try:
            incident_row = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE i.status = 'open') as open_count
                FROM incidents i
                JOIN site_appliances sa ON sa.appliance_id = i.appliance_id
                WHERE sa.site_id IN (SELECT site_id FROM sites WHERE partner_id = $1)
            """, _uid(partner_id))
        except Exception:
            incident_row = {'total': 0, 'open_count': 0}

        # Get recent activity count
        activity_row = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as recent
            FROM partner_activity_log
            WHERE partner_id = $1
        """, _uid(partner_id))

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
                    'appliance_count': app_map.get(s['site_id'], {}).get('count', 0),
                    'last_checkin': app_map.get(s['site_id'], {}).get('last_checkin', None).isoformat() if app_map.get(s['site_id'], {}).get('last_checkin') else None,
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
            'stats': {
                'total_sites': len(sites),
                'total_users': len(users),
                'total_appliances': sum(a['count'] for a in app_map.values()),
                'total_incidents': incident_row['total'] if incident_row else 0,
                'open_incidents': incident_row['open_count'] if incident_row else 0,
                'total_activity': activity_row['total'] if activity_row else 0,
                'recent_activity': activity_row['recent'] if activity_row else 0,
            },
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

    values.append(_uid(partner_id))

    query = f"""
        UPDATE partners
        SET {', '.join(updates)}
        WHERE id = ${param_num}
        RETURNING id, name, slug
    """

    async with admin_connection(pool) as conn:
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

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE partners
            SET api_key_hash = $1, updated_at = NOW()
            WHERE id = $2
            RETURNING name
        """, api_key_hash, _uid(partner_id))

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


@router.delete("/{partner_id}")
async def delete_partner(request: Request, partner_id: str, admin: dict = Depends(require_admin)):
    """Delete a partner (admin only). Cascades to sessions, provisions, etc."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Get partner info for audit log before deleting
        partner = await conn.fetchrow(
            "SELECT id, name, slug FROM partners WHERE id = $1",
            _uid(partner_id),
        )
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Unlink sites (set partner_id to NULL rather than deleting sites)
        await conn.execute(
            "UPDATE sites SET partner_id = NULL WHERE partner_id = $1",
            _uid(partner_id),
        )

        # Delete the partner (cascades to sessions, provisions, etc.)
        await conn.execute("DELETE FROM partners WHERE id = $1", _uid(partner_id))

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.PARTNER_UPDATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={
            "action": "deleted",
            "partner_name": partner["name"],
            "partner_slug": partner["slug"],
            "admin_user": admin.get("sub", "unknown"),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {"status": "deleted", "id": str(partner_id), "name": partner["name"]}


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

    async with admin_connection(pool) as conn:
        # Verify partner exists
        partner = await conn.fetchval("SELECT 1 FROM partners WHERE id = $1", _uid(partner_id))
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Check email uniqueness within partner
        existing = await conn.fetchval("""
            SELECT 1 FROM partner_users
            WHERE partner_id = $1 AND email = $2
        """, _uid(partner_id), user.email.lower())
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
            _uid(partner_id),
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

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE partner_users
            SET magic_token = $1, magic_token_expires = $2
            WHERE id = $3 AND partner_id = $4
            RETURNING email
        """, magic_token, magic_expires, _uid(user_id), _uid(partner_id))

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

    async with tenant_connection(pool, site_id=site_id) as conn:
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

    async with tenant_connection(pool, site_id=site_id) as conn:
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

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        cred = await conn.fetchrow("""
            SELECT sc.*, s.site_id
            FROM site_credentials sc
            JOIN sites s ON s.id = sc.site_id
            WHERE sc.id = $1 AND s.partner_id = $2
        """, _uid(credential_id), partner['id'])

        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")

        # Update validation status to pending
        await conn.execute("""
            UPDATE site_credentials
            SET validation_status = 'pending',
                last_validated_at = NOW()
            WHERE id = $1
        """, _uid(credential_id))

        # Find an active appliance to run the validation
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 AND status = 'online'
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

        if not appliance:
            appliance = await conn.fetchrow("""
                SELECT appliance_id FROM site_appliances
                WHERE site_id = $1
                ORDER BY last_checkin DESC NULLS LAST
                LIMIT 1
            """, site_id)

        validation_result = {
            'can_connect': None,
            'can_read_ad': None,
            'is_domain_admin': None,
            'servers_found': [],
            'servers_accessible': [],
            'warnings': [],
            'errors': [],
        }

        if appliance:
            import secrets
            from datetime import timedelta
            from .order_signing import sign_admin_order

            order_id = f"ORD-{secrets.token_hex(8).upper()}"
            now_ts = datetime.now(timezone.utc)
            expires_at = now_ts + timedelta(hours=1)
            validate_params = {
                'credential_id': str(cred['id']),
                'hostname': cred.get('hostname', ''),
                'credential_type': cred['credential_type'],
            }

            nonce, signature, signed_payload = sign_admin_order(
                order_id, 'validate_credential', validate_params, now_ts, expires_at,
                target_appliance_id=appliance['appliance_id'],
            )

            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, 'validate_credential', $4::jsonb, 1, 'pending', $5, $6, $7, $8, $9)
            """,
                order_id,
                appliance['appliance_id'],
                site_id,
                json.dumps(validate_params),
                now_ts,
                expires_at,
                nonce,
                signature,
                signed_payload,
            )
            validation_result['warnings'].append('Validation order queued to appliance')
        else:
            validation_result['warnings'].append('No appliance available — validation will run on next checkin')
            validation_result['errors'].append('No active appliance found for this site')

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

    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.execute("""
            DELETE FROM site_credentials sc
            USING sites s
            WHERE sc.id = $1
            AND sc.site_id = s.id
            AND s.partner_id = $2
        """, _uid(credential_id), partner['id'])

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


@router.get("/me/sites/{site_id}/drift-config")
async def get_partner_drift_config(site_id: str, partner=Depends(require_partner)):
    """Get drift scan configuration for a partner-managed site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify partner owns this site
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        rows = await conn.fetch(
            "SELECT check_type, enabled, notes FROM site_drift_config WHERE site_id = $1 ORDER BY check_type",
            site_id)
        if not rows:
            rows = await conn.fetch(
                "SELECT check_type, enabled, notes FROM site_drift_config WHERE site_id = '__defaults__' ORDER BY check_type")

        def _platform(ct):
            if ct.startswith("macos_"): return "macos"
            if ct.startswith("linux_"): return "linux"
            return "windows"

        checks = [{"check_type": r["check_type"], "enabled": r["enabled"], "platform": _platform(r["check_type"]), "notes": r["notes"] or ""} for r in rows]
    return {"site_id": site_id, "checks": checks}


@router.put("/me/sites/{site_id}/drift-config")
async def update_partner_drift_config(site_id: str, body: dict, partner=Depends(require_partner)):
    """Update drift scan configuration for a partner-managed site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        checks = body.get("checks", [])

        # Safety bounds: prevent disabling all checks or critical checks
        from .routes import _validate_drift_config_checks
        _validate_drift_config_checks(checks)

        async with conn.transaction():
            for item in checks:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, modified_by, modified_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, modified_by = $4, modified_at = NOW()
                """, site_id, item["check_type"], item["enabled"], f"partner:{partner.get('org_name', partner['id'])}")
    return {"status": "ok", "site_id": site_id, "updated": len(checks)}


# =============================================================================
# MAINTENANCE MODE (partner-scoped)
# =============================================================================

class PartnerMaintenanceRequest(BaseModel):
    duration_hours: float
    reason: str


@router.put("/me/sites/{site_id}/maintenance")
async def set_partner_maintenance(
    site_id: str,
    body: PartnerMaintenanceRequest,
    partner=Depends(require_partner),
):
    """Set a maintenance window for a partner-managed site."""
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    if body.duration_hours < 0.5 or body.duration_hours > 48:
        raise HTTPException(status_code=422, detail="duration_hours must be between 0.5 and 48")

    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        set_by = f"partner:{partner.get('name', partner['id'])}"
        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NOW() + ($1 || ' hours')::INTERVAL,
                maintenance_reason = $2,
                maintenance_set_by = $3
            WHERE site_id = $4
        """, str(body.duration_hours), body.reason.strip(), set_by, site_id)

    logger.info("Partner maintenance window set",
                site_id=site_id,
                duration_hours=body.duration_hours,
                partner_id=str(partner['id']))

    return {"status": "ok", "site_id": site_id, "duration_hours": body.duration_hours}


@router.delete("/me/sites/{site_id}/maintenance")
async def cancel_partner_maintenance(
    site_id: str,
    partner=Depends(require_partner),
):
    """Cancel an active maintenance window for a partner-managed site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NULL,
                maintenance_reason = NULL,
                maintenance_set_by = NULL
            WHERE site_id = $1
        """, site_id)

    logger.info("Partner maintenance window cancelled",
                site_id=site_id,
                partner_id=str(partner['id']))

    return {"status": "ok", "site_id": site_id, "maintenance_until": None}


@router.get("/me/onboarding")
async def get_partner_onboarding(request: Request, partner=Depends(require_partner)):
    """Get onboarding pipeline for partner's sites (excludes active/compliant)."""
    from datetime import timezone
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.contact_name, s.contact_email,
                   s.onboarding_stage, s.notes, s.blockers, s.created_at,
                   s.lead_at, s.discovery_at, s.proposal_at, s.contract_at,
                   s.intake_at, s.creds_at, s.shipped_at, s.received_at,
                   s.connectivity_at, s.scanning_at, s.baseline_at, s.active_at,
                   COUNT(sa.id) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin,
                   (SELECT COUNT(*) FROM site_credentials sc WHERE sc.site_id = s.site_id) as credential_count
            FROM sites s
            LEFT JOIN site_appliances sa ON s.site_id = sa.site_id
            WHERE s.partner_id = $1
            GROUP BY s.site_id, s.clinic_name, s.contact_name, s.contact_email,
                     s.onboarding_stage, s.notes, s.blockers, s.created_at,
                     s.lead_at, s.discovery_at, s.proposal_at, s.contract_at,
                     s.intake_at, s.creds_at, s.shipped_at, s.received_at,
                     s.connectivity_at, s.scanning_at, s.baseline_at, s.active_at
            ORDER BY s.created_at DESC
        """, partner['id'])

        stage_progress = {
            'lead': 10, 'discovery': 20, 'proposal': 30, 'contract': 40,
            'intake': 50, 'creds': 60, 'shipped': 70, 'received': 80,
            'connectivity': 85, 'scanning': 90, 'baseline': 95,
            'compliant': 100, 'active': 100,
        }
        stage_col_map = {
            'lead': 'lead_at', 'discovery': 'discovery_at', 'proposal': 'proposal_at',
            'contract': 'contract_at', 'intake': 'intake_at', 'creds': 'creds_at',
            'shipped': 'shipped_at', 'received': 'received_at', 'connectivity': 'connectivity_at',
            'scanning': 'scanning_at', 'baseline': 'baseline_at', 'active': 'active_at',
        }

        from datetime import datetime
        now = datetime.now(timezone.utc)
        pipeline = []
        for row in rows:
            stage = row['onboarding_stage'] or 'lead'
            ts_col = stage_col_map.get(stage)
            stage_entered = row[ts_col] if ts_col and row.get(ts_col) else row['created_at']
            days_in_stage = (now - stage_entered).days if stage_entered else 0

            # Auto-detect blockers
            blockers = []
            if row['blockers']:
                try:
                    blockers = json.loads(row['blockers']) if isinstance(row['blockers'], str) else row['blockers']
                except (json.JSONDecodeError, TypeError):
                    pass

            # Add system-detected blockers
            if stage in ('creds', 'intake') and row['credential_count'] == 0:
                blockers.append("No credentials configured")
            if stage in ('connectivity', 'scanning') and row['appliance_count'] == 0:
                blockers.append("No appliance connected")
            if stage in ('received', 'shipped') and row['last_checkin'] is None:
                blockers.append("Appliance has not checked in yet")

            pipeline.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'contact_name': row['contact_name'],
                'contact_email': row['contact_email'],
                'stage': stage,
                'progress_percent': stage_progress.get(stage, 0),
                'days_in_stage': days_in_stage,
                'stage_entered_at': stage_entered.isoformat() if stage_entered else None,
                'blockers': blockers,
                'notes': row['notes'],
                'appliance_count': row['appliance_count'],
                'credential_count': row['credential_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            })

    # Split into active pipeline vs completed
    in_progress = [s for s in pipeline if s['stage'] not in ('active', 'compliant')]
    completed = [s for s in pipeline if s['stage'] in ('active', 'compliant')]

    return {
        'pipeline': in_progress,
        'completed': completed,
        'total': len(pipeline),
        'in_progress_count': len(in_progress),
        'completed_count': len(completed),
    }


@router.post("/me/sites/{site_id}/trigger-checkin")
async def trigger_site_checkin(
    request: Request,
    site_id: str,
    partner=Depends(require_partner)
):
    """Request immediate checkin from site's appliance (creates force_checkin order)."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow(
            "SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner['id']
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Find appliance
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 ORDER BY last_checkin DESC NULLS LAST LIMIT 1
        """, site_id)
        if not appliance:
            raise HTTPException(status_code=400, detail="No appliance connected to this site")

        # Create force_checkin order
        import uuid
        from datetime import timedelta
        order_id = str(uuid.uuid4())
        now_ts = datetime.now(timezone.utc)
        exp = now_ts + timedelta(hours=1)

        await conn.execute("""
            INSERT INTO admin_orders (id, appliance_id, order_type, parameters, status, expires_at, created_at)
            VALUES ($1, $2, 'force_checkin', '{}'::jsonb, 'pending', $3, $4)
        """, uuid.UUID(order_id), appliance['appliance_id'], exp, now_ts)

    return {'status': 'queued', 'order_id': order_id, 'message': 'Checkin request queued for next poll cycle.'}


@router.get("/me/sites/{site_id}/assets")
async def list_site_assets(
    site_id: str,
    status: Optional[str] = None,
    partner=Depends(require_partner)
):
    """List discovered assets for a site."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
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

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify ownership
        asset = await conn.fetchrow("""
            SELECT da.id FROM discovered_assets da
            JOIN sites s ON s.id = da.site_id
            WHERE da.id = $1 AND s.partner_id = $2
        """, _uid(asset_id), partner['id'])

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

        values.append(_uid(asset_id))

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

    async with tenant_connection(pool, site_id=site_id) as conn:
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
            from .order_signing import sign_admin_order
            order_id = f"ORD-{secrets.token_hex(8).upper()}"
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(hours=24)  # Discovery can take time
            discovery_params = {
                'scan_id': str(scan['id']),
                'scan_type': 'full',
                'triggered_by': partner.get('email', 'partner')
            }

            nonce, signature, signed_payload = sign_admin_order(
                order_id, 'run_discovery', discovery_params, now, expires_at,
                target_appliance_id=appliance['appliance_id'],
            )

            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, 'run_discovery', $4::jsonb, 1, 'pending', $5, $6, $7, $8, $9)
            """,
                order_id,
                appliance['appliance_id'],
                site_id,
                json.dumps(discovery_params),
                now,
                expires_at,
                nonce,
                signature,
                signed_payload,
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

    async with admin_connection(pool) as conn:
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
