"""
Compliance status API routes.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/summary")
async def get_compliance_summary(request: Request) -> dict:
    """
    Get overall compliance summary.

    Shows compliance rate and breakdown by status.
    """
    db = request.app.state.db

    summary = db.get_compliance_summary()
    device_counts = db.get_device_counts()

    return {
        "total_devices": device_counts["total"],
        "assessed_devices": summary["total"],
        "medical_excluded": device_counts["medical"],
        "compliance": {
            "compliant": summary["compliant"],
            "drifted": summary["drifted"],
            "unknown": summary["unknown"],
            "excluded": summary["excluded"],
            "rate": summary["compliance_rate"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/drifted")
async def get_drifted_devices(request: Request) -> dict:
    """
    Get devices that have drifted from compliance.

    Priority list for remediation.
    """
    db = request.app.state.db

    devices = db.get_devices(compliance_status="drifted", limit=100)

    return {
        "drifted_count": len(devices),
        "devices": devices,
    }


@router.get("/by-control")
async def get_compliance_by_control(request: Request) -> dict:
    """
    Get compliance grouped by HIPAA control.

    Shows which controls have the most issues.
    """
    db = request.app.state.db

    # Query compliance checks grouped by control
    cursor = db.conn.execute("""
        SELECT
            hipaa_control,
            COUNT(*) as total_checks,
            SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) as passed,
            SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN status = 'warn' THEN 1 ELSE 0 END) as warnings
        FROM device_compliance
        WHERE hipaa_control IS NOT NULL
        GROUP BY hipaa_control
        ORDER BY failed DESC, warnings DESC
    """)

    controls = []
    for row in cursor.fetchall():
        total = row["total_checks"]
        passed = row["passed"]
        controls.append({
            "control": row["hipaa_control"],
            "total_checks": total,
            "passed": passed,
            "failed": row["failed"],
            "warnings": row["warnings"],
            "compliance_rate": round(passed / total * 100, 1) if total > 0 else 0.0,
        })

    return {
        "controls": controls,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/recent-checks")
async def get_recent_checks(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, description="Filter by status (pass/fail/warn)"),
) -> dict:
    """Get recent compliance check results."""
    db = request.app.state.db

    query = """
        SELECT
            dc.*,
            d.hostname,
            d.ip_address,
            d.device_type
        FROM device_compliance dc
        JOIN devices d ON dc.device_id = d.id
    """
    params = []

    if status:
        query += " WHERE dc.status = ?"
        params.append(status)

    query += " ORDER BY dc.checked_at DESC LIMIT ?"
    params.append(limit)

    cursor = db.conn.execute(query, params)
    checks = [dict(row) for row in cursor.fetchall()]

    return {
        "checks": checks,
        "count": len(checks),
    }


@router.get("/device/{device_id}")
async def get_device_compliance(
    device_id: str,
    request: Request,
) -> dict:
    """
    Get compliance details for a specific device.

    Shows all compliance checks and their results.
    """
    db = request.app.state.db

    device = db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    checks = db.get_device_compliance_checks(device_id)

    # Group by check type
    by_type = {}
    for check in checks:
        check_type = check["check_type"]
        if check_type not in by_type:
            by_type[check_type] = []
        by_type[check_type].append(check)

    # Calculate device compliance
    total = len(checks)
    passed = sum(1 for c in checks if c["status"] == "pass")

    return {
        "device_id": device_id,
        "hostname": device["hostname"],
        "ip_address": device["ip_address"],
        "compliance_status": device["compliance_status"],
        "total_checks": total,
        "passed_checks": passed,
        "compliance_rate": round(passed / total * 100, 1) if total > 0 else 0.0,
        "checks_by_type": by_type,
        "all_checks": checks,
    }
