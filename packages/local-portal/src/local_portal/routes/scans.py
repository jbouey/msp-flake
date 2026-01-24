"""
Scan management API routes.
"""

from typing import Optional

import aiohttp
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class TriggerScanRequest(BaseModel):
    """Request to trigger a scan."""
    scan_type: str = "full"  # full, quick


class TriggerScanResponse(BaseModel):
    """Response from scan trigger."""
    scan_id: str
    status: str
    message: str


@router.get("")
async def list_scans(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Get scan history."""
    db = request.app.state.db

    scans = db.get_scan_history(limit=limit)

    return {
        "scans": scans,
        "total": len(scans),
    }


@router.get("/latest")
async def get_latest_scan(request: Request) -> dict:
    """Get the most recent scan."""
    db = request.app.state.db

    scan = db.get_latest_scan()
    if not scan:
        return {"scan": None, "message": "No scans have been performed yet"}

    return {"scan": scan}


@router.post("/trigger")
async def trigger_scan(
    request: Request,
    body: TriggerScanRequest,
) -> TriggerScanResponse:
    """
    Trigger an on-demand network scan.

    Sends request to the network-scanner service.
    """
    config = request.app.state.config

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{config.scanner_api_url}/api/scans/trigger",
                json={
                    "scan_type": body.scan_type,
                    "triggered_by": "portal",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return TriggerScanResponse(
                        scan_id=data.get("scan_id", ""),
                        status="started",
                        message="Scan initiated successfully",
                    )
                else:
                    error_text = await resp.text()
                    raise HTTPException(
                        status_code=resp.status,
                        detail=f"Scanner service error: {error_text}",
                    )
    except aiohttp.ClientError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach scanner service: {str(e)}",
        )


@router.get("/status/{scan_id}")
async def get_scan_status(
    scan_id: str,
    request: Request,
) -> dict:
    """Get status of a specific scan."""
    db = request.app.state.db

    # Look up in scan history
    scans = db.get_scan_history(limit=100)
    scan = next((s for s in scans if s["id"] == scan_id), None)

    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    return {
        "scan_id": scan_id,
        "status": scan["status"],
        "started_at": scan["started_at"],
        "completed_at": scan["completed_at"],
        "devices_found": scan["devices_found"],
        "new_devices": scan["new_devices"],
        "medical_devices_excluded": scan.get("medical_devices_excluded", 0),
    }
