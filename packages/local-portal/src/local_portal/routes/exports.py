"""
Export API routes for CSV and PDF generation.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/csv/devices")
async def export_devices_csv(
    request: Request,
    device_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
) -> StreamingResponse:
    """
    Export device inventory as CSV.
    """
    db = request.app.state.db

    devices = db.get_devices(
        device_type=device_type,
        status=status,
        limit=10000,  # Large limit for export
    )

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "hostname",
            "ip_address",
            "mac_address",
            "device_type",
            "os_name",
            "os_version",
            "status",
            "compliance_status",
            "medical_device",
            "scan_policy",
            "first_seen_at",
            "last_seen_at",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(devices)

    output.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=device_inventory_{timestamp}.csv"
        },
    )


@router.get("/csv/compliance")
async def export_compliance_csv(request: Request) -> StreamingResponse:
    """
    Export compliance status as CSV.
    """
    db = request.app.state.db

    # Get devices with compliance info
    devices = db.get_devices(limit=10000)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "hostname",
            "ip_address",
            "device_type",
            "compliance_status",
            "scan_policy",
            "medical_device",
            "last_scan_at",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(devices)

    output.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=compliance_report_{timestamp}.csv"
        },
    )


@router.get("/pdf/compliance")
async def export_compliance_pdf(request: Request) -> StreamingResponse:
    """
    Export compliance report as PDF.
    """
    from ..services.pdf_generator import generate_compliance_report_pdf

    db = request.app.state.db
    config = request.app.state.config

    # Gather data for report
    device_counts = db.get_device_counts()
    compliance_summary = db.get_compliance_summary()
    device_types = db.get_device_types_summary()
    latest_scan = db.get_latest_scan()
    drifted_devices = db.get_devices(compliance_status="drifted", limit=50)

    # Generate PDF
    pdf_bytes = generate_compliance_report_pdf(
        site_name=config.site_name,
        device_counts=device_counts,
        compliance_summary=compliance_summary,
        device_types=device_types,
        latest_scan=latest_scan,
        drifted_devices=drifted_devices,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=compliance_report_{timestamp}.pdf"
        },
    )


@router.get("/pdf/inventory")
async def export_inventory_pdf(request: Request) -> StreamingResponse:
    """
    Export device inventory as PDF.
    """
    from ..services.pdf_generator import generate_inventory_report_pdf

    db = request.app.state.db
    config = request.app.state.config

    # Gather data
    devices = db.get_devices(limit=500)
    device_counts = db.get_device_counts()
    device_types = db.get_device_types_summary()

    # Generate PDF
    pdf_bytes = generate_inventory_report_pdf(
        site_name=config.site_name,
        devices=devices,
        device_counts=device_counts,
        device_types=device_types,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=device_inventory_{timestamp}.pdf"
        },
    )
