"""Sites management endpoints.

Provides PUT/DELETE endpoints for site management operations
that modify site data directly.
"""

import json
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from enum import Enum

from .fleet import get_pool


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
async def update_site(site_id: str, update: SiteUpdate):
    """Update site information."""
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
# ORDER ENDPOINTS
# =============================================================================

@router.post("/{site_id}/appliances/{appliance_id}/orders")
async def create_appliance_order(
    site_id: str,
    appliance_id: str,
    order: OrderCreate,
):
    """Create an order for a specific appliance."""
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
    """Get pending orders for an appliance."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT order_id, order_type, parameters, priority, 
                   created_at, expires_at
            FROM admin_orders
            WHERE appliance_id = $1 AND site_id = $2 
            AND status = 'pending'
            AND expires_at > NOW()
            ORDER BY priority DESC, created_at ASC
        """, appliance_id, site_id)

        return {
            "site_id": site_id,
            "appliance_id": appliance_id,
            "orders": [
                {
                    "order_id": row["order_id"],
                    "order_type": row["order_type"],
                    "parameters": parse_parameters(row["parameters"]),
                    "priority": row["priority"],
                    "created_at": row["created_at"].isoformat(),
                    "expires_at": row["expires_at"].isoformat(),
                }
                for row in rows
            ],
            "count": len(rows)
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
    if age < timedelta(minutes=5):
        return 'online'
    elif age < timedelta(hours=1):
        return 'stale'
    else:
        return 'offline'


@router.get("")
async def list_sites(status: Optional[str] = None):
    """List all sites with aggregated appliance data."""
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
async def get_site(site_id: str):
    """Get details for a specific site."""
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
        
        # Human-readable name
        clinic_name = site_id.replace('-', ' ').title()
        
        return {
            'site_id': site_id,
            'clinic_name': clinic_name,
            'contact_name': None,
            'contact_email': None,
            'contact_phone': None,
            'address': None,
            'provider_count': None,
            'ehr_system': None,
            'notes': None,
            'blockers': [],
            'tracking_number': None,
            'tracking_carrier': None,
            'tier': 'standard',
            'status': overall_status,
            'live_status': live_status,
            'onboarding_stage': 'active',
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
async def add_credential(site_id: str, cred: CredentialCreate):
    """Add a credential for a site. Appliances will pull this on next check-in."""
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
            raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{site_id}/credentials/{credential_id}")
async def delete_credential(site_id: str, credential_id: str):
    """Delete a credential. Appliances will stop receiving it on next check-in."""
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
async def get_site_appliances(site_id: str):
    """Get all appliances for a site."""
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
async def delete_appliance(site_id: str, appliance_id: str):
    """Delete an appliance from a site."""
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
async def clear_stale_appliances(site_id: str, request: ClearStaleRequest):
    """Clear stale appliances that haven't checked in recently.

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
            # Check if order exists but is already acknowledged
            existing = await conn.fetchrow("""
                SELECT order_id, status FROM admin_orders WHERE order_id = $1
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
            "order_type": result['order_type'],
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
        # Update the order status
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
            # Check if order exists
            existing = await conn.fetchrow("""
                SELECT order_id, status FROM admin_orders WHERE order_id = $1
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
            "order_type": result['order_type'],
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


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase with colons (84:3A:5B:91:B6:61)."""
    if not mac:
        return ""
    # Remove all separators, convert to uppercase
    clean = mac.upper().replace(':', '').replace('-', '').replace('.', '')
    # Re-insert colons every 2 chars
    return ':'.join(clean[i:i+2] for i in range(0, len(clean), 2))


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
        # === STEP 1: Find existing appliances with same MAC or hostname ===
        existing = await conn.fetch("""
            SELECT appliance_id, hostname, mac_address, first_checkin
            FROM site_appliances
            WHERE site_id = $1
            AND (
                UPPER(REPLACE(REPLACE(mac_address, ':', ''), '-', '')) = $2
                OR LOWER(hostname) = $3
            )
            ORDER BY last_checkin DESC NULLS LAST
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

        # === STEP 4: Get pending orders for this appliance ===
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

        # === STEP 5: Get windows targets (credential-pull) ===
        windows_targets = []
        try:
            creds = await conn.fetch("""
                SELECT credential_name, encrypted_data
                FROM site_credentials
                WHERE site_id = $1
                AND credential_type IN ('winrm', 'domain_admin', 'local_admin')
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

    return {
        "status": "ok",
        "appliance_id": canonical_id,
        "server_time": now.isoformat(),
        "merged_duplicates": len(merge_from_ids),
        "pending_orders": pending_orders,
        "windows_targets": windows_targets,
        "enabled_runbooks": enabled_runbooks,
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
async def get_site_workstations(site_id: str):
    """Get all workstations for a site with compliance summary.

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


@router.get("/{site_id}/workstations/{workstation_id}")
async def get_workstation(site_id: str, workstation_id: str):
    """Get details for a specific workstation."""
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
async def trigger_workstation_scan(site_id: str):
    """Trigger a workstation compliance scan for a site.

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
