"""
Audit Readiness Endpoints — /api/ops

Provides audit readiness badge (green/yellow/red), checklist, countdown,
and BAA/audit-date configuration for client organizations.

Pure computation functions are separated from DB/HTTP for testability.

HIPAA Controls:
- §164.308(a)(8) — Evaluation (periodic technical/nontechnical evaluation)
- §164.316(b)(2)(i) — Documentation retention and availability
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_auth
from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["Ops / Audit"])


# =============================================================================
# Pure Computation — fully testable, no DB or HTTP
# =============================================================================

def compute_audit_readiness(
    chain_unbroken: bool,
    signing_rate: float,
    ots_current: bool,
    critical_unresolved: int,
    baa_on_file: bool,
    packet_downloadable: bool,
) -> Dict[str, Any]:
    """Compute audit readiness badge + checklist from pre-fetched facts.

    Returns:
        {
            badge: "green" | "yellow" | "red",
            ready: bool,
            checks: [{name, passed, detail}],
            blockers: [str],
            passed_count: int,
            total_checks: int,
        }
    """
    checks: List[Dict[str, Any]] = []
    blockers: List[str] = []

    # 1. Evidence chain integrity
    checks.append({
        "name": "evidence_chain_unbroken",
        "passed": chain_unbroken,
        "detail": "Hash chain intact" if chain_unbroken else "Hash chain broken — evidence may be contested",
    })
    if not chain_unbroken:
        blockers.append("Evidence hash chain is broken")

    # 2. Signing rate >90%
    rate_ok = signing_rate > 90.0
    checks.append({
        "name": "signing_rate_above_90",
        "passed": rate_ok,
        "detail": f"Signing rate {signing_rate:.1f}%" if rate_ok else f"Signing rate {signing_rate:.1f}% (need >90%)",
    })
    if not rate_ok:
        blockers.append(f"Signing rate {signing_rate:.1f}% is below 90% threshold")

    # 3. OTS anchoring current (<24h)
    checks.append({
        "name": "ots_anchoring_current",
        "passed": ots_current,
        "detail": "OTS anchored within 24h" if ots_current else "OTS anchoring stalled (>24h)",
    })
    if not ots_current:
        blockers.append("OpenTimestamps anchoring is stalled")

    # 4. No critical unresolved incidents
    no_critical = critical_unresolved == 0
    checks.append({
        "name": "no_critical_incidents",
        "passed": no_critical,
        "detail": "No open critical incidents" if no_critical else f"{critical_unresolved} open critical incident(s)",
    })
    if not no_critical:
        blockers.append(f"{critical_unresolved} unresolved critical incident(s)")

    # 5. BAA on file
    checks.append({
        "name": "baa_on_file",
        "passed": baa_on_file,
        "detail": "BAA on file" if baa_on_file else "BAA not on file",
    })
    if not baa_on_file:
        blockers.append("Business Associate Agreement not on file")

    # 6. Compliance packet downloadable
    checks.append({
        "name": "packet_downloadable",
        "passed": packet_downloadable,
        "detail": "Compliance packet available" if packet_downloadable else "Compliance packet not available",
    })
    if not packet_downloadable:
        blockers.append("Compliance packet not downloadable")

    passed_count = sum(1 for c in checks if c["passed"])
    total_checks = len(checks)

    # Badge logic:
    #   Red  = chain broken OR OTS stalled OR critical incidents
    #   Green = all pass
    #   Yellow = everything else
    red_conditions = (not chain_unbroken) or (not ots_current) or (critical_unresolved > 0)
    if red_conditions:
        badge = "red"
    elif passed_count == total_checks:
        badge = "green"
    else:
        badge = "yellow"

    return {
        "badge": badge,
        "ready": badge == "green",
        "checks": checks,
        "blockers": blockers,
        "passed_count": passed_count,
        "total_checks": total_checks,
    }


def compute_audit_countdown(
    next_audit_date: Optional[date],
    today: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """Compute days remaining and urgency for next audit.

    Returns None if next_audit_date is not set.

    Urgency levels:
        overdue   — days_remaining < 0
        critical  — days_remaining <= 14
        urgent    — days_remaining <= 30
        normal    — days_remaining > 30
    """
    if next_audit_date is None:
        return None

    if today is None:
        today = date.today()

    days_remaining = (next_audit_date - today).days

    if days_remaining < 0:
        urgency = "overdue"
    elif days_remaining <= 14:
        urgency = "critical"
    elif days_remaining <= 30:
        urgency = "urgent"
    else:
        urgency = "normal"

    return {
        "next_audit_date": next_audit_date.isoformat(),
        "days_remaining": days_remaining,
        "urgency": urgency,
    }


# =============================================================================
# Pydantic Models
# =============================================================================

class AuditConfigUpdate(BaseModel):
    """Body for PUT /api/ops/audit-config/{org_id}."""
    baa_on_file: Optional[bool] = None
    next_audit_date: Optional[str] = Field(None, description="ISO date string (YYYY-MM-DD)")
    next_audit_notes: Optional[str] = Field(None, max_length=2000)


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/audit-readiness/{org_id}")
async def get_audit_readiness(org_id: int, user: dict = Depends(require_auth)):
    """Return audit readiness badge, checklist, and countdown for an org."""
    logger.info("Audit readiness queried for org_id=%s by user=%s", org_id, user.get("username", "unknown"))

    pool = await get_pool()

    try:
        # admin_transaction (wave-5): 6 admin reads must pin to one PgBouncer backend.
        async with admin_transaction(pool) as conn:
            # Verify org exists and get org-level fields
            org = await conn.fetchrow(
                "SELECT id, name, baa_on_file, next_audit_date FROM client_orgs WHERE id = $1",
                org_id,
            )
            if not org:
                raise HTTPException(status_code=404, detail="Organization not found")

            # Counsel-grade gate: "BAA on file" claim requires BOTH the
            # admin flag AND a formal (non-acknowledgment-only) signature.
            # See baa_status.is_baa_on_file_verified docstring + the
            # v1.0-INTERIM master BAA at docs/legal/MASTER_BAA_v1.0_INTERIM.md.
            # Relative-first-then-absolute (Task #72): production runs
            # `dashboard_api` as a package (cwd=/app); bare import would
            # raise ModuleNotFoundError + the outer try-except would
            # silently mask "BAA verified" state. Same class as the
            # 2026-05-13 dashboard outage (sites.py:4231 fix adb7671a).
            try:
                from .baa_status import is_baa_on_file_verified
            except ImportError:
                from baa_status import is_baa_on_file_verified  # type: ignore
            baa_verified = await is_baa_on_file_verified(conn, org_id)

            # Gather site IDs for this org.
            #
            # Session 203 audit fix: the column is `client_org_id`, not
            # `org_id`. Original code referenced `sites.org_id` which
            # doesn't exist in the schema — every call to this endpoint
            # was HTTP 500'ing in prod, meaning the literal "ready for
            # audit" badge on the Ops Center page never rendered.
            site_rows = await conn.fetch(
                "SELECT site_id FROM sites WHERE client_org_id = $1::uuid", org_id
            )
            site_ids = [r["site_id"] for r in site_rows]

            if not site_ids:
                # Org exists but has no sites — everything defaults to failing
                readiness = compute_audit_readiness(
                    chain_unbroken=False,
                    signing_rate=0.0,
                    ots_current=False,
                    critical_unresolved=0,
                    baa_on_file=baa_verified,
                    packet_downloadable=False,
                )
                countdown = compute_audit_countdown(org["next_audit_date"])
                return {"org_id": org_id, "org_name": org["name"], **readiness, "countdown": countdown}

            # Evidence metrics — signing rate + chain integrity.
            #
            # Session 203 audit fix: there is no `chain_valid` column on
            # compliance_bundles. Chain validity is derived at verification
            # time by walking the hash chain (see verify_chain_integrity).
            # As a cheap at-rest proxy we treat "every bundle is signed" as
            # "chain is intact" — if a bundle was tampered, its signature
            # would fail re-verification and `signature_valid` would be
            # false. Zero bundles or any failed signature → chain unbroken
            # reports false, which is the intent of the original query.
            evidence = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE signature_valid = true) AS signed,
                    COUNT(*) FILTER (WHERE signature_valid = false) AS failed_signatures
                FROM compliance_bundles
                WHERE site_id = ANY($1::text[])
            """, site_ids)

            total_bundles = evidence["total"] or 0
            signed_bundles = evidence["signed"] or 0
            failed_signatures = evidence["failed_signatures"] or 0
            chain_unbroken = (
                total_bundles > 0 and failed_signatures == 0 and signed_bundles > 0
            )
            signing_rate = (signed_bundles / total_bundles * 100.0) if total_bundles > 0 else 0.0

            # OTS health — most recent anchoring within 24h.
            #
            # Session 203 audit fix: the join used `compliance_bundles.id`
            # (uuid) against `ots_proofs.bundle_id` (varchar), a cross-type
            # comparison that errors at runtime. The correct join key is
            # `compliance_bundles.bundle_id` (varchar) which matches the
            # format of `ots_proofs.bundle_id`.
            ots_row = await conn.fetchrow("""
                SELECT MAX(anchored_at) AS last_anchor
                FROM ots_proofs
                WHERE status = 'anchored'
                  AND bundle_id IN (
                      SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1::text[])
                  )
            """, site_ids)
            last_anchor = ots_row["last_anchor"] if ots_row else None
            if last_anchor is not None:
                now_utc = datetime.now(timezone.utc)
                ots_current = (now_utc - last_anchor).total_seconds() < 86400
            else:
                ots_current = False

            # Critical incidents
            crit_row = await conn.fetchrow("""
                SELECT COUNT(*) AS cnt
                FROM incidents
                WHERE site_id = ANY($1::text[])
                  AND status = 'open'
                  AND severity = 'critical'
            """, site_ids)
            critical_unresolved = crit_row["cnt"] or 0

            # Packet downloadable — check if any packet exists for this org.
            #
            # Session 203 audit fix: `compliance_packets` has no `org_id`
            # column (verified via information_schema) — it's keyed on
            # `site_id`, `month`, `year`, `framework`. An org-level check
            # is therefore "any packet for any site owned by this org".
            packet_row = await conn.fetchrow("""
                SELECT EXISTS(
                    SELECT 1 FROM compliance_packets
                    WHERE site_id = ANY($1::text[])
                ) AS has_packet
            """, site_ids)
            packet_downloadable = bool(packet_row["has_packet"]) if packet_row else False

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Audit readiness query failed for org_id=%s: %s", org_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error computing audit readiness")

    readiness = compute_audit_readiness(
        chain_unbroken=chain_unbroken,
        signing_rate=signing_rate,
        ots_current=ots_current,
        critical_unresolved=critical_unresolved,
        baa_on_file=baa_verified,
        packet_downloadable=packet_downloadable,
    )
    countdown = compute_audit_countdown(org["next_audit_date"])

    return {
        "org_id": org_id,
        "org_name": org["name"],
        **readiness,
        "countdown": countdown,
    }


@router.put("/audit-config/{org_id}")
async def update_audit_config(org_id: int, body: AuditConfigUpdate, user: dict = Depends(require_auth)):
    """Update BAA status and/or next audit date for an org."""
    # Validate at least one field is provided
    if body.baa_on_file is None and body.next_audit_date is None and body.next_audit_notes is None:
        raise HTTPException(status_code=400, detail="No valid fields provided. Supply baa_on_file, next_audit_date, or next_audit_notes.")

    # Parse date if provided
    parsed_date: Optional[date] = None
    if body.next_audit_date is not None:
        try:
            parsed_date = date.fromisoformat(body.next_audit_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="next_audit_date must be ISO format (YYYY-MM-DD)")

    pool = await get_pool()

    try:
        # admin_transaction (wave-27): update_audit_config issues 2
        # admin statements (org check, UPSERT config).
        async with admin_transaction(pool) as conn:
            # Verify org exists
            org = await conn.fetchrow("SELECT id FROM client_orgs WHERE id = $1", org_id)
            if not org:
                raise HTTPException(status_code=404, detail="Organization not found")

            # Build dynamic SET clause
            sets: List[str] = []
            params: List[Any] = []
            idx = 2  # $1 is org_id

            if body.baa_on_file is not None:
                sets.append(f"baa_on_file = ${idx}")
                params.append(body.baa_on_file)
                idx += 1
                if body.baa_on_file:
                    sets.append(f"baa_uploaded_at = ${idx}")
                    params.append(datetime.now(timezone.utc))
                    idx += 1

            if parsed_date is not None:
                sets.append(f"next_audit_date = ${idx}")
                params.append(parsed_date)
                idx += 1

            if body.next_audit_notes is not None:
                sets.append(f"next_audit_notes = ${idx}")
                params.append(body.next_audit_notes)
                idx += 1

            query = f"UPDATE client_orgs SET {', '.join(sets)} WHERE id = $1"
            await conn.execute(query, org_id, *params)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Audit config update failed for org_id=%s: %s", org_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error updating audit config")

    logger.info(
        "Audit config updated for org_id=%s by user=%s: baa=%s, date=%s, notes=%s",
        org_id,
        user.get("username", "unknown"),
        body.baa_on_file,
        body.next_audit_date,
        "yes" if body.next_audit_notes else "no",
    )

    return {"status": "updated", "org_id": org_id}
