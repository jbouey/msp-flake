"""Ops Center Health endpoints.

Provides two endpoints:
  GET /api/ops/health         — admin-only platform-wide subsystem health
  GET /api/ops/health/{org_id} — partner-scoped subsystem health for an org

Each subsystem returns a traffic-light status (green/yellow/red) with
supporting metrics.  The five subsystems are:

  1. Evidence   — bundle submission flow
  2. Signing    — cryptographic signing coverage
  3. OTS        — OpenTimestamps anchoring pipeline
  4. Healing    — L1/L2/L3 auto-healing pipeline
  5. Fleet      — appliance connectivity
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_auth
from .fleet import get_pool
from .partners import require_partner_role
from .tenant_middleware import admin_connection, admin_transaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["ops"])

# =============================================================================
# THRESHOLD CONSTANTS
# =============================================================================

# Evidence thresholds (minutes since last submission)
EVIDENCE_YELLOW_MINUTES = 30
EVIDENCE_RED_MINUTES = 60

# Signing thresholds (percentage)
SIGNING_GREEN_PCT = 90.0
SIGNING_YELLOW_PCT = 70.0

# OTS thresholds
OTS_YELLOW_PENDING = 100
OTS_RED_PENDING = 500
OTS_YELLOW_BATCH_HOURS = 2.0
OTS_RED_BATCH_HOURS = 6.0

# Healing thresholds
HEALING_GREEN_PCT = 90.0
HEALING_YELLOW_PCT = 70.0
HEALING_YELLOW_EXHAUSTED = 5
HEALING_RED_EXHAUSTED = 10

# Fleet thresholds (minutes offline)
FLEET_YELLOW_OFFLINE_MINUTES = 30
FLEET_RED_OFFLINE_MINUTES = 120


# =============================================================================
# PURE COMPUTATION FUNCTIONS
# =============================================================================

def compute_evidence_status(
    total_bundles: int,
    last_submission_minutes_ago: Optional[float],
    chain_gaps: int,
    signing_rate: float,
) -> Dict[str, Any]:
    """Compute evidence subsystem traffic-light status.

    Args:
        total_bundles: Total evidence bundles in window.
        last_submission_minutes_ago: Minutes since the most recent bundle.
            None means no bundles exist.
        chain_gaps: Number of gaps detected in the evidence chain.
        signing_rate: Percentage of bundles with valid signatures (0-100).

    Returns:
        Dict with status (green/yellow/red), label, and metrics.
    """
    status = "green"
    label = "Evidence pipeline healthy"

    if total_bundles == 0 or last_submission_minutes_ago is None:
        status = "red"
        label = "No evidence bundles found"
    elif chain_gaps > 0:
        status = "red"
        label = f"{chain_gaps} chain gap(s) detected"
    elif last_submission_minutes_ago > EVIDENCE_RED_MINUTES:
        status = "red"
        label = f"Last submission {last_submission_minutes_ago:.0f}m ago"
    elif last_submission_minutes_ago > EVIDENCE_YELLOW_MINUTES:
        status = "yellow"
        label = f"Last submission {last_submission_minutes_ago:.0f}m ago"

    return {
        "status": status,
        "label": label,
        "total_bundles": total_bundles,
        "last_submission_minutes_ago": last_submission_minutes_ago,
        "chain_gaps": chain_gaps,
        "signing_rate": signing_rate,
    }


def compute_signing_status(
    signing_rate: float,
    key_mismatches_24h: int,
    unsigned_legacy: int,
    signature_failures: int,
) -> Dict[str, Any]:
    """Compute signing subsystem traffic-light status.

    Args:
        signing_rate: Percentage of bundles with valid signatures (0-100).
        key_mismatches_24h: Key mismatch events in the last 24 hours.
        unsigned_legacy: Bundles without any signature (pre-signing era).
        signature_failures: Bundles where signature verification failed.

    Returns:
        Dict with status, label, and metrics.
    """
    status = "green"
    label = "Signing coverage healthy"

    if key_mismatches_24h > 0:
        status = "red"
        label = f"{key_mismatches_24h} key mismatch(es) in 24h"
    elif signing_rate < SIGNING_YELLOW_PCT:
        status = "red"
        label = f"Signing rate {signing_rate:.1f}% below threshold"
    elif signing_rate < SIGNING_GREEN_PCT:
        status = "yellow"
        label = f"Signing rate {signing_rate:.1f}%"

    return {
        "status": status,
        "label": label,
        "signing_rate": signing_rate,
        "key_mismatches_24h": key_mismatches_24h,
        "unsigned_legacy": unsigned_legacy,
        "signature_failures": signature_failures,
    }


def compute_ots_status(
    anchored: int,
    pending: int,
    batching: int,
    latest_batch_age_hours: Optional[float],
) -> Dict[str, Any]:
    """Compute OpenTimestamps subsystem traffic-light status.

    Args:
        anchored: Number of proofs fully anchored to Bitcoin.
        pending: Number of proofs awaiting anchoring.
        batching: Number of proofs in the current batch.
        latest_batch_age_hours: Hours since the latest batch was created.
            None means no batches exist.

    Returns:
        Dict with status, label, and metrics.
    """
    status = "green"
    label = "OTS anchoring healthy"

    batch_age = latest_batch_age_hours or 0.0

    if pending > OTS_RED_PENDING:
        status = "red"
        label = f"{pending} proofs pending (>{OTS_RED_PENDING})"
    elif batch_age > OTS_RED_BATCH_HOURS:
        status = "red"
        label = f"Latest batch {batch_age:.1f}h old"
    elif pending > OTS_YELLOW_PENDING:
        status = "yellow"
        label = f"{pending} proofs pending"
    elif batch_age > OTS_YELLOW_BATCH_HOURS:
        status = "yellow"
        label = f"Latest batch {batch_age:.1f}h old"

    return {
        "status": status,
        "label": label,
        "anchored": anchored,
        "pending": pending,
        "batching": batching,
        "latest_batch_age_hours": latest_batch_age_hours,
    }


def compute_healing_status(
    l1_heal_rate: float,
    exhausted_count: int,
    stuck_count: int,
) -> Dict[str, Any]:
    """Compute healing pipeline traffic-light status.

    Args:
        l1_heal_rate: Percentage of incidents auto-healed at L1 (0-100).
        exhausted_count: Incidents that exhausted all remediation attempts.
        stuck_count: Incidents stuck (unresolved > 24h, not escalated).

    Returns:
        Dict with status, label, and metrics.
    """
    status = "green"
    label = "Healing pipeline healthy"

    if l1_heal_rate < HEALING_YELLOW_PCT:
        status = "red"
        label = f"L1 heal rate {l1_heal_rate:.1f}% below threshold"
    elif exhausted_count > HEALING_RED_EXHAUSTED:
        status = "red"
        label = f"{exhausted_count} incidents exhausted remediation"
    elif l1_heal_rate < HEALING_GREEN_PCT:
        status = "yellow"
        label = f"L1 heal rate {l1_heal_rate:.1f}%"
    elif exhausted_count > HEALING_YELLOW_EXHAUSTED:
        status = "yellow"
        label = f"{exhausted_count} incidents exhausted remediation"

    return {
        "status": status,
        "label": label,
        "l1_heal_rate": l1_heal_rate,
        "exhausted_count": exhausted_count,
        "stuck_count": stuck_count,
    }


def compute_fleet_status(
    total_appliances: int,
    online_count: int,
    max_offline_minutes: Optional[float],
) -> Dict[str, Any]:
    """Compute fleet connectivity traffic-light status.

    Args:
        total_appliances: Total registered appliances.
        online_count: Appliances with a recent checkin.
        max_offline_minutes: Longest time any single appliance has been
            offline, in minutes.  None means all are online or none exist.

    Returns:
        Dict with status, label, and metrics.
    """
    status = "green"
    label = "Fleet connectivity healthy"

    offline_count = total_appliances - online_count

    if total_appliances == 0:
        status = "yellow"
        label = "No appliances registered"
    elif max_offline_minutes is not None and max_offline_minutes > FLEET_RED_OFFLINE_MINUTES:
        status = "red"
        label = f"{offline_count} appliance(s) offline, worst {max_offline_minutes:.0f}m"
    elif max_offline_minutes is not None and max_offline_minutes > FLEET_YELLOW_OFFLINE_MINUTES:
        status = "yellow"
        label = f"{offline_count} appliance(s) offline, worst {max_offline_minutes:.0f}m"

    return {
        "status": status,
        "label": label,
        "total_appliances": total_appliances,
        "online_count": online_count,
        "offline_count": offline_count,
        "max_offline_minutes": max_offline_minutes,
    }


# =============================================================================
# DATABASE QUERY HELPERS
# =============================================================================

async def _query_evidence_metrics(
    conn,
    site_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Query evidence bundle metrics from compliance_bundles.

    Returns raw numbers for compute_evidence_status / compute_signing_status.
    """
    where = "WHERE checked_at > NOW() - INTERVAL '30 days'"
    args: list = []
    if site_filter:
        where += " AND site_id = ANY($1)"
        args.append(site_filter)

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) AS total_bundles,
            EXTRACT(EPOCH FROM (NOW() - MAX(checked_at))) / 60.0
                AS last_submission_minutes_ago,
            COUNT(*) FILTER (WHERE agent_signature IS NOT NULL) AS signed,
            COUNT(*) FILTER (WHERE signature_valid = true) AS verified,
            COUNT(*) FILTER (WHERE signature_valid = false
                             AND agent_signature IS NOT NULL) AS sig_failures
        FROM compliance_bundles
        {where}
    """, *args)

    total = row["total_bundles"] or 0
    signed = row["signed"] or 0
    verified = row["verified"] or 0
    sig_failures = row["sig_failures"] or 0
    signing_rate = (verified / total * 100.0) if total > 0 else 100.0
    last_min = float(row["last_submission_minutes_ago"]) if row["last_submission_minutes_ago"] is not None else None
    unsigned_legacy = total - signed

    # Chain gaps: bundles where chain_position has discontinuities
    gap_row = await conn.fetchrow(f"""
        SELECT COUNT(*) AS gaps
        FROM (
            SELECT chain_position,
                   LAG(chain_position) OVER (ORDER BY chain_position) AS prev
            FROM compliance_bundles
            {where}
              AND chain_position IS NOT NULL
        ) sub
        WHERE chain_position - prev > 1
    """, *args)
    chain_gaps = gap_row["gaps"] or 0

    # Key mismatches: signature present but verification failed in last 24h
    mm_where = "WHERE checked_at > NOW() - INTERVAL '24 hours'"
    mm_args: list = []
    if site_filter:
        mm_where += " AND site_id = ANY($1)"
        mm_args.append(site_filter)

    mm_row = await conn.fetchrow(f"""
        SELECT COUNT(*) AS mismatches
        FROM compliance_bundles
        {mm_where}
          AND agent_signature IS NOT NULL
          AND signature_valid = false
    """, *mm_args)
    key_mismatches = mm_row["mismatches"] or 0

    return {
        "total_bundles": total,
        "last_submission_minutes_ago": last_min,
        "chain_gaps": chain_gaps,
        "signing_rate": signing_rate,
        "key_mismatches_24h": key_mismatches,
        "unsigned_legacy": unsigned_legacy,
        "signature_failures": sig_failures,
    }


async def _query_ots_metrics(conn) -> Dict[str, Any]:
    """Query OpenTimestamps proof metrics from ots_proofs."""
    row = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'anchored') AS anchored,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE status = 'batching') AS batching,
            EXTRACT(EPOCH FROM (NOW() - MAX(submitted_at))) / 3600.0
                AS latest_batch_age_hours
        FROM ots_proofs
    """)

    return {
        "anchored": row["anchored"] or 0,
        "pending": row["pending"] or 0,
        "batching": row["batching"] or 0,
        "latest_batch_age_hours": float(row["latest_batch_age_hours"]) if row["latest_batch_age_hours"] is not None else None,
    }


async def _query_healing_metrics(
    conn,
    site_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Query healing pipeline metrics from incidents (30-day window)."""
    where = "WHERE reported_at > NOW() - INTERVAL '30 days'"
    args: list = []
    if site_filter:
        where += " AND site_id = ANY($1)"
        args.append(site_filter)

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE resolution_tier = 'L1'
                             AND status = 'resolved') AS l1_resolved,
            COUNT(*) FILTER (WHERE remediation_exhausted = true) AS exhausted,
            COUNT(*) FILTER (
                WHERE status != 'resolved'
                  AND resolution_tier IS NULL
                  AND reported_at < NOW() - INTERVAL '24 hours'
            ) AS stuck
        FROM incidents
        {where}
    """, *args)

    total = row["total"] or 0
    l1_resolved = row["l1_resolved"] or 0
    l1_rate = (l1_resolved / total * 100.0) if total > 0 else 100.0

    return {
        "l1_heal_rate": l1_rate,
        "exhausted_count": row["exhausted"] or 0,
        "stuck_count": row["stuck"] or 0,
    }


async def _query_fleet_metrics(
    conn,
    site_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Query operator-only fleet connectivity metrics.

    Soft-deleted rows are intentionally included so 'max_offline_minutes'
    can surface decommissioned appliances that are still calling home
    out-of-band (orphan-recovery signal). Counted in 'total' but
    contribute to 'offline' bucket via stale last_checkin.
    """
    where = ""
    args: list = []
    if site_filter:
        where = "WHERE site_id = ANY($1)"
        args.append(site_filter)

    row = await conn.fetchrow(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE last_checkin > NOW() - INTERVAL '15 minutes'
            ) AS online,
            EXTRACT(EPOCH FROM (
                NOW() - MIN(last_checkin) FILTER (
                    WHERE last_checkin <= NOW() - INTERVAL '15 minutes'
                )
            )) / 60.0 AS max_offline_minutes
        FROM site_appliances  -- noqa: site-appliances-deleted-include — operator-only fleet rollup; soft-deleted included to detect decommissioned-but-still-checking-in appliances
        {where}
    """, *args)

    return {
        "total_appliances": row["total"] or 0,
        "online_count": row["online"] or 0,
        "max_offline_minutes": float(row["max_offline_minutes"]) if row["max_offline_minutes"] is not None else None,
    }


# =============================================================================
# HELPER: assemble all 5 statuses
# =============================================================================

async def _build_health_response(
    conn,
    site_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Query all subsystems and compute traffic-light statuses."""
    ev = await _query_evidence_metrics(conn, site_filter=site_filter)
    ots = await _query_ots_metrics(conn)
    heal = await _query_healing_metrics(conn, site_filter=site_filter)
    fleet = await _query_fleet_metrics(conn, site_filter=site_filter)

    evidence = compute_evidence_status(
        total_bundles=ev["total_bundles"],
        last_submission_minutes_ago=ev["last_submission_minutes_ago"],
        chain_gaps=ev["chain_gaps"],
        signing_rate=ev["signing_rate"],
    )
    signing = compute_signing_status(
        signing_rate=ev["signing_rate"],
        key_mismatches_24h=ev["key_mismatches_24h"],
        unsigned_legacy=ev["unsigned_legacy"],
        signature_failures=ev["signature_failures"],
    )
    ots_status = compute_ots_status(**ots)
    healing = compute_healing_status(**heal)
    fleet_status = compute_fleet_status(**fleet)

    return {
        "evidence": evidence,
        "signing": signing,
        "ots": ots_status,
        "healing": healing,
        "fleet": fleet_status,
    }


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/health")
async def ops_health(user: dict = Depends(require_auth)):
    """Platform-wide ops health — admin only."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        result = await _build_health_response(conn)

    logger.info(
        "ops_health queried by %s — evidence=%s signing=%s ots=%s healing=%s fleet=%s",
        user.get("username", "unknown"),
        result["evidence"]["status"],
        result["signing"]["status"],
        result["ots"]["status"],
        result["healing"]["status"],
        result["fleet"]["status"],
    )
    return result


@router.get("/health/{org_id}")
async def ops_health_org(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Org-scoped ops health — partner role required."""
    pool = await get_pool()

    # admin_transaction (wave-44): ops_health_org issues 2 admin
    # reads (org ownership verify, ops-health metrics).
    async with admin_transaction(pool) as conn:
        # Verify the org belongs to this partner
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id,
            partner["id"],
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get site_ids for this org
        site_rows = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1",
            org_id,
        )
        site_ids = [r["site_id"] for r in site_rows]

        if not site_ids:
            raise HTTPException(status_code=404, detail="No sites in organization")

        result = await _build_health_response(conn, site_filter=site_ids)

    logger.info(
        "ops_health_org queried for org=%s by partner=%s — evidence=%s healing=%s fleet=%s",
        org_id,
        partner.get("id", "unknown"),
        result["evidence"]["status"],
        result["healing"]["status"],
        result["fleet"]["status"],
    )
    return result
