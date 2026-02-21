"""Sites management endpoints.

Provides PUT/DELETE endpoints for site management operations
that modify site data directly.
"""

import json
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from enum import Enum

from .fleet import get_pool
from .auth import require_auth, require_operator
from .websocket_manager import broadcast_event

logger = logging.getLogger(__name__)


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
        except:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

        # Insert order - cast json string to jsonb
        await conn.execute("""
            INSERT INTO admin_orders (
                order_id, appliance_id, site_id, order_type,
                parameters, priority, status, created_at, expires_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        """,
            order_id,
            appliance_id,
            site_id,
            order.order_type.value,
            json.dumps(order.parameters),
            order.priority,
            'pending',
            now,
            expires_at
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

    async with pool.acquire() as conn:
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
        except Exception:
            pass  # Non-critical: expiration is best-effort

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

def calculate_live_status(last_checkin):
    """Calculate live status based on last checkin time."""
    if last_checkin is None:
        return 'pending'
    now = datetime.now(timezone.utc)
    age = now - last_checkin
    if age < timedelta(minutes=15):
        return 'online'
    elif age < timedelta(hours=1):
        return 'stale'
    else:
        return 'offline'


@router.get("")
async def list_sites(status: Optional[str] = None, user: dict = Depends(require_auth)):
    """List all sites with aggregated appliance data. Requires authentication."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # Get unique sites from site_appliances with aggregated info
        rows = await conn.fetch("""
            SELECT 
                site_id,
                COUNT(*) as appliance_count,
                MAX(last_checkin) as last_checkin,
                MIN(first_checkin) as created_at,
                MAX(last_checkin) as updated_at,
                array_agg(DISTINCT COALESCE(status, 'pending')) as statuses
            FROM site_appliances
            GROUP BY site_id
            ORDER BY site_id
        """)
        
        sites = []
        for row in rows:
            last_checkin = row['last_checkin']
            live_status = calculate_live_status(last_checkin)
            
            # Filter by status if provided
            if status and live_status != status:
                continue
            
            # Determine overall status from appliance statuses
            statuses = row['statuses'] or []
            if 'online' in statuses:
                overall_status = 'online'
            elif 'stale' in statuses:
                overall_status = 'stale'
            else:
                overall_status = 'offline'
            
            # Create human-readable name from site_id
            clinic_name = row['site_id'].replace('-', ' ').title()
            
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': clinic_name,
                'contact_name': None,
                'contact_email': None,
                'tier': 'standard',
                'status': overall_status,
                'live_status': live_status,
                'onboarding_stage': 'active',
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                'last_checkin': last_checkin.isoformat() if last_checkin else None,
                'appliance_count': row['appliance_count'],
            })
        
        return {'sites': sites, 'count': len(sites)}


@router.get("/{site_id}")
async def get_site(site_id: str, user: dict = Depends(require_auth)):
    """Get details for a specific site. Requires authentication."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # Get appliances for this site
        appliance_rows = await conn.fetch("""
            SELECT 
                appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds
            FROM site_appliances
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)
        
        if not appliance_rows:
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
            
            live_status = calculate_live_status(last_checkin)
            
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
        
        # Determine overall site live status
        live_status = calculate_live_status(latest_checkin)

        # Fetch credentials (without exposing passwords)
        cred_rows = await conn.fetch("""
            SELECT id, credential_type, credential_name, encrypted_data, created_at
            FROM site_credentials
            WHERE site_id = $1
            ORDER BY created_at DESC
        """, site_id)

        credentials = []
        for cred in cred_rows:
            try:
                cred_data = json.loads(cred['encrypted_data']) if cred['encrypted_data'] else {}
                credentials.append({
                    'id': str(cred['id']),
                    'credential_type': cred['credential_type'],
                    'credential_name': cred['credential_name'],
                    'host': cred_data.get('host', ''),
                    'username': cred_data.get('username', ''),
                    'domain': cred_data.get('domain', ''),
                    'created_at': cred['created_at'].isoformat() if cred['created_at'] else None,
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

        # Get site metadata from sites table
        site_row = await conn.fetchrow("""
            SELECT clinic_name, contact_name, contact_email, contact_phone,
                   address, notes, tier, onboarding_stage, healing_tier
            FROM sites
            WHERE site_id = $1
        """, site_id)

        # Human-readable name (from sites table or derived)
        clinic_name = site_row['clinic_name'] if site_row and site_row['clinic_name'] else site_id.replace('-', ' ').title()

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
            'appliances': appliances,
            'credentials': credentials,
        }


# =============================================================================
# CREDENTIAL MANAGEMENT
# =============================================================================

class CredentialCreate(BaseModel):
    credential_type: str  # domain_admin, local_admin, winrm, service_account
    credential_name: str  # Human-readable name like "North Valley DC"
    host: str             # Target hostname/IP
    username: str
    password: str
    domain: Optional[str] = None
    use_ssl: Optional[bool] = False


@router.post("/{site_id}/credentials")
async def add_credential(site_id: str, cred: CredentialCreate, user: dict = Depends(require_operator)):
    """Add a credential for a site. Appliances will pull this on next check-in. Requires operator or admin access."""
    pool = await get_pool()

    # Build credential data as JSON
    cred_data = json.dumps({
        'host': cred.host,
        'username': cred.username,
        'password': cred.password,
        'domain': cred.domain or '',
        'use_ssl': cred.use_ssl or False,
    })

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow("""
                INSERT INTO site_credentials (site_id, credential_type, credential_name, encrypted_data)
                VALUES ($1, $2, $3, $4)
                RETURNING id, created_at
            """, site_id, cred.credential_type, cred.credential_name, cred_data.encode())

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

    async with pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM site_credentials
            WHERE site_id = $1 AND id = $2
        """, site_id, credential_id)

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Credential not found")

        return {'message': 'Credential deleted. Appliances will stop receiving it on next check-in.'}


@router.get("/{site_id}/appliances")
async def get_site_appliances(site_id: str, user: dict = Depends(require_auth)):
    """Get all appliances for a site. Requires authentication."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, first_checkin,
                last_checkin, uptime_seconds
            FROM site_appliances
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)
        
        appliances = []
        for row in rows:
            last_checkin = row['last_checkin']
            live_status = calculate_live_status(last_checkin)
            
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
            })
        
        return {
            'site_id': site_id,
            'appliances': appliances,
            'count': len(appliances)
        }


@router.delete("/{site_id}/appliances/{appliance_id}")
async def delete_appliance(site_id: str, appliance_id: str, user: dict = Depends(require_operator)):
    """Delete an appliance from a site. Requires operator or admin access."""
    pool = await get_pool()

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
        # Get all appliances for this site
        appliances = await conn.fetch("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1
        """, site_id)

        if not appliances:
            raise HTTPException(status_code=404, detail=f"No appliances found for site {site_id}")

        for row in appliances:
            order_id = generate_order_id()
            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            """,
                order_id,
                row['appliance_id'],
                site_id,
                order.order_type.value,
                json.dumps(order.parameters),
                0,
                'pending',
                now,
                expires_at
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
async def acknowledge_order(order_id: str):
    """Acknowledge that an order has been received and is being executed.

    Called by the appliance agent when it picks up a pending order.
    Updates status from 'pending' to 'acknowledged'.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
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
async def complete_order(order_id: str, request: OrderCompleteRequest):
    """Mark an order as completed (success or failure).

    Called by the appliance agent after executing an order.
    Updates status to 'completed' or 'failed' based on success flag.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    new_status = 'completed' if request.success else 'failed'
    result_data = request.result or {}
    if request.error_message:
        result_data['error_message'] = request.error_message

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
# APPLIANCE CHECK-IN WITH SMART DEDUPLICATION
# =============================================================================

# Separate router for appliance check-in endpoint
appliances_router = APIRouter(prefix="/api/appliances", tags=["appliances"])


class ApplianceCheckin(BaseModel):
    """Check-in request from appliance agent."""
    site_id: str
    hostname: str
    mac_address: str
    ip_addresses: list = []
    uptime_seconds: Optional[int] = None
    agent_version: Optional[str] = None
    nixos_version: Optional[str] = None
    has_local_credentials: bool = False  # If True, appliance has fresh local creds
    agent_public_key: Optional[str] = None  # Ed25519 public key hex for evidence signing


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
async def report_discovered_domain(report: DiscoveredDomainReport):
    """
    Receive domain discovery report from appliance.
    
    Triggers:
    1. Store discovered domain info in site record
    2. Notify partner that credentials are needed
    3. Update dashboard to show "awaiting credentials" state
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with pool.acquire() as conn:
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
async def report_enumeration_results(report: EnumerationResultsReport):
    """
    Receive AD enumeration results from appliance.
    
    Stores enumeration results and updates site with discovered targets.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with pool.acquire() as conn:
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
    
    async with pool.acquire() as conn:
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
            ) VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT (site_id, credential_type, credential_name) 
            DO UPDATE SET encrypted_data = $4::jsonb, updated_at = $5
        """,
            site_id,
            creds.credential_type,
            f"domain_{creds.domain_name}",
            json.dumps(credential_data),
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
    
    async with pool.acquire() as conn:
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
                except:
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
    
    async with pool.acquire() as conn:
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
        
        # Return credentials (should be decrypted in production)
        cred_data = cred['encrypted_data']
        if isinstance(cred_data, str):
            cred_data = json.loads(cred_data)
        
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
async def report_agent_deployments(report: AgentDeploymentReport):
    """
    Receive Go agent deployment results from appliance.
    
    Stores deployment status in agent_deployments table.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    
    async with pool.acquire() as conn:
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
async def appliance_checkin(checkin: ApplianceCheckin):
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
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # Normalize inputs
    mac_normalized = normalize_mac(checkin.mac_address)
    hostname_lower = checkin.hostname.lower().strip()

    # Generate appliance_id from site_id + normalized MAC
    mac_clean = mac_normalized.replace(':', '')
    appliance_id = f"{checkin.site_id}-{mac_normalized}"

    async with pool.acquire() as conn:
      async with conn.transaction():
        # === STEP 1: Find existing appliances with same MAC or hostname ===
        # Use FOR UPDATE to prevent concurrent check-ins from racing
        existing = await conn.fetch("""
            SELECT appliance_id, hostname, mac_address, first_checkin
            FROM site_appliances
            WHERE site_id = $1
            AND (
                UPPER(REPLACE(REPLACE(mac_address, ':', ''), '-', '')) = $2
                OR LOWER(hostname) = $3
            )
            ORDER BY last_checkin DESC NULLS LAST
            FOR UPDATE
        """, checkin.site_id, mac_clean.upper(), hostname_lower)

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

        # === STEP 3: Upsert the canonical appliance entry ===
        await conn.execute("""
            INSERT INTO site_appliances (
                site_id, appliance_id, hostname, mac_address, ip_addresses,
                agent_version, nixos_version, status, uptime_seconds,
                first_checkin, last_checkin
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, 'online', $8, $9, $10)
            ON CONFLICT (appliance_id) DO UPDATE SET
                hostname = EXCLUDED.hostname,
                mac_address = EXCLUDED.mac_address,
                ip_addresses = EXCLUDED.ip_addresses,
                agent_version = EXCLUDED.agent_version,
                nixos_version = EXCLUDED.nixos_version,
                status = 'online',
                uptime_seconds = EXCLUDED.uptime_seconds,
                last_checkin = EXCLUDED.last_checkin
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
            now
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

        # === STEP 4: Get pending orders for this appliance ===
        # Check admin_orders table (fleet management orders)
        order_rows = await conn.fetch("""
            SELECT order_id, order_type, parameters, priority, created_at, expires_at
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
            }
            for row in order_rows
        ]

        # Also check orders table (healing orders from incidents)
        # These are created by the MCP server when incidents are reported
        try:
            healing_order_rows = await conn.fetch("""
                SELECT o.order_id, o.runbook_id, o.parameters, o.issued_at, o.expires_at,
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
                })
        except Exception as e:
            import logging
            logging.warning(f"Failed to fetch healing orders: {e}")

        # === STEP 5: Get windows targets (conditional credential delivery) ===
        # Only send credentials if appliance doesn't have fresh local copies
        windows_targets = []
        if not checkin.has_local_credentials:
            try:
                creds = await conn.fetch("""
                    SELECT credential_name, encrypted_data
                    FROM site_credentials
                    WHERE site_id = $1
                    AND credential_type IN ('winrm', 'domain_admin', 'domain_member', 'service_account', 'local_admin')
                    ORDER BY created_at DESC
                """, checkin.site_id)

                for cred in creds:
                    if cred['encrypted_data']:
                        try:
                            cred_data = json.loads(cred['encrypted_data'])
                            # Transform credentials to expected format
                            hostname = cred_data.get('host') or cred_data.get('target_host')
                            username = cred_data.get('username', '')
                            password = cred_data.get('password', '')
                            domain = cred_data.get('domain', '')
                            use_ssl = cred_data.get('use_ssl', False)

                            # Combine domain\username for NTLM auth
                            full_username = f"{domain}\\{username}" if domain else username

                            if hostname:
                                windows_targets.append({
                                    "hostname": hostname,
                                    "username": full_username,
                                    "password": password,
                                    "use_ssl": use_ssl,
                                })
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass  # Don't fail checkin if credentials lookup fails

        # === STEP 5b: Get linux targets (SSH credentials) ===
        linux_targets = []
        if not checkin.has_local_credentials:
            try:
                ssh_creds = await conn.fetch("""
                    SELECT credential_name, encrypted_data
                    FROM site_credentials
                    WHERE site_id = $1
                    AND credential_type IN ('ssh_password', 'ssh_key')
                    ORDER BY created_at DESC
                """, checkin.site_id)

                for cred in ssh_creds:
                    if cred['encrypted_data']:
                        try:
                            cred_data = json.loads(cred['encrypted_data'])
                            hostname = cred_data.get('host') or cred_data.get('target_host')
                            target_entry = {
                                "hostname": hostname,
                                "port": cred_data.get('port', 22),
                                "username": cred_data.get('username', 'root'),
                            }
                            if cred_data.get('password'):
                                target_entry["password"] = cred_data['password']
                            if cred_data.get('private_key'):
                                target_entry["private_key"] = cred_data['private_key']
                            if cred_data.get('distro'):
                                target_entry["distro"] = cred_data['distro']
                            if hostname:
                                linux_targets.append(target_entry)
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass  # Don't fail checkin if SSH credentials lookup fails

        # === STEP 6: Get enabled runbooks (runbook config pull) ===
        enabled_runbooks = []
        try:
            # Get all runbooks with effective enabled status
            # Hierarchy: appliance override > site config > default (enabled)
            runbook_rows = await conn.fetch("""
                SELECT
                    r.runbook_id,
                    COALESCE(
                        arc.enabled,           -- Appliance override takes priority
                        src.enabled,           -- Site config second
                        true                   -- Default is enabled
                    ) as enabled
                FROM runbooks r
                LEFT JOIN site_runbook_config src ON src.runbook_id = r.runbook_id AND src.site_id = $1
                LEFT JOIN appliance_runbook_config arc ON arc.runbook_id = r.runbook_id AND arc.appliance_id = $2
                ORDER BY r.runbook_id
            """, checkin.site_id, canonical_id)

            enabled_runbooks = [row['runbook_id'] for row in runbook_rows if row['enabled']]
        except Exception:
            pass  # Don't fail checkin if runbook lookup fails (table may not exist yet)

        # === STEP 7: Check for enumeration/scan triggers (zero-friction deployment) ===
        trigger_enumeration = False
        trigger_immediate_scan = False
        try:
            appliance = await conn.fetchrow("""
                SELECT trigger_enumeration, trigger_immediate_scan
                FROM site_appliances
                WHERE appliance_id = $1
            """, canonical_id)
            
            if appliance:
                trigger_enumeration = appliance.get('trigger_enumeration', False)
                trigger_immediate_scan = appliance.get('trigger_immediate_scan', False)
                
                # Clear trigger flags after sending
                if trigger_enumeration or trigger_immediate_scan:
                    await conn.execute("""
                        UPDATE site_appliances 
                        SET trigger_enumeration = false, trigger_immediate_scan = false
                        WHERE appliance_id = $1
                    """, canonical_id)
        except Exception:
            pass  # Don't fail checkin if trigger lookup fails

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
    except Exception:
        pass  # Don't fail checkin if broadcast fails

    return {
        "status": "ok",
        "appliance_id": canonical_id,
        "server_time": now.isoformat(),
        "merged_duplicates": len(merge_from_ids),
        "pending_orders": pending_orders,
        "windows_targets": windows_targets,
        "linux_targets": linux_targets,
        "enabled_runbooks": enabled_runbooks,
        "trigger_enumeration": trigger_enumeration,
        "trigger_immediate_scan": trigger_immediate_scan,
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

    async with pool.acquire() as conn:
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
                except:
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
                        except:
                            details = {}

                    hipaa = check['hipaa_controls']
                    if isinstance(hipaa, str):
                        try:
                            hipaa = json.loads(hipaa)
                        except:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
                    except:
                        details = {}

                hipaa = check['hipaa_controls']
                if isinstance(hipaa, str):
                    try:
                        hipaa = json.loads(hipaa)
                    except:
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

    async with pool.acquire() as conn:
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

        await conn.execute("""
            INSERT INTO admin_orders (
                order_id, appliance_id, site_id, order_type,
                parameters, priority, status, created_at, expires_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        """,
            order_id,
            appliance['appliance_id'],
            site_id,
            'run_command',
            json.dumps({'command': 'workstation_scan', 'force': True}),
            10,  # High priority
            'pending',
            now,
            expires_at
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

    async with pool.acquire() as conn:
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
                except:
                    agents_by_tier = {}

            agents_by_version = summary_row['agents_by_version']
            if isinstance(agents_by_version, str):
                try:
                    agents_by_version = json.loads(agents_by_version)
                except:
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
                        except:
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

    async with pool.acquire() as conn:
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
            except:
                agents_by_tier = {}

        agents_by_version = summary_row['agents_by_version']
        if isinstance(agents_by_version, str):
            try:
                agents_by_version = json.loads(agents_by_version)
            except:
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

    async with pool.acquire() as conn:
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
                    except:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
