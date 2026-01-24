"""
Dashboard API routes.

Provides KPIs and summary data for the main dashboard view.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard(request: Request) -> dict:
    """
    Get dashboard summary data.

    Returns KPIs, device counts, compliance summary, and recent activity.
    """
    db = request.app.state.db
    config = request.app.state.config

    # Get device counts
    device_counts = db.get_device_counts()

    # Get compliance summary
    compliance = db.get_compliance_summary()

    # Get device types breakdown
    device_types = db.get_device_types_summary()

    # Get last scan info
    latest_scan = db.get_latest_scan()

    return {
        "site_name": config.site_name,
        "appliance_id": config.appliance_id,
        "devices": {
            "total": device_counts["total"],
            "monitored": device_counts["monitored"],
            "discovered": device_counts["discovered"],
            "excluded": device_counts["excluded"],
            "offline": device_counts["offline"],
            "medical": device_counts["medical"],
        },
        "compliance": {
            "total_assessed": compliance["total"],
            "compliant": compliance["compliant"],
            "drifted": compliance["drifted"],
            "unknown": compliance["unknown"],
            "compliance_rate": compliance["compliance_rate"],
        },
        "device_types": device_types,
        "last_scan": {
            "scan_id": latest_scan["id"] if latest_scan else None,
            "started_at": latest_scan["started_at"] if latest_scan else None,
            "completed_at": latest_scan["completed_at"] if latest_scan else None,
            "devices_found": latest_scan["devices_found"] if latest_scan else 0,
            "new_devices": latest_scan["new_devices"] if latest_scan else 0,
            "status": latest_scan["status"] if latest_scan else None,
        } if latest_scan else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/kpis")
async def get_kpis(request: Request) -> dict:
    """
    Get key performance indicators.

    Lightweight endpoint for just the numbers.
    """
    db = request.app.state.db

    device_counts = db.get_device_counts()
    compliance = db.get_compliance_summary()

    return {
        "total_devices": device_counts["total"],
        "monitored_devices": device_counts["monitored"],
        "medical_devices_excluded": device_counts["medical"],
        "compliance_rate": compliance["compliance_rate"],
        "devices_drifted": compliance["drifted"],
    }
