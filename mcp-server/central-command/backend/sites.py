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


router = APIRouter(prefix="/api/sites", tags=["sites"])


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
                'ip_addresses': row['ip_addresses'] or [],
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
            'credentials': [],
        }


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
                'ip_addresses': row['ip_addresses'] or [],
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
