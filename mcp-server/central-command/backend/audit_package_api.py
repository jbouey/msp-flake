"""
Audit package API (#150, Session 206).

Client-facing endpoints for generating, downloading, and tracking audit
packages. Mounts at /api/client/audit-package/*.

Psychology-first design (see round-table spec):
  * "Your package is ready" — framing implies readiness, not chore
  * List is pre-sorted newest-first
  * Download records the actor in audit_package_downloads (shows client
    exactly who fetched it, when)
  * Send-to-email records delivery in the same ledger
"""

from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .auth import require_auth
from .audit_package import AuditPackage, PackagePeriod

logger = logging.getLogger(__name__)

audit_package_router = APIRouter(prefix="/api/client/audit-package", tags=["audit-package"])

_OUTPUT_DIR = Path(os.getenv("AUDIT_PACKAGE_DIR", "/var/lib/osiriscare/audit-packages"))


class GeneratePackageRequest(BaseModel):
    site_id: str
    period_start: date
    period_end: date
    framework: str = Field(default="hipaa")


class SendPackageRequest(BaseModel):
    auditor_email: EmailStr
    note: Optional[str] = Field(default=None, max_length=500)


# =============================================================================
# Generate
# =============================================================================

@audit_package_router.post("/generate")
async def generate_audit_package(
    req: GeneratePackageRequest,
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Kick off generation. Runs synchronously for now — a typical site
    with a quarter of evidence is 10-30 seconds. Async job queue is a
    future optimization if generation blocks UX."""
    if req.period_end < req.period_start:
        raise HTTPException(400, "period_end must be >= period_start")
    if (req.period_end - req.period_start).days > 400:
        raise HTTPException(400, "period cannot exceed ~13 months")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1",
            req.site_id,
        )
        if not site:
            raise HTTPException(404, f"site_id={req.site_id} not found")

        pkg = AuditPackage(
            site_id=req.site_id,
            site_name=site["clinic_name"] or req.site_id,
            period=PackagePeriod(start=req.period_start, end=req.period_end),
            generated_by=user.get("email", user.get("username", "unknown")),
            output_dir=_OUTPUT_DIR / req.site_id,
            framework=req.framework,
        )
        result = await pkg.generate(conn)

    return result


# =============================================================================
# List
# =============================================================================

@audit_package_router.get("/list")
async def list_audit_packages(
    site_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """List audit packages. Admins see all; scoped to site_id if provided."""
    pool = await get_pool()
    args = []
    where = []
    if site_id:
        args.append(site_id)
        where.append(f"site_id = ${len(args)}")
    args.append(limit)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT package_id, site_id, period_start, period_end, generated_at,
               generated_by, bundles_count, packets_count, zip_sha256,
               zip_size_bytes, framework, download_count, last_downloaded_at,
               delivered_to_email, delivered_at
        FROM audit_packages
        {clause}
        ORDER BY generated_at DESC
        LIMIT ${len(args)}
    """
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(sql, *args)
    packages = []
    for r in rows:
        d = dict(r)
        for k in ("package_id",):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("period_start", "period_end"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        for k in ("generated_at", "last_downloaded_at", "delivered_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        packages.append(d)
    return {"count": len(packages), "packages": packages}


# =============================================================================
# Download
# =============================================================================

@audit_package_router.get("/{package_id}/download")
async def download_audit_package(
    package_id: str,
    request: Request,
    user: dict = Depends(require_auth),
):
    """Download the ZIP. Records the download in audit_package_downloads so
    the client has evidence of who fetched it."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT package_id, site_id, zip_path, zip_sha256, zip_size_bytes,
                   manifest_signature
            FROM audit_packages
            WHERE package_id = $1::uuid
            """,
            package_id,
        )
        if not row:
            raise HTTPException(404, "package not found")

        zip_path = Path(row["zip_path"])
        if not zip_path.exists():
            raise HTTPException(
                410,
                "ZIP no longer on disk (retention cleanup?). "
                "Regenerate from the original period.",
            )

        # Record delivery.
        await conn.execute(
            """
            INSERT INTO audit_package_downloads
                (package_id, downloader, ip_address, user_agent, referrer)
            VALUES ($1, $2, $3::inet, $4, $5)
            """,
            row["package_id"],
            user.get("email") or user.get("username") or "unknown",
            request.client.host if request.client else None,
            request.headers.get("user-agent", ""),
            request.headers.get("referer", ""),
        )
        # Single-row UPDATE by package_id UUID — Migration 192 row-guard
        # trigger is scoped to site_appliances + appliances only, so this
        # one-row update proceeds without the bulk flag.
        await conn.execute(
            "UPDATE audit_packages SET download_count = download_count + 1, "
            "last_downloaded_at = NOW() WHERE package_id = $1",
            row["package_id"],
        )

    # Stream the file.
    filename = zip_path.name
    headers = {
        "X-Audit-Package-SHA256": row["zip_sha256"],
        "X-Audit-Package-Signature": row["manifest_signature"] or "",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        headers=headers,
    )


# =============================================================================
# Send to auditor
# =============================================================================

@audit_package_router.post("/{package_id}/send")
async def send_to_auditor(
    package_id: str,
    req: SendPackageRequest,
    request: Request,
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Record the auditor email delivery + optionally send the link.

    For now this just registers the delivery in the audit_package_downloads
    ledger and stamps delivered_to_email / delivered_at on the package row.
    Actual email send integrates with email_alerts in a future iteration.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT package_id, site_id FROM audit_packages WHERE package_id = $1::uuid",
            package_id,
        )
        if not row:
            raise HTTPException(404, "package not found")
        await conn.execute(
            """
            UPDATE audit_packages
            SET delivered_to_email = $1, delivered_at = NOW()
            WHERE package_id = $2
            """,
            req.auditor_email,
            row["package_id"],
        )
        await conn.execute(
            """
            INSERT INTO audit_package_downloads
                (package_id, downloader, ip_address, user_agent, referrer)
            VALUES ($1, $2, $3::inet, $4, 'api:send')
            """,
            row["package_id"],
            f"sent-to:{req.auditor_email}",
            request.client.host if request.client else None,
            f"note:{(req.note or '')[:200]}",
        )
    return {"delivered_to_email": req.auditor_email, "status": "recorded"}


# =============================================================================
# Download audit log (so client sees who fetched)
# =============================================================================

@audit_package_router.get("/{package_id}/audit-log")
async def get_package_audit_log(
    package_id: str,
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Every download + send event for a package. Shown in the client UI so
    the operator can see exactly who retrieved their audit package and when."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT download_id, downloaded_at, downloader, ip_address,
                   user_agent, referrer
            FROM audit_package_downloads
            WHERE package_id = $1::uuid
            ORDER BY downloaded_at DESC
            """,
            package_id,
        )
    return {
        "package_id": package_id,
        "events": [
            {
                "download_id": r["download_id"],
                "downloaded_at": r["downloaded_at"].isoformat(),
                "downloader": r["downloader"],
                "ip_address": str(r["ip_address"]) if r["ip_address"] else None,
                "user_agent": r["user_agent"],
                "referrer": r["referrer"],
            }
            for r in rows
        ],
    }
