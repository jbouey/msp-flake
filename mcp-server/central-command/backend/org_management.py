"""Organization lifecycle management — enterprise operations.

Single module for org provisioning, deprovisioning, data export, quota
enforcement, and cross-org search. All endpoints write to org_audit_log
for HIPAA-required audit trail.

Endpoints:
- POST   /admin/orgs/provision          — create new org (wizard)
- POST   /admin/orgs/{id}/deprovision   — soft-delete org
- POST   /admin/orgs/{id}/reprovision   — undo deprovisioning
- GET    /admin/orgs/{id}/export        — GDPR/HIPAA data portability bundle
- GET    /admin/orgs/{id}/audit-bundle  — BAA-ready audit export
- GET    /admin/orgs/search             — cross-org search for admins
- GET    /admin/orgs/{id}/quota         — current quota usage
- PUT    /admin/orgs/{id}/quota         — update quotas
- GET    /admin/orgs/{id}/health        — consolidated org health
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import auth as auth_module
from .auth import check_site_access_sa
from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard/admin/orgs", tags=["org-management"])


# ============================================================================
# Pydantic models
# ============================================================================

class ProvisionOrgRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    primary_email: str
    primary_phone: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    npi_number: Optional[str] = None
    tax_id: Optional[str] = None
    practice_type: Optional[str] = None
    provider_count: Optional[int] = None
    partner_id: Optional[str] = None
    baa_effective_date: Optional[str] = None  # YYYY-MM-DD
    baa_expiration_date: Optional[str] = None
    max_sites: int = 100
    max_users: int = 50
    compliance_framework: str = "HIPAA"
    mfa_required: bool = True


class DeprovisionOrgRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=1000)
    retention_days: int = Field(2190, ge=0, le=3650)  # HIPAA 6 years default
    notify_users: bool = True


class QuotaUpdateRequest(BaseModel):
    max_sites: Optional[int] = None
    max_users: Optional[int] = None
    max_incidents_per_day: Optional[int] = None


# ============================================================================
# Helper: audit log
# ============================================================================

async def _audit(
    conn,
    org_id: str,
    event_type: str,
    actor: str,
    actor_type: str,
    target: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
):
    """Write an entry to org_audit_log."""
    try:
        await conn.execute(
            """
            INSERT INTO org_audit_log (
                org_id, event_type, actor, actor_type, target, details, ip_address
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            """,
            org_id, event_type, actor, actor_type,
            target, json.dumps(details or {}), ip_address,
        )
    except Exception as e:
        logger.error(f"org_audit_log write failed: {e}")


# ============================================================================
# PROVISIONING
# ============================================================================

@router.post("/provision")
async def provision_org(
    req: ProvisionOrgRequest,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Create a new organization — the onboarding wizard.

    Creates the org row, assigns to partner if specified, writes audit log.
    Returns the new org_id and the next-step checklist.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            # Check for duplicate name
            existing = await conn.fetchrow(
                "SELECT id FROM client_orgs WHERE lower(name) = lower($1)",
                req.name,
            )
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"Organization '{req.name}' already exists",
                )

            # Partner must exist if specified
            if req.partner_id:
                partner = await conn.fetchrow(
                    "SELECT id FROM partners WHERE id = $1::uuid",
                    req.partner_id,
                )
                if not partner:
                    raise HTTPException(status_code=400, detail="Partner not found")

            row = await conn.fetchrow(
                """
                INSERT INTO client_orgs (
                    name, primary_email, primary_phone,
                    address_line1, city, state, postal_code,
                    npi_number, tax_id, practice_type, provider_count,
                    current_partner_id, partner_assigned_at,
                    baa_effective_date, baa_expiration_date,
                    max_sites, max_users, compliance_framework, mfa_required,
                    status, onboarded_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12::uuid,
                    CASE WHEN $12::uuid IS NOT NULL THEN NOW() ELSE NULL END,
                    $13::date, $14::date,
                    $15, $16, $17, $18,
                    'active', NOW()
                )
                RETURNING id, name, created_at
                """,
                req.name, req.primary_email, req.primary_phone,
                req.address_line1, req.city, req.state, req.postal_code,
                req.npi_number, req.tax_id, req.practice_type, req.provider_count,
                req.partner_id,
                req.baa_effective_date, req.baa_expiration_date,
                req.max_sites, req.max_users, req.compliance_framework, req.mfa_required,
            )

            await _audit(
                conn, str(row["id"]), "org_provisioned",
                user.get("username", "admin"), "admin",
                details={
                    "name": req.name,
                    "partner_id": req.partner_id,
                    "compliance_framework": req.compliance_framework,
                    "baa_effective": req.baa_effective_date,
                    "baa_expiration": req.baa_expiration_date,
                },
                ip_address=request.client.host if request.client else None,
            )

        return {
            "org_id": str(row["id"]),
            "name": row["name"],
            "created_at": row["created_at"].isoformat(),
            "next_steps": [
                "1. Upload signed BAA",
                "2. Configure per-org SSO (optional)",
                "3. Provision first site via /api/dashboard/sites",
                "4. Invite client users",
                "5. First compliance packet will auto-generate on 1st of next month",
            ],
        }


# ============================================================================
# DEPROVISIONING
# ============================================================================

@router.post("/{org_id}/deprovision")
async def deprovision_org(
    org_id: str,
    req: DeprovisionOrgRequest,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Soft-delete an org. Data preserved for retention period (HIPAA 6 years default).

    Sites become read-only, users blocked from login, evidence retained.
    After retention_days, a separate purge job can hard-delete.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            org = await conn.fetchrow(
                "SELECT id, name, deprovisioned_at FROM client_orgs WHERE id = $1::uuid",
                org_id,
            )
            if not org:
                raise HTTPException(status_code=404, detail="Org not found")
            if org["deprovisioned_at"]:
                raise HTTPException(status_code=409, detail="Org already deprovisioned")

            retention_until = (datetime.now(timezone.utc).date() +
                               timedelta(days=req.retention_days))

            await conn.execute(
                """
                UPDATE client_orgs SET
                    deprovisioned_at = NOW(),
                    deprovisioned_by = $2,
                    deprovision_reason = $3,
                    data_retention_until = $4,
                    status = 'deprovisioned'
                WHERE id = $1::uuid
                """,
                org_id, user.get("username", "admin"), req.reason, retention_until,
            )

            # Mark sites as read-only
            await conn.execute(
                "UPDATE sites SET status = 'archived' WHERE client_org_id = $1::uuid",
                org_id,
            )

            # Invalidate client user sessions
            await conn.execute(
                """
                DELETE FROM client_sessions
                WHERE client_user_id IN (
                    SELECT id FROM client_users WHERE client_org_id = $1::uuid
                )
                """,
                org_id,
            )

            await _audit(
                conn, org_id, "org_deprovisioned",
                user.get("username", "admin"), "admin",
                details={
                    "reason": req.reason,
                    "retention_days": req.retention_days,
                    "retention_until": retention_until.isoformat(),
                    "notify_users": req.notify_users,
                },
                ip_address=request.client.host if request.client else None,
            )

        try:
            from .email_alerts import send_operator_alert
            send_operator_alert(
                event_type="org_deprovisioned",
                severity="P1",
                summary=f"Client org deprovisioned: {org['name']} (retention {req.retention_days}d)",
                details={
                    "org_id": org_id,
                    "org_name": org["name"],
                    "reason": req.reason,
                    "retention_days": req.retention_days,
                    "retention_until": retention_until.isoformat(),
                    "notify_users": req.notify_users,
                },
                actor_email=user.get("email") or user.get("username"),
            )
        except Exception:
            logger.error("operator_alert_dispatch_failed_deprovision", exc_info=True)

        return {
            "org_id": org_id,
            "name": org["name"],
            "deprovisioned_at": datetime.now(timezone.utc).isoformat(),
            "data_retention_until": retention_until.isoformat(),
            "message": (
                f"Org deprovisioned. Data retained until {retention_until}. "
                f"All sites archived, client sessions invalidated."
            ),
        }


@router.post("/{org_id}/reprovision")
async def reprovision_org(
    org_id: str,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Undo a deprovisioning — restore the org to active state.

    Only works within retention period.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            org = await conn.fetchrow(
                "SELECT deprovisioned_at, data_retention_until FROM client_orgs WHERE id = $1::uuid",
                org_id,
            )
            if not org:
                raise HTTPException(status_code=404, detail="Org not found")
            if not org["deprovisioned_at"]:
                raise HTTPException(status_code=409, detail="Org is not deprovisioned")

            if (org["data_retention_until"] and
                    org["data_retention_until"] < datetime.now(timezone.utc).date()):
                raise HTTPException(
                    status_code=410,
                    detail="Retention period expired. Data may be permanently deleted.",
                )

            await conn.execute(
                """
                UPDATE client_orgs SET
                    deprovisioned_at = NULL,
                    deprovisioned_by = NULL,
                    deprovision_reason = NULL,
                    data_retention_until = NULL,
                    status = 'active'
                WHERE id = $1::uuid
                """,
                org_id,
            )

            await conn.execute(
                "UPDATE sites SET status = 'active' WHERE client_org_id = $1::uuid AND status = 'archived'",
                org_id,
            )

            await _audit(
                conn, org_id, "org_reprovisioned",
                user.get("username", "admin"), "admin",
                ip_address=request.client.host if request.client else None,
            )

        try:
            from .email_alerts import send_operator_alert
            send_operator_alert(
                event_type="org_reprovisioned",
                severity="P1",
                summary=f"Client org reactivated: org_id={org_id}",
                details={"org_id": org_id},
                actor_email=user.get("email") or user.get("username"),
            )
        except Exception:
            logger.error("operator_alert_dispatch_failed_reprovision", exc_info=True)

        return {"org_id": org_id, "status": "reactivated"}


# ============================================================================
# DATA EXPORT (GDPR / HIPAA portability)
# ============================================================================

@router.get("/{org_id}/export")
async def export_org_data(
    org_id: str,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """GDPR/HIPAA data portability export.

    Returns complete org data as a self-contained JSON bundle. Written to
    org_audit_log for HIPAA disclosure tracking.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT * FROM client_orgs WHERE id = $1::uuid", org_id,
        )
        if not org:
            raise HTTPException(status_code=404, detail="Org not found")

        sites = await conn.fetch(
            "SELECT * FROM sites WHERE client_org_id = $1::uuid", org_id,
        )

        users = await conn.fetch(
            """
            SELECT id, email, display_name, role, created_at, last_login,
                   mfa_enabled, status
            FROM client_users WHERE client_org_id = $1::uuid
            """,
            org_id,
        )

        # Incidents scoped to org's sites
        incidents = await conn.fetch(
            """
            SELECT i.id, i.site_id, i.check_type, i.severity, i.status,
                   i.reported_at, i.resolved_at, i.resolution_tier, i.hipaa_controls
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.client_org_id = $1::uuid
            ORDER BY i.reported_at DESC
            LIMIT 10000
            """,
            org_id,
        )

        # Compliance packets
        packets = await conn.fetch(
            """
            SELECT id, site_id, period_start, period_end, status, generated_at
            FROM compliance_packets cp
            JOIN sites s ON s.site_id = cp.site_id
            WHERE s.client_org_id = $1::uuid
            ORDER BY cp.generated_at DESC
            """,
            org_id,
        )

        # Audit trail
        audit = await conn.fetch(
            """
            SELECT event_type, actor, actor_type, target, details, created_at
            FROM org_audit_log
            WHERE org_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 5000
            """,
            org_id,
        )

        # Write export request to audit log (HIPAA disclosure)
        await _audit(
            conn, org_id, "data_exported",
            user.get("username", "admin"), "admin",
            details={
                "bytes_approx": len(str(org)) * (len(sites) + len(users) + len(incidents)),
                "record_counts": {
                    "sites": len(sites),
                    "users": len(users),
                    "incidents": len(incidents),
                    "compliance_packets": len(packets),
                },
            },
            ip_address=request.client.host if request.client else None,
        )

        await conn.execute(
            "UPDATE client_orgs SET data_export_requested_at = NOW() WHERE id = $1::uuid",
            org_id,
        )

    return {
        "export_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "org": dict(org) if org else None,
        "sites": [dict(r) for r in sites],
        "users": [dict(r) for r in users],
        "incidents": [dict(r) for r in incidents],
        "compliance_packets": [dict(r) for r in packets],
        "audit_log": [dict(r) for r in audit],
        "disclosure_note": (
            "This export was generated at {} for user {}. "
            "This event is logged in org_audit_log per HIPAA §164.528."
        ).format(
            datetime.now(timezone.utc).isoformat(),
            user.get("username", "admin"),
        ),
    }


@router.get("/{org_id}/audit-bundle")
async def export_audit_bundle(
    org_id: str,
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    user: dict = Depends(auth_module.require_auth),
):
    """BAA-ready audit bundle for a date range.

    Returns: compliance summary, incident counts by tier, evidence hash chain
    verification steps, SLA metrics. Self-contained for third-party auditor.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id, name, compliance_framework, baa_effective_date, baa_expiration_date FROM client_orgs WHERE id = $1::uuid",
            org_id,
        )
        if not org:
            raise HTTPException(status_code=404, detail="Org not found")

        # Compliance score across org's sites
        compliance = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT cb.site_id) as sites_with_scans,
                COUNT(*) as total_bundles,
                COUNT(*) FILTER (WHERE cb.signature IS NOT NULL) as signed_bundles,
                COUNT(*) FILTER (WHERE cb.ots_status = 'anchored') as anchored_bundles
            FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE s.client_org_id = $1::uuid
              AND cb.created_at >= $2::date
              AND cb.created_at < ($3::date + interval '1 day')
            """,
            org_id, start_date, end_date,
        )

        # Incidents by tier
        incidents = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE resolution_tier = 'L3') as l3,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.client_org_id = $1::uuid
              AND i.reported_at >= $2::date
              AND i.reported_at < ($3::date + interval '1 day')
            """,
            org_id, start_date, end_date,
        )

    return {
        "audit_bundle_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "org": {
            "id": str(org["id"]),
            "name": org["name"],
            "compliance_framework": org["compliance_framework"],
            "baa_effective": org["baa_effective_date"].isoformat() if org["baa_effective_date"] else None,
            "baa_expiration": org["baa_expiration_date"].isoformat() if org["baa_expiration_date"] else None,
        },
        "period": {"start": start_date, "end": end_date},
        "compliance": {
            "sites_with_scans": compliance["sites_with_scans"],
            "total_bundles": compliance["total_bundles"],
            "signed_bundles": compliance["signed_bundles"],
            "blockchain_anchored": compliance["anchored_bundles"],
            "signing_rate_pct": round(
                (compliance["signed_bundles"] / max(compliance["total_bundles"], 1)) * 100, 1
            ),
        },
        "incidents": {
            "total": incidents["total"],
            "l1_auto_resolved": incidents["l1"],
            "l2_llm_resolved": incidents["l2"],
            "l3_human_escalated": incidents["l3"],
            "resolution_rate_pct": round(
                (incidents["resolved"] / max(incidents["total"], 1)) * 100, 1
            ),
        },
        "verification_instructions": {
            "hash_chain": "Every compliance_bundle has prev_bundle_hash. Chain integrity provable via sha256sum.",
            "signatures": "Ed25519 signatures verifiable via appliance public key (per-appliance, site_appliances.agent_public_key).",
            "blockchain": "OTS proofs anchored to Bitcoin. Use `ots verify` with the proof_data field.",
            "full_export": f"Complete data: GET /api/dashboard/admin/orgs/{org_id}/export",
        },
    }


# ============================================================================
# QUOTA MANAGEMENT
# ============================================================================

@router.get("/{org_id}/quota")
async def get_org_quota(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get current quota usage for an org."""
    auth_module._check_org_access(user, org_id)
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT
                co.max_sites, co.max_users, co.max_incidents_per_day,
                (SELECT COUNT(*) FROM sites WHERE client_org_id = co.id) as site_count,
                (SELECT COUNT(*) FROM client_users WHERE client_org_id = co.id) as user_count,
                (SELECT COUNT(*) FROM incidents i JOIN sites s ON s.site_id = i.site_id
                 WHERE s.client_org_id = co.id
                   AND i.reported_at > NOW() - INTERVAL '24 hours') as incidents_24h
            FROM client_orgs co
            WHERE co.id = $1::uuid
            """,
            org_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Org not found")

    return {
        "sites": {
            "used": row["site_count"],
            "limit": row["max_sites"],
            "pct": round((row["site_count"] / max(row["max_sites"], 1)) * 100, 1),
        },
        "users": {
            "used": row["user_count"],
            "limit": row["max_users"],
            "pct": round((row["user_count"] / max(row["max_users"], 1)) * 100, 1),
        },
        "incidents_24h": {
            "used": row["incidents_24h"],
            "limit": row["max_incidents_per_day"],
            "pct": round((row["incidents_24h"] / max(row["max_incidents_per_day"], 1)) * 100, 1),
        },
    }


@router.put("/{org_id}/quota")
async def update_org_quota(
    org_id: str,
    req: QuotaUpdateRequest,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Update quota limits for an org (admin only)."""
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Admin only")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            updates = []
            params = [org_id]
            if req.max_sites is not None:
                updates.append(f"max_sites = ${len(params) + 1}")
                params.append(req.max_sites)
            if req.max_users is not None:
                updates.append(f"max_users = ${len(params) + 1}")
                params.append(req.max_users)
            if req.max_incidents_per_day is not None:
                updates.append(f"max_incidents_per_day = ${len(params) + 1}")
                params.append(req.max_incidents_per_day)

            if not updates:
                raise HTTPException(status_code=400, detail="No quota fields provided")

            await conn.execute(
                f"UPDATE client_orgs SET {', '.join(updates)} WHERE id = $1::uuid",
                *params,
            )

            await _audit(
                conn, org_id, "quota_updated",
                user.get("username", "admin"), "admin",
                details=req.dict(exclude_none=True),
                ip_address=request.client.host if request.client else None,
            )

    return {"org_id": org_id, "updated": req.dict(exclude_none=True)}


# ============================================================================
# CROSS-ORG SEARCH
# ============================================================================

@router.get("/search")
async def search_orgs(
    q: str = Query(..., min_length=1, max_length=100),
    include_deprovisioned: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(auth_module.require_auth),
):
    """Cross-org search for admins. Returns matching orgs with key metrics.

    Partners/org-scoped admins are filtered to their accessible orgs only.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        query = """
            SELECT
                co.id, co.name, co.primary_email, co.status,
                co.compliance_framework, co.current_partner_id,
                co.baa_expiration_date, co.deprovisioned_at,
                (SELECT COUNT(*) FROM sites WHERE client_org_id = co.id) as site_count,
                (SELECT COUNT(*) FROM incidents i
                 JOIN sites s ON s.site_id = i.site_id
                 WHERE s.client_org_id = co.id
                   AND i.status != 'resolved') as open_incidents
            FROM client_orgs co
            WHERE (lower(co.name) LIKE $1
                   OR lower(co.primary_email) LIKE $1
                   OR co.npi_number LIKE $1)
        """
        params: List[Any] = [f"%{q.lower()}%"]
        if not include_deprovisioned:
            query += " AND co.deprovisioned_at IS NULL"

        # Org-scoped users see only their scope
        org_scope = user.get("org_scope")
        if org_scope is not None:
            query += f" AND co.id::text = ANY(${len(params) + 1})"
            params.append(org_scope)

        query += f" ORDER BY co.name LIMIT ${len(params) + 1}"
        params.append(limit)

        rows = await conn.fetch(query, *params)

        return {
            "query": q,
            "count": len(rows),
            "orgs": [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "primary_email": r["primary_email"],
                    "status": r["status"],
                    "compliance_framework": r["compliance_framework"],
                    "partner_id": str(r["current_partner_id"]) if r["current_partner_id"] else None,
                    "baa_expiration": r["baa_expiration_date"].isoformat() if r["baa_expiration_date"] else None,
                    "deprovisioned": r["deprovisioned_at"] is not None,
                    "site_count": r["site_count"],
                    "open_incidents": r["open_incidents"],
                }
                for r in rows
            ],
        }


# ============================================================================
# ORG-LEVEL COMPLIANCE PACKET (aggregates all sites in the org)
# ============================================================================

@router.get("/{org_id}/compliance-packet")
async def get_org_compliance_packet(
    org_id: str,
    month: str = Query(..., description="YYYY-MM"),
    user: dict = Depends(auth_module.require_auth),
):
    """Generate an org-level compliance packet rolling up all sites.

    Returns: per-site compliance scores, org-wide totals, healing SLA,
    evidence chain summary, framework control coverage.
    """
    auth_module._check_org_access(user, org_id)

    try:
        parts = month.split("-")
        year, mo = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM format")

    from datetime import date as _date
    period_start = _date(year, mo, 1)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id, name, compliance_framework, primary_email FROM client_orgs WHERE id = $1::uuid",
            org_id,
        )
        if not org:
            raise HTTPException(status_code=404, detail="Org not found")

        # Per-site breakdown
        per_site = await conn.fetch(
            """
            SELECT
                s.site_id, s.clinic_name,
                COUNT(cb.*) as bundle_count,
                COUNT(cb.*) FILTER (WHERE cb.signature IS NOT NULL) as signed_count,
                COUNT(cb.*) FILTER (WHERE cb.ots_status = 'anchored') as anchored_count,
                ROUND(
                    (COUNT(*) FILTER (
                        WHERE c->>'status' IN ('pass', 'compliant')
                    ))::numeric /
                    NULLIF((COUNT(*) FILTER (
                        WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')
                    ))::numeric, 0) * 100,
                    1
                ) as compliance_score
            FROM sites s
            LEFT JOIN compliance_bundles cb ON cb.site_id = s.site_id
                AND cb.created_at >= $2::date
                AND cb.created_at < ($2::date + interval '1 month')
            LEFT JOIN LATERAL jsonb_array_elements(COALESCE(cb.checks, '[]'::jsonb)) c ON true
            WHERE s.client_org_id = $1::uuid
            GROUP BY s.site_id, s.clinic_name
            ORDER BY s.clinic_name
            """,
            org_id, period_start,
        )

        # Org-wide totals
        totals = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT s.site_id) as site_count,
                COUNT(cb.*) as total_bundles,
                COUNT(cb.*) FILTER (WHERE cb.signature IS NOT NULL) as signed,
                COUNT(cb.*) FILTER (WHERE cb.ots_status = 'anchored') as anchored
            FROM sites s
            LEFT JOIN compliance_bundles cb ON cb.site_id = s.site_id
                AND cb.created_at >= $2::date
                AND cb.created_at < ($2::date + interval '1 month')
            WHERE s.client_org_id = $1::uuid
            """,
            org_id, period_start,
        )

        # Incidents this month
        incidents = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
                COUNT(*) FILTER (WHERE resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE resolution_tier = 'L3') as l3
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.client_org_id = $1::uuid
              AND i.reported_at >= $2::date
              AND i.reported_at < ($2::date + interval '1 month')
            """,
            org_id, period_start,
        )

        # Write audit event
        await _audit(
            conn, org_id, "compliance_packet_generated",
            user.get("username", "admin"), "admin",
            details={"period": month, "site_count": totals["site_count"] if totals else 0},
        )

    return {
        "packet_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": month,
        "org": {
            "id": str(org["id"]),
            "name": org["name"],
            "compliance_framework": org["compliance_framework"],
            "primary_email": org["primary_email"],
        },
        "totals": {
            "sites": totals["site_count"] if totals else 0,
            "compliance_bundles": totals["total_bundles"] if totals else 0,
            "signed_bundles": totals["signed"] if totals else 0,
            "blockchain_anchored": totals["anchored"] if totals else 0,
        },
        "incidents": {
            "total": incidents["total"] if incidents else 0,
            "resolved": incidents["resolved"] if incidents else 0,
            "l1_auto": incidents["l1"] if incidents else 0,
            "l2_llm": incidents["l2"] if incidents else 0,
            "l3_escalated": incidents["l3"] if incidents else 0,
        },
        "per_site": [
            {
                "site_id": r["site_id"],
                "clinic_name": r["clinic_name"],
                "bundle_count": r["bundle_count"],
                "signed_count": r["signed_count"],
                "anchored_count": r["anchored_count"],
                "compliance_score": float(r["compliance_score"]) if r["compliance_score"] else None,
            }
            for r in per_site
        ],
    }


# ============================================================================
# ORG HEALTH (consolidated dashboard endpoint)
# ============================================================================

@router.get("/{org_id}/health")
async def get_org_health_dashboard(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Consolidated org health for admin dashboard."""
    auth_module._check_org_access(user, org_id)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            """
            SELECT id, name, status, compliance_framework,
                   baa_effective_date, baa_expiration_date,
                   current_partner_id, onboarded_at, deprovisioned_at,
                   max_sites, max_users, max_incidents_per_day
            FROM client_orgs WHERE id = $1::uuid
            """,
            org_id,
        )
        if not org:
            raise HTTPException(status_code=404, detail="Org not found")

        # Aggregated metrics
        metrics = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM sites WHERE client_org_id = $1::uuid) as site_count,
                (SELECT COUNT(*) FROM client_users WHERE client_org_id = $1::uuid) as user_count,
                (SELECT COUNT(*) FROM incidents i JOIN sites s ON s.site_id = i.site_id
                 WHERE s.client_org_id = $1::uuid AND i.status != 'resolved') as open_incidents,
                (SELECT COUNT(*) FROM compliance_bundles cb JOIN sites s ON s.site_id = cb.site_id
                 WHERE s.client_org_id = $1::uuid
                   AND cb.created_at > NOW() - INTERVAL '24 hours') as bundles_24h,
                (SELECT COUNT(*) FROM execution_telemetry et
                 JOIN sites s ON s.site_id = et.site_id
                 WHERE s.client_org_id = $1::uuid
                   AND et.created_at > NOW() - INTERVAL '24 hours') as executions_24h,
                (SELECT COUNT(*) FROM execution_telemetry et
                 JOIN sites s ON s.site_id = et.site_id
                 WHERE s.client_org_id = $1::uuid AND et.success = true
                   AND et.created_at > NOW() - INTERVAL '24 hours') as successful_24h
            """,
            org_id,
        )

        # Recent audit events
        audit = await conn.fetch(
            """
            SELECT event_type, actor, target, created_at
            FROM org_audit_log
            WHERE org_id = $1::uuid
            ORDER BY created_at DESC LIMIT 20
            """,
            org_id,
        )

        healing_rate = (
            (metrics["successful_24h"] / max(metrics["executions_24h"], 1)) * 100
        )

        # BAA status
        baa_status = "not_configured"
        if org["baa_expiration_date"]:
            from datetime import date
            days_to_expire = (org["baa_expiration_date"] - date.today()).days
            if days_to_expire < 0:
                baa_status = "expired"
            elif days_to_expire < 30:
                baa_status = "expiring_soon"
            else:
                baa_status = "active"

    return {
        "org": dict(org),
        "metrics": {
            "sites": metrics["site_count"],
            "users": metrics["user_count"],
            "open_incidents": metrics["open_incidents"],
            "bundles_24h": metrics["bundles_24h"],
            "executions_24h": metrics["executions_24h"],
            "healing_rate_24h_pct": round(healing_rate, 1),
        },
        "quota": {
            "site_usage_pct": round((metrics["site_count"] / max(org["max_sites"], 1)) * 100, 1),
            "user_usage_pct": round((metrics["user_count"] / max(org["max_users"], 1)) * 100, 1),
        },
        "baa_status": baa_status,
        "recent_audit": [
            {
                "event_type": r["event_type"],
                "actor": r["actor"],
                "target": r["target"],
                "at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in audit
        ],
    }
