"""Sites management endpoints.

Provides PUT/DELETE endpoints for site management operations
that modify site data directly.
"""

import json
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
from .shared import require_appliance_bearer
from .tenant_middleware import tenant_connection, admin_connection
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
async def update_site(site_id: str, update: SiteUpdate, user: dict = Depends(require_operator)):
    """Update site information. Requires operator or admin access."""
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
        result = await conn.fetchrow(query, *values)
        if not result:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

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
async def update_healing_tier(site_id: str, update: HealingTierUpdate, user: dict = Depends(require_operator)):
    """Update the healing tier for a site. Requires operator or admin access.

    Healing tiers control which L1 rules are active:
    - standard: 4 core rules (firewall, defender, bitlocker, ntp)
    - full_coverage: All 21 L1 rules for comprehensive auto-healing
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.fetchrow("""
            UPDATE sites
            SET healing_tier = $1, updated_at = $2
            WHERE site_id = $3
            RETURNING site_id, clinic_name, healing_tier
        """, update.healing_tier.value, datetime.now(timezone.utc), site_id)

        if not result:
            raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    logger.info(f"Updated healing tier for {site_id} to {update.healing_tier.value}")

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
        # Verify appliance exists
        appliance = await conn.fetchrow("""
            SELECT appliance_id, site_id
            FROM site_appliances
            WHERE appliance_id = $1 AND site_id = $2
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
async def get_pending_orders(site_id: str, appliance_id: str):
    """Get pending orders for an appliance (both admin and healing orders)."""
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
                JOIN appliances a ON o.appliance_id = a.id
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
        where_clauses = ["COALESCE(s.status, 'pending') != 'inactive'"]
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
        # Get appliances for this site
        appliance_rows = await conn.fetch("""
            SELECT
                appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds,
                COALESCE(auth_failure_count, 0) as auth_failure_count
            FROM site_appliances
            WHERE site_id = $1
            ORDER BY hostname
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
                'mac_address': row['mac_address'],
                'ip_addresses': parse_ip_addresses(row['ip_addresses']),
                'agent_version': row['agent_version'],
                'nixos_version': row['nixos_version'],
                'status': row['status'] or 'pending',
                'live_status': live_status,
                'first_checkin': first_checkin.isoformat() if first_checkin else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'uptime_seconds': row['uptime_seconds'],
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

            # Upsert into discovered_devices for inventory visibility
            # Use appliances.id (UUID) not site_appliances.appliance_id (compound string)
            await conn.execute("""
                INSERT INTO discovered_devices (
                    appliance_id, site_id, local_device_id, hostname, ip_address,
                    device_type, os_name, discovery_source, compliance_status,
                    first_seen_at, last_seen_at
                )
                SELECT
                    a.id, $1, $2, $3, $4,
                    $5, $6, 'manual', 'unknown',
                    NOW(), NOW()
                FROM appliances a
                WHERE a.site_id = $1
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

            # Register in discovered_devices with device_type = 'network'
            await conn.execute("""
                INSERT INTO discovered_devices (
                    appliance_id, site_id, local_device_id, hostname, ip_address,
                    device_type, os_name, discovery_source, compliance_status,
                    first_seen_at, last_seen_at
                )
                SELECT
                    a.id, $1, $2, $3, $4,
                    'network', $5, 'manual', 'unknown',
                    NOW(), NOW()
                FROM appliances a
                WHERE a.site_id = $1
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
async def get_site_appliances(site_id: str, user: dict = Depends(require_auth)):
    """Get all appliances for a site. Requires authentication."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        rows = await conn.fetch("""
            SELECT
                appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds,
                COALESCE(l2_mode, 'auto') as l2_mode,
                offline_since,
                COALESCE(auth_failure_count, 0) as auth_failure_count,
                daemon_health
            FROM site_appliances
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)

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

            appliances.append({
                'appliance_id': row['appliance_id'],
                'hostname': row['hostname'],
                'mac_address': row['mac_address'],
                'ip_addresses': parse_ip_addresses(row['ip_addresses']),
                'agent_version': row['agent_version'],
                'nixos_version': row['nixos_version'],
                'status': row['status'] or 'pending',
                'live_status': live_status,
                'first_checkin': row['first_checkin'].isoformat() if row['first_checkin'] else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'uptime_seconds': row['uptime_seconds'],
                'l2_mode': row['l2_mode'],
                'offline_since': row['offline_since'].isoformat() if row['offline_since'] else None,
                'mesh_peer_count': mesh_peer_count,
                'mesh_ring_size': mesh_ring_size,
                'mesh_peer_macs': mesh_peer_macs,
            })
        
        return {
            'site_id': site_id,
            'appliances': appliances,
            'count': len(appliances)
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
                   dd.last_seen
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
                'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
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
        result = await conn.execute("""
            DELETE FROM site_appliances
            WHERE appliance_id = $1 AND site_id = $2
        """, appliance_id, site_id)

        if result == "DELETE 0":
            raise HTTPException(
                status_code=404,
                detail=f"Appliance {appliance_id} not found in site {site_id}"
            )

        return {
            'status': 'deleted',
            'appliance_id': appliance_id,
            'site_id': site_id
        }


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

        # Update the site_id
        await conn.execute(
            "UPDATE site_appliances SET site_id = $1 WHERE appliance_id = $2",
            body.target_site_id, appliance_id,
        )

        # Also update the appliances table if it has a site_id column
        await conn.execute(
            "UPDATE appliances SET site_id = $1 WHERE appliance_id = $2",
            body.target_site_id, appliance_id,
        )

        # Update appliance_provisioning by MAC address
        mac_row = await conn.fetchrow(
            "SELECT mac_address FROM appliances WHERE appliance_id = $1",
            appliance_id,
        )
        if mac_row and mac_row["mac_address"]:
            await conn.execute(
                """UPDATE appliance_provisioning
                   SET site_id = $1,
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
                FROM appliances a
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
            async with admin_connection(pool) as conn:
                await record_fleet_order_completion(conn, fleet_order_id, appliance_id, new_status)
                return {
                    "status": new_status,
                    "order_id": order_id,
                    "order_type": "fleet",
                    "completed_at": now.isoformat()
                }

    async with admin_connection(pool) as conn:
        # Try admin_orders first
        result = await conn.fetchrow("""
            UPDATE admin_orders
            SET status = $1,
                completed_at = $2,
                result = $3::jsonb
            WHERE order_id = $4
            AND status IN ('pending', 'acknowledged')
            RETURNING order_id, appliance_id, site_id, order_type, acknowledged_at
        """, new_status, now, json.dumps(result_data), order_id)

        if not result:
            # Try healing orders table (orders created by L1/L2/L3 engine)
            result = await conn.fetchrow("""
                UPDATE orders o
                SET status = $1,
                    completed_at = $2,
                    result = $3::jsonb
                FROM appliances a
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
async def get_order(order_id: str):
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
                INSERT INTO audit_log (action, actor, target_type, target_id, details)
                VALUES ($1, $2, 'site', $3, $4::jsonb)
            """,
                f"healing_{action}",
                user.get("username") or user.get("email"),
                site_id,
                json.dumps({"order_id": str(row["id"]), "site_name": site["clinic_name"]}),
            )
        except Exception as e:
            logger.warning(f"Audit log for healing toggle failed: {e}")

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
    ip_addresses: list = Field(default=[], max_length=100)
    uptime_seconds: Optional[int] = None
    agent_version: Optional[str] = None
    nixos_version: Optional[str] = None
    has_local_credentials: bool = False  # If True, appliance has fresh local creds
    agent_public_key: Optional[str] = None  # Ed25519 public key hex for evidence signing
    connected_agents: Optional[list[ConnectedAgentInfo]] = None  # Go agents on this appliance
    discovery_results: Optional[Dict[str, Any]] = None  # App protection profile discovery results
    encryption_public_key: str = ""  # X25519 public key hex for credential envelope encryption
    deploy_results: Optional[list[Dict[str, Any]]] = None  # Results from previous deploy attempts
    wg_connected: bool = False  # Whether WireGuard tunnel is active
    wg_ip: Optional[str] = None  # WireGuard VPN IP (10.100.0.x)
    daemon_health: Optional[Dict[str, Any]] = None  # Go runtime stats (goroutines, heap, GC)
    bundle_hashes: Optional[List[Dict[str, str]]] = None  # Recent evidence bundle hashes for peer witnessing
    witness_attestations: Optional[List[Dict[str, str]]] = None  # Counter-signatures of sibling bundle hashes


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
        
        # Trigger immediate enumeration AND scan via next checkin
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
    # Authenticate appliance via Bearer token
    await require_appliance_auth(request)

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # Normalize inputs
    mac_normalized = normalize_mac(checkin.mac_address)
    hostname_lower = checkin.hostname.lower().strip()

    # Generate appliance_id from site_id + normalized MAC
    mac_clean = mac_normalized.replace(':', '')
    appliance_id = f"{checkin.site_id}-{mac_normalized}"

    async with tenant_connection(pool, site_id=checkin.site_id) as conn:
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
        canonical_id = appliance_id
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

        # === STEP 3: Upsert the canonical appliance entry ===
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
                offline_notified = false
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
            json.dumps(checkin.daemon_health) if checkin.daemon_health else None,
        )

        # === STEP 3.5: Also update appliances table for Fleet Updates ===
        try:
            await conn.execute("""
                UPDATE appliances SET
                    last_checkin = $2,
                    agent_version = $3,
                    nixos_version = $4,
                    ip_address = $5::inet,
                    status = 'active',
                    updated_at = $2
                WHERE site_id = $1
            """,
                checkin.site_id,
                now,
                checkin.agent_version,
                checkin.nixos_version,
                checkin.ip_addresses[0] if checkin.ip_addresses else None
            )
        except Exception as e:
            # Don't fail checkin if fleet update fails
            import logging
            logging.warning(f"Failed to update appliances table: {e}")

        # === STEP 3.6: Register/update agent signing key ===
        if checkin.agent_public_key and len(checkin.agent_public_key) == 64:
            try:
                existing_key = await conn.fetchval(
                    "SELECT agent_public_key FROM sites WHERE site_id = $1",
                    checkin.site_id
                )
                if existing_key != checkin.agent_public_key:
                    await conn.execute(
                        "UPDATE sites SET agent_public_key = $1 WHERE site_id = $2",
                        checkin.agent_public_key, checkin.site_id
                    )
                    if existing_key:
                        import logging
                        logging.warning(
                            f"Agent signing key ROTATED for site={checkin.site_id} "
                            f"old={existing_key[:12]}... new={checkin.agent_public_key[:12]}..."
                        )
                    else:
                        import logging
                        logging.info(
                            f"Agent signing key registered for site={checkin.site_id} "
                            f"key={checkin.agent_public_key[:12]}..."
                        )
            except Exception as e:
                import logging
                logging.warning(f"Failed to register agent public key: {e}")

        # === STEP 3.6b: Update WireGuard VPN status ===
        if checkin.wg_connected and checkin.wg_ip:
            try:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE sites SET wg_connected_at = NOW(), wg_ip = $1 WHERE site_id = $2",
                        checkin.wg_ip, checkin.site_id
                    )
            except Exception as e:
                import logging
                logging.warning(f"Checkin {checkin.site_id}: WireGuard status update failed: {e}")

        # === STEP 3.7: Sync connected Go agents to go_agents table ===
        # Use a savepoint so failures here don't poison the outer transaction
        if checkin.connected_agents:
            try:
                async with conn.transaction():
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
                        last_heartbeat_dt = _parse_ts(agent.last_heartbeat)
                        # Go zero time (0001-01-01) means "never heartbeated" — treat as None
                        if last_heartbeat_dt and last_heartbeat_dt.year < 2000:
                            last_heartbeat_dt = None
                        if connected_at_dt and connected_at_dt.year < 2000:
                            connected_at_dt = None
                        # Delete any existing row with same (site_id, hostname) but different agent_id
                        # This handles agent reinstalls that generate a new agent_id
                        await conn.execute("""
                            DELETE FROM go_agents
                            WHERE site_id = $1 AND hostname = $2 AND agent_id != $3
                        """, checkin.site_id, agent.hostname, agent.agent_id)
                        compliance_pct = round(
                            (agent.checks_passed / agent.checks_total * 100)
                            if agent.checks_total > 0 else 0.0, 2
                        )
                        await conn.execute("""
                            INSERT INTO go_agents (
                                agent_id, site_id, hostname, agent_version,
                                ip_address, os_name, os_version,
                                capability_tier, status, checks_passed, checks_total,
                                compliance_percentage, connected_at, last_heartbeat,
                                updated_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, 'connected', $8, $9, $10, $11, $12, NOW())
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
                    await conn.execute("""
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
                    for agent in checkin.connected_agents:
                        if not agent.hostname:
                            continue
                        # Upsert workstation from Go agent data
                        await conn.execute("""
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
                        """, checkin.site_id, agent.hostname,
                            agent.checks_passed, agent.checks_total)

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

                    # Expire stale: mark offline if no activity in 7 days
                    await conn.execute("""
                        UPDATE workstations SET online = false
                        WHERE site_id = $1 AND online = true
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
            logger.warning(f"Checkin {checkin.site_id}: mesh peer lookup failed: {e}")

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
            logger.warning(f"Witness exchange during checkin: {e}")

        # === STEP 4: Get pending orders for this appliance ===
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

        # Also check orders table (healing orders from incidents)
        # These are created by the MCP server when incidents are reported
        try:
            healing_order_rows = await conn.fetch("""
                SELECT o.order_id, o.runbook_id, o.parameters, o.issued_at, o.expires_at,
                       o.nonce, o.signature, o.signed_payload,
                       i.id as incident_id
                FROM orders o
                JOIN appliances a ON o.appliance_id = a.id
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
            import logging
            logging.warning(f"Failed to fetch healing orders: {e}")

        # === STEP 4.5: Get fleet-wide orders ===
        try:
            fleet_orders = await get_fleet_orders_for_appliance(
                conn, canonical_id, checkin.agent_version
            )
            pending_orders.extend(fleet_orders)
        except Exception as e:
            import logging
            logging.warning(f"Failed to fetch fleet orders: {e}")

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
            """, appliance_db_id)
            owned_ips = {r['ip_address'] for r in own_rows}
        except Exception:
            pass  # owner_appliance_id column may not exist yet
        if not owned_ips:
            # Fallback: all devices this appliance discovered
            try:
                disc_rows = await conn.fetch("""
                    SELECT DISTINCT ip_address FROM discovered_devices
                    WHERE appliance_id = $1 AND ip_address IS NOT NULL
                """, appliance_db_id)
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
            """, appliance_db_id)
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

    return {
        "status": "ok",
        "appliance_id": canonical_id,
        "server_time": now.isoformat(),
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
async def send_email_alert(request: EmailAlertRequest):
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
        # Get summary (if exists)
        summary_row = await conn.fetchrow("""
            SELECT site_id, total_agents, active_agents, offline_agents,
                   error_agents, pending_agents, overall_compliance_rate,
                   agents_by_tier, agents_by_version, rmm_detected_count,
                   last_event
            FROM site_go_agent_summaries
            WHERE site_id = $1
        """, site_id)

        summary = None
        if summary_row:
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

            summary = {
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

        # Get agents with their latest checks
        agent_rows = await conn.fetch("""
            SELECT agent_id, hostname, ip_address, agent_version,
                   capability_tier, status, checks_passed, checks_total,
                   compliance_percentage, rmm_detected, rmm_disabled,
                   offline_queue_size, connected_at, last_heartbeat
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
