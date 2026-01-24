"""
Central Command sync routes.
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.central_sync import sync_to_central

router = APIRouter()


class SyncTriggerRequest(BaseModel):
    """Request to trigger Central Command sync."""
    central_url: Optional[str] = None  # Override from config
    api_key: Optional[str] = None  # Override from config


class SyncResponse(BaseModel):
    """Response from sync operation."""
    status: str
    devices_synced: Optional[int] = None
    devices_created: Optional[int] = None
    devices_updated: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None


@router.post("/sync", response_model=SyncResponse)
async def trigger_central_sync(
    request: Request,
    body: Optional[SyncTriggerRequest] = None,
) -> SyncResponse:
    """
    Trigger sync of device inventory to Central Command.

    Pushes all discovered devices to the central server for fleet-wide visibility.
    """
    db = request.app.state.db
    config = request.app.state.config

    # Get Central Command URL (from request, config, or environment)
    central_url = (
        (body and body.central_url)
        or os.environ.get("CENTRAL_COMMAND_URL")
        or "https://central.osiriscare.net"
    )

    # Get API key
    api_key = (
        (body and body.api_key)
        or os.environ.get("CENTRAL_COMMAND_API_KEY")
    )

    # Get appliance/site identifiers
    appliance_id = config.appliance_id or os.environ.get("APPLIANCE_ID", "unknown-appliance")
    site_id = os.environ.get("SITE_ID", "unknown-site")

    try:
        result = await sync_to_central(
            db=db,
            central_url=central_url,
            appliance_id=appliance_id,
            site_id=site_id,
            api_key=api_key,
        )

        if result["status"] == "success":
            return SyncResponse(
                status="success",
                devices_synced=result.get("devices_synced"),
                devices_created=result.get("devices_created"),
                devices_updated=result.get("devices_updated"),
                message=result.get("message"),
            )
        else:
            return SyncResponse(
                status="error",
                error=result.get("error"),
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync/status")
async def get_sync_status(request: Request) -> dict:
    """
    Get Central Command sync status.

    Returns info about last sync and connectivity.
    """
    config = request.app.state.config
    central_url = os.environ.get("CENTRAL_COMMAND_URL", "https://central.osiriscare.net")

    return {
        "central_url": central_url,
        "appliance_id": config.appliance_id,
        "site_id": os.environ.get("SITE_ID"),
        "configured": bool(config.appliance_id and os.environ.get("SITE_ID")),
        "note": "Use POST /api/sync to push device inventory to Central Command",
    }
