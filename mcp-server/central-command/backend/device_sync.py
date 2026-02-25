"""Device inventory sync from appliance network scanners.

Receives device inventory reports from appliance local-portals and stores
them in Central Command for fleet-wide visibility.
"""

from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field
import asyncpg
from fastapi import APIRouter, HTTPException, Query

from .fleet import get_pool


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class DeviceSyncEntry(BaseModel):
    """A single device from the appliance inventory."""
    device_id: str = Field(..., description="Local device ID from appliance")
    hostname: Optional[str] = None
    ip_address: str
    mac_address: Optional[str] = None
    device_type: str = "unknown"
    os_name: Optional[str] = None
    os_version: Optional[str] = None

    # Medical device handling
    medical_device: bool = False
    scan_policy: str = "standard"  # standard, limited, excluded
    manually_opted_in: bool = False

    # Compliance
    compliance_status: str = "unknown"  # compliant, drifted, unknown, excluded
    open_ports: List[int] = Field(default_factory=list)

    # Discovery metadata
    discovery_source: str = "nmap"
    first_seen_at: datetime
    last_seen_at: datetime
    last_scan_at: Optional[datetime] = None


class DeviceSyncReport(BaseModel):
    """Batch device sync report from an appliance."""
    appliance_id: str = Field(..., description="Appliance identifier")
    site_id: str = Field(..., description="Site/client identifier")
    scan_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Device inventory
    devices: List[DeviceSyncEntry]

    # Summary stats
    total_devices: int
    monitored_devices: int
    excluded_devices: int
    medical_devices: int
    compliance_rate: float  # 0-100 percentage


class DeviceSyncResponse(BaseModel):
    """Response to device sync request."""
    status: str  # success, partial, error
    devices_received: int
    devices_updated: int
    devices_created: int
    message: str


# =============================================================================
# DATABASE OPERATIONS
# =============================================================================


async def sync_devices(report: DeviceSyncReport) -> DeviceSyncResponse:
    """
    Sync device inventory from an appliance to Central Command.

    Creates or updates devices in the central database.
    """
    pool = await get_pool()

    devices_updated = 0
    devices_created = 0
    errors = []

    async with pool.acquire() as conn:
        # Look up appliance by site_id (unique constraint)
        appliance_row = await conn.fetchrow(
            "SELECT id FROM appliances WHERE site_id = $1",
            report.site_id,
        )

        if not appliance_row:
            return DeviceSyncResponse(
                status="error",
                devices_received=len(report.devices),
                devices_updated=0,
                devices_created=0,
                message=f"Unknown site_id: {report.site_id}. Appliance must checkin first.",
            )

        appliance_db_id = appliance_row["id"]

        # Process each device
        for device in report.devices:
            try:
                # Check if device exists (by appliance + local device ID)
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM discovered_devices
                    WHERE appliance_id = $1 AND local_device_id = $2
                    """,
                    appliance_db_id,
                    device.device_id,
                )

                if existing:
                    # Update existing device
                    await conn.execute(
                        """
                        UPDATE discovered_devices SET
                            hostname = $3,
                            ip_address = $4,
                            mac_address = $5,
                            device_type = $6,
                            os_name = $7,
                            os_version = $8,
                            medical_device = $9,
                            scan_policy = $10,
                            manually_opted_in = $11,
                            compliance_status = $12,
                            open_ports = $13,
                            discovery_source = $14,
                            last_seen_at = $15,
                            last_scan_at = $16,
                            sync_updated_at = NOW()
                        WHERE appliance_id = $1 AND local_device_id = $2
                        """,
                        appliance_db_id,
                        device.device_id,
                        device.hostname,
                        device.ip_address,
                        device.mac_address,
                        device.device_type,
                        device.os_name,
                        device.os_version,
                        device.medical_device,
                        device.scan_policy,
                        device.manually_opted_in,
                        device.compliance_status,
                        device.open_ports,
                        device.discovery_source,
                        device.last_seen_at,
                        device.last_scan_at,
                    )
                    devices_updated += 1
                else:
                    # Insert new device
                    await conn.execute(
                        """
                        INSERT INTO discovered_devices (
                            appliance_id, local_device_id, hostname, ip_address,
                            mac_address, device_type, os_name, os_version,
                            medical_device, scan_policy, manually_opted_in,
                            compliance_status, open_ports, discovery_source,
                            first_seen_at, last_seen_at, last_scan_at,
                            sync_created_at, sync_updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15, $16, $17, NOW(), NOW()
                        )
                        """,
                        appliance_db_id,
                        device.device_id,
                        device.hostname,
                        device.ip_address,
                        device.mac_address,
                        device.device_type,
                        device.os_name,
                        device.os_version,
                        device.medical_device,
                        device.scan_policy,
                        device.manually_opted_in,
                        device.compliance_status,
                        device.open_ports,
                        device.discovery_source,
                        device.first_seen_at,
                        device.last_seen_at,
                        device.last_scan_at,
                    )
                    devices_created += 1

            except Exception as e:
                errors.append(f"Device {device.device_id}: {str(e)}")

        # Update appliance last check-in timestamp
        await conn.execute(
            """
            UPDATE appliances SET
                last_checkin = NOW()
            WHERE id = $1
            """,
            appliance_db_id,
        )

    status = "success"
    message = f"Synced {devices_created} new, {devices_updated} updated"
    if errors:
        status = "partial"
        message += f", {len(errors)} errors"

    return DeviceSyncResponse(
        status=status,
        devices_received=len(report.devices),
        devices_updated=devices_updated,
        devices_created=devices_created,
        message=message,
    )


async def get_site_devices(
    site_id: str,
    device_type: Optional[str] = None,
    compliance_status: Optional[str] = None,
    include_medical: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """
    Get all devices for a site across all appliances.
    """
    pool = await get_pool()

    query = """
        SELECT
            d.*,
            a.host_id as appliance_hostname,
            a.site_id
        FROM discovered_devices d
        JOIN appliances a ON d.appliance_id = a.id
        WHERE a.site_id = $1
    """
    params = [site_id]
    param_idx = 2

    if device_type:
        query += f" AND d.device_type = ${param_idx}"
        params.append(device_type)
        param_idx += 1

    if compliance_status:
        query += f" AND d.compliance_status = ${param_idx}"
        params.append(compliance_status)
        param_idx += 1

    if not include_medical:
        query += " AND d.medical_device = false"

    query += f" ORDER BY d.last_seen_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]


async def get_site_device_counts(site_id: str) -> dict:
    """
    Get device count summary for a site.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE d.compliance_status = 'compliant') as compliant,
                COUNT(*) FILTER (WHERE d.compliance_status = 'drifted') as drifted,
                COUNT(*) FILTER (WHERE d.compliance_status = 'unknown') as unknown,
                COUNT(*) FILTER (WHERE d.medical_device = true) as medical,
                COUNT(*) FILTER (WHERE d.device_type = 'workstation') as workstations,
                COUNT(*) FILTER (WHERE d.device_type = 'server') as servers,
                COUNT(*) FILTER (WHERE d.device_type = 'network') as network_devices,
                COUNT(*) FILTER (WHERE d.device_type = 'printer') as printers
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1
            """,
            site_id,
        )
        return dict(row) if row else {
            "total": 0, "compliant": 0, "drifted": 0, "unknown": 0,
            "medical": 0, "workstations": 0, "servers": 0,
            "network_devices": 0, "printers": 0,
        }


# =============================================================================
# SQL MIGRATION
# =============================================================================

DEVICE_SYNC_MIGRATION = """
-- Add device sync support to Central Command

-- Discovered devices table
CREATE TABLE IF NOT EXISTS discovered_devices (
    id SERIAL PRIMARY KEY,
    appliance_id INTEGER NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
    local_device_id TEXT NOT NULL,

    -- Device info
    hostname TEXT,
    ip_address TEXT NOT NULL,
    mac_address TEXT,
    device_type TEXT DEFAULT 'unknown',
    os_name TEXT,
    os_version TEXT,

    -- Medical device handling
    medical_device BOOLEAN DEFAULT FALSE,
    scan_policy TEXT DEFAULT 'standard',
    manually_opted_in BOOLEAN DEFAULT FALSE,

    -- Compliance
    compliance_status TEXT DEFAULT 'unknown',
    open_ports INTEGER[] DEFAULT '{}',

    -- Discovery
    discovery_source TEXT DEFAULT 'nmap',
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    last_scan_at TIMESTAMPTZ,

    -- Sync metadata
    sync_created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sync_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Unique constraint: one device per appliance
    UNIQUE(appliance_id, local_device_id)
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_discovered_devices_appliance
    ON discovered_devices(appliance_id);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_compliance
    ON discovered_devices(compliance_status);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_type
    ON discovered_devices(device_type);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_medical
    ON discovered_devices(medical_device) WHERE medical_device = TRUE;

-- Add device sync columns to appliances
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS last_device_sync TIMESTAMPTZ;
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS device_count INTEGER DEFAULT 0;
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS medical_device_count INTEGER DEFAULT 0;
"""


# =============================================================================
# FASTAPI ROUTER
# =============================================================================

device_sync_router = APIRouter(prefix="/api/devices", tags=["devices"])


@device_sync_router.post("/sync", response_model=DeviceSyncResponse)
async def receive_device_sync(report: DeviceSyncReport) -> DeviceSyncResponse:
    """
    Receive device inventory sync from an appliance.

    Called by local-portal on appliances to push discovered devices
    to Central Command for fleet-wide visibility.
    """
    try:
        return await sync_devices(report)
    except Exception as e:
        logger.error(f"Device sync failed: {e}")
        raise HTTPException(status_code=500, detail="Device sync failed. Please try again.")


@device_sync_router.get("/sites/{site_id}")
async def list_site_devices(
    site_id: str,
    device_type: Optional[str] = Query(None, description="Filter by device type"),
    compliance_status: Optional[str] = Query(None, description="Filter by compliance status"),
    include_medical: bool = Query(True, description="Include medical devices"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    Get all discovered devices for a site.

    Aggregates devices across all appliances at the site.
    """
    devices = await get_site_devices(
        site_id=site_id,
        device_type=device_type,
        compliance_status=compliance_status,
        include_medical=include_medical,
        limit=limit,
        offset=offset,
    )

    counts = await get_site_device_counts(site_id)

    return {
        "site_id": site_id,
        "devices": devices,
        "counts": counts,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": counts["total"],
        },
    }


@device_sync_router.get("/sites/{site_id}/summary")
async def get_site_device_summary(site_id: str) -> dict:
    """
    Get device inventory summary for a site.

    Returns counts by type, compliance status, and medical device stats.
    """
    counts = await get_site_device_counts(site_id)

    total = counts["total"]
    compliant = counts["compliant"]

    return {
        "site_id": site_id,
        "total_devices": total,
        "compliance_rate": round(compliant / total * 100, 1) if total > 0 else 0.0,
        "by_compliance": {
            "compliant": compliant,
            "drifted": counts["drifted"],
            "unknown": counts["unknown"],
        },
        "by_type": {
            "workstations": counts["workstations"],
            "servers": counts["servers"],
            "network": counts["network_devices"],
            "printers": counts["printers"],
        },
        "medical_devices": {
            "total": counts["medical"],
            "excluded_by_default": True,
        },
    }


@device_sync_router.get("/sites/{site_id}/medical")
async def list_medical_devices(
    site_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    Get all medical devices for a site.

    Medical devices are excluded from compliance scanning by default
    and require manual opt-in.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                d.*,
                a.host_id as appliance_hostname
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1 AND d.medical_device = true
            ORDER BY d.last_seen_at DESC
            LIMIT $2 OFFSET $3
            """,
            site_id,
            limit,
            offset,
        )

        total = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1 AND d.medical_device = true
            """,
            site_id,
        )

    devices = [dict(row) for row in rows]

    return {
        "site_id": site_id,
        "medical_devices": devices,
        "total": total,
        "note": "Medical devices are excluded from compliance scanning by default for patient safety",
    }
