"""
Device inventory API routes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class DeviceListResponse(BaseModel):
    """Response for device list."""
    devices: list[dict]
    total: int
    page: int
    page_size: int


class DevicePolicyUpdate(BaseModel):
    """Request to update device scan policy."""
    scan_policy: str  # standard, limited, excluded
    manually_opted_in: Optional[bool] = None
    reason: Optional[str] = None


@router.get("")
async def list_devices(
    request: Request,
    device_type: Optional[str] = Query(None, description="Filter by device type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    compliance_status: Optional[str] = Query(None, description="Filter by compliance"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
) -> DeviceListResponse:
    """
    List all discovered devices.

    Supports filtering and pagination.
    """
    db = request.app.state.db

    offset = (page - 1) * page_size
    devices = db.get_devices(
        device_type=device_type,
        status=status,
        compliance_status=compliance_status,
        limit=page_size,
        offset=offset,
    )

    # Get total count
    counts = db.get_device_counts()

    return DeviceListResponse(
        devices=devices,
        total=counts["total"],
        page=page,
        page_size=page_size,
    )


@router.get("/{device_id}")
async def get_device(
    device_id: str,
    request: Request,
) -> dict:
    """
    Get detailed information about a device.

    Includes ports, compliance checks, and notes.
    """
    db = request.app.state.db

    device = db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get related data
    ports = db.get_device_ports(device_id)
    compliance_checks = db.get_device_compliance_checks(device_id)
    notes = db.get_device_notes(device_id)

    return {
        "device": device,
        "ports": ports,
        "compliance_checks": compliance_checks,
        "notes": notes,
    }


@router.put("/{device_id}/policy")
async def update_device_policy(
    device_id: str,
    update: DevicePolicyUpdate,
    request: Request,
) -> dict:
    """
    Update scan policy for a device.

    This is how medical devices can be opted-in for scanning.
    """
    db = request.app.state.db

    device = db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Validate policy
    valid_policies = ["standard", "limited", "excluded"]
    if update.scan_policy not in valid_policies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scan policy. Must be one of: {valid_policies}",
        )

    # Medical device validation
    if device["medical_device"]:
        if update.scan_policy != "excluded" and not update.manually_opted_in:
            raise HTTPException(
                status_code=400,
                detail="Medical devices require explicit opt-in (manually_opted_in=true)",
            )
        # Medical devices can only be limited at most, not standard
        if update.scan_policy == "standard":
            raise HTTPException(
                status_code=400,
                detail="Medical devices can only use 'limited' or 'excluded' policy",
            )

    # Update the device policy in the database
    conn = db.conn
    cursor = conn.cursor()

    cursor.execute(
        """UPDATE devices
           SET scan_policy = ?,
               manually_opted_in = COALESCE(?, manually_opted_in),
               status = CASE
                   WHEN ? = 'excluded' THEN 'excluded'
                   WHEN status = 'excluded' THEN 'discovered'
                   ELSE status
               END
           WHERE id = ?""",
        (
            update.scan_policy,
            update.manually_opted_in,
            update.scan_policy,
            device_id,
        ),
    )

    # Add note if reason provided
    if update.reason:
        from datetime import datetime, timezone
        cursor.execute(
            """INSERT INTO device_notes (device_id, note, created_by, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                device_id,
                f"Policy changed to '{update.scan_policy}': {update.reason}",
                "portal_user",
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    conn.commit()

    # Return updated device
    return db.get_device(device_id)


@router.get("/{device_id}/history")
async def get_device_history(
    device_id: str,
    request: Request,
) -> dict:
    """Get scan history for a device."""
    db = request.app.state.db

    device = db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get compliance check history
    compliance_checks = db.get_device_compliance_checks(device_id)

    # Get notes history
    notes = db.get_device_notes(device_id)

    return {
        "device_id": device_id,
        "first_seen_at": device["first_seen_at"],
        "last_seen_at": device["last_seen_at"],
        "last_scan_at": device.get("last_scan_at"),
        "compliance_checks": compliance_checks,
        "notes": notes,
    }
