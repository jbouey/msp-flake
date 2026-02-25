"""FastAPI routes for device sync from appliances.

Endpoints for receiving device inventory from appliance network scanners
and providing fleet-wide device visibility.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

from ..device_sync import (
    DeviceSyncReport,
    DeviceSyncResponse,
    sync_devices,
    get_site_devices,
    get_site_device_counts,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.post("/sync", response_model=DeviceSyncResponse)
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


@router.get("/sites/{site_id}")
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


@router.get("/sites/{site_id}/summary")
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


@router.get("/sites/{site_id}/medical")
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
    from ..device_sync import get_pool

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


@router.get("/sites/{site_id}/device/{device_id}/compliance")
async def get_device_compliance_details(site_id: str, device_id: int) -> dict:
    """
    Get compliance check details for a specific device.

    Returns individual check results with HIPAA control mappings.
    """
    from ..device_sync import get_pool

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify device belongs to site
        device = await conn.fetchrow(
            """
            SELECT d.id, d.hostname, d.ip_address, d.compliance_status
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE d.id = $1 AND a.site_id = $2
            """,
            device_id,
            site_id,
        )

        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        checks = await conn.fetch(
            """
            SELECT check_type, hipaa_control, status, details, checked_at, synced_at
            FROM device_compliance_details
            WHERE discovered_device_id = $1
            ORDER BY checked_at DESC
            """,
            device_id,
        )

    return {
        "device_id": device_id,
        "hostname": device["hostname"],
        "ip_address": device["ip_address"],
        "compliance_status": device["compliance_status"],
        "checks": [dict(row) for row in checks],
        "total_checks": len(checks),
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "warned": sum(1 for c in checks if c["status"] == "warn"),
        "failed": sum(1 for c in checks if c["status"] == "fail"),
    }
