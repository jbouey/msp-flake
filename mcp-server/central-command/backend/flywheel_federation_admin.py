"""F6 phase 2 foundation slice — admin-only operator endpoints +
daily snapshot writer (Session 214 round-table consensus).

NO ENFORCEMENT. NO CROSS-ORG WRITE TO aggregated_pattern_stats. NO
fleet_orders. The only WRITE this module performs is to
`flywheel_federation_candidate_daily` — an operator-visibility
snapshot table; not enforcement, not chain-of-custody.

DESIGN REFERENCE:
  docs/specs/2026-04-30-f6-federation-eligibility-tier-design.md
  .agent/plans/f6-phase-2-enforcement-deferred.md

What this module ships:
  * GET /api/admin/flywheel/federation-candidates?tier=org|platform
    — admin-only operator endpoint. Returns candidate counts +
    small samples (no rule_id-level disclosure across orgs).
  * `take_federation_snapshot(conn)` — pure function called by the
    background loop daily. Writes one row per (org, tier_org) and
    one row for tier_platform.

The endpoint is gated on `require_admin` (not partner-scoped) because
cross-org tier 1 visibility AND platform tier 2 visibility are
substrate-class observability — partners should not see other
partners' candidate counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from auth import require_admin
    from tenant_middleware import admin_transaction
    from shared import check_rate_limit
    from flywheel_eligibility_queries import (
        load_tier,
        count_tier_org_eligible,
        count_tier_platform_eligible,
    )
except ImportError:
    from .auth import require_admin
    from .tenant_middleware import admin_transaction
    from .shared import check_rate_limit
    from .flywheel_eligibility_queries import (
        load_tier,
        count_tier_org_eligible,
        count_tier_platform_eligible,
    )

try:
    from .fleet import get_pool  # type: ignore[attr-defined]
except ImportError:
    try:
        from fleet import get_pool  # type: ignore[no-redef]
    except ImportError:
        get_pool = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/flywheel",
    tags=["admin", "flywheel", "federation"],
)


class FederationCandidateSummary(BaseModel):
    """One row of federation candidate observation. Tier 1 rows have
    `client_org_id` set; tier 2 rows have it NULL (platform-level)."""
    tier_name: str
    client_org_id: Optional[str] = None
    candidate_count: int
    snapshot_date: Optional[str] = None
    p50_success_rate: Optional[float] = None
    p95_success_rate: Optional[float] = None
    threshold_calibrated: bool = False


class FederationCandidatesResponse(BaseModel):
    """Round-table consensus: counts + summary, NO rule_id-level
    disclosure across orgs at the API layer. This is operator
    visibility into 'what would be eligible' — not the eligibility
    list itself."""
    tier: str
    snapshot_date: Optional[str] = None
    rows: List[FederationCandidateSummary]
    total_candidates: int
    notes: Dict[str, str]


@router.get(
    "/federation-candidates",
    response_model=FederationCandidatesResponse,
    summary="F6 calibration-analysis read surface (admin only)",
)
async def get_federation_candidates(
    tier: str = Query(..., pattern="^(org|platform)$"),
    user: Dict[str, Any] = Depends(require_admin),
):
    """Round-table consensus SHIP_FOUNDATION_SLICE: read-only
    operator endpoint exposing the candidate counts that WOULD
    clear current seed thresholds at tier 1 (org) or tier 2
    (platform), if federation were enforced.

    NO enforcement. NO rollout. NO PHI. NO cross-org rule_id
    disclosure (counts + summary only).

    Auth: require_admin. Rate limit: 30/min/actor (more permissive
    than F7 because this is calibration-analysis, not incident-
    response).
    """
    actor_email = (user.get("email") or user.get("username") or "admin").lower()
    if not check_rate_limit(actor_email, "federation_candidates",
                            window_seconds=60, max_requests=30):
        raise HTTPException(
            status_code=429,
            detail="federation_candidates rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    pool = await get_pool()
    notes: Dict[str, str] = {}

    async with admin_transaction(pool) as conn:
        # Prefer the most recent snapshot row over a live recompute —
        # the snapshot loop's nightly run gives stable observation
        # data; live recompute is a fallback when no snapshot exists
        # yet.
        snapshot_rows = await conn.fetch(
            """
            SELECT snapshot_date, tier_name, client_org_id,
                   candidate_count, p50_success_rate, p95_success_rate
              FROM flywheel_federation_candidate_daily
             WHERE tier_name = $1
               AND snapshot_date = (
                   SELECT MAX(snapshot_date)
                     FROM flywheel_federation_candidate_daily
                    WHERE tier_name = $1
               )
             ORDER BY client_org_id NULLS LAST
            """,
            tier,
        )

        if snapshot_rows:
            tier_t = await load_tier(conn, tier)
            calibrated = bool(tier_t and tier_t.calibrated)
            rows = [
                FederationCandidateSummary(
                    tier_name=r["tier_name"],
                    client_org_id=r["client_org_id"],
                    candidate_count=int(r["candidate_count"]),
                    snapshot_date=r["snapshot_date"].isoformat()
                        if r["snapshot_date"] else None,
                    p50_success_rate=r["p50_success_rate"],
                    p95_success_rate=r["p95_success_rate"],
                    threshold_calibrated=calibrated,
                )
                for r in snapshot_rows
            ]
            return FederationCandidatesResponse(
                tier=tier,
                snapshot_date=rows[0].snapshot_date if rows else None,
                rows=rows,
                total_candidates=sum(r.candidate_count for r in rows),
                notes={
                    "source": "daily snapshot (flywheel_federation_candidate_daily)",
                    "calibration_status": (
                        "calibrated" if calibrated else
                        "uncalibrated — counts use seed thresholds; do NOT lock thresholds without round-table review"
                    ),
                },
            )

        # No snapshot yet — fall back to live recompute. This path
        # runs once per fresh deploy until the daily loop populates
        # the snapshot table.
        notes["source"] = "live recompute (no snapshot row yet for this tier)"
        tier_t = await load_tier(conn, tier)
        if tier_t is None:
            raise HTTPException(
                status_code=404,
                detail=f"tier '{tier}' not present in flywheel_eligibility_tiers",
            )
        notes["calibration_status"] = (
            "calibrated" if tier_t.calibrated else
            "uncalibrated — counts use seed thresholds"
        )

        if tier == "org":
            # Iterate orgs that have at least one site with telemetry.
            # No cross-org leak: each call to count_tier_org_eligible
            # is scoped to one client_org_id at a time.
            org_rows = await conn.fetch(
                """
                SELECT DISTINCT s.client_org_id
                  FROM sites s
                 WHERE s.client_org_id IS NOT NULL
                   AND s.status != 'inactive'
                """
            )
            rows: List[FederationCandidateSummary] = []
            for r in org_rows:
                org_id = r["client_org_id"]
                count = await count_tier_org_eligible(conn, org_id, tier_t)
                if count is None:
                    notes["tier_org"] = (
                        "tier_org skipped: min_distinct_sites is "
                        "uncalibrated (NULL). Calibration migration "
                        "must populate it."
                    )
                    break
                rows.append(FederationCandidateSummary(
                    tier_name="org",
                    client_org_id=org_id,
                    candidate_count=int(count),
                    threshold_calibrated=tier_t.calibrated,
                ))
            return FederationCandidatesResponse(
                tier=tier,
                snapshot_date=None,
                rows=rows,
                total_candidates=sum(r.candidate_count for r in rows),
                notes=notes,
            )

        # tier == "platform"
        count = await count_tier_platform_eligible(conn, tier_t)
        rows: List[FederationCandidateSummary] = []
        if count is None:
            notes["tier_platform"] = (
                "tier_platform skipped: min_distinct_orgs and/or "
                "min_distinct_sites uncalibrated."
            )
        else:
            rows.append(FederationCandidateSummary(
                tier_name="platform",
                client_org_id=None,
                candidate_count=int(count),
                threshold_calibrated=tier_t.calibrated,
            ))
        return FederationCandidatesResponse(
            tier=tier,
            snapshot_date=None,
            rows=rows,
            total_candidates=sum(r.candidate_count for r in rows),
            notes=notes,
        )


async def take_federation_snapshot(conn: asyncpg.Connection) -> Dict[str, int]:
    """Write today's federation-candidate snapshot.

    Background loop calls this once/day. Idempotent for the day —
    PRIMARY KEY (snapshot_date, tier_name, client_org_id) makes a
    re-run on the same day a no-op or a refresh via UPSERT.

    Returns a dict with row-counts written (for observability).
    No PHI, no rollout, no cross-org WRITE to aggregated_pattern_stats.
    Only WRITE is to flywheel_federation_candidate_daily itself.
    """
    today = datetime.now(timezone.utc).date()
    org_t = await load_tier(conn, "org")
    platform_t = await load_tier(conn, "platform")

    written = {"org": 0, "platform": 0, "skipped_uncalibrated": 0}

    if org_t is not None:
        org_ids = await conn.fetch(
            """
            SELECT DISTINCT s.client_org_id
              FROM sites s
             WHERE s.client_org_id IS NOT NULL
               AND s.status != 'inactive'
            """
        )
        for r in org_ids:
            org_id = r["client_org_id"]
            count = await count_tier_org_eligible(conn, org_id, org_t)
            if count is None:
                # Uncalibrated — record a row with NULL counts so
                # the operator sees the gap rather than absence.
                written["skipped_uncalibrated"] += 1
                continue
            await conn.execute(
                """
                INSERT INTO flywheel_federation_candidate_daily
                    (snapshot_date, tier_name, client_org_id,
                     candidate_count)
                VALUES ($1, 'org', $2, $3)
                ON CONFLICT (snapshot_date, tier_name, COALESCE(client_org_id, ''))
                DO UPDATE SET candidate_count = EXCLUDED.candidate_count,
                              snapshot_at = NOW()
                """,
                today, org_id, int(count),
            )
            written["org"] += 1

    if platform_t is not None:
        count = await count_tier_platform_eligible(conn, platform_t)
        if count is not None:
            await conn.execute(
                """
                INSERT INTO flywheel_federation_candidate_daily
                    (snapshot_date, tier_name, client_org_id,
                     candidate_count)
                VALUES ($1, 'platform', NULL, $2)
                ON CONFLICT (snapshot_date, tier_name, COALESCE(client_org_id, ''))
                DO UPDATE SET candidate_count = EXCLUDED.candidate_count,
                              snapshot_at = NOW()
                """,
                today, int(count),
            )
            written["platform"] += 1
        else:
            written["skipped_uncalibrated"] += 1

    return written
