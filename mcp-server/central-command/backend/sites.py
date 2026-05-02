"""Sites management endpoints.

Provides PUT/DELETE endpoints for site management operations
that modify site data directly.
"""

import json
import os
import time
import ipaddress
import secrets
import logging
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, Field
from enum import Enum

from .fleet import get_pool
from .auth import require_auth, require_operator, require_admin
from .shared import require_appliance_bearer, async_session as _reconcile_session
# _reconcile_session: SQLAlchemy admin-pool sessionmaker. We use the admin
# posture deliberately for inline reconcile issuance — it matches the
# posture of the POST /api/appliances/reconcile endpoint (which uses
# Depends(get_db) returning the same admin pool). Authorization is
# enforced earlier: auth_site_id is bound to Bearer token at STEP 1 and
# `checkin.site_id = auth_site_id` is assigned at handler entry (line
# ~2902), so a reconcile for site A cannot be issued by site B's token.
# Do NOT flip this to tenant_connection — reconcile_events writes would
# then fail RLS because the inline path runs under the checkin's
# tenant_connection context, which scopes differently.
from .tenant_middleware import tenant_connection, admin_connection, admin_transaction
from .credential_crypto import encrypt_credential, decrypt_credential
from .websocket_manager import broadcast_event
from .fleet_updates import get_fleet_orders_for_appliance, record_fleet_order_completion
from .order_signing import sign_admin_order
from .appliance_delegation import verify_site_api_key

logger = logging.getLogger(__name__)


async def require_appliance_auth(request: Request) -> str:
    """Validate appliance Bearer token from Authorization header.

    Extracts the API key, looks up the site_id from the request body,
    and verifies the key against the api_keys table.

    Returns the validated site_id on success, raises 401 on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    api_key = auth_header[7:]  # Strip "Bearer "
    if not api_key:
        raise HTTPException(status_code=401, detail="Empty API key")

    # Parse request body to extract site_id (handle gzip from log shipper)
    try:
        content_encoding = request.headers.get("content-encoding", "")
        raw_body = await request.body()
        if content_encoding == "gzip":
            import gzip
            raw_body = gzip.decompress(raw_body)
        body = json.loads(raw_body)
    except Exception:
        body = {}

    site_id = body.get("site_id", "")
    if not site_id:
        raise HTTPException(status_code=401, detail="Missing site_id in request body")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        try:
            if await verify_site_api_key(conn, site_id, api_key):
                return site_id
        except Exception as e:
            # Table missing or query error — fall through to fallback
            logger.warning(f"API key verification error for site={site_id}: {e}")

        # No fallback — API key must match. Log for debugging.
        logger.warning(f"Appliance auth REJECTED: site={site_id} invalid API key")

    raise HTTPException(status_code=401, detail="Invalid API key for site")


def _is_routable_ip(raw: str, *, allow_anycast: bool = True) -> bool:
    """False for unintentional APIPA (169.254/16 except the engineered
    mesh anycast 169.254.88.1), IPv6 link-local (fe80::/10), loopback,
    multicast, unspecified, and unparseable garbage. True for RFC1918
    + WireGuard + public addresses AND for the anycast sentinel.

    IMPORTANT: 169.254.88.1 is the mesh anycast address intentionally
    assigned to every online appliance. Stripping it would break the
    `missing_anycast` anomaly detector and make every healthy appliance
    look broken. The whitelist is explicit so a future refactor can't
    silently remove it.

    Rationale for the rest: an appliance whose only *other* IPs are
    APIPA has NOT successfully re-joined the network post-outage; we
    must not persist those as the current address or the admin console
    shows a ghost. The rollup + subnet-drift heuristics also
    mis-classify APIPA as a real move, causing site re-segmentation
    alerts.
    """
    if not raw or not isinstance(raw, str):
        return False
    s = raw.strip()
    if allow_anycast and s == ANYCAST_LINK_LOCAL:
        return True
    try:
        addr = ipaddress.ip_address(s)
    except (ValueError, TypeError):
        return False
    if addr.is_link_local:          # covers 169.254/16 AND fe80::/10
        return False
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        return False
    return True


def filter_routable_ips(raw_ips) -> list:
    """Drop unintentional APIPA + IPv6 link-local + junk. Preserves the
    engineered mesh anycast 169.254.88.1. Returns list in original
    order, deduplicated. Empty list is a legitimate answer (caller
    must decide).
    """
    ips = parse_ip_addresses(raw_ips)
    seen: set = set()
    out: list = []
    for ip in ips:
        if _is_routable_ip(ip) and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def parse_ip_addresses(raw_ips) -> list:
    """Parse ip_addresses from database - could be JSON string or already a list."""
    if not raw_ips:
        return []
    if isinstance(raw_ips, list):
        return raw_ips
    if isinstance(raw_ips, str):
        try:
            return json.loads(raw_ips)
        except json.JSONDecodeError:
            return [raw_ips]  # Single IP as string
    return []


async def _audit_site_change(
    conn,
    user: dict,
    action: str,
    site_id: str,
    details: Dict[str, Any],
    request: Optional[Request] = None,
) -> None:
    """Write a site change event to admin_audit_log (append-only).

    Uses an asyncpg connection — caller must have an active transaction.
    Never raises — audit failures must not block the underlying mutation,
    but they ARE logged at ERROR so we can investigate.
    """
    try:
        user_id = user.get("id") or user.get("user_id")
        username = user.get("username") or user.get("email") or "unknown"
        ip = None
        if request is not None:
            ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else None)
            )
        await conn.execute(
            """
            INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
            """,
            str(user_id) if user_id else None,
            username,
            action,
            site_id,
            json.dumps(details),
            ip,
        )
    except Exception as e:
        logger.error(
            f"audit write failed: action={action} site={site_id} err={e}",
            exc_info=True,
        )


router = APIRouter(prefix="/api/sites", tags=["sites"])

# Separate router for order lifecycle endpoints (acknowledge/complete)
orders_router = APIRouter(prefix="/api/orders", tags=["orders"])


# =============================================================================
# CREATE SITE (called by frontend at POST /api/sites)
# =============================================================================

@router.post("")
async def create_site_api(request: Request, user: dict = Depends(require_auth)):
    """Create a new site from the admin dashboard."""
    import re
    import secrets
    body = await request.json()
    clinic_name = body.get("clinic_name", "").strip()
    if not clinic_name:
        raise HTTPException(status_code=400, detail="clinic_name is required")

    site_id = body.get("site_id") or re.sub(r'[^a-z0-9-]', '', clinic_name.lower().replace(" ", "-"))
    contact_name = body.get("contact_name", "")
    contact_email = body.get("contact_email", "")
    tier = body.get("tier", "mid")

    client_org_id = body.get("client_org_id")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # If no org specified, use the first org (single-tenant shortcut)
        if not client_org_id:
            row = await conn.fetchrow("SELECT id FROM client_orgs ORDER BY created_at LIMIT 1")
            client_org_id = str(row["id"]) if row else None

        try:
            await conn.execute("""
                INSERT INTO sites (site_id, clinic_name, contact_name, contact_email,
                                   tier, status, onboarding_stage, client_org_id, lead_at, created_at)
                VALUES ($1, $2, $3, $4, $5, 'pending', 'lead', $6, NOW(), NOW())
            """, site_id, clinic_name, contact_name, contact_email, tier, client_org_id)
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                raise HTTPException(status_code=409, detail=f"Site '{site_id}' already exists")
            raise HTTPException(status_code=500, detail=str(e))

    return {"status": "created", "site_id": site_id, "clinic_name": clinic_name}


# =============================================================================
# MODELS
# =============================================================================

class OrderType(str, Enum):
    """Types of orders that can be sent to appliances."""
    UPDATE_AGENT = "update_agent"
    RUN_RUNBOOK = "run_runbook"
    RESTART_SERVICE = "restart_service"
    RUN_COMMAND = "run_command"
    COLLECT_LOGS = "collect_logs"
    REBOOT = "reboot"
    FORCE_CHECKIN = "force_checkin"
    RUN_DRIFT = "run_drift"
    SYNC_RULES = "sync_rules"
    NIXOS_REBUILD = "nixos_rebuild"
    UPDATE_ISO = "update_iso"
    DIAGNOSTIC = "diagnostic"
    DEPLOY_SENSOR = "deploy_sensor"
    REMOVE_SENSOR = "remove_sensor"
    UPDATE_CREDENTIALS = "update_credentials"
    RESTART_AGENT = "restart_agent"
    NIX_GC = "nix_gc"


class HealingTier(str, Enum):
    """Healing tier options for L1 rules."""
    STANDARD = "standard"  # 4 core rules: firewall, defender, bitlocker, ntp
    FULL_COVERAGE = "full_coverage"  # All 21 L1 rules


class SiteUpdate(BaseModel):
    """Model for site update request."""
    clinic_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    tier: Optional[str] = None
    onboarding_stage: Optional[str] = None
    notes: Optional[str] = None
    healing_tier: Optional[HealingTier] = None
    partner_id: Optional[str] = None  # UUID string or "null" to unlink


class OrderCreate(BaseModel):
    """Model for creating a new order."""
    order_type: OrderType
    parameters: dict = {}
    priority: int = 0


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_order_id() -> str:
    """Generate a unique order ID."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    random_suffix = secrets.token_hex(4)
    return f"ORD-{timestamp}-{random_suffix}"


def parse_parameters(params):
    """Parse parameters field - handle both dict and string."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    if isinstance(params, str):
        try:
            return json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# =============================================================================
# SITE UPDATE ENDPOINT
# =============================================================================

@router.put("/{site_id}")
async def update_site(
    site_id: str,
    update: SiteUpdate,
    request: Request,
    user: dict = Depends(require_operator),
):
    """Update site information. Requires operator or admin access.

    Writes an admin_audit_log entry for every field that actually changed
    (before → after) so the Site Detail activity timeline can display it.
    """
    pool = await get_pool()

    updates = []
    values = []
    param_num = 1

    if update.clinic_name is not None:
        updates.append(f"clinic_name = ${param_num}")
        values.append(update.clinic_name)
        param_num += 1

    if update.contact_name is not None:
        updates.append(f"contact_name = ${param_num}")
        values.append(update.contact_name)
        param_num += 1

    if update.contact_email is not None:
        updates.append(f"contact_email = ${param_num}")
        values.append(update.contact_email)
        param_num += 1

    if update.tier is not None:
        updates.append(f"tier = ${param_num}")
        values.append(update.tier)
        param_num += 1

    if update.onboarding_stage is not None:
        updates.append(f"onboarding_stage = ${param_num}")
        values.append(update.onboarding_stage)
        param_num += 1

    if update.healing_tier is not None:
        updates.append(f"healing_tier = ${param_num}")
        values.append(update.healing_tier.value)
        param_num += 1

    if update.partner_id is not None:
        if update.partner_id == "null" or update.partner_id == "":
            updates.append(f"partner_id = NULL")
        else:
            updates.append(f"partner_id = ${param_num}::uuid")
            values.append(update.partner_id)
            param_num += 1

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append(f"updated_at = ${param_num}")
    values.append(datetime.now(timezone.utc))
    param_num += 1

    values.append(site_id)

    query = f"""
        UPDATE sites
        SET {', '.join(updates)}
        WHERE site_id = ${param_num}
        RETURNING site_id, clinic_name
    """

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Snapshot before state so we can record a diff to the audit log.
        before = await conn.fetchrow(
            """
            SELECT clinic_name, contact_name, contact_email, tier,
                   onboarding_stage, healing_tier, partner_id
            FROM sites WHERE site_id = $1
            """,
            site_id,
        )
        if not before:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        result = await conn.fetchrow(query, *values)
        if not result:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        # Build the changed-field diff. Only record fields whose value actually moved.
        candidates = {
            "clinic_name": update.clinic_name,
            "contact_name": update.contact_name,
            "contact_email": update.contact_email,
            "tier": update.tier,
            "onboarding_stage": update.onboarding_stage,
            "healing_tier": update.healing_tier.value if update.healing_tier else None,
            "partner_id": update.partner_id,
        }
        diff: Dict[str, Dict[str, Any]] = {}
        for key, new_val in candidates.items():
            if new_val is None:
                continue
            old_val = before[key]
            # Normalize uuid -> str for comparison
            if old_val is not None and not isinstance(old_val, (str, int, float, bool)):
                old_val = str(old_val)
            if old_val != new_val:
                diff[key] = {"from": old_val, "to": new_val}

        if diff:
            await _audit_site_change(
                conn,
                user,
                action="SITE_UPDATED",
                site_id=site_id,
                details={"changes": diff},
                request=request,
            )

    return {
        "status": "updated",
        "site_id": result["site_id"],
        "clinic_name": result["clinic_name"]
    }


# =============================================================================
# HEALING TIER ENDPOINT
# =============================================================================

class HealingTierUpdate(BaseModel):
    """Model for healing tier update request."""
    healing_tier: HealingTier


@router.put("/{site_id}/healing-tier")
async def update_healing_tier(
    site_id: str,
    update: HealingTierUpdate,
    request: Request,
    user: dict = Depends(require_operator),
):
    """Update the healing tier for a site. Requires operator or admin access.

    Healing tiers control which L1 rules are active:
    - standard: 4 core rules (firewall, defender, bitlocker, ntp)
    - full_coverage: All 21 L1 rules for comprehensive auto-healing

    Writes an admin_audit_log entry (action=HEALING_TIER_CHANGED) for every
    real transition so the site activity timeline can display it.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Capture the old value so the diff in the audit log is real.
        before = await conn.fetchrow(
            "SELECT healing_tier FROM sites WHERE site_id = $1",
            site_id,
        )
        if not before:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        result = await conn.fetchrow("""
            UPDATE sites
            SET healing_tier = $1, updated_at = $2
            WHERE site_id = $3
            RETURNING site_id, clinic_name, healing_tier
        """, update.healing_tier.value, datetime.now(timezone.utc), site_id)

        if not result:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        old_tier = before["healing_tier"]
        new_tier = result["healing_tier"]
        if old_tier != new_tier:
            await _audit_site_change(
                conn,
                user,
                action="HEALING_TIER_CHANGED",
                site_id=site_id,
                details={"from": old_tier, "to": new_tier},
                request=request,
            )

    logger.info(
        f"Healing tier for {site_id}: {old_tier} → {new_tier} "
        f"by {user.get('username') or user.get('email') or 'unknown'}"
    )

    return {
        "status": "updated",
        "site_id": result["site_id"],
        "clinic_name": result["clinic_name"],
        "healing_tier": result["healing_tier"]
    }


@router.get("/{site_id}/healing-tier")
async def get_healing_tier(site_id: str, user: dict = Depends(require_auth)):
    """Get the current healing tier for a site. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.fetchrow("""
            SELECT site_id, clinic_name, healing_tier
            FROM sites
            WHERE site_id = $1
        """, site_id)

        if not result:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    return {
        "site_id": result["site_id"],
        "clinic_name": result["clinic_name"],
        "healing_tier": result["healing_tier"] or "standard"
    }


# =============================================================================
# ORDER ENDPOINTS
# =============================================================================

@router.post("/{site_id}/appliances/{appliance_id}/orders")
async def create_appliance_order(
    site_id: str,
    appliance_id: str,
    order: OrderCreate,
    user: dict = Depends(require_operator),
):
    """Create an order for a specific appliance. Requires operator or admin access."""
    pool = await get_pool()

    order_id = generate_order_id()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=1)

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify appliance exists (BUG 1 round-table 2026-05-01:
        # AND deleted_at IS NULL — soft-deleted appliances must not
        # accept new orders; was missing → /sites/{id}/orders fired
        # for a soft-deleted row would 500 the agent).
        appliance = await conn.fetchrow("""
            SELECT appliance_id, site_id
            FROM site_appliances
            WHERE appliance_id = $1 AND site_id = $2
              AND deleted_at IS NULL
        """, appliance_id, site_id)

        if not appliance:
            raise HTTPException(
                status_code=404,
                detail=f"Appliance {appliance_id} not found in site {site_id}"
            )

        # Sign the order for appliance-side verification (host-scoped)
        nonce, signature, signed_payload = sign_admin_order(
            order_id, order.order_type.value, order.parameters, now, expires_at,
            target_appliance_id=appliance_id,
        )

        # Insert order - cast json string to jsonb
        await conn.execute("""
            INSERT INTO admin_orders (
                order_id, appliance_id, site_id, order_type,
                parameters, priority, status, created_at, expires_at,
                nonce, signature, signed_payload
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
        """,
            order_id,
            appliance_id,
            site_id,
            order.order_type.value,
            json.dumps(order.parameters),
            order.priority,
            'pending',
            now,
            expires_at,
            nonce,
            signature,
            signed_payload,
        )

    return {
        "order_id": order_id,
        "appliance_id": appliance_id,
        "site_id": site_id,
        "order_type": order.order_type.value,
        "parameters": order.parameters,
        "priority": order.priority,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


@router.get("/{site_id}/appliances/{appliance_id}/orders/pending")
async def get_pending_orders(site_id: str, appliance_id: str, auth_site_id: str = Depends(require_appliance_bearer)):
    """Get pending orders for an appliance (both admin and healing orders)."""
    # Enforce Bearer site matches path site_id
    if site_id != auth_site_id:
        raise HTTPException(status_code=403, detail="Site ID mismatch: token does not authorize this site")
    pool = await get_pool()

    # Legacy compat: try both colon and hyphen MAC variants in appliance_id.
    # Canonical format is colon-separated (site-AA:BB:CC:DD:EE:FF) but old
    # provisioning code wrote hyphen-separated. Keep both until old orders age out.
    parts = appliance_id.rsplit('-', 6)  # Split off MAC (last 6 parts if hyphen-separated)
    if len(parts) >= 7:
        # MAC is hyphen-separated: site-name-08-00-27-98-FD-84
        site_prefix = '-'.join(parts[:-6])
        mac_parts = parts[-6:]
        mac_colon = ':'.join(mac_parts)
        mac_hyphen = '-'.join(mac_parts)
        appliance_id_colon = f"{site_prefix}-{mac_colon}"
        appliance_id_hyphen = f"{site_prefix}-{mac_hyphen}"
    else:
        # Try splitting by colon in MAC portion
        appliance_id_colon = appliance_id
        # Convert colons to hyphens for alternate format
        appliance_id_hyphen = appliance_id.replace(':', '-')

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Expire stale orders in both tables (piggyback on polling)
        try:
            await conn.execute("""
                UPDATE admin_orders SET status = 'expired'
                WHERE status = 'pending' AND expires_at < NOW()
            """)
            await conn.execute("""
                UPDATE orders SET status = 'expired'
                WHERE status = 'pending' AND expires_at < NOW()
            """)
        except Exception as e:
            logger.warning(f"Order expiration cleanup failed: {e}")

        # Get admin orders
        rows = await conn.fetch("""
            SELECT order_id, order_type, parameters, priority,
                   created_at, expires_at
            FROM admin_orders
            WHERE (appliance_id = $1 OR appliance_id = $2) AND site_id = $3
            AND status = 'pending'
            AND expires_at > NOW()
            ORDER BY priority DESC, created_at ASC
        """, appliance_id_colon, appliance_id_hyphen, site_id)

        orders = [
            {
                "order_id": row["order_id"],
                "order_type": row["order_type"],
                "parameters": parse_parameters(row["parameters"]),
                "priority": row["priority"],
                "created_at": row["created_at"].isoformat(),
                "expires_at": row["expires_at"].isoformat(),
            }
            for row in rows
        ]

        # Also get healing orders from orders table
        try:
            healing_rows = await conn.fetch("""
                SELECT o.order_id, o.runbook_id, o.parameters, o.issued_at, o.expires_at,
                       i.id as incident_id
                FROM orders o
                JOIN v_appliances_current a ON o.appliance_id = a.id
                LEFT JOIN incidents i ON i.order_id = o.id
                WHERE a.site_id = $1
                AND o.status = 'pending'
                AND o.expires_at > NOW()
                ORDER BY o.issued_at ASC
            """, site_id)

            for row in healing_rows:
                params = row["parameters"] if isinstance(row["parameters"], dict) else {}
                params["incident_id"] = str(row["incident_id"]) if row["incident_id"] else None
                orders.append({
                    "order_id": row["order_id"],
                    "order_type": "healing",
                    "runbook_id": row["runbook_id"],
                    "parameters": params,
                    "priority": 10,
                    "created_at": row["issued_at"].isoformat() if row["issued_at"] else None,
                    "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                })
        except Exception as e:
            import logging
            logging.warning(f"Failed to fetch healing orders: {e}")

        return {
            "site_id": site_id,
            "appliance_id": appliance_id,
            "orders": orders,
            "count": len(orders)
        }


# =============================================================================
# SITE LISTING ENDPOINTS
# =============================================================================

def calculate_live_status(last_checkin, auth_failure_count=0):
    """Calculate live status based on last checkin time and auth state."""
    if last_checkin is None:
        return 'pending'
    now = datetime.now(timezone.utc)
    age = now - last_checkin
    # Auth failures take priority — appliance is reaching us but can't authenticate
    if auth_failure_count and auth_failure_count >= 3 and age >= timedelta(minutes=5):
        return 'auth_failed'
    if age < timedelta(minutes=15):
        return 'online'
    elif age < timedelta(hours=1):
        return 'stale'
    else:
        return 'offline'


@router.get("")
async def list_sites(
    status: Optional[str] = Query(None, regex="^(online|offline|stale|pending)$"),
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("clinic_name", regex="^(clinic_name|site_id|last_checkin|tier|onboarding_stage|appliance_count|org_name)$"),
    sort_dir: str = Query("asc", regex="^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_auth),
):
    """List all sites with aggregated appliance data, server-side pagination/search/sort."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        org_scope = user.get("org_scope")
        base_query = """
            SELECT
                sa.site_id,
                COUNT(*) as appliance_count,
                MAX(sa.last_checkin) as last_checkin,
                MIN(sa.first_checkin) as created_at,
                MAX(sa.last_checkin) as updated_at,
                array_agg(DISTINCT COALESCE(sa.status, 'pending')) as statuses,
                COALESCE(MAX(sa.auth_failure_count), 0) as auth_failure_count,
                s.clinic_name,
                s.tier,
                s.onboarding_stage,
                s.client_org_id,
                co.name as org_name
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            LEFT JOIN client_orgs co ON co.id = s.client_org_id
        """
        # BUG 1 round-table 2026-05-01: filter soft-deleted appliances.
        # FROM is site_appliances (NOT a LEFT JOIN), so a site whose
        # appliances are ALL soft-deleted will disappear from the fleet
        # list. Acceptable + desired — fully-deleted sites should not
        # appear in operator UI. Site row stays in `sites` table for
        # forensic access via the dedicated /api/sites/{id} endpoint.
        where_clauses = [
            "COALESCE(s.status, 'pending') != 'inactive'",
            "sa.deleted_at IS NULL",
        ]
        args = []
        arg_idx = 1

        if org_scope is not None:
            where_clauses.append(f"s.client_org_id = ANY(${arg_idx}::uuid[])")
            args.append(org_scope)
            arg_idx += 1

        if search:
            where_clauses.append(f"(s.clinic_name ILIKE ${arg_idx} OR sa.site_id ILIKE ${arg_idx} OR co.name ILIKE ${arg_idx})")
            args.append(f"%{search}%")
            arg_idx += 1

        if where_clauses:
            base_query += " WHERE " + " AND ".join(where_clauses)

        base_query += """
            GROUP BY sa.site_id, s.clinic_name, s.tier, s.onboarding_stage,
                     s.client_org_id, co.name
        """
        rows = await conn.fetch(base_query, *args)

        # Build site objects and compute live_status in Python (depends on time calc)
        all_sites = []
        status_counts = {"online": 0, "stale": 0, "offline": 0, "pending": 0, "auth_failed": 0}
        for row in rows:
            last_checkin = row['last_checkin']
            live_status = calculate_live_status(last_checkin, row.get('auth_failure_count', 0))
            status_counts[live_status] = status_counts.get(live_status, 0) + 1

            # Filter by status if provided
            if status and live_status != status:
                continue

            statuses = row['statuses'] or []
            if 'online' in statuses:
                overall_status = 'online'
            elif 'stale' in statuses:
                overall_status = 'stale'
            else:
                overall_status = 'offline'

            clinic_name = row['clinic_name'] or row['site_id'].replace('-', ' ').title()

            all_sites.append({
                'site_id': row['site_id'],
                'clinic_name': clinic_name,
                'contact_name': None,
                'contact_email': None,
                'tier': row['tier'] or 'standard',
                'status': overall_status,
                'live_status': live_status,
                'onboarding_stage': row['onboarding_stage'] or 'active',
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'appliance_count': row['appliance_count'],
                'client_org_id': str(row['client_org_id']) if row['client_org_id'] else None,
                'org_name': row['org_name'],
            })

        # Sort
        reverse = sort_dir == 'desc'
        def sort_key(s):
            val = s.get(sort_by)
            if val is None:
                return '' if isinstance(s.get('clinic_name'), str) else 0
            return val
        all_sites.sort(key=sort_key, reverse=reverse)

        total = len(all_sites)
        paginated = all_sites[offset:offset + limit]

        return {
            'sites': paginated,
            'count': len(paginated),
            'total': total,
            'limit': limit,
            'offset': offset,
            'stats': status_counts,
        }


@router.get("/{site_id}")
async def get_site(site_id: str, user: dict = Depends(require_auth)):
    """Get details for a specific site. Requires authentication."""
    pool = await get_pool()
    
    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get appliances for this site (BUG 1 round-table 2026-05-01:
        # AND deleted_at IS NULL — was missing, so soft-deleted
        # rows leaked into the SiteDetail UI; user clicked Delete
        # on `osiriscare-installer` row, DELETE handler correctly
        # rejected with 404 because its filter had the predicate.
        # This was the BUG 1 root cause).
        appliance_rows = await conn.fetch("""
            SELECT
                appliance_id, hostname, display_name, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds,
                COALESCE(auth_failure_count, 0) as auth_failure_count,
                COALESCE(jsonb_array_length(assigned_targets), 0) as assigned_target_count
            FROM site_appliances
            WHERE site_id = $1
              AND deleted_at IS NULL
            ORDER BY first_checkin, appliance_id
        """, site_id)
        
        # Verify site exists even if no appliances yet
        if not appliance_rows:
            site_exists = await conn.fetchval(
                "SELECT 1 FROM sites WHERE site_id = $1", site_id
            )
            if not site_exists:
                raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

        # Process appliances
        appliances = []
        latest_checkin = None
        earliest_checkin = None
        
        for row in appliance_rows:
            last_checkin = row['last_checkin']
            first_checkin = row['first_checkin']
            
            # Track overall times
            if last_checkin:
                if latest_checkin is None or last_checkin > latest_checkin:
                    latest_checkin = last_checkin
            if first_checkin:
                if earliest_checkin is None or first_checkin < earliest_checkin:
                    earliest_checkin = first_checkin
            
            live_status = calculate_live_status(last_checkin, row.get('auth_failure_count', 0))

            appliances.append({
                'appliance_id': row['appliance_id'],
                'hostname': row['hostname'],
                'display_name': row.get('display_name'),
                'mac_address': row['mac_address'],
                'ip_addresses': parse_ip_addresses(row['ip_addresses']),
                'agent_version': row['agent_version'],
                'nixos_version': row['nixos_version'],
                # F3 (Phase 15 closing): `status` is the source of truth
                # for UI — live_status is computed fresh from last_checkin
                # so it's never stale. Stored DB status (updated by the
                # mark_stale_appliances_loop every 2 min) is exposed as
                # `stored_status` for admin/diagnostic use only.
                'status': live_status,
                'stored_status': row['status'] or 'pending',
                'live_status': live_status,
                'first_checkin': first_checkin.isoformat() if first_checkin else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'uptime_seconds': row['uptime_seconds'],
                'assigned_target_count': row.get('assigned_target_count', 0) or 0,
            })

        # Determine overall site live status (worst case from appliances)
        max_auth_failures = max((a.get('auth_failure_count', 0) for a in appliance_rows), default=0) if appliance_rows else 0
        live_status = calculate_live_status(latest_checkin, max_auth_failures)

        # Fetch credentials (without exposing passwords)
        cred_rows = await conn.fetch("""
            SELECT id, credential_type, credential_name, encrypted_data, created_at
            FROM site_credentials
            WHERE site_id = $1
            ORDER BY created_at DESC
        """, site_id)

        credentials = []
        stale_credentials_count = 0
        for cred in cred_rows:
            try:
                cred_data = json.loads(decrypt_credential(cred['encrypted_data'])) if cred['encrypted_data'] else {}
                # Flag credentials older than 90 days as stale
                is_stale = False
                if cred['created_at']:
                    age_days = (datetime.now(timezone.utc) - cred['created_at'].replace(tzinfo=timezone.utc)).days
                    if age_days > 90:
                        is_stale = True
                        stale_credentials_count += 1
                credentials.append({
                    'id': str(cred['id']),
                    'credential_type': cred['credential_type'],
                    'credential_name': cred['credential_name'],
                    'host': cred_data.get('host', ''),
                    'username': cred_data.get('username', ''),
                    'domain': cred_data.get('domain', ''),
                    'created_at': cred['created_at'].isoformat() if cred['created_at'] else None,
                    'is_stale': is_stale,
                    # NOTE: password intentionally NOT returned
                })
            except (json.JSONDecodeError, TypeError):
                pass

        # Determine status from appliances
        statuses = [a['status'] for a in appliances]
        if 'online' in statuses:
            overall_status = 'online'
        elif 'stale' in statuses:
            overall_status = 'stale'
        else:
            overall_status = 'offline'

        # Get site metadata from sites table with org info
        site_row = await conn.fetchrow("""
            SELECT s.clinic_name, s.contact_name, s.contact_email, s.contact_phone,
                   s.address, s.notes, s.tier, s.onboarding_stage, s.healing_tier,
                   s.client_org_id, co.name as org_name,
                   s.wg_ip, s.wg_connected_at,
                   s.maintenance_until, s.maintenance_reason, s.maintenance_set_by
            FROM sites s
            LEFT JOIN client_orgs co ON co.id = s.client_org_id
            WHERE s.site_id = $1
        """, site_id)

        # Human-readable name (from sites table or derived)
        clinic_name = site_row['clinic_name'] if site_row and site_row['clinic_name'] else site_id.replace('-', ' ').title()

        # Credential scan status — per-credential last-scan check for onboarding health gate
        credential_scan_status = await conn.fetch("""
            SELECT sc.credential_name, sc.credential_type, sc.sensor_deployed,
                   (SELECT MAX(created_at) FROM incidents
                    WHERE site_id = $1
                      AND details->>'hostname' ILIKE '%' || sc.credential_name || '%'
                      AND created_at > NOW() - INTERVAL '7 days'
                   ) as last_scan_at
            FROM site_credentials sc
            WHERE sc.site_id = $1
        """, site_id)

        credential_health = [
            {
                "name": row["credential_name"],
                "type": row["credential_type"],
                "sensor_deployed": row["sensor_deployed"],
                "last_scan_at": row["last_scan_at"].isoformat() if row["last_scan_at"] else None,
                "status": "healthy" if row["last_scan_at"] else "not_scanned",
            }
            for row in credential_scan_status
        ]

        return {
            'site_id': site_id,
            'clinic_name': clinic_name,
            'contact_name': site_row['contact_name'] if site_row else None,
            'contact_email': site_row['contact_email'] if site_row else None,
            'contact_phone': site_row['contact_phone'] if site_row else None,
            'address': site_row['address'] if site_row else None,
            'provider_count': None,
            'ehr_system': None,
            'notes': site_row['notes'] if site_row else None,
            'blockers': [],
            'tracking_number': None,
            'tracking_carrier': None,
            'tier': site_row['tier'] if site_row and site_row['tier'] else 'standard',
            'healing_tier': site_row['healing_tier'] if site_row and site_row['healing_tier'] else 'standard',
            'status': overall_status,
            'live_status': live_status,
            'onboarding_stage': site_row['onboarding_stage'] if site_row and site_row['onboarding_stage'] else 'active',
            'created_at': earliest_checkin.isoformat() if earliest_checkin else None,
            'updated_at': latest_checkin.isoformat() if latest_checkin else None,
            'last_checkin': latest_checkin.isoformat() if latest_checkin else None,
            'appliance_count': len(appliances),
            'timestamps': {
                'lead_at': None,
                'discovery_at': None,
                'proposal_at': None,
                'contract_at': None,
                'intake_at': None,
                'creds_at': None,
                'shipped_at': None,
                'received_at': None,
                'connectivity_at': earliest_checkin.isoformat() if earliest_checkin else None,
                'scanning_at': None,
                'baseline_at': None,
                'active_at': earliest_checkin.isoformat() if earliest_checkin else None,
            },
            'client_org_id': str(site_row['client_org_id']) if site_row and site_row['client_org_id'] else None,
            'org_name': site_row['org_name'] if site_row else None,
            'wg_ip': site_row['wg_ip'] if site_row else None,
            'wg_connected_at': site_row['wg_connected_at'].isoformat() if site_row and site_row['wg_connected_at'] else None,
            'maintenance_until': site_row['maintenance_until'].isoformat() if site_row and site_row['maintenance_until'] and site_row['maintenance_until'] > datetime.now(timezone.utc) else None,
            'maintenance_reason': site_row['maintenance_reason'] if site_row and site_row['maintenance_until'] and site_row['maintenance_until'] > datetime.now(timezone.utc) else None,
            'maintenance_set_by': site_row['maintenance_set_by'] if site_row and site_row['maintenance_until'] and site_row['maintenance_until'] > datetime.now(timezone.utc) else None,
            'appliances': appliances,
            'credentials': credentials,
            'stale_credentials_count': stale_credentials_count,
            'credential_health': credential_health,
        }


# =============================================================================
# CREDENTIAL MANAGEMENT
# =============================================================================

class CredentialCreate(BaseModel):
    credential_type: str  # domain_admin, local_admin, winrm, service_account, ssh_password, ssh_key
    credential_name: str  # Human-readable name like "North Valley DC"
    host: str             # Target hostname/IP
    username: str
    password: str = ""
    domain: Optional[str] = None
    use_ssl: Optional[bool] = False
    port: Optional[int] = 22
    private_key: Optional[str] = None  # For ssh_key credential type
    distro: Optional[str] = None       # ubuntu, centos, macos, etc.


@router.post("/{site_id}/credentials")
async def add_credential(site_id: str, cred: CredentialCreate, user: dict = Depends(require_operator)):
    """Add a credential for a site. Appliances will pull this on next check-in. Requires operator or admin access."""
    pool = await get_pool()

    # Build credential data as JSON
    cred_data_dict = {
        'host': cred.host,
        'username': cred.username,
        'password': cred.password,
        'domain': cred.domain or '',
        'use_ssl': cred.use_ssl or False,
        'port': cred.port or 22,
    }
    if cred.private_key:
        cred_data_dict['private_key'] = cred.private_key
    if cred.distro:
        cred_data_dict['distro'] = cred.distro
    cred_data = json.dumps(cred_data_dict)

    async with tenant_connection(pool, site_id=site_id) as conn:
        try:
            row = await conn.fetchrow("""
                INSERT INTO site_credentials (site_id, credential_type, credential_name, encrypted_data)
                VALUES ($1, $2, $3, $4)
                RETURNING id, created_at
            """, site_id, cred.credential_type, cred.credential_name, encrypt_credential(cred_data))

            return {
                'id': str(row['id']),
                'credential_type': cred.credential_type,
                'credential_name': cred.credential_name,
                'host': cred.host,
                'username': cred.username,
                'domain': cred.domain or '',
                'created_at': row['created_at'].isoformat(),
                'message': 'Credential added. Appliances will receive it on next check-in.',
            }
        except Exception as e:
            logger.error(f"Failed to add credential: {e}")
            raise HTTPException(status_code=400, detail="Failed to add credential. Please check your input.")


@router.delete("/{site_id}/credentials/{credential_id}")
async def delete_credential(site_id: str, credential_id: str, user: dict = Depends(require_operator)):
    """Delete a credential. Appliances will stop receiving it on next check-in. Requires operator or admin access."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.execute("""
            DELETE FROM site_credentials
            WHERE site_id = $1 AND id = $2
        """, site_id, credential_id)

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Credential not found")

        return {'message': 'Credential deleted. Appliances will stop receiving it on next check-in.'}


# =============================================================================
# MANUAL DEVICE JOIN (Non-AD devices)
# =============================================================================

class ManualDeviceAdd(BaseModel):
    hostname: str           # Hostname or IP to connect to
    ip_address: str         # IP address for device inventory
    device_type: str = "workstation"  # workstation, server
    os_type: str = "linux"  # linux, macos
    ssh_username: str = "root"
    ssh_password: Optional[str] = None
    ssh_key: Optional[str] = None
    port: int = 22
    distro: Optional[str] = None  # ubuntu, centos, macos, etc.


class NetworkDeviceAdd(BaseModel):
    hostname: str               # Display name (e.g. "Core Switch 1")
    ip_address: str             # Management IP
    device_category: str = "switch"  # switch, router, firewall, access_point, other
    vendor: Optional[str] = None     # cisco, ubiquiti, aruba, juniper, etc.
    model: Optional[str] = None
    # Management access (read-only by default)
    mgmt_protocol: str = "snmp"     # snmp, ssh, api
    # SNMP fields
    snmp_community: Optional[str] = None   # SNMPv2c community string
    snmp_version: str = "2c"               # 2c, 3
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth_pass: Optional[str] = None
    snmp_v3_priv_pass: Optional[str] = None
    # SSH fields (for CLI-based read-only checks)
    ssh_username: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key: Optional[str] = None
    ssh_port: int = 22
    # API fields (REST/HTTP)
    api_url: Optional[str] = None
    api_token: Optional[str] = None
    # Policy
    remediation_mode: str = "advisory"  # advisory (L2 suggests, human executes) or disabled


async def _add_manual_device(pool, site_id: str, device: ManualDeviceAdd) -> dict:
    """Shared logic for adding a non-AD device via SSH credentials.
    Used by both admin and portal endpoints."""
    if not device.ssh_password and not device.ssh_key:
        raise HTTPException(status_code=400, detail="Either ssh_password or ssh_key is required.")

    cred_type = "ssh_key" if device.ssh_key else "ssh_password"
    cred_name = f"{device.hostname} ({device.os_type})"

    cred_data_dict = {
        'host': device.hostname,
        'target_host': device.ip_address,
        'username': device.ssh_username,
        'password': device.ssh_password or '',
        'port': device.port,
    }
    if device.ssh_key:
        cred_data_dict['private_key'] = device.ssh_key
    if device.distro:
        cred_data_dict['distro'] = device.distro
    # Set label so the daemon routes to the correct scan engine
    # (macOS targets use macosScanScript instead of linuxScanScript)
    if device.os_type == 'macos':
        cred_data_dict['label'] = 'macos'

    cred_data = json.dumps(cred_data_dict)

    async with tenant_connection(pool, site_id=site_id) as conn:
        async with conn.transaction():
            # Create SSH credential
            row = await conn.fetchrow("""
                INSERT INTO site_credentials (site_id, credential_type, credential_name, encrypted_data)
                VALUES ($1, $2, $3, $4)
                RETURNING id, created_at
            """, site_id, cred_type, cred_name, encrypt_credential(cred_data))

            credential_id = str(row['id'])

            # Upsert into discovered_devices for inventory visibility.
            # appliance_id is a UUID — prefer legacy_uuid (matches pre-M1 rows)
            # and fall back to the site_appliances.id PK for new appliances.
            await conn.execute("""
                INSERT INTO discovered_devices (
                    appliance_id, site_id, local_device_id, hostname, ip_address,
                    device_type, os_name, discovery_source, compliance_status,
                    first_seen_at, last_seen_at
                )
                SELECT
                    COALESCE(sa.legacy_uuid, sa.id), $1, $2, $3, $4,
                    $5, $6, 'manual', 'unknown',
                    NOW(), NOW()
                FROM site_appliances sa
                WHERE sa.site_id = $1 AND sa.deleted_at IS NULL
                LIMIT 1
                ON CONFLICT (appliance_id, local_device_id) DO UPDATE
                SET hostname = EXCLUDED.hostname, ip_address = EXCLUDED.ip_address,
                    last_seen_at = NOW()
            """, site_id, f"manual-{device.ip_address}", device.hostname,
                device.ip_address, device.device_type, device.os_type)

    return {
        'credential_id': credential_id,
        'credential_type': cred_type,
        'hostname': device.hostname,
        'ip_address': device.ip_address,
        'created_at': row['created_at'].isoformat(),
        'message': f'Device {device.hostname} added. Appliance will begin monitoring on next check-in.',
    }


@router.post("/{site_id}/devices/manual")
async def add_manual_device(site_id: str, device: ManualDeviceAdd, user: dict = Depends(require_operator)):
    """Register a non-AD device for SSH-based compliance monitoring."""
    pool = await get_pool()
    return await _add_manual_device(pool, site_id, device)


@router.post("/{site_id}/devices/takeover")
async def take_over_device(
    site_id: str,
    device: ManualDeviceAdd,
    user: dict = Depends(require_operator),
):
    """Save credentials and trigger agent deployment for a discovered device."""
    pool = await get_pool()
    # Reuse existing credential + device creation logic
    result = await _add_manual_device(pool, site_id, device)

    # Set device_status to pending_deploy on the discovered device
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE discovered_devices
            SET device_status = 'pending_deploy'
            WHERE site_id = $1 AND (
                ip_address = $2 OR hostname = $3
            ) AND device_status NOT IN ('agent_active', 'deploying', 'ignored')
        """, site_id, device.ip_address, device.hostname)

    return result


async def _add_network_device(pool, site_id: str, device: NetworkDeviceAdd) -> dict:
    """Register a network device (switch/router/AP/firewall) for read-only monitoring.
    Credentials stay server-side. Daemon gets detection-only config."""

    # Build credential data based on management protocol
    cred_data_dict: dict = {
        'device_category': device.device_category,
        'vendor': device.vendor or '',
        'model': device.model or '',
        'mgmt_protocol': device.mgmt_protocol,
        'management_ip': device.ip_address,
        'remediation_mode': device.remediation_mode,
    }

    if device.mgmt_protocol == 'snmp':
        if not device.snmp_community and device.snmp_version == '2c':
            raise HTTPException(status_code=400, detail="SNMP community string required for v2c.")
        if device.snmp_version == '3' and not device.snmp_v3_user:
            raise HTTPException(status_code=400, detail="SNMPv3 username required.")
        cred_data_dict['snmp_version'] = device.snmp_version
        if device.snmp_community:
            cred_data_dict['snmp_community'] = device.snmp_community
        if device.snmp_v3_user:
            cred_data_dict['snmp_v3_user'] = device.snmp_v3_user
        if device.snmp_v3_auth_pass:
            cred_data_dict['snmp_v3_auth_pass'] = device.snmp_v3_auth_pass
        if device.snmp_v3_priv_pass:
            cred_data_dict['snmp_v3_priv_pass'] = device.snmp_v3_priv_pass
        cred_type = 'network_snmp'
    elif device.mgmt_protocol == 'ssh':
        if not device.ssh_password and not device.ssh_key:
            raise HTTPException(status_code=400, detail="SSH password or key required.")
        cred_data_dict['username'] = device.ssh_username or 'admin'
        cred_data_dict['port'] = device.ssh_port
        if device.ssh_password:
            cred_data_dict['password'] = device.ssh_password
        if device.ssh_key:
            cred_data_dict['private_key'] = device.ssh_key
        cred_type = 'network_ssh'
    elif device.mgmt_protocol == 'api':
        if not device.api_url:
            raise HTTPException(status_code=400, detail="API URL required.")
        cred_data_dict['api_url'] = device.api_url
        if device.api_token:
            cred_data_dict['api_token'] = device.api_token
        cred_type = 'network_api'
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported protocol: {device.mgmt_protocol}")

    cred_name = f"{device.hostname} ({device.device_category})"
    cred_data = json.dumps(cred_data_dict)

    async with tenant_connection(pool, site_id=site_id) as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""
                INSERT INTO site_credentials (site_id, credential_type, credential_name, encrypted_data)
                VALUES ($1, $2, $3, $4)
                RETURNING id, created_at
            """, site_id, cred_type, cred_name, encrypt_credential(cred_data))

            credential_id = str(row['id'])

            # Register in discovered_devices with device_type = 'network'.
            # Prefer legacy_uuid to match existing FK/ON CONFLICT semantics,
            # fall back to site_appliances.id for new appliances post-M1.
            await conn.execute("""
                INSERT INTO discovered_devices (
                    appliance_id, site_id, local_device_id, hostname, ip_address,
                    device_type, os_name, discovery_source, compliance_status,
                    first_seen_at, last_seen_at
                )
                SELECT
                    COALESCE(sa.legacy_uuid, sa.id), $1, $2, $3, $4,
                    'network', $5, 'manual', 'unknown',
                    NOW(), NOW()
                FROM site_appliances sa
                WHERE sa.site_id = $1 AND sa.deleted_at IS NULL
                LIMIT 1
                ON CONFLICT (appliance_id, local_device_id) DO UPDATE
                SET hostname = EXCLUDED.hostname, ip_address = EXCLUDED.ip_address,
                    last_seen_at = NOW()
            """, site_id, f"network-{device.ip_address}", device.hostname,
                device.ip_address, device.device_category)

    return {
        'credential_id': credential_id,
        'credential_type': cred_type,
        'hostname': device.hostname,
        'ip_address': device.ip_address,
        'device_category': device.device_category,
        'mgmt_protocol': device.mgmt_protocol,
        'remediation_mode': device.remediation_mode,
        'created_at': row['created_at'].isoformat(),
        'message': f'Network device {device.hostname} registered. Detection-only monitoring will begin on next check-in.',
    }


@router.post("/{site_id}/devices/network")
async def add_network_device(site_id: str, device: NetworkDeviceAdd, user: dict = Depends(require_operator)):
    """Register a network device (switch/router/AP/firewall) for monitoring.
    Network devices are NEVER auto-remediated. L2 provides advisory commands only."""
    pool = await get_pool()
    return await _add_network_device(pool, site_id, device)


@router.get("/{site_id}/appliances")
def _primary_subnet(ips: list) -> Optional[str]:
    """Extract the /24 subnet an appliance is on, ignoring link-local (169.254)
    and WireGuard (10.100.0) — those are well-known non-routable addresses,
    not the LAN subnet we care about for drift detection."""
    for ip in ips or []:
        if not ip or ip.startswith('169.254') or ip.startswith('10.100.0'):
            continue
        parts = ip.split('.')
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}"
    return None


ANYCAST_LINK_LOCAL = "169.254.88.1"


def _compute_network_anomaly(
    ips: list,
    live_status: str,
    site_subnet_majority: Optional[str],
) -> dict:
    """Detect APPLIANCE_NETWORK_ANOMALY conditions:
      - subnet_drift: this appliance's LAN subnet doesn't match site majority
      - missing_anycast: 169.254.88.1 not assigned (mesh discovery fallback)

    Only flags on online appliances — offline appliances often have stale
    ip_addresses and shouldn't trigger false positives.
    """
    ip_list = ips or []
    subnet = _primary_subnet(ip_list)
    missing_anycast = (
        live_status == 'online'
        and ANYCAST_LINK_LOCAL not in ip_list
    )
    subnet_drift = bool(
        live_status == 'online'
        and subnet
        and site_subnet_majority
        and subnet != site_subnet_majority
    )
    notes: list = []
    if subnet_drift:
        notes.append(f"On {subnet}.x, site majority is {site_subnet_majority}.x")
    if missing_anycast:
        notes.append("Link-local anycast 169.254.88.1 not assigned — mesh discovery degraded")
    return {
        'subnet_drift': subnet_drift,
        'missing_anycast': missing_anycast,
        'observed_subnet': subnet,
        'expected_subnet': site_subnet_majority,
        'notes': notes,
    }


async def get_site_appliances(site_id: str, user: dict = Depends(require_auth)):
    """Get all appliances for a site. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        rows = await conn.fetch("""
            SELECT
                appliance_id, hostname, display_name, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds,
                COALESCE(l2_mode, 'auto') as l2_mode,
                offline_since,
                COALESCE(auth_failure_count, 0) as auth_failure_count,
                daemon_health,
                assigned_targets
            FROM site_appliances
            WHERE site_id = $1
              AND deleted_at IS NULL
            ORDER BY first_checkin, appliance_id
        """, site_id)

        # Pre-compute site subnet majority for drift detection.
        # Look at online appliances only so a single offline outlier doesn't
        # skew the majority on small fleets.
        _subnet_votes: Dict[str, int] = {}
        for _r in rows:
            if calculate_live_status(_r['last_checkin'], _r.get('auth_failure_count', 0)) != 'online':
                continue
            _ip_list = parse_ip_addresses(_r['ip_addresses'])
            _s = _primary_subnet(_ip_list)
            if _s:
                _subnet_votes[_s] = _subnet_votes.get(_s, 0) + 1
        _site_subnet_majority = (
            max(_subnet_votes.items(), key=lambda x: x[1])[0]
            if _subnet_votes else None
        )

        appliances = []
        for row in rows:
            last_checkin = row['last_checkin']
            live_status = calculate_live_status(last_checkin, row.get('auth_failure_count', 0))

            # Extract mesh stats from daemon_health JSONB
            dh = row.get('daemon_health')
            if isinstance(dh, str):
                try:
                    dh = json.loads(dh)
                except (json.JSONDecodeError, TypeError):
                    dh = None
            mesh_peer_count = dh.get('mesh_peer_count', 0) if dh else 0
            mesh_ring_size = dh.get('mesh_ring_size', 0) if dh else 0
            mesh_peer_macs = dh.get('mesh_peer_macs', []) if dh else []

            _ips = parse_ip_addresses(row['ip_addresses'])
            network_anomaly = _compute_network_anomaly(_ips, live_status, _site_subnet_majority)

            appliances.append({
                'appliance_id': row['appliance_id'],
                'hostname': row['hostname'],
                'display_name': row.get('display_name'),
                'mac_address': row['mac_address'],
                'ip_addresses': parse_ip_addresses(row['ip_addresses']),
                'agent_version': row['agent_version'],
                'nixos_version': row['nixos_version'],
                # F3 (Phase 15 closing): `status` is the source of truth
                # for UI — live_status is computed fresh from last_checkin
                # so it's never stale. Stored DB status (updated by the
                # mark_stale_appliances_loop every 2 min) is exposed as
                # `stored_status` for admin/diagnostic use only.
                'status': live_status,
                'stored_status': row['status'] or 'pending',
                'live_status': live_status,
                'first_checkin': row['first_checkin'].isoformat() if row['first_checkin'] else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'uptime_seconds': row['uptime_seconds'],
                'l2_mode': row['l2_mode'],
                'offline_since': row['offline_since'].isoformat() if row['offline_since'] else None,
                'mesh_peer_count': mesh_peer_count,
                'mesh_ring_size': mesh_ring_size,
                'mesh_peer_macs': mesh_peer_macs,
                'assigned_target_count': len(json.loads(row['assigned_targets'])) if row.get('assigned_targets') else 0,
                'network_anomaly': network_anomaly,
            })

        return {
            'site_id': site_id,
            'appliances': appliances,
            'count': len(appliances),
            'site_subnet_majority': _site_subnet_majority,
        }


@router.get("/{site_id}/activity")
async def get_site_activity(
    site_id: str,
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(require_auth),
):
    """Return the recent activity timeline for a site.

    Aggregates three sources:
      1) admin_audit_log — admin actions scoped to this site (target=site_id)
      2) fleet_orders — order lifecycle events
      3) incidents — opened/resolved events over the last 30 days

    Ordered newest-first, truncated to ``limit`` entries.

    Called by the Site Detail page activity sidebar.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # 1) Admin actions targeting this site
        admin_rows = await conn.fetch(
            """
            SELECT id::text AS id, created_at, username, action, target, details
            FROM admin_audit_log
            WHERE target = $1
              AND created_at > NOW() - INTERVAL '90 days'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            site_id,
            limit,
        )

        # 2) Fleet order events — orders completed by an appliance belonging
        #    to this site in the last 30 days. fleet_orders is global (no
        #    site_id column); we join through fleet_order_completions →
        #    site_appliances to get site-scoped activity.
        order_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (fo.id, foc.appliance_id)
                   fo.id, fo.order_type, fo.status,
                   fo.created_at, foc.completed_at, foc.appliance_id,
                   foc.status AS completion_status
            FROM fleet_orders fo
            JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
            JOIN site_appliances sa ON sa.appliance_id = foc.appliance_id
            WHERE sa.site_id = $1
              AND foc.completed_at > NOW() - INTERVAL '30 days'
            ORDER BY fo.id, foc.appliance_id, foc.completed_at DESC
            LIMIT $2
            """,
            site_id,
            limit,
        )

        # 3) Incident open/resolve over last 30 days — pull a bounded sample
        incident_rows = await conn.fetch(
            """
            SELECT id::text AS id, incident_type, severity, status,
                   created_at, resolved_at
            FROM incidents
            WHERE site_id = $1
              AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            site_id,
            limit,
        )

    # Normalize into a unified timeline shape
    events: List[Dict[str, Any]] = []
    for r in admin_rows:
        details = r["details"]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = None
        events.append({
            "kind": "admin_action",
            "event_id": f"audit-{r['id']}",
            "at": r["created_at"].isoformat() if r["created_at"] else None,
            "actor": r["username"],
            "action": r["action"],
            "details": details,
        })

    for r in order_rows:
        # fleet_order_completions is the source of truth for "this site's
        # appliance executed this order". One row per (order, appliance).
        events.append({
            "kind": "fleet_order",
            "event_id": f"order-{r['id']}-{r['appliance_id']}-completed",
            "at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "actor": r["appliance_id"],
            "action": "FLEET_ORDER_COMPLETED",
            "details": {
                "order_id": str(r["id"]),
                "order_type": r["order_type"],
                "order_status": r["status"],
                "completion_status": r["completion_status"],
                "appliance_id": r["appliance_id"],
            },
        })

    for r in incident_rows:
        events.append({
            "kind": "incident",
            "event_id": f"incident-{r['id']}-opened",
            "at": r["created_at"].isoformat() if r["created_at"] else None,
            "actor": "system",
            "action": "INCIDENT_OPENED",
            "details": {
                "incident_id": r["id"],
                "incident_type": r["incident_type"],
                "severity": r["severity"],
            },
        })
        if r["resolved_at"]:
            events.append({
                "kind": "incident",
                "event_id": f"incident-{r['id']}-resolved",
                "at": r["resolved_at"].isoformat(),
                "actor": "system",
                "action": "INCIDENT_RESOLVED",
                "details": {
                    "incident_id": r["id"],
                    "incident_type": r["incident_type"],
                    "severity": r["severity"],
                },
            })

    # Sort newest-first and truncate — `at` may be None for malformed rows
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    events = events[:limit]

    return {
        "site_id": site_id,
        "events": events,
        "count": len(events),
        "limit": limit,
    }


@router.get("/{site_id}/mesh")
async def get_mesh_state(site_id: str, user: dict = Depends(require_auth)):
    """Get comprehensive mesh state for admin dashboard panel.

    Returns:
    - ring_size, peer_count per appliance
    - ring agreement status (all appliances see same ring)
    - target assignments per appliance
    - coverage gaps (overlaps/orphans)
    - recent assignment audit history
    - mesh health score
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Current appliance state (BUG 1 round-table 2026-05-01:
        # AND deleted_at IS NULL — mesh state must not include
        # soft-deleted appliances; their assigned_targets are stale
        # and would skew the consistency checks).
        appliances = await conn.fetch("""
            SELECT appliance_id, display_name, hostname, mac_address,
                   status, last_checkin, daemon_health, assigned_targets,
                   assignment_epoch
            FROM site_appliances
            WHERE site_id = $1
              AND deleted_at IS NULL
            ORDER BY first_checkin ASC
        """, site_id)

        if not appliances:
            return {"site_id": site_id, "appliances": [], "mesh_active": False}

        # Parse mesh state from each appliance
        now = datetime.now(timezone.utc)
        appliance_list = []
        online_count = 0
        ring_sizes = []
        target_owners: dict = {}  # target_ip -> [appliance_ids]

        for row in appliances:
            last_checkin = row["last_checkin"]
            if last_checkin and last_checkin.tzinfo is None:
                last_checkin = last_checkin.replace(tzinfo=timezone.utc)
            online = last_checkin and (now - last_checkin).total_seconds() < 300

            dh = row["daemon_health"]
            if isinstance(dh, str):
                try:
                    dh = json.loads(dh)
                except (ValueError, TypeError):
                    dh = {}
            dh = dh or {}

            ring_size = dh.get("mesh_ring_size", 0)
            peer_count = dh.get("mesh_peer_count", 0)

            assigned = row["assigned_targets"]
            if isinstance(assigned, str):
                try:
                    assigned = json.loads(assigned)
                except (ValueError, TypeError):
                    assigned = []
            assigned = assigned or []

            if online:
                online_count += 1
                if ring_size > 0:
                    ring_sizes.append(ring_size)
                for t in assigned:
                    target_owners.setdefault(t, []).append(row["appliance_id"])

            appliance_list.append({
                "appliance_id": row["appliance_id"],
                "display_name": row["display_name"] or row["hostname"],
                "hostname": row["hostname"],
                "mac_address": row["mac_address"],
                "online": online,
                "ring_size": ring_size,
                "peer_count": peer_count,
                "target_count": len(assigned),
                "assigned_targets": assigned,
                "assignment_epoch": row["assignment_epoch"],
                "last_checkin": row["last_checkin"].isoformat() if row["last_checkin"] else None,
            })

        # Mesh health analysis
        ring_agreement = len(set(ring_sizes)) <= 1 if ring_sizes else True
        ring_drift = bool(ring_sizes) and max(ring_sizes) != online_count
        overlaps = [t for t, owners in target_owners.items() if len(owners) > 1]
        total_assigned = sum(len(owners) for owners in target_owners.values())
        unique_targets = len(target_owners)

        # Recent audit history (last 10 changes)
        audit_rows = await conn.fetch("""
            SELECT appliance_id, assignment_epoch, ring_size, target_count, created_at
            FROM mesh_assignment_audit
            WHERE site_id = $1
            ORDER BY created_at DESC
            LIMIT 10
        """, site_id)

        audit_history = [
            {
                "appliance_id": r["appliance_id"],
                "assignment_epoch": r["assignment_epoch"],
                "ring_size": r["ring_size"],
                "target_count": r["target_count"],
                "changed_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in audit_rows
        ]

        # Overall health score
        health_issues = []
        if not ring_agreement:
            health_issues.append("Appliances disagree on ring size")
        if ring_drift:
            health_issues.append(f"Ring size ({max(ring_sizes)}) != online count ({online_count})")
        if overlaps:
            health_issues.append(f"{len(overlaps)} targets owned by multiple appliances")
        if online_count < len(appliances):
            health_issues.append(f"{len(appliances) - online_count} appliances offline")

        health_status = "healthy" if not health_issues else "degraded"
        if len(health_issues) >= 3:
            health_status = "critical"

        return {
            "site_id": site_id,
            "mesh_active": online_count > 1,
            "appliances": appliance_list,
            "summary": {
                "total_appliances": len(appliances),
                "online_count": online_count,
                "ring_agreement": ring_agreement,
                "ring_drift": ring_drift,
                "unique_targets": unique_targets,
                "total_assignments": total_assigned,
                "overlap_count": len(overlaps),
                "overlap_samples": overlaps[:5],
                "health_status": health_status,
                "health_issues": health_issues,
            },
            "audit_history": audit_history,
        }


@router.get("/{site_id}/appliances/mesh/assignments")
async def get_mesh_scan_assignments(site_id: str, user: dict = Depends(require_auth)):
    """Get per-target scan assignments across appliances (mesh debug view).

    Returns which appliance owns each discovered device, based on the
    owner_appliance_id from the discovered_devices table (set by first-discovery).
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        rows = await conn.fetch("""
            SELECT dd.ip_address, dd.hostname, dd.os_name,
                   dd.owner_appliance_id,
                   sa.hostname as appliance_hostname,
                   sa.mac_address as appliance_mac,
                   dd.last_seen_at
            FROM discovered_devices dd
            LEFT JOIN site_appliances sa ON dd.owner_appliance_id = sa.id
            WHERE dd.site_id = $1
              AND dd.ip_address IS NOT NULL
            ORDER BY dd.ip_address
        """, site_id)

        assignments = []
        for row in rows:
            assignments.append({
                'target_ip': row['ip_address'],
                'target_hostname': row['hostname'],
                'target_os': row['os_name'],
                'scanned_by': row['appliance_hostname'] or (row['owner_appliance_id'] or 'unassigned'),
                'appliance_mac': row['appliance_mac'],
                'last_seen': row['last_seen_at'].isoformat() if row['last_seen_at'] else None,
            })

        return {
            'site_id': site_id,
            'assignments': assignments,
            'count': len(assignments),
        }


@router.delete("/{site_id}/appliances/{appliance_id}")
async def delete_appliance(site_id: str, appliance_id: str, user: dict = Depends(require_operator)):
    """Delete an appliance from a site. Requires operator or admin access."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Soft-delete: set deleted_at instead of destroying the row.
        # Preserves first_checkin, deployment history, and mesh audit trail.
        # Re-registration on next checkin will clear deleted_at.
        result = await conn.execute("""
            UPDATE site_appliances
            SET deleted_at = NOW(),
                deleted_by = $3,
                status = 'deleted'
            WHERE appliance_id = $1 AND site_id = $2 AND deleted_at IS NULL
        """, appliance_id, site_id, user.get("username") or user.get("email", "unknown"))

        if result == "UPDATE 0":
            raise HTTPException(
                status_code=404,
                detail=f"Appliance {appliance_id} not found in site {site_id}"
            )

        return {
            'status': 'deleted',
            'appliance_id': appliance_id,
            'site_id': site_id,
            'recoverable': True,
        }


class RelocateApplianceRequest(BaseModel):
    """Move an appliance from one site to another within the same org.

    Session 210-B 2026-04-25 hardening #6: today's orphan recovery
    surfaced the gap that a partner couldn't move an appliance between
    their owned sites without three layers of manual ops (SQL, SSH,
    config.yaml hand-edit). This endpoint makes it a first-class
    administrative action with audit + scoping + key minting in one
    atomic transaction.

    Session 213 F1-followup round-table P1-SWE-2: target_site_id is
    constrained to the standard substrate site_id format (lowercase
    alphanumeric + hyphens + underscores) so it's safe to interpolate
    into operator-facing messages, audit details, and SQL via bound
    parameters. Quotes/semicolons/whitespace are rejected at the model
    layer.
    """
    target_site_id: str = Field(..., pattern=r"^[a-z0-9_\-]+$", min_length=1, max_length=128)
    reason: str  # Audit context, ≥ 20 chars (validated in handler)


@router.post("/{site_id}/appliances/{appliance_id}/relocate")
async def relocate_appliance(
    site_id: str,
    appliance_id: str,
    req: RelocateApplianceRequest,
    request: Request,
    user: dict = Depends(require_operator),
):
    """Relocate an appliance from `site_id` to `req.target_site_id`.

    Atomic transaction:
      1. Validate source row exists, target site exists, both belong to
         the same client_org_id (cross-org moves not supported here —
         that's a privileged-chain operation).
      2. UPSERT site_appliances at the target site (new appliance_id).
      3. Mint a fresh api_key, INSERT into api_keys for the target.
      4. Soft-delete the source site_appliances row + deactivate its
         api_keys (Migration 209 trigger handles same-(site,appliance)
         dedup, but we also explicitly deactivate the OLD appliance_id
         row since it's about to be unused).
      5. Audit: admin_audit_log with action=appliance.relocate.

    Daemon-side completion (until v0.4.11 ships the relocate_appliance
    fleet-order handler): the response includes a one-shot SSH command
    the operator runs against the appliance, atomically updating
    config.yaml's site_id + api_key + restarting the daemon. After
    daemon v0.4.11 lands, this endpoint will additionally issue a
    fleet_order(type=relocate_appliance, params=...) and the daemon
    will self-relocate without operator intervention.

    Auth: require_operator. Cross-org enforcement: source.client_org_id
    must equal target.client_org_id (a partner can move appliances
    between their owned sites; a partner can NOT move an appliance to
    a different partner's site).
    """
    if not req.target_site_id or req.target_site_id == site_id:
        raise HTTPException(
            status_code=400,
            detail="target_site_id must be different from current site_id",
        )
    if not req.reason or len(req.reason.strip()) < 20:
        raise HTTPException(
            status_code=400,
            detail="reason must be ≥ 20 chars (audit context)",
        )

    import hashlib
    import secrets
    import uuid as _uuid
    import json as _json

    # Daemons that ship a `reprovision` order handler. Below this version
    # the endpoint falls back to the ssh_snippet path. Round-table RT-5.
    MIN_REPROVISION_VERSION = "0.4.11"

    def _version_supports_reprovision(ver: Optional[str]) -> bool:
        """True if the daemon's reported agent_version is ≥ 0.4.11.
        Tolerates None/empty (returns False — assume legacy)."""
        if not ver:
            return False
        try:
            ver_tuple = tuple(int(p) for p in ver.split(".")[:3])
            min_tuple = tuple(int(p) for p in MIN_REPROVISION_VERSION.split(".")[:3])
            return ver_tuple >= min_tuple
        except (ValueError, AttributeError):
            return False

    pool = await get_pool()

    # Multi-statement admin path: 4 sequential reads → privileged writes
    # (mints API key, writes audit log, emits compliance bundle). Use
    # admin_transaction (NOT admin_connection) so the SET LOCAL pins to
    # ONE PgBouncer backend — closes the Session 212 routing-pathology
    # class that would intermittently RLS-hide source rows under load.
    async with admin_transaction(pool) as conn:
        # Step 1: validate source + target are same org. Read agent_version
        # for the version-gate (RT-5).
        source = await conn.fetchrow(
            """
            SELECT sa.id, sa.mac_address, sa.hostname, sa.legacy_uuid,
                   sa.agent_version,
                   s.client_org_id AS source_org
              FROM site_appliances sa
              JOIN sites s ON s.site_id = sa.site_id
             WHERE sa.appliance_id = $1
               AND sa.site_id = $2
               AND sa.deleted_at IS NULL
            """,
            appliance_id, site_id,
        )
        if not source:
            raise HTTPException(
                status_code=404,
                detail=f"Appliance {appliance_id} not found in site {site_id}",
            )

        target = await conn.fetchrow(
            "SELECT client_org_id FROM sites WHERE site_id = $1",
            req.target_site_id,
        )
        if not target:
            raise HTTPException(
                status_code=404,
                detail=f"Target site {req.target_site_id!r} not found",
            )
        if source["source_org"] != target["client_org_id"]:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Cross-org appliance relocation requires privileged-chain "
                    "approval and is not supported via this endpoint. Move both "
                    "sites under the same client_org_id first, or use the "
                    "/api/admin/cross-org-relocate flow (admin-only, attestation-gated)."
                ),
            )

        # Refuse a second relocate when one is already pending for this
        # MAC. Migration 245's UNIQUE(mac, status) catches it at the DB
        # layer; we surface it with a 409 here.
        existing = await conn.fetchval(
            """
            SELECT id FROM relocations
             WHERE mac_address = $1 AND status = 'pending'
            """,
            source["mac_address"],
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A relocation for MAC {source['mac_address']} is already "
                    f"pending (relocations.id={existing}). Wait for it to "
                    "complete, expire, or fail before issuing a new one."
                ),
            )

        mac = source["mac_address"]
        new_appliance_id = f"{req.target_site_id}-{mac}"
        version_ok = _version_supports_reprovision(source["agent_version"])

        # Step 2: UPSERT target row.
        await conn.execute(
            """
            INSERT INTO site_appliances (
                site_id, appliance_id, mac_address, hostname, status,
                first_checkin, legacy_uuid, created_at
            ) VALUES (
                $1, $2, $3, $4, 'pending',
                NOW() - INTERVAL '1 minute', $5, NOW()
            )
            ON CONFLICT (appliance_id) DO UPDATE SET
                site_id = EXCLUDED.site_id,
                deleted_at = NULL,
                deleted_by = NULL,
                status = 'pending',
                auth_failure_since = NULL,
                auth_failure_count = 0,
                last_auth_failure = NULL
            """,
            req.target_site_id, new_appliance_id, mac,
            source["hostname"] or f"relocated-{mac.replace(':','')}",
            source["legacy_uuid"],
        )

        # Step 3: mint api_key for the target appliance_id.
        raw_api_key = secrets.token_urlsafe(32)
        api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
        await conn.execute(
            """
            INSERT INTO api_keys (
                site_id, appliance_id, key_hash, key_prefix,
                description, active, created_at
            ) VALUES ($1, $2, $3, $4, $5, true, NOW())
            """,
            req.target_site_id, new_appliance_id,
            api_key_hash, raw_api_key[:8],
            f"Relocate by {user.get('username','?')} from {site_id}: {req.reason[:160]}",
        )

        # Step 4 (RT-3): mark source as 'relocating', NOT deleted. Keep
        # the row alive so the daemon can keep checking in (unhappily,
        # via 401 → auto-rekey path) until the move completes. The
        # finalize_pending_relocations() sweep flips this to 'relocated'
        # + soft-deletes the row only AFTER the target site shows a
        # successful checkin.
        await conn.execute(
            """
            UPDATE site_appliances
               SET status = 'relocating'
             WHERE appliance_id = $1
               AND site_id = $2
            """,
            appliance_id, site_id,
        )
        # Keep source api_keys ACTIVE for now — they need to stay valid
        # until the daemon ACKs the reprovision order. If we deactivate
        # them eagerly, the daemon's pending-order ACK fails with 401,
        # the order looks "stuck" forever in the orchestrator's view.

        # Step 5 (RT-5+1): if daemon supports it, issue the reprovision
        # fleet_order. Otherwise fall back to ssh_snippet (legacy path).
        #
        # fleet_orders is FLEET-WIDE — there are no site_id/appliance_id
        # columns. Per-appliance scoping is done by embedding
        # `target_appliance_id` in the SIGNED payload, which the
        # daemon's processor.go::verifyHostScope filter checks before
        # executing. Other daemons see the row but skip it because the
        # target_appliance_id doesn't match their own ID.
        fleet_order_id = None
        if version_ok:
            from .order_signing import sign_admin_order
            order_now = datetime.now(timezone.utc)
            order_expires = order_now + timedelta(minutes=15)
            order_params = {
                "new_site_id": req.target_site_id,
                "new_api_key": raw_api_key,
                # daemon order-binding contract (processor.go references
                # parameters.site_id when the handler runs).
                "site_id": site_id,
            }
            # sign_admin_order embeds target_appliance_id in the signed
            # payload. Only the daemon whose appliance_id matches will
            # execute this order; siblings ignore.
            nonce, signature, signed_payload = sign_admin_order(
                "0", "reprovision", order_params,
                order_now, order_expires,
                target_appliance_id=appliance_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO fleet_orders (
                    order_type, parameters, status, expires_at, created_by,
                    nonce, signature, signed_payload
                )
                VALUES ($1, $2::jsonb, 'active', $3, $4, $5, $6, $7)
                RETURNING id
                """,
                "reprovision",
                _json.dumps(order_params),
                order_expires,
                user.get("username", "unknown"),
                nonce,
                signature,
                signed_payload,
            )
            fleet_order_id = str(row["id"])
            logger.info(
                "appliance.relocate: issued reprovision fleet_order id=%s for v%s daemon "
                "target_appliance_id=%s",
                fleet_order_id, source["agent_version"], appliance_id,
            )
        else:
            logger.info(
                "appliance.relocate: daemon v%s < %s, returning ssh_snippet for manual completion",
                source["agent_version"], MIN_REPROVISION_VERSION,
            )

        # Step 6 (RT-3): record the relocation tracker row. The
        # finalize_pending_relocations() loop reads this; the
        # relocation_stalled substrate invariant queries it.
        relocation_id = await conn.fetchval(
            """
            INSERT INTO relocations (
                source_appliance_id, source_site_id,
                target_appliance_id, target_site_id,
                mac_address, status, reason, actor, fleet_order_id
            ) VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8)
            RETURNING id
            """,
            appliance_id, site_id,
            new_appliance_id, req.target_site_id,
            mac, req.reason, user.get("username", "unknown"),
            fleet_order_id,
        )

        # Step 7 (RT-7): customer evidence-chain entry. Every
        # relocation writes a compliance_bundles row keyed by check_type
        # = 'appliance_relocation'. The bundle's bundle_id is recorded
        # in relocations.evidence_bundle_id so an auditor can join the
        # two append-only sources.
        try:
            from .appliance_relocation import emit_admin_relocation_bundle
            evidence_bundle_id = await emit_admin_relocation_bundle(
                conn,
                source_site_id=site_id,
                target_site_id=req.target_site_id,
                mac_address=mac,
                relocation_id=relocation_id,
                actor=user.get("username", "unknown"),
                reason=req.reason,
            )
            if evidence_bundle_id:
                await conn.execute(
                    "UPDATE relocations SET evidence_bundle_id = $1 WHERE id = $2",
                    evidence_bundle_id, relocation_id,
                )
        except (ImportError, AttributeError) as e:
            # Evidence helper not yet shipped — log loudly so the
            # missing chain row is visible. Don't block the relocation
            # itself; the audit_log entry below still captures the
            # operator action even without the cryptographic chain.
            logger.error(
                "appliance.relocate: appliance_relocation.emit_admin_relocation_bundle "
                "missing — relocation %s has no compliance_bundles row: %s",
                relocation_id, e,
            )
            evidence_bundle_id = None

        # Step 8: admin_audit_log (append-only via Migration 151).
        # Schema uses `username` (not `actor`) — verified against
        # \\d admin_audit_log; all sibling INSERTs in this codebase
        # use the same column name. ip_address column exists for
        # retrospective IP-source forensics.
        client_ip = request.client.host if request.client else None
        await conn.execute(
            """
            INSERT INTO admin_audit_log
              (action, username, target, details, ip_address)
            VALUES ($1, $2, $3, $4, $5)
            """,
            "appliance.relocate",
            user.get("username", "unknown"),
            f"appliance:{mac}",
            _json.dumps({
                "from_site_id": site_id,
                "to_site_id": req.target_site_id,
                "from_appliance_id": appliance_id,
                "to_appliance_id": new_appliance_id,
                "reason": req.reason,
                "relocation_id": relocation_id,
                "fleet_order_id": fleet_order_id,
                "evidence_bundle_id": evidence_bundle_id,
                "agent_version": source["agent_version"],
                "method": "fleet_order" if version_ok else "ssh_snippet",
            }),
            client_ip,
        )

    logger.info(
        "appliance.relocate: %s/%s → %s/%s by %s relocation_id=%s method=%s",
        site_id, appliance_id, req.target_site_id, new_appliance_id,
        user.get("username", "?"),
        relocation_id, "fleet_order" if version_ok else "ssh_snippet",
    )

    # Session 213 F1-followup: surface whether the source site is now
    # logically empty so the operator can decide if a canonical mapping
    # is appropriate. We do NOT auto-INSERT into site_canonical_mapping
    # here — a partner moving an appliance between two of THEIR live
    # sites doesn't imply A canonicalizes to B. The operator runs
    # `rename_site(p_from=A, p_to=B, ...)` explicitly when an entire
    # site has been retired in favor of another.
    #
    # Round-table P0-SWE-1: this is an ADVISORY signal. A failure on
    # this count query MUST NOT roll back the relocate (whose primary
    # work is already committed by this point). Wrap in try/except;
    # gracefully omit the field if the query fails. Operator who sees
    # the field missing once will not be confused — the relocate
    # response carries `relocation_id` for tracking either way.
    source_remaining: Optional[int] = None
    try:
        source_remaining = await conn.fetchval(
            """
            SELECT COUNT(*) FROM site_appliances
             WHERE site_id = $1
               AND deleted_at IS NULL
               AND status NOT IN ('relocating', 'relocated', 'decommissioned')
            """,
            site_id,
        )
    except Exception as exc:  # noqa: BLE001 — advisory field, must not abort
        logger.warning(
            "relocate_source_remaining_query_failed",
            extra={
                "site_id": site_id,
                "appliance_id": appliance_id,
                "exception_class": type(exc).__name__,
            },
        )

    # Build the response. Daemon-supported path returns order receipt;
    # legacy path returns ssh_snippet for manual completion.
    response: Dict[str, Any] = {
        "status": "pending" if version_ok else "needs_manual_push",
        "from": {"site_id": site_id, "appliance_id": appliance_id},
        "to": {"site_id": req.target_site_id, "appliance_id": new_appliance_id},
        "new_api_key": raw_api_key,
        "relocation_id": relocation_id,
        "evidence_bundle_id": evidence_bundle_id,
        "agent_version": source["agent_version"],
    }
    if source_remaining is not None:
        # F1-followup signal: 0 = source site is empty post-relocate.
        # Operator may want to call rename_site() to alias source→target
        # if the source site is being retired. We surface the count;
        # we do not act on it (would need explicit operator opt-in).
        response["source_site_remaining_appliance_count"] = int(source_remaining)
        if int(source_remaining) == 0:
            response["canonical_alias_recommended"] = (
                f"Source site '{site_id}' is empty after this relocate. If "
                f"the site is being retired in favor of '{req.target_site_id}', "
                f"call rename_site('{site_id}', '{req.target_site_id}', "
                f"'<your-email>', '<reason ≥20 chars>') to alias future "
                f"telemetry. If the source site is keeping operational "
                f"identity (e.g. new appliances will be onboarded under it), "
                f"no action needed."
            )

    if version_ok:
        response["fleet_order_id"] = fleet_order_id
        response["next_step"] = (
            "Daemon will pick up the reprovision order on its next checkin "
            "(≤60s) and self-relocate. Track via GET "
            f"/api/admin/relocations/{relocation_id}. Source row will "
            "auto-flip to 'relocated' once target checkin lands."
        )
    else:
        # Legacy ssh_snippet for daemons < 0.4.11.
        mac_lower = mac.lower()
        emergency_pass_hash = hashlib.sha256(
            f"osiriscare-emergency-{mac_lower}".encode()
        ).hexdigest()[:8]
        emergency_pass = f"osiris-{emergency_pass_hash}"
        ssh_snippet = (
            f"sshpass -p '{emergency_pass}' ssh -o StrictHostKeyChecking=accept-new "
            f"msp@<APPLIANCE_LAN_IP> "
            f"\"echo '{emergency_pass}' | sudo -S /run/current-system/sw/bin/bash -c '"
            f"cp /var/lib/msp/config.yaml /var/lib/msp/config.yaml.bak.\\$(date -u +%s) && "
            f"yq -i \\\".site_id = \\\\\\\"{req.target_site_id}\\\\\\\"\\\" /var/lib/msp/config.yaml && "
            f"yq -i \\\".api_key = \\\\\\\"{raw_api_key}\\\\\\\"\\\" /var/lib/msp/config.yaml && "
            f"chmod 600 /var/lib/msp/config.yaml && "
            f"systemctl restart appliance-daemon"
            f"'\""
        )
        response["ssh_snippet"] = ssh_snippet
        response["next_step"] = (
            f"Daemon agent_version={source['agent_version']} predates the "
            f"reprovision fleet-order handler ({MIN_REPROVISION_VERSION}+). "
            "Run the ssh_snippet against the appliance's LAN IP to push "
            "site_id + api_key manually. Upgrade daemon to "
            f"{MIN_REPROVISION_VERSION} via update_daemon order to enable "
            "automatic relocate."
        )

    return response


class MeshTopologyRequest(BaseModel):
    """Set mesh topology mode for a site."""
    mesh_topology: str  # 'auto' or 'independent'


@router.put("/{site_id}/mesh-topology")
async def set_mesh_topology(site_id: str, req: MeshTopologyRequest, user: dict = Depends(require_operator)):
    """Set mesh topology mode. 'independent' suppresses mesh alerts for sites
    with consumer-grade routers where cross-subnet routing is impossible.

    Auth: require_operator (Session 213 round-table P1) — was require_auth,
    promoted because this mutates fleet-shaping policy. Mirrors the auth
    posture of sister mutations on the same router."""
    if req.mesh_topology not in ('auto', 'independent'):
        raise HTTPException(status_code=400, detail="mesh_topology must be 'auto' or 'independent'")

    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        await conn.execute(
            "UPDATE sites SET mesh_topology = $1 WHERE site_id = $2",
            req.mesh_topology, site_id,
        )

    return {'site_id': site_id, 'mesh_topology': req.mesh_topology}


@router.get("/{site_id}/mesh-topology")
async def get_mesh_topology(site_id: str, user: dict = Depends(require_auth)):
    """Get mesh topology mode for a site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(mesh_topology, 'auto') FROM sites WHERE site_id = $1",
            site_id,
        )
    return {'site_id': site_id, 'mesh_topology': val or 'auto'}


class NetworkModeRequest(BaseModel):
    """Set network stability mode during onboarding."""
    network_mode: str  # 'static_lease' or 'dynamic_mdns'


@router.put("/{site_id}/network-mode")
async def set_network_mode(site_id: str, req: NetworkModeRequest, user: dict = Depends(require_operator)):
    """Set network stability mode. Onboarding gate — no site should operate
    without an explicit network decision.

    Auth: require_operator (Session 213 round-table P1) — was require_auth,
    promoted because this is the onboarding-gate decision recorded in
    the audit trail."""
    if req.network_mode not in ('static_lease', 'dynamic_mdns', 'pending'):
        raise HTTPException(status_code=400, detail="network_mode must be 'static_lease' or 'dynamic_mdns'")

    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        await conn.execute(
            "UPDATE sites SET network_mode = $1 WHERE site_id = $2",
            req.network_mode, site_id,
        )

    return {'site_id': site_id, 'network_mode': req.network_mode}


@router.get("/{site_id}/network-mode")
async def get_network_mode(site_id: str, user: dict = Depends(require_auth)):
    """Get network stability mode for a site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(network_mode, 'pending') FROM sites WHERE site_id = $1",
            site_id,
        )
    return {'site_id': site_id, 'network_mode': val or 'pending'}


class ApplianceMoveRequest(BaseModel):
    """Request to move an appliance to a different site."""
    target_site_id: str


@router.post("/{site_id}/appliances/{appliance_id}/move")
async def move_appliance(
    site_id: str,
    appliance_id: str,
    body: ApplianceMoveRequest,
    user: dict = Depends(require_operator),
):
    """Move an appliance from one site to another. Requires operator or admin access."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify appliance exists in source site
        existing = await conn.fetchrow(
            "SELECT appliance_id FROM site_appliances WHERE appliance_id = $1 AND site_id = $2",
            appliance_id, site_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail=f"Appliance {appliance_id} not found in site {site_id}")

        # Update the site_id. Per-row filter (appliance_id is PK) satisfies
        # migration 192 row-guard without needing app.allow_multi_row.
        await conn.execute(
            "UPDATE site_appliances SET site_id = $1 WHERE appliance_id = $2",  # noqa: rename-site-gate — per-appliance MAC-scoped move via /relocate endpoint, not site rename
            body.target_site_id, appliance_id,
        )

        # Update appliance_provisioning by MAC address.
        # MAC lives on site_appliances (M1: legacy appliances table dropped).
        mac_row = await conn.fetchrow(
            "SELECT mac_address FROM site_appliances WHERE appliance_id = $1",
            appliance_id,
        )
        if mac_row and mac_row["mac_address"]:
            await conn.execute(
                """UPDATE appliance_provisioning
                   SET site_id = $1,  -- noqa: rename-site-gate — per-appliance MAC-scoped relocate companion to site_appliances UPDATE above
                       notes = COALESCE(notes, '') || $3
                   WHERE UPPER(mac_address) = UPPER($2) AND site_id = $4""",
                body.target_site_id, mac_row["mac_address"],
                f" | Moved {site_id} -> {body.target_site_id} via dashboard",
                site_id,
            )

    logger.info(f"Appliance {appliance_id} moved from {site_id} to {body.target_site_id}")

    return {
        "status": "moved",
        "appliance_id": appliance_id,
        "from_site_id": site_id,
        "to_site_id": body.target_site_id,
    }


class L2ModeUpdate(BaseModel):
    """Request to update L2 healing mode for an appliance."""
    l2_mode: str  # 'auto', 'manual', 'disabled'


@router.patch("/{site_id}/appliances/{appliance_id}/l2-mode")
async def update_appliance_l2_mode(
    site_id: str,
    appliance_id: str,
    body: L2ModeUpdate,
    user: dict = Depends(require_operator),
):
    """Update L2 healing mode for an appliance. Requires operator access.

    Modes:
    - auto: L2 LLM plans execute automatically
    - manual: L2 generates plans but escalates for human approval
    - disabled: L2 is skipped entirely, only L1 deterministic rules run
    """
    if body.l2_mode not in ("auto", "manual", "disabled"):
        raise HTTPException(400, "l2_mode must be 'auto', 'manual', or 'disabled'")

    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.execute("""
            UPDATE site_appliances
            SET l2_mode = $1
            WHERE appliance_id = $2 AND site_id = $3
        """, body.l2_mode, appliance_id, site_id)

        if result == "UPDATE 0":
            raise HTTPException(404, f"Appliance {appliance_id} not found in site {site_id}")

    logger.info("L2 mode updated: appliance=%s mode=%s by=%s", appliance_id, body.l2_mode, user.get("email", "unknown"))
    return {"status": "updated", "appliance_id": appliance_id, "l2_mode": body.l2_mode}


class ClearStaleRequest(BaseModel):
    """Request to clear stale appliances."""
    stale_hours: int = 24


@router.post("/{site_id}/appliances/clear-stale")
async def clear_stale_appliances(site_id: str, request: ClearStaleRequest, user: dict = Depends(require_operator)):
    """Clear stale appliances that haven't checked in recently. Requires operator or admin access.

    Deletes appliances from the site that haven't checked in for more than
    the specified number of hours.
    """
    from datetime import datetime, timezone, timedelta

    pool = await get_pool()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=request.stale_hours)

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get count of stale appliances before deletion
        stale_count = await conn.fetchval("""
            SELECT COUNT(*) FROM site_appliances
            WHERE site_id = $1
            AND (last_checkin IS NULL OR last_checkin < $2)
        """, site_id, cutoff)

        # Delete stale appliances
        result = await conn.execute("""
            DELETE FROM site_appliances
            WHERE site_id = $1
            AND (last_checkin IS NULL OR last_checkin < $2)
        """, site_id, cutoff)

        return {
            'status': 'cleared',
            'site_id': site_id,
            'stale_hours': request.stale_hours,
            'deleted_count': stale_count or 0
        }


class BroadcastOrderCreate(BaseModel):
    """Model for broadcasting an order to all appliances in a site."""
    order_type: OrderType
    parameters: dict = {}


@router.post("/{site_id}/orders/broadcast")
async def broadcast_order(site_id: str, order: BroadcastOrderCreate, user: dict = Depends(require_operator)):
    """Broadcast an order to all appliances in a site. Requires operator or admin access."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=1)
    created_orders = []

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get all appliances for this site
        appliances = await conn.fetch("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1
        """, site_id)

        if not appliances:
            raise HTTPException(status_code=404, detail=f"No appliances found for site {site_id}")

        for row in appliances:
            order_id = generate_order_id()
            nonce, signature, signed_payload = sign_admin_order(
                order_id, order.order_type.value, order.parameters, now, expires_at,
                target_appliance_id=row['appliance_id'],
            )
            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
            """,
                order_id,
                row['appliance_id'],
                site_id,
                order.order_type.value,
                json.dumps(order.parameters),
                0,
                'pending',
                now,
                expires_at,
                nonce,
                signature,
                signed_payload,
            )
            created_orders.append({
                "order_id": order_id,
                "appliance_id": row['appliance_id'],
                "order_type": order.order_type.value,
                "status": "pending",
            })

    return created_orders


# =============================================================================
# ORDER LIFECYCLE ENDPOINTS (acknowledge/complete)
# =============================================================================

class OrderCompleteRequest(BaseModel):
    """Request body for completing an order."""
    success: bool
    result: Optional[dict] = None
    error_message: Optional[str] = None


@orders_router.post("/{order_id}/acknowledge")
async def acknowledge_order(order_id: str, request: Request, auth_site_id: str = Depends(require_appliance_bearer)):
    """Acknowledge that an order has been received and is being executed.

    Called by the appliance agent when it picks up a pending order.
    Updates status from 'pending' to 'acknowledged'.
    Handles fleet-wide orders (prefixed with 'fleet-') by recording in fleet_order_completions.
    """

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # Handle fleet-wide orders (format: fleet::{uuid}::{appliance_id})
    if order_id.startswith("fleet::"):
        parts = order_id.split("::", 2)
        if len(parts) == 3:
            fleet_order_id, appliance_id = parts[1], parts[2]
            async with admin_connection(pool) as conn:
                await record_fleet_order_completion(conn, fleet_order_id, appliance_id, "acknowledged")
                return {
                    "status": "acknowledged",
                    "order_id": order_id,
                    "order_type": "fleet",
                    "acknowledged_at": now.isoformat()
                }

    async with admin_connection(pool) as conn:
        # Update the order status
        result = await conn.fetchrow("""
            UPDATE admin_orders
            SET status = 'acknowledged',
                acknowledged_at = $1
            WHERE order_id = $2
            AND status = 'pending'
            RETURNING order_id, appliance_id, site_id, order_type
        """, now, order_id)

        if not result:
            # Try healing orders table (orders created by L1/L2/L3 engine)
            result = await conn.fetchrow("""
                UPDATE orders o
                SET status = 'acknowledged',
                    acknowledged_at = $1
                FROM v_appliances_current a
                WHERE o.order_id = $2
                AND o.status = 'pending'
                AND o.appliance_id = a.id
                RETURNING o.order_id, o.appliance_id::text as appliance_id, a.site_id, 'healing'::text as order_type
            """, now, order_id)

        if not result:
            # Check if order exists but is already acknowledged
            existing = await conn.fetchrow("""
                SELECT order_id, status FROM admin_orders WHERE order_id = $1
                UNION ALL
                SELECT order_id, status FROM orders WHERE order_id = $1
                LIMIT 1
            """, order_id)

            if existing:
                if existing['status'] == 'acknowledged':
                    return {
                        "status": "already_acknowledged",
                        "order_id": order_id,
                        "message": "Order was already acknowledged"
                    }
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Order {order_id} is in status '{existing['status']}', cannot acknowledge"
                    )
            else:
                raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

        return {
            "status": "acknowledged",
            "order_id": result['order_id'],
            "appliance_id": result['appliance_id'],
            "site_id": result['site_id'],
            "order_type": result['order_type'] if result['order_type'] else 'healing',
            "acknowledged_at": now.isoformat()
        }


@orders_router.post("/{order_id}/complete")
async def complete_order(order_id: str, request: OrderCompleteRequest, auth_site_id: str = Depends(require_appliance_bearer)):
    """Mark an order as completed (success or failure).

    Called by the appliance agent after executing an order.
    Updates status to 'completed' or 'failed' based on success flag.
    Handles fleet-wide orders (prefixed with 'fleet-') by recording in fleet_order_completions.
    """

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    new_status = 'completed' if request.success else 'failed'
    result_data = request.result or {}
    if request.error_message:
        result_data['error_message'] = request.error_message

    # Handle fleet-wide orders (format: fleet::{uuid}::{appliance_id})
    if order_id.startswith("fleet::"):
        parts = order_id.split("::", 2)
        if len(parts) == 3:
            fleet_order_id, appliance_id = parts[1], parts[2]
            # SECURITY: verify completing appliance belongs to authenticated site
            if auth_site_id and not appliance_id.startswith(auth_site_id):
                logger.warning(
                    "Order completion rejected: appliance %s does not belong to site %s",
                    appliance_id, auth_site_id,
                )
                raise HTTPException(status_code=403, detail="Order does not belong to this appliance")
            async with admin_connection(pool) as conn:
                # Phase 12.1 (Session 205): persist diagnostic output so
                # signed fleet-order failures are remotely inspectable.
                _duration_ms = None
                if isinstance(result_data, dict) and "duration_ms" in result_data:
                    try:
                        _duration_ms = int(result_data["duration_ms"])
                    except (TypeError, ValueError):
                        _duration_ms = None
                await record_fleet_order_completion(
                    conn, fleet_order_id, appliance_id, new_status,
                    output=result_data if result_data else None,
                    error_message=request.error_message,
                    duration_ms=_duration_ms,
                )
                return {
                    "status": new_status,
                    "order_id": order_id,
                    "order_type": "fleet",
                    "completed_at": now.isoformat()
                }

    # Mirror result.error_message → top-level error_message column so the
    # runbook's sample SQL (SELECT error_message FROM admin_orders WHERE ...)
    # and any log shipper keyed off the column get the payload. The 0.4.7
    # daemon's head+tail 4KB nix banner is currently buried in result JSONB
    # only; this surfaces it. Preserves existing request.error_message when
    # populated, otherwise lifts from the result dict.
    err_msg_for_column = request.error_message
    if not err_msg_for_column and isinstance(result_data, dict):
        _nested = result_data.get("error_message")
        if isinstance(_nested, str) and _nested.strip():
            err_msg_for_column = _nested

    async with admin_connection(pool) as conn:
        # Try admin_orders first
        result = await conn.fetchrow("""
            UPDATE admin_orders
            SET status = $1,
                completed_at = $2,
                result = $3::jsonb,
                error_message = COALESCE($5, error_message)
            WHERE order_id = $4
            AND status IN ('pending', 'acknowledged')
            RETURNING order_id, appliance_id, site_id, order_type, acknowledged_at
        """, new_status, now, json.dumps(result_data), order_id, err_msg_for_column)

        if not result:
            # Try healing orders table (orders created by L1/L2/L3 engine)
            result = await conn.fetchrow("""
                UPDATE orders o
                SET status = $1,
                    completed_at = $2,
                    result = $3::jsonb
                FROM v_appliances_current a
                WHERE o.order_id = $4
                AND o.status IN ('pending', 'acknowledged')
                AND o.appliance_id = a.id
                RETURNING o.order_id, o.appliance_id::text as appliance_id, a.site_id, 'healing'::text as order_type, o.acknowledged_at
            """, new_status, now, json.dumps(result_data), order_id)

        if not result:
            # Check if order exists in either table
            existing = await conn.fetchrow("""
                SELECT order_id, status FROM admin_orders WHERE order_id = $1
                UNION ALL
                SELECT order_id, status FROM orders WHERE order_id = $1
                LIMIT 1
            """, order_id)

            if existing:
                if existing['status'] in ('completed', 'failed'):
                    return {
                        "status": "already_completed",
                        "order_id": order_id,
                        "message": f"Order was already {existing['status']}"
                    }
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Order {order_id} is in status '{existing['status']}', cannot complete"
                    )
            else:
                raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

        # SECURITY: verify order belongs to authenticated site
        if auth_site_id and result.get('site_id') and result['site_id'] != auth_site_id:
            logger.warning(
                "Order completion rejected: order %s belongs to site %s, not %s",
                order_id, result['site_id'], auth_site_id,
            )
            raise HTTPException(status_code=403, detail="Order does not belong to this site")

        # Post-completion hooks for specific order types
        # Healing order → resolve incident on success, escalate on failure
        if result['order_type'] == 'healing':
            try:
                if request.success:
                    await conn.execute("""
                        UPDATE incidents SET resolved_at = NOW(), status = 'resolved'
                        WHERE order_id = (SELECT id FROM orders WHERE order_id = $1)
                        AND status IN ('resolving', 'escalated') AND resolved_at IS NULL
                    """, order_id)
                else:
                    # L1 healing failed — try L2 LLM planner before escalating to L3
                    l2_handled = False
                    try:
                        from .l2_planner import analyze_incident as l2_analyze, is_l2_available
                        if is_l2_available():
                            # Get incident details for L2 analysis
                            inc_row = await conn.fetchrow("""
                                SELECT i.id, i.incident_type, i.severity, i.check_type, i.details,
                                       i.pre_state, i.hipaa_controls, i.appliance_id
                                FROM incidents i
                                WHERE i.order_id = (SELECT id FROM orders WHERE order_id = $1)
                                AND i.status = 'resolving'
                            """, order_id)
                            if inc_row:
                                details = {}
                                pre_state = {}
                                try:
                                    if inc_row['details']:
                                        details = json.loads(inc_row['details']) if isinstance(inc_row['details'], str) else inc_row['details']
                                    if inc_row['pre_state']:
                                        pre_state = json.loads(inc_row['pre_state']) if isinstance(inc_row['pre_state'], str) else inc_row['pre_state']
                                except (json.JSONDecodeError, TypeError):
                                    pass

                                decision = await l2_analyze(
                                    incident_type=inc_row['incident_type'],
                                    severity=inc_row['severity'] or 'medium',
                                    check_type=inc_row['check_type'] or inc_row['incident_type'],
                                    details=details,
                                    pre_state=pre_state,
                                    hipaa_controls=inc_row['hipaa_controls'] or [],
                                )
                                if decision.runbook_id and decision.confidence >= 0.6 and not decision.requires_human_review:
                                    l2_handled = True
                                    await conn.execute("""
                                        UPDATE incidents SET resolution_tier = 'L2', status = 'resolving'
                                        WHERE id = $1
                                    """, inc_row['id'])
                                    logger.info(f"L1 failed → L2 planner found runbook {decision.runbook_id} "
                                                f"(confidence={decision.confidence}) for {inc_row['incident_type']}")
                    except Exception as l2_err:
                        logger.warning(f"L2 fallback failed for order {order_id}: {l2_err}")

                    if not l2_handled:
                        await conn.execute("""
                            UPDATE incidents SET status = 'escalated', resolution_tier = 'L3'
                            WHERE order_id = (SELECT id FROM orders WHERE order_id = $1)
                            AND status = 'resolving'
                        """, order_id)
            except Exception as e:
                import logging
                logging.warning(f"Failed to update incident for healing order {order_id}: {e}")

        if result['order_type'] == 'validate_credential' and 'credential_id' in result_data:
            cred_status = 'valid' if result_data.get('can_connect') else 'invalid'
            try:
                await conn.execute("""
                    UPDATE site_credentials
                    SET validation_status = $1,
                        last_validated_at = NOW(),
                        validation_details = $2
                    WHERE id = $3::uuid
                """, cred_status, json.dumps(result_data), result_data['credential_id'])
            except Exception as e:
                import logging
                logging.warning(f"Failed to update credential validation: {e}")

        # Calculate execution time if acknowledged
        execution_time_ms = None
        if result['acknowledged_at']:
            execution_time_ms = int((now - result['acknowledged_at']).total_seconds() * 1000)

        return {
            "status": new_status,
            "order_id": result['order_id'],
            "appliance_id": result['appliance_id'],
            "site_id": result['site_id'],
            "order_type": result['order_type'] if result['order_type'] else 'healing',
            "completed_at": now.isoformat(),
            "execution_time_ms": execution_time_ms,
            "success": request.success,
            "result": result_data
        }


@orders_router.get("/{order_id}")
async def get_order(order_id: str, user: dict = Depends(require_auth)):
    """Get order details by ID."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT order_id, appliance_id, site_id, order_type,
                   parameters, priority, status, created_at,
                   expires_at, acknowledged_at, completed_at, result
            FROM admin_orders
            WHERE order_id = $1
        """, order_id)

        if not row:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

        return {
            "order_id": row['order_id'],
            "appliance_id": row['appliance_id'],
            "site_id": row['site_id'],
            "order_type": row['order_type'],
            "parameters": parse_parameters(row['parameters']),
            "priority": row['priority'],
            "status": row['status'],
            "created_at": row['created_at'].isoformat() if row['created_at'] else None,
            "expires_at": row['expires_at'].isoformat() if row['expires_at'] else None,
            "acknowledged_at": row['acknowledged_at'].isoformat() if row['acknowledged_at'] else None,
            "completed_at": row['completed_at'].isoformat() if row['completed_at'] else None,
            "result": parse_parameters(row['result'])
        }


# =============================================================================
# HEALING KILL SWITCH (disable/enable per-site)
# =============================================================================


@router.post("/{site_id}/disable-healing")
async def disable_site_healing(site_id: str, user: dict = Depends(require_admin)):
    """Disable all healing (L1+L2) for a site via fleet order.

    Appliance keeps monitoring + checkin alive, but stops executing remediation.
    Use cases: unpaid client, compromised site, decommission prep.
    """
    return await _toggle_healing(site_id, enabled=False, user=user)


@router.post("/{site_id}/enable-healing")
async def enable_site_healing(site_id: str, user: dict = Depends(require_admin)):
    """Re-enable healing for a site via fleet order."""
    return await _toggle_healing(site_id, enabled=True, user=user)


async def _toggle_healing(site_id: str, enabled: bool, user: dict):
    """Create a fleet order to enable or disable healing for a specific site."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    order_type = "enable_healing" if enabled else "disable_healing"
    action = "enabled" if enabled else "disabled"

    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow("SELECT site_id, clinic_name FROM sites WHERE site_id = $1", site_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get appliance(s) for this site
        appliances = await conn.fetch(
            "SELECT appliance_id FROM site_appliances WHERE site_id = $1",
            site_id,
        )
        if not appliances:
            raise HTTPException(status_code=404, detail="No appliances found for site")

        # Create fleet order targeting this site
        from .fleet_updates import sign_fleet_order
        expires_at = now + timedelta(hours=24)
        parameters = {"site_id": site_id, "reason": f"Admin {action} healing"}

        row = await conn.fetchrow("""
            INSERT INTO fleet_orders (order_type, parameters, status, expires_at, created_by,
                                      nonce, signature, signed_payload)
            VALUES ($1, $2::jsonb, 'active', $3, $4, $5, $6, $7)
            RETURNING id, created_at
        """,
            order_type,
            json.dumps(parameters),
            expires_at,
            user.get("username") or user.get("email"),
            *sign_fleet_order(0, order_type, parameters, now, expires_at),
        )

        # Audit log
        try:
            await conn.execute("""
                INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
                VALUES ($1, $2, 'site', $3, $4::jsonb)
            """,
                f"healing_{action}",
                user.get("username") or user.get("email"),
                site_id,
                json.dumps({"order_id": str(row["id"]), "site_name": site["clinic_name"]}),
            )
        except Exception as e:
            logger.error(f"Audit log for healing toggle failed: {e}", exc_info=True)

        logger.info(f"Healing {action} for site {site_id} by {user.get('username')} (order {row['id']})")

        return {
            "status": "ok",
            "action": action,
            "site_id": site_id,
            "order_id": str(row["id"]),
            "expires_at": expires_at.isoformat(),
            "appliance_count": len(appliances),
        }


# =============================================================================
# APPLIANCE CHECK-IN WITH SMART DEDUPLICATION
# =============================================================================

# Separate router for appliance check-in endpoint
appliances_router = APIRouter(prefix="/api/appliances", tags=["appliances"])


class ConnectedAgentInfo(BaseModel):
    """A Go agent connected to the appliance via gRPC."""
    agent_id: str
    hostname: str
    agent_version: Optional[str] = None
    ip_address: Optional[str] = None
    os_version: Optional[str] = None
    capability_tier: int = 0
    connected_at: Optional[str] = None
    last_heartbeat: Optional[str] = None
    drift_count: int = 0
    checks_passed: int = 0
    checks_total: int = 0


class ApplianceCheckin(BaseModel):
    """Check-in request from appliance agent."""
    site_id: str
    hostname: str
    mac_address: str
    all_mac_addresses: Optional[List[str]] = None  # All physical NIC MACs (ghost detection)
    boot_source: Optional[str] = None  # "live_usb", "installed_disk", or "unknown"
    ip_addresses: list = Field(default=[], max_length=100)
    uptime_seconds: Optional[int] = None
    agent_version: Optional[str] = None
    nixos_version: Optional[str] = None
    has_local_credentials: bool = False  # If True, appliance has fresh local creds
    agent_public_key: Optional[str] = None  # Ed25519 public key hex for evidence signing
    # Daemon's IDENTITY public key (the one signRequest in phonehome.go
    # signs sigauth headers with — distinct from the evidence-bundle key
    # above). Persisted to site_appliances.agent_identity_public_key,
    # consulted by signature_auth.py::_resolve_pubkey. Pre-#179 the
    # daemon only uploaded the evidence key and sigauth's legacy
    # fallback returned the wrong key — substrate
    # signature_verification_failures fired 100% on north-valley-branch-2
    # for that reason. Now the IDENTITY key flows through the checkin
    # path explicitly. 64 hex chars (Ed25519 pubkey).
    agent_identity_public_key: Optional[str] = None
    connected_agents: Optional[list[ConnectedAgentInfo]] = None  # Go agents on this appliance
    discovery_results: Optional[Dict[str, Any]] = None  # App protection profile discovery results
    encryption_public_key: str = ""  # X25519 public key hex for credential envelope encryption
    deploy_results: Optional[list[Dict[str, Any]]] = None  # Results from previous deploy attempts
    wg_connected: bool = False  # Whether WireGuard tunnel is active
    wg_ip: Optional[str] = None  # WireGuard VPN IP (10.100.0.x)
    # WireGuard public key. Primary persistence path is provisioning.py
    # claim_provision_code() which UPDATEs sites.wg_pubkey on claim. This
    # checkin field is defensive — if a daemon ever rekeys its WireGuard
    # pair and the server's stored value drifts, the checkin handler can
    # reconcile. 2026-04-24 lockstep audit flagged this as silently dropped
    # by Pydantic (field was present on daemon side but absent here).
    wg_pubkey: Optional[str] = None
    daemon_health: Optional[Dict[str, Any]] = None  # Go runtime stats (goroutines, heap, GC)
    bundle_hashes: Optional[List[Dict[str, str]]] = None  # Recent evidence bundle hashes for peer witnessing
    witness_attestations: Optional[List[Dict[str, str]]] = None  # Counter-signatures of sibling bundle hashes
    # Time-travel reconciliation state (Session 205). Agent reports these
    # every cycle; CC compares against last-known values to detect VM
    # snapshot revert / backup restore / disk clone scenarios. See
    # backend/reconcile.py and appliance/internal/daemon/reconcile.go.
    boot_counter: Optional[int] = None
    generation_uuid: Optional[str] = None
    reconcile_needed: bool = False
    reconcile_signals: Optional[List[str]] = None
    # D1 (Session 206): Ed25519 hex signature by the appliance over the
    # heartbeat content hash. NULL until the Go daemon D1 PR ships.
    heartbeat_signature: Optional[str] = None


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase with colons (84:3A:5B:91:B6:61)."""
    if not mac:
        return ""
    # Remove all separators, convert to uppercase
    clean = mac.upper().replace(':', '').replace('-', '').replace('.', '')
    # Re-insert colons every 2 chars
    return ':'.join(clean[i:i+2] for i in range(0, len(clean), 2))


class DiscoveredDomainReport(BaseModel):
    """Domain discovery report from appliance."""
    site_id: str
    appliance_id: str
    discovered_domain: dict
    awaiting_credentials: bool = True


@appliances_router.post("/domain-discovered")
async def report_discovered_domain(report: DiscoveredDomainReport, auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Receive domain discovery report from appliance.
    
    Triggers:
    1. Store discovered domain info in site record
    2. Notify partner that credentials are needed
    3. Update dashboard to show "awaiting credentials" state
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with tenant_connection(pool, site_id=report.site_id) as conn:
        # Update site record with discovered domain
        await conn.execute("""
            UPDATE sites 
            SET discovered_domain = $1::jsonb,
                domain_discovery_at = $2,
                awaiting_credentials = $3
            WHERE site_id = $4
        """,
            json.dumps(report.discovered_domain),
            now,
            report.awaiting_credentials,
            report.site_id,
        )

        # Create notification for dashboard (deduplicated — skip if recent notification exists)
        domain_name = report.discovered_domain.get('domain_name', 'Unknown')
        domain_controllers = report.discovered_domain.get('domain_controllers', [])

        existing = await conn.fetchval("""
            SELECT COUNT(*) FROM notifications
            WHERE site_id = $1 AND category = 'deployment'
              AND title = $2
              AND created_at > NOW() - INTERVAL '24 hours'
        """, report.site_id, f'Domain Discovered: {domain_name}')

        if existing == 0:
            await conn.execute("""
                INSERT INTO notifications (
                    site_id, appliance_id, severity, category, title, message, metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            """,
                report.site_id,
                report.appliance_id,
                'info',
                'deployment',
                f'Domain Discovered: {domain_name}',
                f'Appliance discovered Active Directory domain "{domain_name}". '
                f'Please enter domain administrator credentials to begin automatic enumeration.',
                json.dumps({
                    "domain_name": domain_name,
                    "domain_controllers": domain_controllers,
                    "action_required": "Enter domain administrator credentials",
                    "dashboard_link": f"/sites/{report.site_id}/credentials",
                }),
                now,
            )

            # Only send email on first notification (not repeats)
            try:
                from .email_alerts import send_critical_alert, is_email_configured
                if is_email_configured():
                    dc_list = ', '.join(domain_controllers) if domain_controllers else 'None found'
                    send_critical_alert(
                        title=f"Domain Discovered: {domain_name}",
                        message=(
                            f'Appliance discovered AD domain "{domain_name}". '
                            f'Domain Controllers: {dc_list}. '
                            f'Action required: enter domain admin credentials in dashboard.'
                        ),
                        site_id=report.site_id,
                        category="deployment",
                        severity="info",
                    )
            except Exception as e:
                logger.warning(f"Failed to send email notification: {e}")
        else:
            logger.debug(f"Skipping duplicate domain discovery notification for {domain_name} on {report.site_id}")
    
    return {"status": "ok", "message": "Domain discovery recorded"}


class EnumerationResultsReport(BaseModel):
    """Enumeration results report from appliance."""
    site_id: str
    appliance_id: str
    results: dict


@appliances_router.post("/enumeration-results")
async def report_enumeration_results(report: EnumerationResultsReport, auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Receive AD enumeration results from appliance.
    
    Stores enumeration results and updates site with discovered targets.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with tenant_connection(pool, site_id=report.site_id) as conn:
        # Store enumeration results
        await conn.execute("""
            INSERT INTO enumeration_results (
                site_id, appliance_id, enumeration_time,
                total_servers, total_workstations,
                reachable_servers, reachable_workstations,
                results_json, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
        """,
            report.site_id,
            report.appliance_id,
            datetime.fromisoformat(report.results.get('enumeration_time', now.isoformat())),
            report.results.get('total_servers', 0),
            report.results.get('total_workstations', 0),
            report.results.get('reachable_servers', 0),
            report.results.get('reachable_workstations', 0),
            json.dumps(report.results),
            now,
        )
        
        logger.info(f"Enumeration results stored: {report.results.get('total_servers', 0)} servers, "
                   f"{report.results.get('total_workstations', 0)} workstations")
    
    return {"status": "ok", "message": "Enumeration results recorded"}


class DomainCredentialInput(BaseModel):
    """Domain credential submission."""
    domain_name: str
    username: str          # e.g., "Administrator" or "DOMAIN\\admin"
    password: str
    credential_type: str = "domain_admin"  # domain_admin, service_account


@router.post("/{site_id}/domain-credentials")
async def submit_domain_credentials(
    site_id: str,
    creds: DomainCredentialInput,
    user: dict = Depends(require_operator),
):
    """
    Submit domain credentials after discovery. Requires operator or admin access.

    This is the ONE human touchpoint in zero-friction deployment.
    After this, enumeration happens automatically.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with tenant_connection(pool, site_id=site_id) as conn:
        # Validate site exists and is awaiting credentials
        site = await conn.fetchrow("""
            SELECT site_id, discovered_domain, awaiting_credentials
            FROM sites WHERE site_id = $1
        """, site_id)

        if not site:
            raise HTTPException(404, "Site not found")

        # Store credential (encrypted)
        # Using existing site_credentials table
        credential_data = {
            "host": creds.domain_name,  # Use domain as "host" identifier
            "username": creds.username,
            "password": creds.password,
            "domain": creds.domain_name,
        }
        
        await conn.execute("""
            INSERT INTO site_credentials (
                site_id, credential_type, credential_name, encrypted_data,
                created_at
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (site_id, credential_type, credential_name)
            DO UPDATE SET encrypted_data = $4, updated_at = $5
        """,
            site_id,
            creds.credential_type,
            f"domain_{creds.domain_name}",
            encrypt_credential(json.dumps(credential_data)),
            now,
        )

        # Clear awaiting_credentials flag
        await conn.execute("""
            UPDATE sites
            SET awaiting_credentials = false,
                credentials_submitted_at = $1
            WHERE site_id = $2
        """, now, site_id)
        
        # Trigger immediate enumeration AND scan via next checkin.
        # Intentional bulk: all appliances at this site should pick up
        # the new domain credentials + rescan on their next checkin.
        # Declare bulk intent via LOCAL flag so Migration 192 trigger
        # allows it; transaction-scoped so it doesn't leak to other
        # statements.
        await conn.execute("SET LOCAL app.allow_multi_row = 'true'")
        await conn.execute("""
            UPDATE site_appliances
            SET trigger_enumeration = true,
                trigger_immediate_scan = true
            WHERE site_id = $1
        """, site_id)

        logger.info(f"Domain credentials submitted for site {site_id}, enumeration triggered")
    
    return {
        "status": "ok", 
        "message": "Credentials saved - enumeration and first scan will begin immediately"
    }


@router.get("/{site_id}/deployment-status")
async def get_deployment_status(site_id: str, user: dict = Depends(require_auth)):
    """
    Get zero-friction deployment status for a site. Requires authentication.

    Returns current phase and progress details.
    """
    pool = await get_pool()
    
    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get site deployment state
        site = await conn.fetchrow("""
            SELECT 
                discovered_domain,
                domain_discovery_at,
                awaiting_credentials,
                credentials_submitted_at
            FROM sites
            WHERE site_id = $1
        """, site_id)

        if not site:
            raise HTTPException(404, "Site not found")

        # Determine current phase
        phase = "discovering"
        details = {}
        
        if site.get('discovered_domain'):
            domain_data = site['discovered_domain']
            if isinstance(domain_data, str):
                try:
                    domain_data = json.loads(domain_data)
                except (json.JSONDecodeError, TypeError):
                    domain_data = {}
            domain_name = domain_data.get('domain_name') if domain_data else None
            
            if site.get('awaiting_credentials'):
                phase = "awaiting_credentials"
                details['domain_discovered'] = domain_name
            elif site.get('credentials_submitted_at'):
                # Check if enumeration has run
                enum_result = await conn.fetchrow("""
                    SELECT 
                        total_servers,
                        total_workstations,
                        reachable_servers,
                        reachable_workstations,
                        enumeration_time
                    FROM enumeration_results
                    WHERE site_id = $1
                    ORDER BY enumeration_time DESC
                    LIMIT 1
                """, site_id)

                if enum_result:
                    details['servers_found'] = enum_result.get('total_servers', 0)
                    details['workstations_found'] = enum_result.get('total_workstations', 0)
                    phase = "enumerating"
                    
                    # Check if agents are being deployed
                    deployment_result = await conn.fetchrow("""
                        SELECT COUNT(*) as total, SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful
                        FROM agent_deployments
                        WHERE site_id = $1
                    """, site_id)

                    if deployment_result and deployment_result.get('total', 0) > 0:
                        phase = "deploying"
                        details['agents_deployed'] = deployment_result.get('successful', 0)
                        
                        # Check if all workstations have agents
                        if details.get('agents_deployed', 0) >= details.get('workstations_found', 0):
                            # Check if first scan has completed
                            first_scan = await conn.fetchrow("""
                                SELECT COUNT(*) as count
                                FROM compliance_bundles
                                WHERE site_id = $1
                                AND checked_at > $2
                            """, site_id, site.get('credentials_submitted_at'))

                            if first_scan and first_scan.get('count', 0) > 0:
                                phase = "scanning"
                                details['first_scan_complete'] = True
                                
                                # If scan complete, deployment is done
                                phase = "complete"
                else:
                    phase = "enumerating"
                    details['domain_discovered'] = domain_name
        
        # Calculate progress percentage
        phase_progress = {
            "discovering": 10,
            "awaiting_credentials": 20,
            "enumerating": 40,
            "deploying": 60,
            "scanning": 80,
            "complete": 100,
        }
        
        return {
            "phase": phase,
            "progress": phase_progress.get(phase, 0),
            "details": details,
        }


@router.get("/{site_id}/domain-credentials")
async def get_domain_credentials(site_id: str, user: dict = Depends(require_operator)):
    """
    Get domain credentials for a site (for appliance enumeration). Requires operator or admin access.

    Returns domain admin credentials if available.
    WARNING: This endpoint returns sensitive credentials - access is restricted to operators/admins.
    """
    pool = await get_pool()
    
    async with tenant_connection(pool, site_id=site_id) as conn:
        cred = await conn.fetchrow("""
            SELECT encrypted_data
            FROM site_credentials
            WHERE site_id = $1
            AND credential_type IN ('domain_admin', 'service_account')
            ORDER BY created_at DESC
            LIMIT 1
        """, site_id)

        if not cred or not cred['encrypted_data']:
            return None

        cred_data = json.loads(decrypt_credential(cred['encrypted_data']))

        return {
            "username": cred_data.get('username', ''),
            "password": cred_data.get('password', ''),
            "domain": cred_data.get('domain', ''),
        }


class AgentDeploymentReport(BaseModel):
    """Agent deployment results report from appliance."""
    site_id: str
    appliance_id: str
    deployments: List[dict]  # List of DeploymentResult dicts


@appliances_router.post("/agent-deployments")
async def report_agent_deployments(report: AgentDeploymentReport, auth_site_id: str = Depends(require_appliance_bearer)):
    """
    Receive Go agent deployment results from appliance.
    
    Stores deployment status in agent_deployments table.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with tenant_connection(pool, site_id=report.site_id) as conn:
        for deployment in report.deployments:
            await conn.execute("""
                INSERT INTO agent_deployments (
                    site_id, hostname, deployment_method, agent_version,
                    success, error_message, deployed_at, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (site_id, hostname) 
                DO UPDATE SET
                    deployment_method = EXCLUDED.deployment_method,
                    agent_version = EXCLUDED.agent_version,
                    success = EXCLUDED.success,
                    error_message = EXCLUDED.error_message,
                    deployed_at = EXCLUDED.deployed_at,
                    updated_at = EXCLUDED.updated_at
            """,
                report.site_id,
                deployment.get('hostname'),
                deployment.get('method', 'winrm'),
                deployment.get('agent_version'),
                deployment.get('success', False),
                deployment.get('error'),
                datetime.fromisoformat(deployment['deployed_at']) if deployment.get('deployed_at') else now,
                now,
                now,
            )
        
        logger.info(f"Stored {len(report.deployments)} agent deployment records")
    
    return {"status": "ok", "message": "Deployment results recorded"}


@appliances_router.post("/checkin")
async def appliance_checkin(checkin: ApplianceCheckin, request: Request, auth_site_id: str = Depends(require_appliance_bearer)):
    """Smart check-in with automatic deduplication.

    This endpoint implements smart deduplication logic to prevent duplicate
    appliance entries when:
    - An appliance re-provisions with a new MAC (NIC change)
    - An appliance re-provisions with a new hostname
    - Case differences in MAC addresses

    Deduplication rules (same site):
    1. If MAC matches existing appliance → update that appliance
    2. If hostname matches existing appliance (but different MAC) → merge entries
    3. Otherwise → create new appliance entry

    Returns pending orders and windows targets (credential-pull RMM pattern).
    """
    # SECURITY: Use auth_site_id from Bearer token, not checkin.site_id from body.
    # This prevents an appliance from spoofing another site's checkin.
    checkin.site_id = auth_site_id

    # HYGIENE (Session 209): drop APIPA / IPv6 link-local / junk IPs.
    # An appliance emerging from a DHCP outage may briefly carry a
    # 169.254.x address before a lease returns; persisting it makes
    # the admin console show the stale APIPA as "current" even after
    # recovery. Filter once at the edge so every downstream consumer
    # (rollup matview, heartbeat row, subnet-drift detector, mesh
    # target assigner) sees only routable IPs.
    if checkin.ip_addresses:
        _raw = list(checkin.ip_addresses)
        _clean = filter_routable_ips(_raw)
        if len(_clean) != len(_raw):
            logger.info(
                "checkin_ip_sanitize site=%s mac=%s dropped=%s kept=%s",
                checkin.site_id, checkin.mac_address,
                [ip for ip in _raw if ip not in _clean], _clean,
            )
        checkin.ip_addresses = _clean

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # Normalize inputs
    mac_normalized = normalize_mac(checkin.mac_address)
    hostname_lower = checkin.hostname.lower().strip()

    # Generate appliance_id from site_id + normalized MAC
    mac_clean = mac_normalized.replace(':', '')
    appliance_id = f"{checkin.site_id}-{mac_normalized}"

    # Week 1+4+5 of the composed identity stack:
    #   * Verify the X-Appliance-Signature when present
    #   * Record the outcome in sigauth_observations
    #   * If this appliance is in 'enforce' mode (Week 5) AND the
    #     signature is missing or invalid, return 401 BEFORE doing
    #     any state-changing work. Bearer auth above already
    #     authenticated the caller; enforcement adds the device-bound
    #     signature requirement on top.
    #
    # Hard kill switch: SIGAUTH_GLOBAL_ENFORCE_OVERRIDE=disabled in the
    # process env disables enforcement fleet-wide instantly without
    # touching the DB. Phase 5C operational lever.
    sig_result = None
    # Task #168/#169 fix (Session 212, 2026-04-28). Wrap the verify
    # path's queries in an EXPLICIT TRANSACTION + re-issue
    # `SET LOCAL app.is_admin TO 'true'` inside it. PgBouncer in
    # transaction-pooling mode assigns ONE backend per transaction,
    # so SET LOCAL pins the admin context to that backend for every
    # subsequent query in the same txn.
    #
    # Pre-fix mechanism: `admin_connection` issues a session-level
    # `SET app.is_admin TO 'true'`, but each subsequent autocommit
    # query is its own implicit transaction — PgBouncer can route
    # SET and SELECT to different backends. When that happens, the
    # SELECT runs without admin context and Migration 234's
    # is_admin-default-false RLS hides the row → `_resolve_pubkey`
    # returns None → checkin gets `unknown_pubkey` 401. Observed
    # 3-4 events / 72h on north-valley-branch-2 (#168). The
    # sigauth_observations INSERT keeps its own savepoint so a
    # constraint failure doesn't poison the outer txn (asyncpg
    # savepoint invariant from Session 205).
    try:
        from .signature_auth import verify_appliance_signature
        body_bytes = await request.body()
        async with admin_connection(pool) as _sigauth_conn:
            async with _sigauth_conn.transaction():
                await _sigauth_conn.execute("SET LOCAL app.is_admin TO 'true'")
                sig_result = await verify_appliance_signature(
                    request, _sigauth_conn,
                    site_id=checkin.site_id,
                    mac_address=mac_normalized,
                    body_bytes=body_bytes,
                )
                if sig_result.present:
                    try:
                        async with _sigauth_conn.transaction():
                            await _sigauth_conn.execute(
                                """
                                INSERT INTO sigauth_observations
                                      (site_id, mac_address, valid, reason, fingerprint)
                                VALUES ($1, $2, $3, $4, $5)
                                """,
                                checkin.site_id, mac_normalized,
                                sig_result.valid, sig_result.reason or "",
                                sig_result.pubkey_fingerprint or None,
                            )
                    except Exception:  # noqa: BLE001
                        # Session 205 "no silent write failures" rule:
                        # DB writes log-and-raise (or log-and-savepoint).
                        # logger.warning is BANNED on write failures.
                        logger.error("sigauth_observations insert failed", exc_info=True)

                # Week 5: per-appliance enforcement check. Look up the
                # row's signature_enforcement value. Default 'observe'
                # if the appliance row doesn't exist yet (first checkin).
                enforce_row = await _sigauth_conn.fetchrow(
                    """
                    SELECT signature_enforcement
                      FROM site_appliances
                     WHERE site_id = $1 AND mac_address = $2 AND deleted_at IS NULL
                    """,
                    checkin.site_id, mac_normalized,
                )
                sig_mode = (enforce_row["signature_enforcement"] if enforce_row else "observe")

        if sig_result.present:
            logger.info(
                "sigauth observed: site=%s mac=%s valid=%s reason=%s fp=%s mode=%s",
                checkin.site_id, mac_normalized,
                sig_result.valid, sig_result.reason,
                sig_result.pubkey_fingerprint, sig_mode,
            )

        # Enforcement gate. Global env override wins over the per-row
        # value so an operator can disable enforcement instantly
        # without a DB write.
        global_override = os.environ.get("SIGAUTH_GLOBAL_ENFORCE_OVERRIDE", "").strip().lower()
        if global_override != "disabled" and sig_mode == "enforce":
            if not sig_result.present or not sig_result.valid:
                logger.warning(
                    "sigauth enforce 401: site=%s mac=%s present=%s valid=%s reason=%s",
                    checkin.site_id, mac_normalized,
                    sig_result.present, sig_result.valid, sig_result.reason,
                )
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "appliance signature required for enforce-mode checkin",
                        "code": "SIGAUTH_ENFORCE_REJECTED",
                        "reason": sig_result.reason or "no_headers",
                    },
                )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — never let observation kill checkin
        logger.warning("sigauth observation hook raised", exc_info=True)

    # Session 206 D2: tag the authenticated appliance so the cross-appliance
    # audit trigger (Migration 197/199) can reject any UPDATE that touches
    # a different appliance's row.
    _auth_actor_aid = appliance_id

    # === STEP -1: Live-USB installer isolation (Migration 190) ===
    # If the daemon reports boot_source='live_usb', it's running from the
    # installer ISO — not an installed appliance. Route to install_sessions
    # (ephemeral, 24h TTL) instead of site_appliances so phantom rows never
    # pollute the fleet. Return the minimum shape needed to keep the daemon
    # alive until install completes.
    boot_source_early = getattr(checkin, 'boot_source', None) or ''
    if boot_source_early == 'live_usb':
        async with tenant_connection(pool, site_id=checkin.site_id, actor_appliance_id=_auth_actor_aid) as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO install_sessions (
                        session_id, site_id, mac_address, hostname, ip_addresses,
                        agent_version, nixos_version, boot_source,
                        first_seen, last_seen, checkin_count, install_stage, expires_at
                    ) VALUES (
                        $1, $2, $3, $4, $5::jsonb, $6, $7, 'live_usb',
                        $8::timestamptz, $8::timestamptz, 1, 'live_usb',
                        $8::timestamptz + INTERVAL '24 hours'
                    )
                    ON CONFLICT (session_id) DO UPDATE SET
                        hostname = EXCLUDED.hostname,
                        ip_addresses = EXCLUDED.ip_addresses,
                        agent_version = EXCLUDED.agent_version,
                        nixos_version = EXCLUDED.nixos_version,
                        last_seen = EXCLUDED.last_seen,
                        checkin_count = install_sessions.checkin_count + 1,
                        expires_at = EXCLUDED.last_seen + INTERVAL '24 hours'
                """,
                    f"{checkin.site_id}:{mac_normalized}",
                    checkin.site_id,
                    mac_normalized,
                    checkin.hostname,
                    json.dumps(checkin.ip_addresses or []),
                    checkin.agent_version,
                    checkin.nixos_version,
                    now,
                )
        logger.info(
            f"install_session site={checkin.site_id} mac={mac_normalized} "
            f"hostname={checkin.hostname} agent_version={checkin.agent_version}"
        )
        return {
            "status": "ok",
            "appliance_id": None,
            "install_session": True,
            "server_time": now.isoformat(),
            "rotated_api_key": None,
            "server_public_key": None,
            "server_public_keys": [],
            "merged_duplicates": 0,
            "pending_orders": [],
            "windows_targets": [],
            "linux_targets": [],
            "encrypted_credentials": [],
            "enabled_runbooks": [],
            "disabled_checks": [],
            "maintenance_until": None,
            "trigger_enumeration": False,
            "trigger_immediate_scan": False,
            "billing_hold": False,
            "billing_status": None,
            "pending_deploys": [],
            "l2_confidence_threshold": 0.8,
            "peer_bundle_hashes": [],
            "mesh_peers": [],
            "target_assignments": [],
            "client_alert_mode": None,
            "compliance_framework": None,
            "reconcile_plan": None,
        }

    async with tenant_connection(pool, site_id=checkin.site_id, actor_appliance_id=_auth_actor_aid) as conn:
      # Micro-transaction architecture (Session 200):
      # tenant_connection wraps in one transaction for SET LOCAL RLS.
      # Each step uses its own savepoint (conn.transaction()) for isolation.
      # Failure in any optional step does NOT abort the checkin.
      # Core identity (STEP 0-3) runs bare — failure aborts the whole checkin.
      async with conn.transaction():
        # === STEP 0: Process deploy results from previous checkin cycle ===
        if checkin.deploy_results:
            try:
                async with conn.transaction():
                    for result in checkin.deploy_results:
                        new_status = "agent_active" if result.get("status") == "success" else "deploy_failed"
                        await conn.execute("""
                            UPDATE discovered_devices
                            SET device_status = $1,
                                agent_deploy_error = $2,
                                deploy_attempts = deploy_attempts + 1
                            WHERE site_id = $3 AND local_device_id = $4
                        """, new_status, result.get("error"), checkin.site_id, result.get("device_id"))

                        # Mark sensor_deployed on the credential record for this host
                        deploy_hostname = result.get("hostname")
                        if result.get("status") == "success" and deploy_hostname:
                            await conn.execute("""
                                UPDATE site_credentials
                                SET sensor_deployed = true,
                                    sensor_deployed_at = NOW()
                                WHERE site_id = $1
                                  AND credential_name LIKE $2 || ' (%'
                                  AND (sensor_deployed IS NOT TRUE)
                            """, checkin.site_id, deploy_hostname)
            except Exception as e:
                import logging
                logging.warning(f"Checkin {checkin.site_id}: failed to process deploy results: {e}")

        # Default values — may be overridden by ghost detection or Step 1
        canonical_id = appliance_id
        merge_from_ids = []
        earliest_first_checkin = now

        # === STEP 0.9: Multi-NIC ghost detection ===
        # A physical machine with two NICs can register as two appliances if the
        # daemon alternates which NIC's MAC it sends. Two detection methods:
        #
        # Method 1 (v0.3.86+): Daemon sends all_mac_addresses — if any MAC in the
        #   list matches an existing appliance at this site, it's the same machine.
        # Method 2 (fallback): If another appliance at this site shares an IP and
        #   checked in within the last 5 seconds, it's the same machine.
        _ghost_detected = False

        # Method 1: all_mac_addresses overlap (definitive, no timing dependency)
        if not _ghost_detected and checkin.all_mac_addresses and len(checkin.all_mac_addresses) > 1:
            for other_mac in checkin.all_mac_addresses:
                other_mac_clean = other_mac.upper().replace(":", "").replace("-", "")
                if other_mac_clean == mac_clean.upper():
                    continue  # skip self
                other_aid = f"{checkin.site_id}-{normalize_mac(other_mac)}"
                mac_overlap = await conn.fetchrow(
                    "SELECT appliance_id, mac_address FROM site_appliances WHERE appliance_id = $1",
                    other_aid,
                )
                if mac_overlap:
                    logger.warning(
                        f"Multi-NIC ghost detected (MAC list): {mac_normalized} is secondary NIC on "
                        f"same machine as {mac_overlap['appliance_id']}. Skipping registration."
                    )
                    canonical_id = mac_overlap['appliance_id']
                    _ghost_detected = True
                    break

        # Method 2: IP + timing overlap (fallback for daemons that don't send all_mac_addresses)
        # Exclude IP ranges that are NOT unique per physical machine:
        #   - 169.254.0.0/16 link-local: every interface has one, siblings always overlap
        #   - 10.100.0.0/16 WireGuard tunnel: shared topology, overlap is expected
        #   - 127.0.0.0/8 loopback: identical on every machine
        # Leaving these in the probe produced a false-positive cascade where
        # two real appliances at the same site flip-flopped as each other's
        # ghosts, routing fleet orders to the wrong canonical_id.
        if not _ghost_detected and checkin.ip_addresses:
            probe_ips = [
                ip for ip in checkin.ip_addresses
                if ip
                and not ip.startswith("169.254.")
                and not ip.startswith("10.100.")
                and not ip.startswith("127.")
            ]
            if probe_ips:
                ip_overlap = await conn.fetchrow("""
                    SELECT appliance_id, mac_address, hostname
                    FROM site_appliances
                    WHERE site_id = $1
                      AND appliance_id != $2
                      AND last_checkin > NOW() - INTERVAL '5 seconds'
                      AND ip_addresses::jsonb ?| $3::text[]
                """, checkin.site_id, appliance_id, probe_ips)
                if ip_overlap:
                    logger.warning(
                        f"Multi-NIC ghost detected (IP overlap): MAC {mac_normalized} shares IP with "
                        f"{ip_overlap['appliance_id']} (MAC {ip_overlap['mac_address']}). "
                        f"Skipping duplicate registration — same physical machine."
                    )
                    canonical_id = ip_overlap['appliance_id']
                    _ghost_detected = True

        if _ghost_detected:
            # Skip Steps 1-3 — use the canonical appliance from the ghost check
            last_checkin_time = await conn.fetchval(
                "SELECT last_checkin FROM site_appliances WHERE appliance_id = $1",
                canonical_id
            )
        else:
            # === STEP 1: Find existing appliances with same MAC ===
            # MAC is the primary identity for physical appliances. Hostname matching
            # was removed because multiple appliances per site often share the same
            # hostname (e.g., NixOS defaults to "osiriscare"), causing false merges.
            # Use FOR UPDATE to prevent concurrent check-ins from racing.
            existing = await conn.fetch("""
                SELECT appliance_id, hostname, mac_address, first_checkin
                FROM site_appliances
                WHERE site_id = $1
                AND UPPER(REPLACE(REPLACE(mac_address, ':', ''), '-', '')) = $2
                ORDER BY last_checkin DESC NULLS LAST
                FOR UPDATE
            """, checkin.site_id, mac_clean.upper())

            merge_from_ids = []
            earliest_first_checkin = now

            if existing:
                # Find the "canonical" entry (oldest first_checkin) - this is the one we keep
                for row in existing:
                    if row['first_checkin'] and row['first_checkin'] < earliest_first_checkin:
                        earliest_first_checkin = row['first_checkin']
                        canonical_id = row['appliance_id']

                # All other entries are duplicates to merge
                for row in existing:
                    if row['appliance_id'] != canonical_id:
                        merge_from_ids.append(row['appliance_id'])

            # === STEP 2: Delete duplicates (if any) ===
            if merge_from_ids:
                await conn.execute("""
                    DELETE FROM site_appliances
                    WHERE appliance_id = ANY($1)
                """, merge_from_ids)

            # Fetch previous last_checkin for credential freshness comparison
            last_checkin_time = await conn.fetchval(
                "SELECT last_checkin FROM site_appliances WHERE appliance_id = $1",
                canonical_id
            )

        # === STEP 2.9: Live USB detection ===
        # If the appliance reports boot_source="live_usb", it's running from the
        # installer ISO — NOT from an installed system. Flag it prominently.
        # This prevents the bug where an admin accepts a MAC, pulls the USB early,
        # and thinks the appliance is deployed when the disk was never written.
        _boot_source = getattr(checkin, 'boot_source', None) or 'unknown'
        if _boot_source == 'live_usb':
            logger.warning(
                f"INSTALL INCOMPLETE: appliance {canonical_id} is running from LIVE USB, "
                f"not installed disk. Installation may not have completed. "
                f"hostname={checkin.hostname} mac={mac_normalized}"
            )

        # Bootstrap → installed transition: auto-teardown WireGuard.
        # When an appliance transitions from live_usb to installed_disk, the
        # bootstrap phase is complete. WireGuard served its provisioning purpose —
        # tear it down. This is the automatic enforcement of "no persistent access."
        if _boot_source == 'installed_disk' and not _ghost_detected:
            prev_health = await conn.fetchval(
                "SELECT daemon_health->>'boot_source' FROM site_appliances WHERE appliance_id = $1",
                canonical_id
            )
            if prev_health == 'live_usb':
                logger.info(
                    f"Bootstrap complete: {canonical_id} transitioned from live_usb to installed_disk. "
                    f"Auto-disabling WireGuard tunnel."
                )
                try:
                    from .fleet_updates import create_fleet_order_for_site
                    await create_fleet_order_for_site(
                        conn,
                        site_id=checkin.site_id,
                        order_type="disable_emergency_access",
                        parameters={"disabled_by": "bootstrap_auto_teardown", "reason": "install complete"},
                        expires_hours=1,
                    )
                except Exception as e:
                    logger.warning(f"Bootstrap WG teardown order failed: {e}")

        # Merge boot_source into daemon_health for storage (no schema change needed)
        _health = dict(checkin.daemon_health) if checkin.daemon_health else {}
        _health['boot_source'] = _boot_source
        _health_json = json.dumps(_health)

        # === STEP 3: Upsert the canonical appliance entry (skip for ghosts) ===
        # recovered_at is stamped atomically inside the upsert when the
        # prior status was 'offline'. STEP 3.0a below reads it and fires
        # the recovery alert.
        if not _ghost_detected:
            await conn.execute("""
                INSERT INTO site_appliances (
                    site_id, appliance_id, hostname, mac_address, ip_addresses,
                    agent_version, nixos_version, status, uptime_seconds,
                    first_checkin, last_checkin, daemon_health
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, 'online', $8, $9, $10, $11::jsonb)
                ON CONFLICT (appliance_id) DO UPDATE SET
                    hostname = EXCLUDED.hostname,
                    mac_address = EXCLUDED.mac_address,
                    ip_addresses = EXCLUDED.ip_addresses,
                    agent_version = EXCLUDED.agent_version,
                    nixos_version = EXCLUDED.nixos_version,
                    status = 'online',
                    uptime_seconds = EXCLUDED.uptime_seconds,
                    last_checkin = EXCLUDED.last_checkin,
                    daemon_health = COALESCE(EXCLUDED.daemon_health, site_appliances.daemon_health),
                    offline_since = NULL,
                    offline_notified = false,
                    recovered_at = CASE
                        WHEN site_appliances.status = 'offline' THEN NOW()
                        ELSE site_appliances.recovered_at
                    END,
                    deleted_at = NULL,
                    deleted_by = NULL
            """,
                checkin.site_id,
                canonical_id,
                checkin.hostname,
                mac_normalized,
                json.dumps(checkin.ip_addresses),
                checkin.agent_version,
                checkin.nixos_version,
                checkin.uptime_seconds,
                earliest_first_checkin,
                now,
                _health_json,
            )

        # === STEP 3.0x: v40.4 first_outbound_success_at stamp ===
        # Round-table Rec #4 (2026-04-23): install_sessions.first_outbound_
        # success_at was historically set by the installer itself via
        # /api/install/report/net-ready. That makes the column USELESS for
        # detecting the installer-ran-once-then-silent class that bricked
        # v40.0-v40.2 — the installer hit /start, completed the install,
        # rebooted, and the installed system's 4-stage gate was what
        # actually broke. The installer never called /net-ready so the
        # column stayed NULL even after a perfectly-working installed
        # system was checking in. provisioning_network_fail (Session 209)
        # keys off this column and thus stayed blind.
        #
        # Fix: stamp first_outbound_success_at when the installed system
        # posts its FIRST successful checkin — we're here, we auth'd,
        # this IS outbound success. Scoped to install_sessions rows < 24h
        # old so we don't retrocauselessly stamp ancient rows. Idempotent:
        # IS NULL guard means repeat checkins don't churn the column.
        # Savepoint-isolated so any failure (missing row, race) doesn't
        # abort the parent checkin transaction.
        if not _ghost_detected:
            try:
                async with conn.transaction():
                    await conn.execute("""
                        UPDATE install_sessions
                           SET first_outbound_success_at = NOW()
                         WHERE UPPER(mac_address) = UPPER($1)
                           AND site_id = $2
                           AND first_outbound_success_at IS NULL
                           AND first_seen > NOW() - INTERVAL '24 hours'
                    """, mac_normalized, checkin.site_id)
            except Exception:
                logger.error(
                    "sites.checkin: install_sessions first_outbound stamp failed",
                    exc_info=True,
                    extra={"mac": mac_normalized, "site_id": checkin.site_id},
                )

        # === STEP 3.0: Append heartbeat row (Migration 191) ===
        # One row per checkin. Append-only. Used for cadence detection,
        # uptime SLA, and dashboard rollup. Savepoint-isolated so an
        # insert failure (e.g., partition missing) cannot abort the
        # parent checkin transaction.
        if not _ghost_detected:
            try:
                async with conn.transaction():
                    _hb_subnet = _primary_subnet(checkin.ip_addresses or [])
                    _hb_has_anycast = ANYCAST_LINK_LOCAL in (checkin.ip_addresses or [])
                    # D1: optional signed-heartbeat signature from the Go
                    # daemon. Current daemon builds don't send it — column
                    # will be NULL for pre-D1 daemons. When the daemon PR
                    # ships, checkin.heartbeat_signature will carry an
                    # Ed25519 hex over the heartbeat content hash.
                    _hb_sig = getattr(checkin, 'heartbeat_signature', None)
                    await conn.execute("""
                        INSERT INTO appliance_heartbeats
                            (site_id, appliance_id, observed_at, status,
                             agent_version, boot_source, primary_subnet,
                             has_anycast, agent_signature)
                        VALUES ($1, $2, $3, 'online', $4, $5, $6, $7, $8)
                    """,
                        checkin.site_id,
                        canonical_id,
                        now,
                        checkin.agent_version,
                        _boot_source,
                        _hb_subnet,
                        _hb_has_anycast,
                        _hb_sig,
                    )
            except Exception:
                logger.error(
                    f"heartbeat insert failed for {canonical_id}",
                    exc_info=True,
                )

        # STEP 3.0a: detect + announce offline→online recovery.
        # We rely on `offline_since IS NOT NULL BEFORE the UPSERT` which is
        # captured via `recovered_at` being set by the UPDATE above. A
        # non-NULL recovered_at that's within this request window means we
        # just transitioned. Running in the checkin transaction is OK —
        # recovered_at is idempotent once set.
        if not _ghost_detected:
            try:
                async with conn.transaction():
                    rec = await conn.fetchrow("""
                        SELECT display_name, hostname, recovered_at, offline_event_count
                        FROM site_appliances
                        WHERE appliance_id = $1
                          AND recovered_at IS NOT NULL
                          AND recovered_at > NOW() - INTERVAL '30 seconds'
                    """, canonical_id)
                if rec:
                    label = rec["display_name"] or rec["hostname"] or canonical_id
                    logger.info(
                        f"Appliance recovered from offline: appliance_id={canonical_id} "
                        f"site_id={checkin.site_id} display_name={label}"
                    )
                    # Per-site coalesce (Steve adversarial round-table
                    # 2026-05-02). When a site-wide network event recovers
                    # and all N appliances flip back online within seconds
                    # of each other, ship ONE batch email — not N. Use
                    # earliest-in-60s-window-wins: query all siblings at
                    # this site that recovered in the last 60s; only
                    # actually email if THIS appliance is the earliest.
                    # Race window: two recoveries arriving simultaneously
                    # may both think they're earliest → 2 emails instead
                    # of 1. Acceptable trade-off; still dramatically
                    # better than N emails per site flap.
                    try:
                        async with conn.transaction():
                            batch = await conn.fetch("""
                                SELECT appliance_id, display_name, hostname,
                                       recovered_at, offline_event_count
                                  FROM site_appliances
                                 WHERE site_id = $1
                                   AND recovered_at IS NOT NULL
                                   AND recovered_at > NOW() - INTERVAL '60 seconds'
                                   AND deleted_at IS NULL
                              ORDER BY recovered_at ASC
                            """, checkin.site_id)
                        if batch and batch[0]["appliance_id"] == canonical_id:
                            from dashboard_api.email_alerts import send_critical_alert
                            n = len(batch)
                            labels = [
                                r["display_name"] or r["hostname"] or r["appliance_id"]
                                for r in batch
                            ]
                            if n == 1:
                                title = f"Appliance recovered: {label}"
                                message = (
                                    f"Appliance {label} at site {checkin.site_id} "
                                    f"resumed check-ins. Lifetime offline events: "
                                    f"{rec['offline_event_count']}."
                                )
                            else:
                                title = f"{n} appliances recovered at {checkin.site_id}"
                                message = (
                                    f"{n} appliances at site {checkin.site_id} "
                                    f"resumed check-ins within the last 60 seconds: "
                                    f"{', '.join(labels)}. Most likely a site-wide "
                                    f"network event resolved (LAN, WAN, or upstream "
                                    f"ISP) rather than {n} independent recoveries."
                                )
                            send_critical_alert(
                                title=title,
                                message=message,
                                site_id=checkin.site_id,
                                category="appliance_health",
                                severity="info",
                                metadata={
                                    "appliance_count": n,
                                    "appliance_ids": [r["appliance_id"] for r in batch],
                                    "labels": labels,
                                    "event": "appliance_recovered_batch" if n > 1 else "appliance_recovered",
                                },
                            )
                        else:
                            logger.info(
                                f"recovery alert suppressed (sibling already alerted within 60s) "
                                f"appliance_id={canonical_id} site_id={checkin.site_id}"
                            )
                    except Exception:
                        logger.error(
                            f"Failed to send appliance_recovered alert for {canonical_id}",
                            exc_info=True,
                        )
            except Exception as e:
                logger.error(
                    f"Appliance recovery detection failed for {canonical_id}",
                    exc_info=True,
                )

        # === STEP 3.3: Auto-generate display_name if missing ===
        # Uniqueness-enforced per (site_id, display_name). Prior logic counted
        # rows sharing checkin.hostname and appended `-{count}`, which silently
        # collided when two appliances had different hostnames (e.g. `osiriscare`
        # and `osiriscare-installer`) but the counter produced the same `-N`
        # suffix — observed on `north-valley-branch-2` where BOTH the t740
        # install-loop box and the real .227 appliance ended up as
        # `osiriscare-3`, leaving operators unable to tell which "osiriscare-3"
        # the dashboard was showing online / offline.
        #
        # New logic:
        #  - Installer-boxes (hostname contains "installer") get a MAC-suffix
        #    label (e.g. `osiriscare-installer-1FFFE4`) so it's instantly
        #    obvious they're on the live USB, and each mac stays stable.
        #  - Everyone else starts with candidate=hostname and increments a
        #    `-{N}` counter UNTIL no other row at the site has the same
        #    display_name. Guarantees per-site uniqueness without relying
        #    on hostname collision counting.
        if not _ghost_detected:
            try:
                current_display = await conn.fetchval(
                    "SELECT display_name FROM site_appliances WHERE appliance_id = $1",
                    canonical_id
                )
                if not current_display:
                    host = checkin.hostname or "appliance"
                    if "installer" in host.lower() and checkin.mac_address:
                        mac_suffix = checkin.mac_address.replace(":", "").upper()[-6:]
                        display = f"{host}-{mac_suffix}"
                    else:
                        display = host

                    # Probe + increment until unique at this site. Bounded so a
                    # runaway count can't spin forever on a pathological dataset.
                    for counter in range(2, 200):
                        clash = await conn.fetchval("""
                            SELECT 1 FROM site_appliances
                             WHERE site_id = $1
                               AND display_name = $2
                               AND appliance_id != $3
                               AND deleted_at IS NULL
                             LIMIT 1
                        """, checkin.site_id, display, canonical_id)
                        if not clash:
                            break
                        display = f"{host}-{counter}"
                    await conn.execute(
                        "UPDATE site_appliances SET display_name = $1 WHERE appliance_id = $2",
                        display, canonical_id
                    )
                    logger.info(f"Auto-generated display_name '{display}' for {canonical_id}")
            except Exception as e:
                logger.debug(f"Display name generation skipped: {e}")

        # === STEP 3.4: Stale device cleanup on subnet change ===
        # When an appliance moves subnets (e.g., 192.168.88.x → 192.168.1.x),
        # devices discovered on the old subnet become unreachable phantoms.
        # Detect subnet change by comparing IP prefixes and mark old devices stale.
        #
        # v36 addition: also write a compliance_bundle for the move
        # (HIPAA §164.310(d)(1) — hardware movement tracking). The bundle
        # is the DETECTION half of the chain; an operator ACKNOWLEDGMENT
        # bundle with reason is added via
        # POST /api/admin/appliances/{id}/acknowledge-relocation.
        # If not acknowledged within 24h, the substrate invariant
        # `appliance_moved_unack` fires on the admin panel.
        if not _ghost_detected and last_checkin_time and checkin.ip_addresses:
            try:
                old_ips_row = await conn.fetchval(
                    "SELECT ip_addresses FROM site_appliances WHERE appliance_id = $1",
                    canonical_id
                )
                if old_ips_row:
                    import json as _json
                    old_ips = _json.loads(old_ips_row) if isinstance(old_ips_row, str) else old_ips_row
                    old_subnets = {'.'.join(ip.split('.')[:3]) for ip in old_ips if ip and not ip.startswith('169.254') and not ip.startswith('10.100')}
                    new_subnets = {'.'.join(ip.split('.')[:3]) for ip in checkin.ip_addresses if ip and not ip.startswith('169.254') and not ip.startswith('10.100')}
                    lost_subnets = old_subnets - new_subnets
                    if lost_subnets:
                        logger.info(
                            f"Subnet change detected for {canonical_id}: lost {lost_subnets}, "
                            f"now on {new_subnets}. Marking old-subnet devices stale."
                        )
                        for subnet in lost_subnets:
                            await conn.execute("""
                                UPDATE discovered_devices
                                SET device_status = 'stale_subnet_move',
                                    sync_updated_at = NOW()
                                WHERE site_id = $1
                                  AND ip_address LIKE $2
                                  AND device_status NOT IN ('ignored', 'stale_subnet_move')
                            """, checkin.site_id, f"{subnet}.%")

                        # v36: attestation half — write the detection
                        # compliance_bundle. Separate savepoint because
                        # signing-backend hiccups must not poison the
                        # parent checkin transaction.
                        try:
                            from dashboard_api.appliance_relocation import (
                                detect_and_record_relocation,
                            )
                        except ImportError:
                            from appliance_relocation import (  # type: ignore
                                detect_and_record_relocation,
                            )
                        async with conn.transaction():
                            await detect_and_record_relocation(
                                conn,
                                site_id=checkin.site_id,
                                appliance_id=canonical_id,
                                mac_address=checkin.mac_address or "",
                                hostname=checkin.hostname,
                                previous_ips=list(old_ips or []),
                                current_ips=list(checkin.ip_addresses or []),
                            )
            except Exception as e:
                logger.debug(f"Subnet change check skipped: {e}")

        # === STEP 3.5: legacy appliances-table sync — REMOVED in M1 ===
        # Pre-M1 we mirrored checkin state into the legacy `appliances` table
        # for Fleet Updates compatibility. The reconciliation_loop copied it
        # back to site_appliances (see Session 206 audit for how that masked
        # offline state). M1 drops the legacy table: site_appliances is
        # already updated earlier in the checkin flow, so no second write
        # is needed here.

        # === STEP 3.5b: Persist time-travel state (Session 205 Phase 2) ===
        # Store the daemon's reported boot_counter + generation_uuid. The
        # `GREATEST()` for boot_counter tracks the highest-ever value so a
        # future regression is detectable server-side. generation_uuid
        # only updates when the daemon has written one (first checkin
        # after a reconcile or initial baseline).
        # Wrapped in savepoint — persistence failures must not poison the
        # outer checkin transaction.
        if checkin.boot_counter is not None or checkin.generation_uuid:
            try:
                async with conn.transaction():
                    await conn.execute("""
                        UPDATE site_appliances
                        SET boot_counter = GREATEST(COALESCE(boot_counter, 0), COALESCE($1::BIGINT, 0)),
                            generation_uuid = COALESCE(NULLIF($2, '')::uuid, generation_uuid)
                        WHERE appliance_id = $3 AND site_id = $4
                    """, checkin.boot_counter, checkin.generation_uuid,
                        canonical_id, checkin.site_id)
            except Exception as e:
                # Transactional step failure — escalated to error (see note above).
                logger.error(
                    f"Checkin {checkin.site_id}: time-travel state persist failed: {e}"
                )

        # === STEP 3.6: Register/update agent signing key ===
        # Per-appliance signing keys (Session 196): write to
        # site_appliances.agent_public_key scoped by (site_id, mac),
        # NOT to sites.agent_public_key. Multi-appliance sites would
        # otherwise alternate-overwrite a single site-level row on
        # every checkin while the per-MAC row stays stale, breaking
        # sigauth (which reads from site_appliances). The 2026-04-25
        # signature_verification_failures invariant on north-valley-
        # branch-2 fired exactly this way: two daemons at one site
        # ROTATED the dead sites.agent_public_key 100+ times/hour
        # while their site_appliances rows held a single shared key
        # that didn't match either daemon's private half.
        if checkin.agent_public_key and len(checkin.agent_public_key) == 64:
            try:
                async with conn.transaction():
                    existing_key = await conn.fetchval(
                        """
                        SELECT agent_public_key FROM site_appliances
                         WHERE site_id = $1 AND mac_address = $2
                           AND deleted_at IS NULL
                        """,
                        checkin.site_id, mac_normalized,
                    )
                    if existing_key != checkin.agent_public_key:
                        await conn.execute(
                            """
                            UPDATE site_appliances
                               SET agent_public_key = $1
                             WHERE site_id = $2 AND mac_address = $3
                               AND deleted_at IS NULL
                            """,
                            checkin.agent_public_key,
                            checkin.site_id,
                            mac_normalized,
                        )
                        if existing_key:
                            logger.warning(
                                f"Agent signing key ROTATED for site={checkin.site_id} "
                                f"mac={mac_normalized} "
                                f"old={existing_key[:12]}... new={checkin.agent_public_key[:12]}..."
                            )
                        else:
                            logger.info(
                                f"Agent signing key registered for site={checkin.site_id} "
                                f"mac={mac_normalized} "
                                f"key={checkin.agent_public_key[:12]}..."
                            )
            except Exception as e:
                logger.error(
                    f"Failed to register agent public key for "
                    f"site={checkin.site_id} mac={mac_normalized}: {e}",
                    exc_info=True,
                )

        # === STEP 3.6c: Register/update agent IDENTITY signing key (#179) ===
        # The daemon has TWO Ed25519 keypairs by design (key separation):
        # the EVIDENCE key (above, used to sign evidence bundles) and
        # the IDENTITY key (this block, used by phonehome.go::signRequest
        # to sign sigauth headers). Until v0.4.13 the daemon only
        # uploaded the evidence key; signature_auth.py's legacy fallback
        # then verified sigauth against the wrong key and the substrate
        # signature_verification_failures invariant fired 100% on
        # affected sites. This block persists the identity key per
        # (site_id, mac_address); signature_auth.py::_resolve_pubkey
        # will read this column ahead of the legacy view fallback.
        # Daemons running pre-v0.4.13 won't supply the field — we
        # tolerate the absence gracefully (None → no-op).
        if (
            checkin.agent_identity_public_key
            and len(checkin.agent_identity_public_key) == 64
        ):
            try:
                async with conn.transaction():
                    existing_id_key = await conn.fetchval(
                        """
                        SELECT agent_identity_public_key FROM site_appliances
                         WHERE site_id = $1 AND mac_address = $2
                           AND deleted_at IS NULL
                        """,
                        checkin.site_id, mac_normalized,
                    )
                    if existing_id_key != checkin.agent_identity_public_key:
                        await conn.execute(
                            """
                            UPDATE site_appliances
                               SET agent_identity_public_key = $1
                             WHERE site_id = $2 AND mac_address = $3
                               AND deleted_at IS NULL
                            """,
                            checkin.agent_identity_public_key,
                            checkin.site_id,
                            mac_normalized,
                        )
                        if existing_id_key:
                            logger.warning(
                                f"Agent IDENTITY key ROTATED for "
                                f"site={checkin.site_id} mac={mac_normalized} "
                                f"old={existing_id_key[:12]}... "
                                f"new={checkin.agent_identity_public_key[:12]}..."
                            )
                        else:
                            logger.info(
                                f"Agent IDENTITY key registered for "
                                f"site={checkin.site_id} mac={mac_normalized} "
                                f"key={checkin.agent_identity_public_key[:12]}..."
                            )
            except Exception as e:
                logger.error(
                    f"Failed to register agent IDENTITY key for "
                    f"site={checkin.site_id} mac={mac_normalized}: {e}",
                    exc_info=True,
                )

        # === STEP 3.6b: Update WireGuard VPN status ===
        if checkin.wg_connected and checkin.wg_ip:
            try:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE sites SET wg_connected_at = NOW(), wg_ip = $1 WHERE site_id = $2",
                        checkin.wg_ip, checkin.site_id
                    )
            except Exception as e:
                logger.error(f"Checkin {checkin.site_id}: WireGuard status update failed: {e}")

        # === STEP 3.7: Sync connected Go agents to go_agents table ===
        # Use a savepoint so failures here don't poison the outer transaction
        if checkin.connected_agents:
            try:
                async with admin_connection(pool) as admin_conn:
                    async with admin_conn.transaction():
                        for agent in checkin.connected_agents:
                            def _parse_ts(s):
                                if not s:
                                    return datetime.utcnow()
                                try:
                                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                                    return dt.replace(tzinfo=None)
                                except (ValueError, AttributeError):
                                    return datetime.utcnow()
                            connected_at_dt = _parse_ts(agent.connected_at)
                            # Agent is in the checkin payload = actively connected NOW.
                            # Use server time, not the daemon's relayed timestamp which
                            # may be stale or zero-time.
                            last_heartbeat_dt = datetime.utcnow()
                            if connected_at_dt and connected_at_dt.year < 2000:
                                connected_at_dt = None
                            # Delete any existing row with same (site_id, hostname) but different agent_id
                            # This handles agent reinstalls that generate a new agent_id
                            await admin_conn.execute("""
                                DELETE FROM go_agents
                                WHERE site_id = $1 AND hostname = $2 AND agent_id != $3
                            """, checkin.site_id, agent.hostname, agent.agent_id)
                            compliance_pct = round(
                                (agent.checks_passed / agent.checks_total * 100)
                                if agent.checks_total > 0 else 0.0, 2
                            )
                            await admin_conn.execute("""
                                INSERT INTO go_agents (
                                    agent_id, site_id, hostname, agent_version,
                                    ip_address, os_name, os_version,
                                    capability_tier, status, checks_passed, checks_total,
                                    compliance_percentage, connected_at, last_heartbeat,
                                    updated_at
                                ) VALUES ($1, $2, $3, $4, $5, NULL, $6, $7, 'connected', $8, $9, $10, $11, $12, NOW())
                                ON CONFLICT (agent_id) DO UPDATE SET
                                    hostname = EXCLUDED.hostname,
                                    agent_version = COALESCE(NULLIF(EXCLUDED.agent_version, ''), go_agents.agent_version),
                                    ip_address = COALESCE(NULLIF(EXCLUDED.ip_address, ''), go_agents.ip_address),
                                    os_name = COALESCE(NULLIF(EXCLUDED.os_name, ''), go_agents.os_name),
                                    os_version = COALESCE(NULLIF(EXCLUDED.os_version, ''), go_agents.os_version),
                                    capability_tier = EXCLUDED.capability_tier,
                                    status = 'connected',
                                    checks_passed = EXCLUDED.checks_passed,
                                    checks_total = EXCLUDED.checks_total,
                                    compliance_percentage = EXCLUDED.compliance_percentage,
                                    last_heartbeat = COALESCE(EXCLUDED.last_heartbeat, go_agents.last_heartbeat),
                                    updated_at = NOW()
                            """,
                                agent.agent_id,
                                checkin.site_id,
                                agent.hostname,
                                agent.agent_version,
                                agent.ip_address,
                                agent.os_version,
                                agent.capability_tier,
                                agent.checks_passed,
                                agent.checks_total,
                                compliance_pct,
                                connected_at_dt,
                                last_heartbeat_dt,
                            )
                        # Mark agents not in this batch as disconnected
                        active_ids = [a.agent_id for a in checkin.connected_agents]
                        await admin_conn.execute("""
                            UPDATE go_agents SET status = 'disconnected', updated_at = NOW()
                            WHERE site_id = $1 AND status = 'connected'
                            AND agent_id != ALL($2)
                        """, checkin.site_id, active_ids)
            except Exception as e:
                import logging
                logging.warning(f"Failed to sync go_agents: {e}")

        # === STEP 3.7b: Link discovered devices → workstations table ===
        try:
            async with conn.transaction():
                from .device_sync import _link_devices_to_workstations
                await _link_devices_to_workstations(conn, checkin.site_id)
        except Exception as e:
            import logging
            logging.debug(f"Device→workstation linkage during checkin: {e}")

        # === STEP 3.7c: Sync Go agent data → workstations table + cleanup ===
        if checkin.connected_agents:
            try:
                async with conn.transaction():
                    # Batch upsert workstations from Go agent data (single executemany)
                    ws_rows = [
                        (checkin.site_id, a.hostname, a.checks_passed, a.checks_total)
                        for a in checkin.connected_agents if a.hostname
                    ]
                    if ws_rows:
                        await conn.executemany("""
                            INSERT INTO workstations (site_id, hostname, ip_address, online,
                                compliance_status, last_compliance_check, last_seen, updated_at)
                            VALUES ($1, $2, $2, true,
                                CASE WHEN $3 > 0 AND $4 > 0 AND $3 = $4 THEN 'compliant'
                                     WHEN $4 > 0 THEN 'drifted'
                                     ELSE 'unknown' END,
                                CASE WHEN $4 > 0 THEN NOW() ELSE NULL END,
                                NOW(), NOW())
                            ON CONFLICT (site_id, hostname) DO UPDATE SET
                                online = true,
                                compliance_status = CASE
                                    WHEN $3 > 0 AND $4 > 0 AND $3 = $4 THEN 'compliant'
                                    WHEN $4 > 0 THEN 'drifted'
                                    ELSE workstations.compliance_status END,
                                last_compliance_check = CASE
                                    WHEN $4 > 0 THEN NOW()
                                    ELSE workstations.last_compliance_check END,
                                last_seen = NOW(),
                                updated_at = NOW()
                        """, ws_rows)

                    # Deduplicate: remove IP-only entries when a named workstation has the same IP
                    await conn.execute("""
                        DELETE FROM workstations w1
                        WHERE w1.site_id = $1
                        AND w1.hostname ~ '^\d+\.\d+\.\d+\.\d+$'
                        AND EXISTS (
                            SELECT 1 FROM workstations w2
                            WHERE w2.site_id = w1.site_id
                            AND w2.ip_address = w1.ip_address
                            AND w2.hostname !~ '^\d+\.\d+\.\d+\.\d+$'
                        )
                    """, checkin.site_id)

                    # Remove appliance IPs and gateway from workstation list
                    # These are infrastructure devices, not compliance targets
                    await conn.execute("""
                        DELETE FROM workstations
                        WHERE site_id = $1
                        AND (
                            hostname IN (SELECT DISTINCT unnest(string_to_array(
                                regexp_replace(ip_addresses::text, '[\[\]" ]', '', 'g'), ','))
                                FROM site_appliances WHERE site_id = $1)
                            OR hostname = 'router.lan'
                            OR hostname LIKE '%.1'
                        )
                    """, checkin.site_id)

                    # Expire stale: mark offline if no activity in 7 days
                    await conn.execute("""
                        UPDATE workstations SET online = false
                        WHERE site_id = $1 AND online = true
                        AND last_seen < NOW() - INTERVAL '7 days'
                    """, checkin.site_id)

                    # Remove stale IP-only entries after 7 days (DHCP drift artifacts)
                    await conn.execute("""
                        DELETE FROM workstations
                        WHERE site_id = $1
                        AND hostname ~ '^\d+\.\d+\.\d+\.\d+$'
                        AND last_seen < NOW() - INTERVAL '7 days'
                    """, checkin.site_id)

                    # Remove ancient: delete workstations with no activity in 30 days
                    await conn.execute("""
                        DELETE FROM workstations
                        WHERE site_id = $1
                        AND last_seen < NOW() - INTERVAL '30 days'
                        AND compliance_status = 'unknown'
                    """, checkin.site_id)
            except Exception as e:
                import logging
                logging.warning(f"Failed to sync go_agent→workstations: {e}")

        # === STEP 3.8: Handle app protection discovery results ===
        if checkin.discovery_results and checkin.discovery_results.get("profile_id"):
            try:
                async with conn.transaction():
                    from .protection_profiles import receive_discovery_results
                    profile_id = checkin.discovery_results["profile_id"]
                    assets_data = checkin.discovery_results.get("assets", {})
                    pid = _uuid.UUID(profile_id)
                    now_disc = datetime.now(timezone.utc)

                    # Site_id guard: only allow the appliance to update profiles for its own site
                    await conn.execute(
                        "UPDATE app_protection_profiles SET discovery_data = $2::jsonb, status = 'discovered', updated_at = $3 WHERE id = $1 AND site_id = $4",
                        pid, json.dumps(assets_data), now_disc, checkin.site_id,
                    )

                    # Clear previous assets and re-create from discovery
                    await conn.execute(
                        "DELETE FROM app_profile_assets WHERE profile_id = $1 AND profile_id IN (SELECT id FROM app_protection_profiles WHERE site_id = $2)",
                        pid, checkin.site_id,
                    )
                    from .protection_profiles import ASSET_RUNBOOK_MAP
                    for asset_type, items in assets_data.items():
                        runbook_id = ASSET_RUNBOOK_MAP.get(asset_type)
                        for item in items:
                            asset_id = _uuid.uuid4()
                            await conn.execute("""
                                INSERT INTO app_profile_assets
                                    (id, profile_id, asset_type, asset_name, display_name,
                                     baseline_value, enabled, runbook_id, created_at)
                                VALUES ($1, $2, $3, $4, $5, $6::jsonb, true, $7, $8)
                            """,
                                asset_id, pid, asset_type,
                                item.get("name", ""),
                                item.get("display_name"),
                                json.dumps(item.get("value", {})),
                                runbook_id, now_disc,
                            )
                    import logging as _log
                    _log.info(f"Stored app discovery results for profile {profile_id}")
            except Exception as e:
                import logging as _log
                _log.warning(f"Failed to process discovery results: {e}")

        # === STEP 3.8b: Mesh peer discovery (cross-subnet) ===
        # Deliver sibling appliance IPs + MACs so daemon can probe them directly,
        # enabling mesh target splitting across subnets (ARP only works on same L2).
        mesh_peers = []
        try:
            async with conn.transaction():
                sibling_rows = await conn.fetch("""
                    SELECT mac_address, ip_addresses
                    FROM site_appliances
                    WHERE site_id = $1
                    AND appliance_id != $2
                    AND status = 'online'
                    AND last_checkin > NOW() - INTERVAL '5 minutes'
                """, checkin.site_id, canonical_id)
                for row in sibling_rows:
                    ips = row['ip_addresses']
                    if isinstance(ips, str):
                        ips = json.loads(ips)
                    # Filter out WireGuard/tunnel IPs (10.x.x.x) — mesh probes
                    # should only use LAN IPs. Tunnel IPs cross org boundaries
                    # on the shared VPS hub and must not be used for peer discovery.
                    if ips:
                        ips = [ip for ip in ips if not ip.startswith('10.')]
                    if ips and row['mac_address']:
                        mesh_peers.append({
                            "mac": row['mac_address'],
                            "ips": ips,
                        })
                if mesh_peers:
                    logger.info(f"Checkin {checkin.site_id}: delivering {len(mesh_peers)} mesh peer(s) for cross-subnet discovery")
        except Exception as e:
            logger.error(f"Checkin {checkin.site_id}: mesh peer lookup failed: {e}")

        # === STEP 3.9: Peer witness hash exchange ===
        # Store incoming witness attestations and bundle hashes.
        # Retrieve sibling bundle hashes to deliver in response.
        peer_bundle_hashes = []
        try:
            async with conn.transaction():
                # Store witness attestations (counter-signatures from this appliance)
                if checkin.witness_attestations:
                    for att in checkin.witness_attestations:
                        await conn.execute("""
                            INSERT INTO witness_attestations
                                (bundle_id, bundle_hash, source_appliance, witness_appliance,
                                 witness_public_key, witness_signature)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            ON CONFLICT (bundle_id, witness_appliance) DO NOTHING
                        """,
                            att.get('bundle_id', ''),
                            att.get('bundle_hash', ''),
                            att.get('source_appliance', ''),
                            canonical_id,
                            att.get('witness_public_key', ''),
                            att.get('witness_signature', ''),
                        )
                    logger.info(f"Checkin {checkin.site_id}: stored {len(checkin.witness_attestations)} witness attestation(s)")

                # Fetch sibling bundle hashes for this appliance to counter-sign.
                # Uses admin_connection for cross-site org JOIN (RLS blocks it in tenant conn).
                async with admin_connection(pool) as admin_conn:
                    sibling_rows = await admin_conn.fetch("""
                        SELECT cb.bundle_id, cb.bundle_hash, sa.appliance_id as source_appliance,
                               s.agent_public_key as source_public_key
                        FROM compliance_bundles cb
                        JOIN site_appliances sa ON cb.site_id = sa.site_id
                        JOIN sites s ON cb.site_id = s.site_id
                        WHERE s.client_org_id = (SELECT client_org_id FROM sites WHERE site_id = $1)
                        AND sa.appliance_id != $2
                        AND cb.checked_at > NOW() - INTERVAL '30 minutes'
                        AND cb.bundle_hash IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM witness_attestations wa
                            WHERE wa.bundle_id = cb.bundle_id AND wa.witness_appliance = $2
                        )
                        ORDER BY cb.checked_at DESC
                        LIMIT 10
                    """, checkin.site_id, canonical_id)

                for r in sibling_rows:
                    peer_bundle_hashes.append({
                        "bundle_id": r['bundle_id'],
                        "bundle_hash": r['bundle_hash'],
                        "source_appliance": r['source_appliance'],
                        "source_public_key": r['source_public_key'] or "",
                    })
                if peer_bundle_hashes:
                    logger.info(f"Checkin {checkin.site_id}: delivering {len(peer_bundle_hashes)} peer bundle hash(es) for witnessing")
        except Exception as e:
            logger.error(f"Witness exchange during checkin: {e}")

        # === STEP 4: Get pending orders for this appliance ===
        pending_orders = []
        try:
            async with conn.transaction():
                # Check admin_orders table (fleet management orders)
                order_rows = await conn.fetch("""
                    SELECT order_id, order_type, parameters, priority, created_at, expires_at,
                           nonce, signature, signed_payload
                    FROM admin_orders
                    WHERE appliance_id = $1
                    AND status = 'pending'
                    AND expires_at > NOW()
                    ORDER BY priority DESC, created_at ASC
                """, canonical_id)

                pending_orders = [
                    {
                        "order_id": row["order_id"],
                        "order_type": row["order_type"],
                        "parameters": parse_parameters(row["parameters"]),
                        "priority": row["priority"],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                        "nonce": row["nonce"],
                        "signature": row["signature"],
                        "signed_payload": row["signed_payload"],
                    }
                    for row in order_rows
                ]
        except Exception as e:
            logger.error(f"Failed to fetch admin orders: {e}")

        # Also check orders table (healing orders from incidents)
        try:
            async with conn.transaction():
                healing_order_rows = await conn.fetch("""
                    SELECT o.order_id, o.runbook_id, o.parameters, o.issued_at, o.expires_at,
                           o.nonce, o.signature, o.signed_payload,
                           i.id as incident_id
                    FROM orders o
                    JOIN v_appliances_current a ON o.appliance_id = a.id
                    LEFT JOIN incidents i ON i.order_id = o.id
                    WHERE a.site_id = $1
                    AND o.status = 'pending'
                    AND o.expires_at > NOW()
                    ORDER BY o.issued_at ASC
                """, checkin.site_id)

                for row in healing_order_rows:
                    params = row["parameters"] if isinstance(row["parameters"], dict) else {}
                    params["incident_id"] = str(row["incident_id"]) if row["incident_id"] else None
                    pending_orders.append({
                        "order_id": row["order_id"],
                        "order_type": "healing",
                        "runbook_id": row["runbook_id"],
                        "parameters": params,
                        "priority": 10,  # Healing orders are high priority
                        "created_at": row["issued_at"].isoformat() if row["issued_at"] else None,
                        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                        "nonce": row["nonce"],
                        "signature": row["signature"],
                        "signed_payload": row["signed_payload"],
                    })
        except Exception as e:
            logger.error(f"Failed to fetch healing orders: {e}")

        # === STEP 4.5: Get fleet-wide orders ===
        try:
            async with conn.transaction():
                fleet_orders = await get_fleet_orders_for_appliance(
                    conn, canonical_id, checkin.agent_version
                )
                pending_orders.extend(fleet_orders)
        except Exception as e:
            logger.error(f"Failed to fetch fleet orders: {e}")

        # === STEP 5: Get windows targets (conditional credential delivery) ===
        # Always check credential freshness — if creds were updated since last checkin,
        # force delivery even if appliance thinks it has local copies
        creds_updated_at = None
        try:
            creds_updated_at = await conn.fetchval("""
                SELECT MAX(COALESCE(updated_at, created_at))
                FROM site_credentials WHERE site_id = $1
            """, checkin.site_id)
        except Exception:
            pass  # Table may not have updated_at column yet

        # Always send credentials — the bandwidth optimization of skipping
        # caused appliances to miss newly-added credential types (e.g., macOS SSH
        # creds added after initial Windows creds were cached). Credential JSON
        # is small (~1KB) and checkins happen every 60s, so always including them
        # is acceptable.
        should_send_creds = True

        # === STEP 5 PRELUDE: Discovery-based target filtering ===
        # Each appliance only scans hosts it OWNS via first-discovery.
        # Prevents duplicate scanning when multiple appliances share a subnet.
        # Falls back to all-discovered if ownership not yet assigned (pre-migration 114).
        # First checkin (no discoveries yet) gets all creds to bootstrap scanning.
        owned_ips = set()
        discovered_ips = set()
        try:
            # Prefer ownership-based filter (migration 114+)
            own_rows = await conn.fetch("""
                SELECT DISTINCT ip_address FROM discovered_devices
                WHERE owner_appliance_id = $1 AND ip_address IS NOT NULL
            """, canonical_id)
            owned_ips = {r['ip_address'] for r in own_rows}
        except Exception:
            pass  # owner_appliance_id column may not exist yet
        if not owned_ips:
            # Fallback: all devices this appliance discovered
            try:
                disc_rows = await conn.fetch("""
                    SELECT DISTINCT ip_address FROM discovered_devices
                    WHERE appliance_id = $1 AND ip_address IS NOT NULL
                """, canonical_id)
                discovered_ips = {r['ip_address'] for r in disc_rows}
            except Exception:
                pass
        scan_ips = owned_ips or discovered_ips
        has_discoveries = len(scan_ips) > 0

        # Build hostname→IP lookup from discovered devices for credential delivery.
        # Credentials may store hostnames (e.g., "NVDC01") that some appliances
        # can't DNS resolve (router DNS without AD records). Map them to IPs
        # using discovery data so the daemon gets reachable targets.
        hostname_to_ip = {}
        try:
            hn_rows = await conn.fetch("""
                SELECT DISTINCT hostname, ip_address FROM discovered_devices
                WHERE appliance_id = $1 AND hostname IS NOT NULL AND hostname != ''
                AND ip_address IS NOT NULL
            """, canonical_id)
            for hr in hn_rows:
                hostname_to_ip[hr['hostname'].lower()] = hr['ip_address']
        except Exception:
            pass

        windows_targets = []
        if should_send_creds:
            try:
                async with conn.transaction():
                    creds = await conn.fetch("""
                        SELECT credential_name, credential_type, encrypted_data
                        FROM site_credentials
                        WHERE site_id = $1
                        AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                        ORDER BY CASE WHEN credential_type = 'domain_admin' THEN 0 ELSE 1 END, created_at DESC
                    """, checkin.site_id)

                    # Org-level credential inheritance: if this site has no Windows creds,
                    # inherit from sibling sites in the same org (same network, shared AD).
                    # Uses admin connection to bypass tenant RLS for the cross-site JOIN.
                    if not creds:
                        async with admin_connection(pool) as admin_conn:
                            creds = await admin_conn.fetch("""
                                SELECT sc.credential_name, sc.credential_type, sc.encrypted_data
                                FROM site_credentials sc
                                JOIN sites s1 ON sc.site_id = s1.site_id
                                JOIN sites s2 ON s1.client_org_id = s2.client_org_id
                                WHERE s2.site_id = $1
                                AND sc.site_id != $1
                                AND sc.credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                                ORDER BY CASE WHEN sc.credential_type = 'domain_admin' THEN 0 ELSE 1 END, sc.created_at DESC
                            """, checkin.site_id)
                        if creds:
                            logger.info(f"Checkin {checkin.site_id}: inherited {len(creds)} Windows credential(s) from org siblings")

                for cred in creds:
                    cred_type = cred.get('credential_type', 'winrm')
                    hostname = None
                    username = ''
                    password = ''
                    use_ssl = False

                    # Credentials stored as JSON blob in encrypted_data
                    if cred['encrypted_data']:
                        try:
                            raw = cred['encrypted_data']
                            # Decrypt (handles both Fernet-encrypted and legacy plaintext)
                            cred_data = json.loads(decrypt_credential(raw))
                            hostname = cred_data.get('host') or cred_data.get('target_host')
                            username = cred_data.get('username', '')
                            password = cred_data.get('password', '')
                            use_ssl = cred_data.get('use_ssl', False)
                        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as e:
                            logger.warning(f"Checkin {checkin.site_id}: malformed credential JSON for {cred.get('credential_name', '?')}: {e}")
                            continue
                    else:
                        continue  # No usable credential data

                    if hostname:
                        # Resolve hostname to IP if the credential uses a name that
                        # the appliance may not be able to DNS-resolve (e.g., "NVDC01"
                        # on an appliance using router DNS without AD records).
                        resolved = hostname
                        if hostname_to_ip and hostname.lower() in hostname_to_ip:
                            resolved = hostname_to_ip[hostname.lower()]

                        # Discovery filter: only deliver targets this appliance has discovered.
                        # Check both the original hostname and the resolved IP.
                        if has_discoveries and hostname not in scan_ips and resolved not in scan_ips:
                            continue
                        windows_targets.append({
                            "hostname": resolved,
                            "username": username,
                            "password": password,
                            "use_ssl": use_ssl,
                            "role": cred_type,
                        })
            except Exception as e:
                logger.error(f"Checkin {checkin.site_id}: Windows credentials lookup failed: {e}")

        # === STEP 5b: Get linux targets (SSH credentials) ===
        linux_targets = []
        if should_send_creds:
            try:
                async with conn.transaction():
                    ssh_creds = await conn.fetch("""
                        SELECT credential_name, encrypted_data
                        FROM site_credentials
                        WHERE site_id = $1
                        AND credential_type IN ('ssh_password', 'ssh_key')
                        ORDER BY created_at DESC
                    """, checkin.site_id)

                    # Org-level credential inheritance for SSH targets (admin conn for RLS bypass)
                    if not ssh_creds:
                        async with admin_connection(pool) as admin_conn:
                            ssh_creds = await admin_conn.fetch("""
                                SELECT sc.credential_name, sc.encrypted_data
                                FROM site_credentials sc
                                JOIN sites s1 ON sc.site_id = s1.site_id
                                JOIN sites s2 ON s1.client_org_id = s2.client_org_id
                                WHERE s2.site_id = $1
                                AND sc.site_id != $1
                                AND sc.credential_type IN ('ssh_password', 'ssh_key')
                                ORDER BY sc.created_at DESC
                            """, checkin.site_id)
                        if ssh_creds:
                            logger.info(f"Checkin {checkin.site_id}: inherited {len(ssh_creds)} SSH credential(s) from org siblings")

                for cred in ssh_creds:
                    if cred['encrypted_data']:
                        try:
                            raw = cred['encrypted_data']
                            # Decrypt (handles both Fernet-encrypted and legacy plaintext)
                            cred_data = json.loads(decrypt_credential(raw))
                            hostname = cred_data.get('host') or cred_data.get('target_host')
                            target_entry = {
                                "hostname": hostname,
                                "port": cred_data.get('port', 22),
                                "username": cred_data.get('username', 'root'),
                            }
                            if cred_data.get('password'):
                                target_entry["password"] = cred_data['password']
                                # Use password as sudo_password if not explicitly set
                                if not cred_data.get('sudo_password'):
                                    target_entry["sudo_password"] = cred_data['password']
                            if cred_data.get('sudo_password'):
                                target_entry["sudo_password"] = cred_data['sudo_password']
                            if cred_data.get('private_key'):
                                target_entry["private_key"] = cred_data['private_key']
                            if cred_data.get('distro'):
                                target_entry["distro"] = cred_data['distro']
                            if cred_data.get('label'):
                                target_entry["label"] = cred_data['label']
                            if hostname:
                                # Resolve hostname→IP (same as Windows targets)
                                resolved = hostname
                                if hostname_to_ip and hostname.lower() in hostname_to_ip:
                                    resolved = hostname_to_ip[hostname.lower()]
                                    target_entry["hostname"] = resolved

                                # Discovery filter: check hostname + resolved IP
                                if has_discoveries and hostname not in scan_ips and resolved not in scan_ips:
                                    continue
                                linux_targets.append(target_entry)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Checkin {checkin.site_id}: malformed SSH credential JSON for {cred.get('credential_name', '?')}: {e}")
            except Exception as e:
                logger.error(f"Checkin {checkin.site_id}: SSH credentials lookup failed: {e}")

        # === STEP 3.8c: Server-side target assignment ===
        # Backend-authoritative: compute which targets this appliance should scan
        # using the same consistent hash ring algorithm as the Go daemon.
        # Also reassigns ALL other online siblings in the same transaction so
        # mesh membership changes propagate instantly instead of waiting for
        # each sibling's own checkin (avoids up-to-5-minute imbalance windows).
        target_assignments = {}
        try:
            async with conn.transaction():
                online_appliances = await conn.fetch("""
                    SELECT appliance_id, mac_address
                    FROM site_appliances
                    WHERE site_id = $1
                    AND status = 'online'
                    AND last_checkin > NOW() - INTERVAL '5 minutes'
                    ORDER BY mac_address
                """, checkin.site_id)

                if online_appliances:
                    from .hash_ring import HashRing, normalize_mac_for_ring
                    ring_macs = [normalize_mac_for_ring(r['mac_address']) for r in online_appliances if r['mac_address']]
                    mac_to_aid = {
                        normalize_mac_for_ring(r['mac_address']): r['appliance_id']
                        for r in online_appliances if r['mac_address']
                    }
                    this_mac = normalize_mac_for_ring(checkin.mac_address) if checkin.mac_address else ""

                    if ring_macs and this_mac in ring_macs:
                        all_target_ips = set()
                        for t in windows_targets:
                            host = t.get('host') or t.get('hostname')
                            if host:
                                all_target_ips.add(host)
                        for t in linux_targets:
                            host = t.get('host') or t.get('hostname')
                            if host:
                                all_target_ips.add(host)

                        ring = HashRing(ring_macs)
                        my_targets = ring.targets_for_node(this_mac, sorted(all_target_ips))
                        epoch = int(time.time())

                        target_assignments = {
                            "your_targets": my_targets,
                            "ring_members": ring_macs,
                            "assignment_epoch": epoch,
                        }

                        # Persist assignment for observability
                        await conn.execute("""
                            UPDATE site_appliances
                            SET assigned_targets = $1::jsonb, assignment_epoch = $2
                            WHERE appliance_id = $3
                        """, json.dumps(my_targets), epoch, canonical_id)

                        # Immutable audit log — only insert when assignment actually changes
                        # to avoid flooding the table with identical rows every checkin.
                        last_audit = await conn.fetchrow("""
                            SELECT assigned_targets, ring_members
                            FROM mesh_assignment_audit
                            WHERE appliance_id = $1
                            ORDER BY created_at DESC
                            LIMIT 1
                        """, canonical_id)

                        # Deterministic comparison: parse JSONB (may come back as list OR str)
                        def _as_sorted_list(v):
                            if v is None:
                                return []
                            if isinstance(v, str):
                                try:
                                    v = json.loads(v)
                                except (ValueError, TypeError):
                                    return []
                            return sorted(v) if isinstance(v, list) else []

                        prev_targets = _as_sorted_list(last_audit["assigned_targets"]) if last_audit else None
                        prev_ring = _as_sorted_list(last_audit["ring_members"]) if last_audit else None
                        new_targets_sorted = sorted(my_targets)
                        new_ring_sorted = sorted(ring_macs)

                        if prev_targets != new_targets_sorted or prev_ring != new_ring_sorted:
                            await conn.execute("""
                                INSERT INTO mesh_assignment_audit (
                                    site_id, appliance_id, appliance_mac, assignment_epoch,
                                    ring_size, ring_members, assigned_targets, target_count
                                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
                            """,
                                checkin.site_id, canonical_id, this_mac, epoch,
                                len(ring_macs), json.dumps(ring_macs),
                                json.dumps(my_targets), len(my_targets),
                            )

                        logger.info(
                            "target_assignment site_id=%s appliance_mac=%s target_count=%d ring_size=%d epoch=%d",
                            checkin.site_id,
                            this_mac,
                            len(my_targets),
                            len(ring_macs),
                            epoch,
                        )

                        # === Sibling reassignment ===
                        # Recompute + persist each other online sibling's
                        # assignment in this same transaction. Without this,
                        # a new node joining the ring doesn't redistribute
                        # targets until each sibling's next checkin — up to
                        # a 5-minute window of double-coverage / dead-zones.
                        # Each sibling wrapped in a savepoint so one failure
                        # doesn't poison the outer transaction.
                        #
                        # Migration 222 gate (Session 207 fix): sibling
                        # UPDATEs target OTHER appliance rows, which the
                        # D2 cross-appliance guard rejects by default. SET
                        # LOCAL the scoped bypass flag so the trigger
                        # recognizes this as a legitimate reassignment —
                        # flag is transaction-local, cleared automatically
                        # at commit, and re-reset to 'false' immediately
                        # after the sibling loop so later unrelated UPDATEs
                        # in this same tx don't ride the bypass.
                        await conn.execute(
                            "SET LOCAL app.allow_cross_appliance_reassignment = 'true'"
                        )
                        sibling_macs = [m for m in ring_macs if m != this_mac]
                        siblings_reassigned = 0
                        for sib_mac in sibling_macs:
                            sib_aid = mac_to_aid.get(sib_mac)
                            if not sib_aid:
                                continue
                            try:
                                async with conn.transaction():
                                    sib_targets = ring.targets_for_node(sib_mac, sorted(all_target_ips))
                                    await conn.execute("""
                                        UPDATE site_appliances
                                        SET assigned_targets = $1::jsonb, assignment_epoch = $2
                                        WHERE appliance_id = $3
                                    """, json.dumps(sib_targets), epoch, sib_aid)

                                    sib_last = await conn.fetchrow("""
                                        SELECT assigned_targets, ring_members
                                        FROM mesh_assignment_audit
                                        WHERE appliance_id = $1
                                        ORDER BY created_at DESC
                                        LIMIT 1
                                    """, sib_aid)
                                    sib_prev_t = _as_sorted_list(sib_last["assigned_targets"]) if sib_last else None
                                    sib_prev_r = _as_sorted_list(sib_last["ring_members"]) if sib_last else None
                                    sib_new_t = sorted(sib_targets)

                                    if sib_prev_t != sib_new_t or sib_prev_r != new_ring_sorted:
                                        await conn.execute("""
                                            INSERT INTO mesh_assignment_audit (
                                                site_id, appliance_id, appliance_mac, assignment_epoch,
                                                ring_size, ring_members, assigned_targets, target_count
                                            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
                                        """,
                                            checkin.site_id, sib_aid, sib_mac, epoch,
                                            len(ring_macs), json.dumps(ring_macs),
                                            json.dumps(sib_targets), len(sib_targets),
                                        )
                                        logger.info(
                                            "sibling_reassignment site_id=%s trigger_mac=%s sibling_mac=%s target_count=%d epoch=%d",
                                            checkin.site_id, this_mac, sib_mac, len(sib_targets), epoch,
                                        )
                                    siblings_reassigned += 1
                            except Exception as _se:
                                logger.error(
                                    "sibling_reassignment_failed site_id=%s trigger_mac=%s sibling_mac=%s error=%s",
                                    checkin.site_id, this_mac, sib_mac, str(_se),
                                    exc_info=True,
                                )

                        # Scope the bypass tightly: reset to 'false' so any
                        # subsequent UPDATE in this transaction (STEP 6+
                        # runbook config, etc.) goes through the normal
                        # guard. Belt-and-suspenders — SET LOCAL also
                        # clears at commit.
                        await conn.execute(
                            "SET LOCAL app.allow_cross_appliance_reassignment = 'false'"
                        )

                        if siblings_reassigned:
                            logger.info(
                                "mesh_rebalance_complete site_id=%s trigger_mac=%s siblings_reassigned=%d ring_size=%d epoch=%d",
                                checkin.site_id, this_mac, siblings_reassigned, len(ring_macs), epoch,
                            )
        except Exception as e:
            # Logged at ERROR (not WARNING) per the project's "no silent write
            # failures" rule — target assignment is a delivery contract and a
            # failure here equals an imbalanced mesh that the dashboard needs
            # to surface, not quietly eat.
            logger.error(
                "target_assignment_failed site_id=%s error=%s", checkin.site_id, str(e),
                exc_info=True,
            )

        # === STEP 6: Get enabled runbooks (runbook config pull) ===
        enabled_runbooks = []
        try:
            async with conn.transaction():
                runbook_rows = await conn.fetch("""
                    SELECT
                        r.runbook_id,
                        COALESCE(
                            arc.enabled,
                            src.enabled,
                            true
                        ) as enabled
                    FROM runbooks r
                    LEFT JOIN site_runbook_config src ON src.runbook_id = r.runbook_id AND src.site_id = $1
                    LEFT JOIN appliance_runbook_config arc ON arc.runbook_id = r.runbook_id AND arc.appliance_id = $2
                    ORDER BY r.runbook_id
                """, checkin.site_id, canonical_id)
            enabled_runbooks = [row['runbook_id'] for row in runbook_rows if row['enabled']]
        except Exception as e:
            logger.warning(f"Checkin {checkin.site_id}: runbook lookup failed: {e}")

        # === STEP 6b: Get drift scan config (disabled check types) ===
        # Both disabled and not_applicable checks are sent to the daemon as disabled
        # so the appliance skips scanning them entirely
        disabled_checks = []
        try:
            async with conn.transaction():
                # Site-specific overrides take precedence over defaults
                drift_rows = await conn.fetch("""
                    SELECT check_type, enabled, COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status
                    FROM site_drift_config
                    WHERE site_id = $1
                """, checkin.site_id)

                if drift_rows:
                    disabled_checks = [r['check_type'] for r in drift_rows if not r['enabled'] or r['status'] == 'not_applicable']
                else:
                    # Fall back to defaults
                    default_rows = await conn.fetch("""
                        SELECT check_type, enabled, COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status
                        FROM site_drift_config
                        WHERE site_id = '__defaults__'
                    """)
                    disabled_checks = [r['check_type'] for r in default_rows if not r['enabled'] or r['status'] == 'not_applicable']
        except Exception as e:
            logger.warning(f"Checkin {checkin.site_id}: drift config lookup failed: {e}")

        # === STEP 6b-2: Resolve effective alert mode for daemon ===
        client_alert_mode_site = None
        client_alert_mode_org = None
        compliance_framework = "hipaa"
        try:
            async with conn.transaction():
                mode_row = await conn.fetchrow(
                    """SELECT s.client_alert_mode as site_mode, co.client_alert_mode as org_mode,
                              co.compliance_framework as org_framework
                       FROM sites s
                       LEFT JOIN client_orgs co ON co.id = s.client_org_id
                       WHERE s.site_id = $1""",
                    checkin.site_id
                )
                if mode_row:
                    client_alert_mode_site = mode_row["site_mode"]
                    client_alert_mode_org = mode_row["org_mode"]
                    compliance_framework = mode_row["org_framework"] or "hipaa"
        except Exception as e:
            logger.debug(f"Alert mode resolution failed (non-fatal): {e}")
        effective_alert_mode = client_alert_mode_site or client_alert_mode_org or "informed"

        # === STEP 6c: Get maintenance window (if active) ===
        maintenance_until = None
        try:
            async with conn.transaction():
                maint_row = await conn.fetchval("""
                    SELECT maintenance_until FROM sites
                    WHERE site_id = $1 AND maintenance_until > NOW()
                """, checkin.site_id)
                if maint_row:
                    maintenance_until = maint_row.isoformat()
        except Exception as e:
            logger.warning(f"Checkin {checkin.site_id}: maintenance window lookup failed: {e}")

        # === STEP 7: Check for enumeration/scan triggers (zero-friction deployment) ===
        trigger_enumeration = False
        trigger_immediate_scan = False
        try:
            async with conn.transaction():
                appliance = await conn.fetchrow("""
                    SELECT trigger_enumeration, trigger_immediate_scan
                    FROM site_appliances
                    WHERE appliance_id = $1
                """, canonical_id)

                if appliance:
                    trigger_enumeration = appliance.get('trigger_enumeration', False)
                    trigger_immediate_scan = appliance.get('trigger_immediate_scan', False)

                    if trigger_enumeration or trigger_immediate_scan:
                        await conn.execute("""
                            UPDATE site_appliances
                            SET trigger_enumeration = false, trigger_immediate_scan = false
                            WHERE appliance_id = $1
                        """, canonical_id)
        except Exception as e:
            logger.warning(f"Checkin {checkin.site_id}: trigger flags lookup failed: {e}")

    # === STEP 7b: Check billing status ===
    billing_hold = False
    billing_status = "none"
    try:
        from .billing_guard import check_billing_status
        async with tenant_connection(pool, site_id=checkin.site_id) as bconn:
            billing_status, billing_active = await check_billing_status(bconn, checkin.site_id)
            billing_hold = not billing_active
    except Exception as e:
        logger.warning(f"Checkin {checkin.site_id}: billing check failed (allowing): {e}")

    # If billing hold, strip orders (keep checkin alive for visibility)
    if billing_hold:
        pending_orders = []
        logger.info(f"Checkin {checkin.site_id}: billing hold active (status={billing_status}), orders suppressed")

    # Broadcast checkin event to connected dashboard clients
    try:
        await broadcast_event("appliance_checkin", {
            "site_id": checkin.site_id,
            "appliance_id": canonical_id,
            "hostname": checkin.hostname,
            "status": "online",
            "last_checkin": now.isoformat(),
            "agent_version": checkin.agent_version,
            "ip_addresses": checkin.ip_addresses,
            "uptime_seconds": checkin.uptime_seconds,
        })
    except Exception as e:
        logger.debug(f"Checkin {checkin.site_id}: broadcast failed: {e}")

    # Get server public key(s) for order signature verification
    server_public_key = None
    server_public_keys = []
    try:
        from main import get_public_key_hex, get_all_public_keys_hex
        server_public_key = get_public_key_hex()
        server_public_keys = get_all_public_keys_hex()
    except Exception as e:
        logger.warning(f"Checkin: server public key not available: {e}")

    # Phase 13 H5: record which pubkey fingerprint we're delivering to
    # this appliance on this checkin. Divergence between this value
    # (what we sent) and the server's current key means the appliance
    # has an out-of-date cache.  We update this AFTER the appliance
    # record is created/upserted further below — see STEP just before
    # `return {... server_public_key ...}`.
    pubkey_fingerprint = server_public_key[:16] if server_public_key else None

    # Envelope-encrypt credentials if appliance supports it
    encrypted_credentials = None
    if checkin.encryption_public_key and (windows_targets or linux_targets):
        try:
            from .credential_envelope import encrypt_credentials
            encrypted_credentials = encrypt_credentials(
                checkin.encryption_public_key,
                windows_targets,
                linux_targets,
            )
            # Clear plaintext — daemon will decrypt from envelope
            windows_targets = []
            linux_targets = []
        except Exception as e:
            logger.warning(f"Checkin {checkin.site_id}: credential envelope encryption failed, falling back to plaintext: {e}")

    # === STEP 7c: Query devices pending deployment for this site ===
    pending_deploys = []
    try:
        async with tenant_connection(pool, site_id=checkin.site_id) as deploy_conn:
            pending_rows = await deploy_conn.fetch("""
                SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name,
                       sc.encrypted_data, sc.credential_type
                FROM discovered_devices dd
                JOIN site_credentials sc ON sc.site_id = $1
                    AND sc.credential_name LIKE dd.hostname || ' (%'
                WHERE dd.site_id = $1
                    AND dd.device_status = 'pending_deploy'
                LIMIT 5
            """, checkin.site_id)

            for row in pending_rows:
                try:
                    raw = row["encrypted_data"]
                    # Decrypt (handles both Fernet-encrypted and legacy plaintext)
                    cred_data = json.loads(decrypt_credential(raw))
                    os_name = row["os_name"] or "linux"
                    pending_deploys.append({
                        "device_id": row["local_device_id"],
                        "ip_address": row["ip_address"],
                        "hostname": row["hostname"],
                        "os_type": os_name,
                        "deploy_method": "ssh" if row["credential_type"] in ("ssh_key", "ssh_password") else "winrm",
                        "username": cred_data.get("username", ""),
                        "password": cred_data.get("password", ""),
                        "ssh_key": cred_data.get("private_key", ""),
                        "agent_binary_url": f"https://api.osiriscare.net/updates/osiris-agent-{os_name}-amd64",
                    })
                except Exception:
                    continue

            # Transition matched devices to 'deploying'
            if pending_deploys:
                device_ids = [p["device_id"] for p in pending_deploys]
                await deploy_conn.execute("""
                    UPDATE discovered_devices SET device_status = 'deploying',
                        agent_deploy_attempted_at = NOW()
                    WHERE site_id = $1 AND local_device_id = ANY($2::text[])
                """, checkin.site_id, device_ids)
    except Exception as e:
        import logging
        logging.warning(f"Checkin {checkin.site_id}: pending deploys lookup failed: {e}")

    # API key single-use rotation: on first checkin, rotate the key so the
    # USB-provisioned key becomes useless. If the USB is lost before deployment,
    # the original key is dead.
    rotated_api_key = None
    if not last_checkin_time and not _ghost_detected:
        try:
            import hashlib as _hl
            new_key = secrets.token_urlsafe(32)
            new_hash = _hl.sha256(new_key.encode()).hexdigest()
            # Deactivate old key
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                old_key = auth_header[7:]
                old_hash = _hl.sha256(old_key.encode()).hexdigest()
                await conn.execute(
                    "UPDATE api_keys SET active = false WHERE key_hash = $1", old_hash
                )
            # Create new key
            await conn.execute("""
                INSERT INTO api_keys (key_hash, site_id, active, created_at, description)
                VALUES ($1, $2, true, NOW(), 'Rotated on first checkin')
            """, new_hash, checkin.site_id)
            rotated_api_key = new_key
            logger.info(f"API key rotated on first checkin for {canonical_id}")
        except Exception as e:
            logger.warning(f"API key rotation failed for {canonical_id}: {e}")

        # Send welcome email to client contact on first appliance checkin
        try:
            client_email = await conn.fetchval(
                "SELECT client_contact_email FROM sites WHERE site_id = $1", checkin.site_id
            )
            clinic_name = await conn.fetchval(
                "SELECT clinic_name FROM sites WHERE site_id = $1", checkin.site_id
            )
            if client_email:
                from .email_alerts import _send_smtp_with_retry
                subject = f"OsirisCare — Your compliance monitoring is active"
                body = (
                    f"Hi,\n\n"
                    f"Your OsirisCare compliance appliance for {clinic_name or checkin.site_id} is now online "
                    f"and monitoring your network.\n\n"
                    f"What happens next:\n"
                    f"  1. The appliance will discover devices on your network\n"
                    f"  2. You'll receive a notification to register device credentials\n"
                    f"  3. Compliance scanning begins automatically\n\n"
                    f"You can view your compliance dashboard at any time through your client portal.\n\n"
                    f"— OsirisCare\n"
                )
                await _send_smtp_with_retry(client_email, subject, body)
                logger.info(f"Welcome email sent to {client_email} for site {checkin.site_id}")
        except Exception as e:
            logger.debug(f"Welcome email skipped: {e}")

    # === Time-travel reconciliation (Session 205 Phase 2) ===
    # If the daemon reported ≥2 signals and requested reconcile, generate
    # a signed plan inline so the agent gets it in the same round-trip.
    # Failure here MUST NOT break the checkin — the daemon will retry
    # next cycle (signals persist until a successful reconcile).
    reconcile_plan_payload = None
    if checkin.reconcile_needed and checkin.reconcile_signals and len(checkin.reconcile_signals) >= 2:
        from .reconcile import (
            ReconcileRequest as _RR,
            issue_reconcile_plan as _issue_plan,
        )
        rr = _RR(
            appliance_id=canonical_id,
            site_id=checkin.site_id,
            reported_boot_counter=checkin.boot_counter or 0,
            reported_generation_uuid=checkin.generation_uuid,
            reported_uptime_seconds=checkin.uptime_seconds or 0,
            clock_skew_seconds=0,  # Server is authoritative; agent NTP-sync is prerequisite
            detection_signals=checkin.reconcile_signals,
        )
        # Open a fresh SQLAlchemy session so a reconcile failure cannot
        # poison the asyncpg `tenant_connection` transaction used for the
        # rest of checkin. Belt-and-suspenders rollback in the except
        # handler — issue_reconcile_plan does commit() on both success +
        # rejection paths, but if sign_data or the INSERT raises between
        # branches, we want explicit cleanup rather than relying on
        # implicit __aexit__ rollback semantics.
        _rsess = _reconcile_session()
        try:
            plan = await _issue_plan(_rsess, rr)
            reconcile_plan_payload = {
                "plan_id": plan.event_id,
                "new_generation_uuid": plan.generation_uuid,
                "nonce_epoch_hex": plan.nonce_epoch_hex,
                "runbook_ids": plan.runbook_ids,
                "issued_at": plan.issued_at,
                "appliance_id": canonical_id,
                "signature_hex": plan.plan_signature_hex,
                # signed_payload: the EXACT canonical JSON string that was
                # signed. Agent verifies signature_hex against this string
                # byte-for-byte. Reconstructing client-side causes
                # cross-language whitespace divergence (Python vs Go JSON).
                "signed_payload": plan.signed_payload,
            }
            logger.warning(
                f"Reconcile plan issued inline for {canonical_id}: "
                f"signals={checkin.reconcile_signals} event_id={plan.event_id}"
            )
        except HTTPException as he:
            # Rejection (400) or appliance-not-registered (404) — don't ship
            # a plan this cycle. Audit row already written by issue_reconcile_plan.
            try:
                await _rsess.rollback()
            except Exception:
                pass
            logger.info(
                f"Reconcile skipped for {canonical_id}: {he.status_code} {he.detail}"
            )
        except Exception as e:
            try:
                await _rsess.rollback()
            except Exception:
                pass
            logger.error(f"Reconcile plan issuance failed for {canonical_id}: {e}")
        finally:
            try:
                await _rsess.close()
            except Exception:
                pass

    # Phase 13 H5: stamp the fingerprint we're delivering so divergence
    # from the current server key is queryable + gauge-able. Best-effort
    # (checkin already succeeded; failure here must not break the response).
    if pubkey_fingerprint and canonical_id:
        try:
            async with admin_connection(pool) as _fp_conn:
                await _fp_conn.execute(
                    "UPDATE site_appliances "
                    "SET server_pubkey_fingerprint_seen = $1, "
                    "    server_pubkey_fingerprint_seen_at = NOW() "
                    "WHERE appliance_id = $2",
                    pubkey_fingerprint, canonical_id,
                )
        except Exception as _e:
            logger.debug(f"pubkey fingerprint stamp failed: {_e}")

    return {
        "status": "ok",
        "appliance_id": canonical_id,
        "server_time": now.isoformat(),
        "rotated_api_key": rotated_api_key,
        "server_public_key": server_public_key,
        "server_public_keys": server_public_keys,
        "merged_duplicates": len(merge_from_ids),
        "pending_orders": pending_orders,
        "windows_targets": windows_targets,
        "linux_targets": linux_targets,
        "encrypted_credentials": encrypted_credentials,
        "enabled_runbooks": enabled_runbooks,
        "disabled_checks": disabled_checks,
        "maintenance_until": maintenance_until,
        "trigger_enumeration": trigger_enumeration,
        "trigger_immediate_scan": trigger_immediate_scan,
        "billing_hold": billing_hold,
        "billing_status": billing_status,
        "pending_deploys": pending_deploys,
        # L2 confidence threshold: daemon uses this to gate auto-execution.
        # Default 0.8 for production safety (higher than the 0.6 hardcoded in daemon).
        # Will be made per-site configurable via site_drift_config in a future update.
        "l2_confidence_threshold": 0.8,
        "peer_bundle_hashes": peer_bundle_hashes,
        "mesh_peers": mesh_peers,
        "target_assignments": target_assignments,
        "client_alert_mode": effective_alert_mode,
        "compliance_framework": compliance_framework,
        # Time-travel reconciliation plan (Session 205 Phase 2). Null unless
        # daemon reported ≥2 detection signals AND validation accepted them.
        "reconcile_plan": reconcile_plan_payload,
    }


# =============================================================================
# STATUS ROLLUP — fleet-wide appliance status (Migration 191)
# =============================================================================

@appliances_router.get("/status-rollup")
async def get_status_rollup(
    site_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    user: dict = Depends(require_auth),
):
    """Fleet-wide appliance status from the appliance_status_rollup MV.

    Reads the materialized view (refreshed every 60s by
    heartbeat_rollup_loop) instead of site_appliances. The MV pre-computes
    live_status, stale_seconds, and 24h uptime ratios, so the dashboard
    doesn't have to recompute them per viewer.

    Query params:
      site_id — restrict to one site
      status — filter by live_status (online | stale | offline)
      limit — cap returned rows (1..5000)
    """
    pool = await get_pool()
    where_parts: List[str] = []
    args: List[Any] = []
    if site_id:
        args.append(site_id)
        where_parts.append(f"site_id = ${len(args)}")
    if status:
        args.append(status)
        where_parts.append(f"live_status = ${len(args)}")
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    args.append(limit)
    sql = f"""
        SELECT
            appliance_id, site_id, hostname, display_name, mac_address,
            ip_addresses, agent_version, live_status,
            cached_last_checkin AS last_checkin,
            last_heartbeat_at,
            stale_seconds,
            liveness_drift_seconds,
            checkin_count_24h, online_count_24h,
            uptime_ratio_24h
        FROM appliance_status_rollup
        {where_clause}
        ORDER BY
            CASE live_status WHEN 'offline' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END,
            stale_seconds DESC
        LIMIT ${len(args)}
    """
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(sql, *args)
    counts: Dict[str, int] = {'online': 0, 'stale': 0, 'offline': 0}
    out = []
    for r in rows:
        d = dict(r)
        # asyncpg returns ip_addresses as str (jsonb) — normalize to list
        if isinstance(d.get('ip_addresses'), str):
            try:
                d['ip_addresses'] = json.loads(d['ip_addresses'])
            except Exception:
                d['ip_addresses'] = []
        if d.get('last_checkin'):
            d['last_checkin'] = d['last_checkin'].isoformat()
        if d.get('last_heartbeat_at'):
            d['last_heartbeat_at'] = d['last_heartbeat_at'].isoformat()
        counts[d['live_status']] = counts.get(d['live_status'], 0) + 1
        out.append(d)
    return {
        'appliances': out,
        'count': len(out),
        'totals': counts,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# INSTALL SESSIONS — visibility for in-flight installer ISOs (Migration 190)
# =============================================================================

@appliances_router.get("/install-sessions")
async def list_install_sessions(user: dict = Depends(require_auth)):
    """List active and recently abandoned install sessions.

    Installer ISOs (boot_source='live_usb') register here instead of
    site_appliances to prevent phantom rows. Operators can see what's
    currently trying to install and spot stuck/abandoned attempts.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT
                session_id, site_id, mac_address, hostname,
                ip_addresses, agent_version, nixos_version,
                boot_source, first_seen, last_seen, checkin_count,
                install_stage, expires_at,
                EXTRACT(EPOCH FROM (NOW() - last_seen))::int AS stale_seconds,
                EXTRACT(EPOCH FROM (expires_at - NOW()))::int AS ttl_seconds
            FROM install_sessions
            WHERE last_seen > NOW() - INTERVAL '48 hours'
            ORDER BY last_seen DESC
            LIMIT 200
        """)
    return {
        "count": len(rows),
        "sessions": [dict(r) for r in rows],
    }


# =============================================================================
# ALERTS API
# =============================================================================

alerts_router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class EmailAlertRequest(BaseModel):
    """Request to send an email alert."""
    site_id: Optional[str] = None
    alert_type: str = "escalation"
    severity: str = "high"
    subject: str
    body: str
    incident_id: Optional[str] = None
    recipient: Optional[str] = None  # Override default recipient


@alerts_router.post("/email")
async def send_email_alert(request: EmailAlertRequest, auth_site_id: str = Depends(require_appliance_bearer)):
    """Send an email alert for L3 escalations or other critical events.

    This endpoint allows agents and chaos probes to send email alerts
    when incidents escalate to L3 (human intervention required).

    Returns:
        Success status and email details
    """
    from .email_alerts import send_critical_alert, is_email_configured, ALERT_EMAIL

    if not is_email_configured():
        raise HTTPException(
            status_code=503,
            detail="Email not configured. Set SMTP_USER and SMTP_PASSWORD environment variables."
        )

    # Build metadata for the email
    metadata = {
        "alert_type": request.alert_type,
        "incident_id": request.incident_id,
    }

    # Send the email
    success = send_critical_alert(
        title=request.subject,
        message=request.body,
        site_id=request.site_id,
        category=request.alert_type,
        metadata=metadata
    )

    if success:
        return {
            "status": "sent",
            "recipient": request.recipient or ALERT_EMAIL,
            "subject": request.subject,
            "alert_type": request.alert_type,
            "incident_id": request.incident_id,
        }
    else:
        raise HTTPException(
            status_code=500,
            detail="Failed to send email alert. Check server logs for details."
        )


# =============================================================================
# WORKSTATION COMPLIANCE API
# =============================================================================

@router.get("/{site_id}/workstations")
async def get_site_workstations(site_id: str, user: dict = Depends(require_auth)):
    """Get all workstations for a site with compliance summary. Requires authentication.

    Returns workstation discovery and compliance check results from
    the appliance's AD-based workstation scanning.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get summary (if exists)
        summary_row = await conn.fetchrow("""
            SELECT site_id, total_workstations, online_workstations,
                   compliant_workstations, drifted_workstations, error_workstations,
                   unknown_workstations, overall_compliance_rate, check_compliance,
                   last_scan
            FROM site_workstation_summaries
            WHERE site_id = $1
        """, site_id)

        summary = None
        if summary_row:
            check_compliance = summary_row['check_compliance']
            if isinstance(check_compliance, str):
                try:
                    check_compliance = json.loads(check_compliance)
                except (json.JSONDecodeError, TypeError):
                    check_compliance = {}

            summary = {
                'site_id': summary_row['site_id'],
                'total_workstations': summary_row['total_workstations'],
                'online_workstations': summary_row['online_workstations'],
                'compliant_workstations': summary_row['compliant_workstations'],
                'drifted_workstations': summary_row['drifted_workstations'],
                'error_workstations': summary_row['error_workstations'],
                'unknown_workstations': summary_row['unknown_workstations'],
                'overall_compliance_rate': float(summary_row['overall_compliance_rate'] or 0),
                'check_compliance': check_compliance or {},
                'last_scan': summary_row['last_scan'].isoformat() if summary_row['last_scan'] else None,
            }

        # Get workstations with latest checks
        ws_rows = await conn.fetch("""
            SELECT w.id, w.hostname, w.ip_address, w.os_name, w.os_version,
                   w.online, w.last_seen, w.compliance_status, w.last_compliance_check,
                   w.compliance_percentage
            FROM workstations w
            WHERE w.site_id = $1
            ORDER BY w.hostname
        """, site_id)

        workstations = []
        for ws in ws_rows:
            # Get check results for this workstation
            check_rows = await conn.fetch("""
                SELECT check_type, status, compliant, details, hipaa_controls, checked_at
                FROM workstation_checks
                WHERE workstation_id = $1
                ORDER BY checked_at DESC
            """, ws['id'])

            # Build checks dict (latest per check_type)
            checks = {}
            seen_types = set()
            for check in check_rows:
                if check['check_type'] not in seen_types:
                    seen_types.add(check['check_type'])
                    details = check['details']
                    if isinstance(details, str):
                        try:
                            details = json.loads(details)
                        except (json.JSONDecodeError, TypeError):
                            details = {}

                    hipaa = check['hipaa_controls']
                    if isinstance(hipaa, str):
                        try:
                            hipaa = json.loads(hipaa)
                        except (json.JSONDecodeError, TypeError):
                            hipaa = []

                    checks[check['check_type']] = {
                        'check_type': check['check_type'],
                        'status': check['status'],
                        'compliant': check['compliant'],
                        'details': details or {},
                        'hipaa_controls': hipaa or [],
                        'checked_at': check['checked_at'].isoformat() if check['checked_at'] else None,
                    }

            workstations.append({
                'id': str(ws['id']),
                'hostname': ws['hostname'],
                'ip_address': ws['ip_address'],
                'os_name': ws['os_name'],
                'os_version': ws['os_version'],
                'online': ws['online'],
                'last_seen': ws['last_seen'].isoformat() if ws['last_seen'] else None,
                'compliance_status': ws['compliance_status'] or 'unknown',
                'last_compliance_check': ws['last_compliance_check'].isoformat() if ws['last_compliance_check'] else None,
                'compliance_percentage': float(ws['compliance_percentage'] or 0),
                'checks': checks,
            })

        return {
            'summary': summary,
            'workstations': workstations,
        }


# =============================================================================
# RMM Comparison Endpoints
# =============================================================================
# NOTE: These routes MUST be defined BEFORE /{workstation_id} to avoid
# "rmm-compare" being captured as a workstation_id by the dynamic route.


@router.post("/{site_id}/workstations/rmm-compare")
async def compare_workstations_with_rmm(
    site_id: str,
    rmm_data: Dict[str, Any],
    user: dict = Depends(require_operator),
):
    """Compare site workstations with RMM tool data. Requires operator or admin access.

    Accepts RMM device data and returns a comparison report showing:
    - Matched devices (with confidence scores)
    - Coverage gaps (missing from RMM or AD)
    - Deduplication recommendations

    Request body:
    {
        "provider": "connectwise" | "datto" | "ninja" | "syncro" | "manual",
        "devices": [
            {
                "hostname": "WS01",
                "ip_address": "192.168.1.101",
                "mac_address": "00:1A:2B:3C:4D:5E",
                "os_name": "Windows 10",
                "serial_number": "ABC123",
                "device_id": "RMM-001"
            },
            ...
        ]
    }
    """
    pool = await get_pool()

    # Validate request
    provider = rmm_data.get("provider", "manual")
    devices = rmm_data.get("devices", [])

    if not devices:
        raise HTTPException(
            status_code=400,
            detail="No RMM devices provided"
        )

    async with admin_connection(pool) as conn:
        # Get our workstations for this site
        ws_rows = await conn.fetch("""
            SELECT hostname, ip_address, mac_address, os_name, os_version, online
            FROM workstations
            WHERE site_id = $1
        """, site_id)

        if not ws_rows:
            return {
                'error': 'no_workstations',
                'message': f'No workstations discovered for site {site_id}. Run a workstation scan first.',
            }

        workstations = [dict(row) for row in ws_rows]

        # Perform comparison (inline implementation to avoid agent dependency)
        comparison = _compare_workstations_with_rmm(workstations, devices, provider)

        # Store comparison result for audit trail
        await conn.execute("""
            INSERT INTO rmm_comparison_reports (
                site_id, provider, our_count, rmm_count,
                matched_count, coverage_rate, report_data, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            ON CONFLICT (site_id) DO UPDATE SET
                provider = EXCLUDED.provider,
                our_count = EXCLUDED.our_count,
                rmm_count = EXCLUDED.rmm_count,
                matched_count = EXCLUDED.matched_count,
                coverage_rate = EXCLUDED.coverage_rate,
                report_data = EXCLUDED.report_data,
                created_at = EXCLUDED.created_at
        """,
            site_id,
            provider,
            comparison['summary']['our_device_count'],
            comparison['summary']['rmm_device_count'],
            comparison['summary']['matched_count'],
            comparison['summary']['coverage_rate'],
            json.dumps(comparison),
            datetime.now(timezone.utc),
        )

        return comparison


@router.get("/{site_id}/workstations/rmm-compare")
async def get_rmm_comparison_report(site_id: str, user: dict = Depends(require_auth)):
    """Get the latest RMM comparison report for a site. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        row = await conn.fetchrow("""
            SELECT site_id, provider, our_count, rmm_count,
                   matched_count, coverage_rate, report_data, created_at
            FROM rmm_comparison_reports
            WHERE site_id = $1
        """, site_id)

        if not row:
            return {
                'error': 'no_report',
                'message': f'No RMM comparison report found for site {site_id}. Upload RMM data to generate one.',
            }

        return {
            'site_id': row['site_id'],
            'provider': row['provider'],
            'summary': {
                'our_device_count': row['our_count'],
                'rmm_device_count': row['rmm_count'],
                'matched_count': row['matched_count'],
                'coverage_rate': float(row['coverage_rate'] or 0),
            },
            'report': row['report_data'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        }


@router.get("/{site_id}/workstations/{workstation_id}")
async def get_workstation(site_id: str, workstation_id: str, user: dict = Depends(require_auth)):
    """Get details for a specific workstation. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        ws = await conn.fetchrow("""
            SELECT id, hostname, ip_address, os_name, os_version,
                   online, last_seen, compliance_status, last_compliance_check,
                   compliance_percentage
            FROM workstations
            WHERE site_id = $1 AND id = $2
        """, site_id, workstation_id)

        if not ws:
            raise HTTPException(status_code=404, detail=f"Workstation {workstation_id} not found")

        # Get check results
        check_rows = await conn.fetch("""
            SELECT check_type, status, compliant, details, hipaa_controls, checked_at
            FROM workstation_checks
            WHERE workstation_id = $1
            ORDER BY checked_at DESC
        """, ws['id'])

        checks = {}
        seen_types = set()
        for check in check_rows:
            if check['check_type'] not in seen_types:
                seen_types.add(check['check_type'])
                details = check['details']
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except (json.JSONDecodeError, TypeError):
                        details = {}

                hipaa = check['hipaa_controls']
                if isinstance(hipaa, str):
                    try:
                        hipaa = json.loads(hipaa)
                    except (json.JSONDecodeError, TypeError):
                        hipaa = []

                checks[check['check_type']] = {
                    'check_type': check['check_type'],
                    'status': check['status'],
                    'compliant': check['compliant'],
                    'details': details or {},
                    'hipaa_controls': hipaa or [],
                    'checked_at': check['checked_at'].isoformat() if check['checked_at'] else None,
                }

        return {
            'id': str(ws['id']),
            'hostname': ws['hostname'],
            'ip_address': ws['ip_address'],
            'os_name': ws['os_name'],
            'os_version': ws['os_version'],
            'online': ws['online'],
            'last_seen': ws['last_seen'].isoformat() if ws['last_seen'] else None,
            'compliance_status': ws['compliance_status'] or 'unknown',
            'last_compliance_check': ws['last_compliance_check'].isoformat() if ws['last_compliance_check'] else None,
            'compliance_percentage': float(ws['compliance_percentage'] or 0),
            'checks': checks,
        }


@router.post("/{site_id}/workstations/scan")
async def trigger_workstation_scan(site_id: str, user: dict = Depends(require_operator)):
    """Trigger a workstation compliance scan for a site. Requires operator or admin access.

    Creates an order for the appliance to initiate an immediate
    workstation discovery and compliance check cycle.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Find the first online appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id
            FROM site_appliances
            WHERE site_id = $1 AND status = 'online'
            ORDER BY last_checkin DESC
            LIMIT 1
        """, site_id)

        if not appliance:
            raise HTTPException(
                status_code=404,
                detail=f"No online appliance found for site {site_id}"
            )

        # Create an order to trigger workstation scan
        order_id = generate_order_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)
        scan_params = {'command': 'workstation_scan', 'force': True}

        nonce, signature, signed_payload = sign_admin_order(
            order_id, 'run_command', scan_params, now, expires_at,
            target_appliance_id=appliance['appliance_id'],
        )

        await conn.execute("""
            INSERT INTO admin_orders (
                order_id, appliance_id, site_id, order_type,
                parameters, priority, status, created_at, expires_at,
                nonce, signature, signed_payload
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
        """,
            order_id,
            appliance['appliance_id'],
            site_id,
            'run_command',
            json.dumps(scan_params),
            10,  # High priority
            'pending',
            now,
            expires_at,
            nonce,
            signature,
            signed_payload,
        )

        return {
            'status': 'scan_requested',
            'order_id': order_id,
            'site_id': site_id,
            'appliance_id': appliance['appliance_id'],
            'message': 'Workstation scan will run on next appliance check-in',
        }


# =============================================================================
# RMM Comparison Helper Function
# =============================================================================


def _compare_workstations_with_rmm(
    workstations: list,
    rmm_devices: list,
    provider: str,
) -> Dict[str, Any]:
    """
    Compare workstations with RMM devices.

    This is a simplified version of the agent's RMMComparisonEngine
    for use in the Central Command backend.
    """
    import re

    # Normalize functions
    def normalize_hostname(hostname: str) -> str:
        return hostname.upper().strip().split('.')[0] if hostname else ""

    def normalize_mac(mac: str) -> str:
        if not mac:
            return ""
        return re.sub(r'[^A-Fa-f0-9]', '', mac).upper()

    # Build RMM device index
    rmm_by_hostname = {}
    rmm_by_ip = {}
    rmm_by_mac = {}

    for device in rmm_devices:
        hostname = normalize_hostname(device.get("hostname", ""))
        ip = device.get("ip_address", "")
        mac = normalize_mac(device.get("mac_address", ""))

        if hostname:
            rmm_by_hostname.setdefault(hostname, []).append(device)
        if ip:
            rmm_by_ip.setdefault(ip, []).append(device)
        if mac:
            rmm_by_mac.setdefault(mac, []).append(device)

    matches = []
    matched_rmm_ids = set()

    for ws in workstations:
        ws_hostname = normalize_hostname(ws.get("hostname", ""))
        ws_ip = ws.get("ip_address", "")
        ws_mac = normalize_mac(ws.get("mac_address", ""))

        best_match = None
        best_score = 0
        matching_fields = []

        # Try hostname match
        if ws_hostname in rmm_by_hostname:
            for rmm in rmm_by_hostname[ws_hostname]:
                score = 0.35
                fields = ["hostname"]

                # Check additional fields
                if ws_ip and rmm.get("ip_address") == ws_ip:
                    score += 0.30
                    fields.append("ip_address")
                if ws_mac and normalize_mac(rmm.get("mac_address", "")) == ws_mac:
                    score += 0.35
                    fields.append("mac_address")

                if score > best_score:
                    best_score = score
                    best_match = rmm
                    matching_fields = fields

        # Try IP match if no hostname match
        if not best_match and ws_ip in rmm_by_ip:
            for rmm in rmm_by_ip[ws_ip]:
                score = 0.30
                fields = ["ip_address"]
                if ws_mac and normalize_mac(rmm.get("mac_address", "")) == ws_mac:
                    score += 0.35
                    fields.append("mac_address")
                if score > best_score:
                    best_score = score
                    best_match = rmm
                    matching_fields = fields

        # Try MAC match if no other match
        if not best_match and ws_mac in rmm_by_mac:
            for rmm in rmm_by_mac[ws_mac]:
                best_score = 0.35
                best_match = rmm
                matching_fields = ["mac_address"]

        # Determine confidence
        if best_score >= 0.90:
            confidence = "exact"
        elif best_score >= 0.60:
            confidence = "high"
        elif best_score >= 0.35:
            confidence = "medium"
        elif best_score >= 0.15:
            confidence = "low"
        else:
            confidence = "no_match"

        matches.append({
            "our_hostname": ws.get("hostname", ""),
            "rmm_device": best_match,
            "confidence": confidence,
            "confidence_score": best_score,
            "matching_fields": matching_fields,
        })

        if best_match:
            rmm_id = best_match.get("device_id") or best_match.get("hostname", "")
            matched_rmm_ids.add(rmm_id)

    # Find gaps
    gaps = []

    # Our devices not in RMM
    for match in matches:
        if match["confidence"] == "no_match":
            gaps.append({
                "gap_type": "missing_from_rmm",
                "device": {"hostname": match["our_hostname"]},
                "recommendation": f"Add {match['our_hostname']} to RMM or verify exclusion",
                "severity": "medium",
            })

    # RMM devices not in our data
    for rmm in rmm_devices:
        rmm_id = rmm.get("device_id") or rmm.get("hostname", "")
        if rmm_id not in matched_rmm_ids:
            gaps.append({
                "gap_type": "missing_from_ad",
                "device": rmm,
                "recommendation": f"Device {rmm.get('hostname', 'unknown')} in RMM but not in AD",
                "severity": "medium",
            })

    # Calculate metrics
    matched_count = sum(1 for m in matches if m["confidence"] != "no_match")
    exact_count = sum(1 for m in matches if m["confidence"] == "exact")
    coverage_rate = (matched_count / len(workstations) * 100) if workstations else 0

    return {
        "summary": {
            "our_device_count": len(workstations),
            "rmm_device_count": len(rmm_devices),
            "matched_count": matched_count,
            "exact_match_count": exact_count,
            "coverage_rate": round(coverage_rate, 1),
        },
        "matches": matches,
        "gaps": gaps,
        "metadata": {
            "provider": provider,
            "comparison_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# =============================================================================
# GO AGENTS API (Workstation-scale gRPC agents)
# =============================================================================
# These endpoints support the Go agent dashboard in the frontend.
# Go agents are lightweight Windows workstation agents that push drift
# events to the appliance via gRPC (port 50051).
#
# NOTE: The actual gRPC communication is handled by the appliance's
# grpc_server.py. These REST endpoints are for the dashboard to view
# and manage registered agents.
# =============================================================================

class GoAgentTierUpdate(BaseModel):
    """Model for updating Go agent capability tier."""
    capability_tier: int  # 0=monitor_only, 1=self_heal, 2=full_remediation


# Capability tier mapping
CAPABILITY_TIERS = {
    0: 'monitor_only',
    1: 'self_heal',
    2: 'full_remediation',
}


@router.get("/{site_id}/agents")
async def get_site_go_agents(site_id: str, user: dict = Depends(require_auth)):
    """Get all Go agents for a site with summary. Requires authentication.

    Returns registered Go agents and aggregated compliance summary.
    Go agents push drift events via gRPC to the appliance.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Get agents with their latest checks (live query, no stale summary)
        agent_rows = await conn.fetch("""
            SELECT agent_id, hostname, ip_address, agent_version,
                   capability_tier, status, checks_passed, checks_total,
                   compliance_percentage, rmm_detected, rmm_disabled,
                   offline_queue_size, connected_at, last_heartbeat,
                   GREATEST(last_heartbeat, updated_at) as effective_heartbeat
            FROM go_agents
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)

        agents = []
        for row in agent_rows:
            # Get latest checks for this agent
            check_rows = await conn.fetch("""
                SELECT check_type, status, message, details, hipaa_control, checked_at
                FROM go_agent_checks
                WHERE agent_id = $1
                ORDER BY checked_at DESC
            """, row['agent_id'])

            # Deduplicate by check_type (keep most recent)
            checks = []
            seen_types = set()
            for check in check_rows:
                if check['check_type'] not in seen_types:
                    seen_types.add(check['check_type'])
                    details = check['details']
                    if isinstance(details, str):
                        try:
                            details = json.loads(details)
                        except (json.JSONDecodeError, TypeError):
                            details = {}

                    checks.append({
                        'check_type': check['check_type'],
                        'status': check['status'],
                        'message': check['message'],
                        'details': details,
                        'hipaa_control': check['hipaa_control'],
                        'checked_at': check['checked_at'].isoformat() if check['checked_at'] else None,
                    })

            agents.append({
                'id': row['agent_id'],
                'hostname': row['hostname'],
                'ip_address': row['ip_address'],
                'agent_version': row['agent_version'],
                'capability_tier': CAPABILITY_TIERS.get(row['capability_tier'], 'monitor_only'),
                'status': row['status'],
                'checks_passed': row['checks_passed'] or 0,
                'checks_total': row['checks_total'] or 0,
                'compliance_percentage': float(row['compliance_percentage'] or 0),
                'rmm_detected': row['rmm_detected'],
                'rmm_disabled': row['rmm_disabled'] or False,
                'offline_queue_size': row['offline_queue_size'] or 0,
                'connected_at': row['connected_at'].isoformat() if row['connected_at'] else None,
                'last_heartbeat': row['last_heartbeat'].isoformat() if row['last_heartbeat'] else None,
                'checks': checks,
            })

        # Compute summary live from agents (no stale summary table)
        tier_counts = {}
        version_counts = {}
        total_comp = 0.0
        active = offline = error = 0
        for a in agents:
            tier_counts[a['capability_tier']] = tier_counts.get(a['capability_tier'], 0) + 1
            if a['agent_version']:
                version_counts[a['agent_version']] = version_counts.get(a['agent_version'], 0) + 1
            total_comp += a['compliance_percentage']
            if a['status'] == 'connected':
                active += 1
            elif a['status'] == 'error':
                error += 1
            else:
                offline += 1

        summary = {
            'site_id': site_id,
            'total_agents': len(agents),
            'active_agents': active,
            'offline_agents': offline,
            'error_agents': error,
            'pending_agents': 0,
            'overall_compliance_rate': round(total_comp / len(agents), 1) if agents else 0,
            'agents_by_tier': tier_counts,
            'agents_by_version': version_counts,
            'rmm_detected_count': sum(1 for a in agents if a.get('rmm_detected')),
            'last_event': max((a['last_heartbeat'] for a in agents if a['last_heartbeat']), default=None),
        }

        return {
            'summary': summary,
            'agents': agents,
        }


@router.get("/{site_id}/agents/summary")
async def get_go_agent_summary(site_id: str, user: dict = Depends(require_auth)):
    """Get Go agent summary for a site. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        summary_row = await conn.fetchrow("""
            SELECT site_id, total_agents, active_agents, offline_agents,
                   error_agents, pending_agents, overall_compliance_rate,
                   agents_by_tier, agents_by_version, rmm_detected_count,
                   last_event
            FROM site_go_agent_summaries
            WHERE site_id = $1
        """, site_id)

        if not summary_row:
            return {
                'site_id': site_id,
                'total_agents': 0,
                'active_agents': 0,
                'offline_agents': 0,
                'error_agents': 0,
                'pending_agents': 0,
                'overall_compliance_rate': 0,
                'agents_by_tier': {'monitor_only': 0, 'self_heal': 0, 'full_remediation': 0},
                'agents_by_version': {},
                'rmm_detected_count': 0,
                'last_event': None,
            }

        agents_by_tier = summary_row['agents_by_tier']
        if isinstance(agents_by_tier, str):
            try:
                agents_by_tier = json.loads(agents_by_tier)
            except (json.JSONDecodeError, TypeError):
                agents_by_tier = {}

        agents_by_version = summary_row['agents_by_version']
        if isinstance(agents_by_version, str):
            try:
                agents_by_version = json.loads(agents_by_version)
            except (json.JSONDecodeError, TypeError):
                agents_by_version = {}

        return {
            'site_id': summary_row['site_id'],
            'total_agents': summary_row['total_agents'] or 0,
            'active_agents': summary_row['active_agents'] or 0,
            'offline_agents': summary_row['offline_agents'] or 0,
            'error_agents': summary_row['error_agents'] or 0,
            'pending_agents': summary_row['pending_agents'] or 0,
            'overall_compliance_rate': float(summary_row['overall_compliance_rate'] or 0),
            'agents_by_tier': agents_by_tier,
            'agents_by_version': agents_by_version,
            'rmm_detected_count': summary_row['rmm_detected_count'] or 0,
            'last_event': summary_row['last_event'].isoformat() if summary_row['last_event'] else None,
        }


@router.get("/{site_id}/agents/{agent_id}")
async def get_go_agent(site_id: str, agent_id: str, user: dict = Depends(require_auth)):
    """Get details for a specific Go agent. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        agent = await conn.fetchrow("""
            SELECT agent_id, hostname, ip_address, mac_address,
                   agent_version, os_name, os_version, capability_tier,
                   status, checks_passed, checks_total, compliance_percentage,
                   rmm_detected, rmm_disabled, offline_queue_size,
                   connected_at, last_heartbeat, created_at
            FROM go_agents
            WHERE site_id = $1 AND agent_id = $2
        """, site_id, agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Go agent {agent_id} not found")

        # Get check results
        check_rows = await conn.fetch("""
            SELECT check_type, status, message, details, hipaa_control, checked_at
            FROM go_agent_checks
            WHERE agent_id = $1
            ORDER BY checked_at DESC
        """, agent_id)

        checks = []
        seen_types = set()
        for check in check_rows:
            if check['check_type'] not in seen_types:
                seen_types.add(check['check_type'])
                details = check['details']
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except (json.JSONDecodeError, TypeError):
                        details = {}

                checks.append({
                    'check_type': check['check_type'],
                    'status': check['status'],
                    'message': check['message'],
                    'details': details,
                    'hipaa_control': check['hipaa_control'],
                    'checked_at': check['checked_at'].isoformat() if check['checked_at'] else None,
                })

        return {
            'id': agent['agent_id'],
            'hostname': agent['hostname'],
            'ip_address': agent['ip_address'],
            'mac_address': agent['mac_address'],
            'agent_version': agent['agent_version'],
            'os_name': agent['os_name'],
            'os_version': agent['os_version'],
            'capability_tier': CAPABILITY_TIERS.get(agent['capability_tier'], 'monitor_only'),
            'status': agent['status'],
            'checks_passed': agent['checks_passed'] or 0,
            'checks_total': agent['checks_total'] or 0,
            'compliance_percentage': float(agent['compliance_percentage'] or 0),
            'rmm_detected': agent['rmm_detected'],
            'rmm_disabled': agent['rmm_disabled'] or False,
            'offline_queue_size': agent['offline_queue_size'] or 0,
            'connected_at': agent['connected_at'].isoformat() if agent['connected_at'] else None,
            'last_heartbeat': agent['last_heartbeat'].isoformat() if agent['last_heartbeat'] else None,
            'created_at': agent['created_at'].isoformat() if agent['created_at'] else None,
            'checks': checks,
        }


@router.put("/{site_id}/agents/{agent_id}/tier")
async def update_go_agent_tier(site_id: str, agent_id: str, data: GoAgentTierUpdate, user: dict = Depends(require_operator)):
    """Update the capability tier for a Go agent. Requires operator or admin access.

    Tiers control what the agent can do:
    - 0 (monitor_only): Just reports drift, no remediation
    - 1 (self_heal): Can fix drift locally (e.g., enable Defender)
    - 2 (full_remediation): Full automation including disruptive actions
    """
    if data.capability_tier not in CAPABILITY_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid capability_tier. Must be 0, 1, or 2."
        )

    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Check agent exists
        agent = await conn.fetchrow("""
            SELECT agent_id FROM go_agents
            WHERE site_id = $1 AND agent_id = $2
        """, site_id, agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Go agent {agent_id} not found")

        # Update tier
        await conn.execute("""
            UPDATE go_agents
            SET capability_tier = $1, updated_at = NOW()
            WHERE agent_id = $2
        """, data.capability_tier, agent_id)

        # Create order to notify agent of tier change
        order_id = generate_order_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)

        await conn.execute("""
            INSERT INTO go_agent_orders (order_id, agent_id, site_id, order_type,
                                         parameters, status, created_at, expires_at)
            VALUES ($1, $2, $3, 'update_tier', $4, 'pending', $5, $6)
        """, order_id, agent_id, site_id,
            json.dumps({'capability_tier': data.capability_tier}),
            now, expires_at)

        return {'status': 'success', 'capability_tier': CAPABILITY_TIERS[data.capability_tier]}


@router.post("/{site_id}/agents/{agent_id}/check")
async def trigger_go_agent_check(site_id: str, agent_id: str, user: dict = Depends(require_operator)):
    """Trigger an immediate compliance check on a Go agent. Requires operator or admin access.

    Creates an order for the agent to run all compliance checks
    and report results.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Check agent exists
        agent = await conn.fetchrow("""
            SELECT agent_id, status FROM go_agents
            WHERE site_id = $1 AND agent_id = $2
        """, site_id, agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Go agent {agent_id} not found")

        # Create order
        order_id = generate_order_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=5)

        await conn.execute("""
            INSERT INTO go_agent_orders (order_id, agent_id, site_id, order_type,
                                         parameters, status, created_at, expires_at)
            VALUES ($1, $2, $3, 'run_check', '{}', 'pending', $4, $5)
        """, order_id, agent_id, site_id, now, expires_at)

        return {
            'status': 'success',
            'message': f'Check request queued for agent {agent["agent_id"]}'
        }


@router.delete("/{site_id}/agents/{agent_id}")
async def remove_go_agent(site_id: str, agent_id: str, user: dict = Depends(require_operator)):
    """Remove a Go agent from the registry. Requires operator or admin access.

    This removes the agent record and all associated check results.
    The agent will need to re-register on next connection.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Check agent exists
        agent = await conn.fetchrow("""
            SELECT agent_id FROM go_agents
            WHERE site_id = $1 AND agent_id = $2
        """, site_id, agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Go agent {agent_id} not found")

        # Delete agent (cascades to checks and orders)
        await conn.execute("""
            DELETE FROM go_agents WHERE agent_id = $1
        """, agent_id)

        return {'status': 'success', 'agent_id': agent_id}


# =============================================================================
# CREDENTIAL IP UPDATE
# =============================================================================

@router.post("/credentials/{credential_id}/update-host")
async def update_credential_host(
    credential_id: str,
    body: dict,
    user: dict = Depends(require_auth),
):
    """Update the host/IP in a credential. Used when DHCP changes a device IP.

    Body: {"new_host": "192.168.88.233"}
    """
    import json as _json
    from .credential_crypto import decrypt_credential, encrypt_credential

    new_host = body.get("new_host")
    if not new_host:
        raise HTTPException(status_code=400, detail="new_host is required")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT id, site_id, credential_name, encrypted_data
            FROM site_credentials WHERE id = $1
        """, credential_id)
        if not row:
            raise HTTPException(status_code=404, detail="Credential not found")

        try:
            cred_json = decrypt_credential(row["encrypted_data"])
            cred_data = _json.loads(cred_json)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt credential")

        old_host = cred_data.get("host") or cred_data.get("target_host")
        cred_data["host"] = new_host
        if "target_host" in cred_data:
            cred_data["target_host"] = new_host

        new_encrypted = encrypt_credential(_json.dumps(cred_data))
        await conn.execute("""
            UPDATE site_credentials SET encrypted_data = $1, updated_at = NOW()
            WHERE id = $2
        """, new_encrypted, credential_id)

        logger.info(f"Credential {credential_id} host updated: {old_host} → {new_host} by {user.get('username')}")

    return {"status": "updated", "old_host": old_host, "new_host": new_host}


# =============================================================================
# SITE POLISH ENDPOINTS (Session 203 round-table recommendations)
# SLA indicator, in-site search, portal link expiry
# =============================================================================


@router.get("/{site_id}/sla")
async def get_site_sla(
    site_id: str,
    user: dict = Depends(require_auth),
):
    """Return the site's current healing SLA state.

    Primary source: `site_healing_sla` table (hourly rollups). When empty
    for this site, falls back to computing from execution_telemetry over
    the last 24h so the UI still gets a meaningful number.

    Response includes:
      - sla_target (static 90.0 unless overridden in the rollup table)
      - current_rate (most recent period's healing_rate)
      - sla_met (most recent period's boolean)
      - periods_last_7d / periods_met_last_7d / met_pct_7d
      - trend (up to 168 most recent hourly entries)
      - source: "site_healing_sla" | "execution_telemetry" | "none"
    """
    pool = await get_pool()

    # Primary query: pull latest SLA period + 7-day trend from the rollup table.
    # Try tenant_connection first so RLS is enforced; fall back to admin_connection
    # if the tenant context fails (e.g. transient RLS misconfig).
    sla_rows: List[Any] = []
    latest_row: Optional[Any] = None
    summary_row: Optional[Any] = None

    async def _fetch_sla(conn):
        latest = await conn.fetchrow(
            """
            SELECT site_id, period_start, period_end, total_attempts,
                   successful_heals, healing_rate, sla_target, sla_met, created_at
            FROM site_healing_sla
            WHERE site_id = $1
            ORDER BY period_start DESC
            LIMIT 1
            """,
            site_id,
        )
        trend = await conn.fetch(
            """
            SELECT period_start, healing_rate, sla_met
            FROM site_healing_sla
            WHERE site_id = $1
              AND period_start > NOW() - INTERVAL '7 days'
            ORDER BY period_start DESC
            LIMIT 168
            """,
            site_id,
        )
        summary = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS periods,
                   COUNT(*) FILTER (WHERE sla_met IS TRUE)::int AS met
            FROM site_healing_sla
            WHERE site_id = $1
              AND period_start > NOW() - INTERVAL '7 days'
            """,
            site_id,
        )
        return latest, trend, summary

    try:
        async with tenant_connection(pool, site_id=site_id) as conn:
            latest_row, sla_rows, summary_row = await _fetch_sla(conn)
    except Exception as e:
        logger.warning(f"site_sla: tenant_connection failed for {site_id}: {e} — retrying as admin")
        try:
            async with admin_connection(pool) as conn:
                latest_row, sla_rows, summary_row = await _fetch_sla(conn)
        except Exception as e2:
            logger.error(f"site_sla: admin fallback also failed for {site_id}: {e2}")
            latest_row = None
            sla_rows = []
            summary_row = None

    # If the primary table has data, use it.
    if latest_row is not None:
        trend = [
            {
                "period_start": r["period_start"].isoformat() if r["period_start"] else None,
                "healing_rate": float(r["healing_rate"]) if r["healing_rate"] is not None else None,
                "sla_met": bool(r["sla_met"]) if r["sla_met"] is not None else None,
            }
            for r in sla_rows
        ]
        periods_7d = int(summary_row["periods"]) if summary_row else 0
        met_7d = int(summary_row["met"]) if summary_row else 0
        met_pct = round((met_7d / periods_7d) * 100.0, 2) if periods_7d > 0 else None

        return {
            "site_id": site_id,
            "sla_target": float(latest_row["sla_target"]) if latest_row["sla_target"] is not None else 90.0,
            "current_rate": float(latest_row["healing_rate"]) if latest_row["healing_rate"] is not None else None,
            "sla_met": bool(latest_row["sla_met"]) if latest_row["sla_met"] is not None else None,
            "total_attempts": int(latest_row["total_attempts"]) if latest_row["total_attempts"] is not None else 0,
            "successful_heals": int(latest_row["successful_heals"]) if latest_row["successful_heals"] is not None else 0,
            "period_start": latest_row["period_start"].isoformat() if latest_row["period_start"] else None,
            "period_end": latest_row["period_end"].isoformat() if latest_row["period_end"] else None,
            "periods_last_7d": periods_7d,
            "periods_met_last_7d": met_7d,
            "met_pct_7d": met_pct,
            "trend": trend,
            "source": "site_healing_sla",
        }

    # Fallback: compute from execution_telemetry over the last 24h
    telemetry_row: Optional[Any] = None
    try:
        async with tenant_connection(pool, site_id=site_id) as conn:
            telemetry_row = await conn.fetchrow(
                """
                SELECT
                  COUNT(*)::int AS total_attempts,
                  COUNT(*) FILTER (WHERE status='success')::int AS successful_heals
                FROM execution_telemetry
                WHERE site_id = $1
                  AND created_at > NOW() - INTERVAL '24 hours'
                """,
                site_id,
            )
    except Exception as e:
        logger.warning(f"site_sla fallback: tenant_connection failed for {site_id}: {e} — retrying as admin")
        try:
            async with admin_connection(pool) as conn:
                telemetry_row = await conn.fetchrow(
                    """
                    SELECT
                      COUNT(*)::int AS total_attempts,
                      COUNT(*) FILTER (WHERE status='success')::int AS successful_heals
                    FROM execution_telemetry
                    WHERE site_id = $1
                      AND created_at > NOW() - INTERVAL '24 hours'
                    """,
                    site_id,
                )
        except Exception as e2:
            logger.error(f"site_sla fallback: admin fallback failed for {site_id}: {e2}")
            telemetry_row = None

    sla_target_default = 90.0
    if telemetry_row and (telemetry_row["total_attempts"] or 0) > 0:
        total = int(telemetry_row["total_attempts"])
        success = int(telemetry_row["successful_heals"])
        rate = round((success / total) * 100.0, 2)
        return {
            "site_id": site_id,
            "sla_target": sla_target_default,
            "current_rate": rate,
            "sla_met": rate >= sla_target_default,
            "total_attempts": total,
            "successful_heals": success,
            "period_start": None,
            "period_end": None,
            "periods_last_7d": 0,
            "periods_met_last_7d": 0,
            "met_pct_7d": None,
            "trend": [],
            "source": "execution_telemetry",
        }

    # Nothing found anywhere — explicit null state so frontend can render "no data"
    return {
        "site_id": site_id,
        "sla_target": sla_target_default,
        "current_rate": None,
        "sla_met": None,
        "total_attempts": 0,
        "successful_heals": 0,
        "period_start": None,
        "period_end": None,
        "periods_last_7d": 0,
        "periods_met_last_7d": 0,
        "met_pct_7d": None,
        "trend": [],
        "source": "none",
    }


@router.get("/{site_id}/search")
async def search_site(
    site_id: str,
    q: str = Query("", description="Search term, min 2 chars"),
    limit: int = Query(25, ge=1, le=100),
    user: dict = Depends(require_auth),
):
    """Search across incidents, devices, credentials, and workstations for a site.

    Used by the Site Detail page search bar. Each category is capped at
    ``limit`` rows (default 25, max 100). Search is case-insensitive via
    ILIKE on the relevant columns.

    Returns 400 for queries shorter than 2 characters to prevent
    accidentally dumping the entire site's data.
    """
    term = (q or "").strip()
    if len(term) < 2:
        raise HTTPException(status_code=400, detail="query must be at least 2 characters")

    # Parameterized ILIKE pattern — never f-string the user input
    pattern = f"%{term}%"

    pool = await get_pool()
    results: Dict[str, List[Dict[str, Any]]] = {
        "incidents": [],
        "devices": [],
        "credentials": [],
        "workstations": [],
    }

    async with tenant_connection(pool, site_id=site_id) as conn:
        # 1) Incidents — title, incident_type, details::text
        try:
            incident_rows = await conn.fetch(
                """
                SELECT id::text AS id, incident_type, COALESCE(details->>'title','') AS title,
                       severity, status, created_at
                FROM incidents
                WHERE site_id = $1
                  AND (
                    COALESCE(details->>'title','') ILIKE $2
                    OR incident_type ILIKE $2
                    OR details::text ILIKE $2
                  )
                ORDER BY created_at DESC
                LIMIT $3
                """,
                site_id,
                pattern,
                limit,
            )
            for r in incident_rows:
                results["incidents"].append({
                    "id": r["id"],
                    "incident_type": r["incident_type"],
                    "title": r["title"] or r["incident_type"],
                    "severity": r["severity"],
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
        except Exception as e:
            logger.warning(f"search_site: incidents query failed for {site_id}: {e}")

        # 2) Discovered devices — hostname, ip_address, mac_address
        # NOTE: incidents.title lives in details JSONB — there's no title column.
        try:
            device_rows = await conn.fetch(
                """
                SELECT id::text AS id, hostname, ip_address, mac_address, device_type
                FROM discovered_devices
                WHERE site_id = $1
                  AND (
                    COALESCE(hostname,'') ILIKE $2
                    OR COALESCE(ip_address,'') ILIKE $2
                    OR COALESCE(mac_address,'') ILIKE $2
                  )
                ORDER BY last_seen_at DESC NULLS LAST
                LIMIT $3
                """,
                site_id,
                pattern,
                limit,
            )
            for r in device_rows:
                results["devices"].append({
                    "id": r["id"],
                    "hostname": r["hostname"],
                    "ip_address": r["ip_address"],
                    "mac_address": r["mac_address"],
                    "device_type": r["device_type"],
                })
        except Exception as e:
            logger.warning(f"search_site: devices query failed for {site_id}: {e}")

        # 3) Site credentials — credential_name, credential_type
        # Never return encrypted_data — only metadata.
        try:
            cred_rows = await conn.fetch(
                """
                SELECT id::text AS id, credential_type, credential_name
                FROM site_credentials
                WHERE site_id = $1
                  AND (
                    credential_name ILIKE $2
                    OR credential_type ILIKE $2
                  )
                ORDER BY updated_at DESC
                LIMIT $3
                """,
                site_id,
                pattern,
                limit,
            )
            for r in cred_rows:
                results["credentials"].append({
                    "id": r["id"],
                    "credential_type": r["credential_type"],
                    "credential_name": r["credential_name"],
                })
        except Exception as e:
            logger.warning(f"search_site: credentials query failed for {site_id}: {e}")

        # 4) Workstations — hostname, os_name, compliance_status
        # Table has os_name (not "os"); we expose it under "os" in the response
        # to match the documented shape. Skip gracefully if table is missing.
        try:
            ws_rows = await conn.fetch(
                """
                SELECT id::text AS id, hostname,
                       COALESCE(os_name,'') AS os_name,
                       COALESCE(os_version,'') AS os_version,
                       compliance_status
                FROM workstations
                WHERE site_id = $1
                  AND (
                    hostname ILIKE $2
                    OR COALESCE(os_name,'') ILIKE $2
                    OR COALESCE(compliance_status,'') ILIKE $2
                  )
                ORDER BY last_seen DESC NULLS LAST
                LIMIT $3
                """,
                site_id,
                pattern,
                limit,
            )
            for r in ws_rows:
                os_label = r["os_name"]
                if r["os_version"]:
                    os_label = f"{os_label} {r['os_version']}".strip()
                results["workstations"].append({
                    "id": r["id"],
                    "hostname": r["hostname"],
                    "os": os_label or None,
                    "compliance_status": r["compliance_status"],
                })
        except Exception as e:
            # workstations table may not exist in all deployments — skip category
            logger.warning(f"search_site: workstations query skipped for {site_id}: {e}")

    total = sum(len(v) for v in results.values())
    return {
        "site_id": site_id,
        "query": term,
        "limit": limit,
        "results": results,
        "total": total,
    }
