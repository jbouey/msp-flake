"""F6 phase 2 — read-only eligibility query helpers (Session 214).

Pure functions that compute "what would be eligible at tier X" for a
given scope, using calibrated thresholds from `flywheel_eligibility_tiers`
when available. NEVER WRITES — these are diagnostic-only helpers
intended for the F7 endpoint and future calibration analysis. The
actual enforcement path (UPDATE aggregated_pattern_stats SET
promotion_eligible=TRUE) lives in main.py Step 2 and is gated behind
the triple-switch (env flag + tier.enabled + calibrated_at).

CROSS-ORG HIPAA POSTURE:
  * Tier 0 (local): scope is `site_id` only. No cross-org concern.
  * Tier 1 (org): aggregates ACROSS sites WITHIN the same
    client_org_id. The JOIN to `sites.client_org_id` is the critical
    boundary; a bug here would aggregate cross-org → privacy breach
    class. The function takes `client_org_id` as parameter and
    filters explicitly. NEVER call without a scoped org.
  * Tier 2 (platform): aggregates cross-org via the existing
    `platform_pattern_stats` table (mig 058+ aggregator already
    crosses orgs by design — that's what makes the tier valuable).
    The HIPAA boundary at this tier is on ROLLOUT, not aggregation.
    Phase 2 enforcement decisions for tier 2 are explicitly deferred
    pending HIPAA round-table.

These query helpers DO NOT make rollout decisions. They report
eligibility counts/lists. The F7 diagnostic endpoint surfaces them
for operator visibility; they have no other consumer in this slice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import asyncpg


@dataclass(frozen=True)
class TierThresholds:
    """Threshold bundle for one tier. Mirrors columns in
    flywheel_eligibility_tiers. None values mean the tier hasn't
    been calibrated for that dimension; query helpers SKIP the
    tier rather than apply a placeholder."""
    tier_name: str
    tier_level: int
    min_total_occurrences: int
    min_success_rate: float
    min_l2_resolutions: int
    max_age_days: int
    min_distinct_orgs: Optional[int]
    min_distinct_sites: Optional[int]
    enabled: bool
    calibrated: bool


async def load_tier(conn: asyncpg.Connection, tier_name: str) -> Optional[TierThresholds]:
    """Read one tier's row. Returns None if not present."""
    row = await conn.fetchrow(
        """
        SELECT tier_name, tier_level, min_total_occurrences,
               min_success_rate, min_l2_resolutions, max_age_days,
               min_distinct_orgs, min_distinct_sites,
               enabled, (calibrated_at IS NOT NULL) AS calibrated
          FROM flywheel_eligibility_tiers
         WHERE tier_name = $1
        """,
        tier_name,
    )
    if not row:
        return None
    return _row_to_thresholds(row)


def _row_to_thresholds(row: Any) -> TierThresholds:
    return TierThresholds(
        tier_name=row["tier_name"],
        tier_level=row["tier_level"],
        min_total_occurrences=row["min_total_occurrences"],
        min_success_rate=row["min_success_rate"],
        min_l2_resolutions=row["min_l2_resolutions"],
        max_age_days=row["max_age_days"],
        min_distinct_orgs=row["min_distinct_orgs"],
        min_distinct_sites=row["min_distinct_sites"],
        enabled=bool(row["enabled"]),
        calibrated=bool(row["calibrated"]),
    )


async def load_tiers(
    conn: asyncpg.Connection, tier_names: List[str]
) -> Dict[str, TierThresholds]:
    """Round-table P2-9: combined load — saves 2 round trips when
    compute_tier_resolution wants all three tiers."""
    rows = await conn.fetch(
        """
        SELECT tier_name, tier_level, min_total_occurrences,
               min_success_rate, min_l2_resolutions, max_age_days,
               min_distinct_orgs, min_distinct_sites,
               enabled, (calibrated_at IS NOT NULL) AS calibrated
          FROM flywheel_eligibility_tiers
         WHERE tier_name = ANY($1)
        """,
        tier_names,
    )
    return {r["tier_name"]: _row_to_thresholds(r) for r in rows}


async def count_tier_local_eligible(
    conn: asyncpg.Connection,
    site_id: str,
    thresholds: TierThresholds,
) -> int:
    """Tier 0 — count patterns in this site's aggregated_pattern_stats
    that meet the local thresholds. Does NOT update; pure read."""
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n
          FROM aggregated_pattern_stats
         WHERE site_id = $1
           AND total_occurrences >= $2
           AND success_rate >= $3
           AND l2_resolutions >= $4
           AND last_seen > NOW() - make_interval(days => $5)
        """,
        site_id,
        thresholds.min_total_occurrences,
        thresholds.min_success_rate,
        thresholds.min_l2_resolutions,
        thresholds.max_age_days,
    )
    return int(row["n"]) if row else 0


async def count_tier_org_eligible(
    conn: asyncpg.Connection,
    client_org_id: str,
    thresholds: TierThresholds,
) -> Optional[int]:
    """Tier 1 — count patterns that would be eligible aggregated
    ACROSS sites within `client_org_id`. Returns None if the tier's
    `min_distinct_sites` is uncalibrated (we can't run the query
    without that threshold).

    HIPAA boundary: the JOIN to `sites` is filtered by
    `client_org_id = $1` BEFORE aggregation. Cross-org leak would
    require the JOIN to skip this filter — verify in the SQL below.
    """
    if thresholds.min_distinct_sites is None:
        return None
    # Round-table 2026-04-30 P1-1: per-site freshness gate INSIDE the
    # CTE (before GROUP BY) so a stale 4-of-5-sites pattern doesn't
    # pass the org freshness check via one recently-active site.
    # Round-table P1-2: site_scope excludes 'inactive' sites — `sites`
    # uses status enum (pending/online/offline/inactive) not
    # deleted_at; 'inactive' is the soft-delete equivalent. 'pending'
    # sites have no telemetry yet so they wouldn't contribute rows
    # to aggregated_pattern_stats anyway; the filter is for honesty.
    row = await conn.fetchrow(
        """
        WITH site_scope AS (
            SELECT site_id
              FROM sites
             WHERE client_org_id = $1
               AND status != 'inactive'
        ),
        fresh_pattern_rows AS (
            SELECT aps.pattern_signature, aps.site_id,
                   aps.total_occurrences, aps.success_count,
                   aps.l2_resolutions, aps.last_seen
              FROM aggregated_pattern_stats aps
              JOIN site_scope ss ON ss.site_id = aps.site_id
             WHERE aps.last_seen > NOW() - make_interval(days => $5)
        ),
        org_aggregated AS (
            -- Sum raw counts and recompute the rate downstream;
            -- AVG(success_rate) would be wrong (Simpson's paradox).
            SELECT
                pattern_signature,
                COUNT(DISTINCT site_id) AS distinct_sites,
                SUM(total_occurrences) AS total_occurrences,
                SUM(success_count) AS success_count,
                SUM(l2_resolutions) AS l2_resolutions,
                MAX(last_seen) AS most_recent_seen
              FROM fresh_pattern_rows
             GROUP BY pattern_signature
        )
        SELECT COUNT(*) AS n
          FROM org_aggregated
         WHERE total_occurrences >= $2
           AND CASE WHEN total_occurrences > 0
                    THEN success_count::FLOAT / total_occurrences
                    ELSE 0
               END >= $3
           AND l2_resolutions >= $4
           AND distinct_sites >= $6
        """,
        client_org_id,
        thresholds.min_total_occurrences,
        thresholds.min_success_rate,
        thresholds.min_l2_resolutions,
        thresholds.max_age_days,
        thresholds.min_distinct_sites,
    )
    return int(row["n"]) if row else 0


async def count_tier_platform_eligible(
    conn: asyncpg.Connection,
    thresholds: TierThresholds,
) -> Optional[int]:
    """Tier 2 — count platform-aggregated patterns clearing tier 2
    thresholds. Reads from `platform_pattern_stats` (already
    aggregates cross-org by design). Returns None if either
    distinct threshold is uncalibrated.

    The HIPAA boundary at this tier is on ROLLOUT, not aggregation.
    `platform_pattern_stats` was designed to aggregate cross-org —
    that's the whole point of tier 2. Eligibility readout is
    informational; rollout decisions stay scoped per-site/per-org
    via the existing flywheel spine (mig 181).
    """
    if (
        thresholds.min_distinct_orgs is None
        or thresholds.min_distinct_sites is None
    ):
        return None
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n
          FROM platform_pattern_stats
         WHERE total_occurrences >= $1
           AND success_rate >= $2
           AND last_seen > NOW() - make_interval(days => $3)
           AND distinct_orgs >= $4
           AND distinct_sites >= $5
        """,
        thresholds.min_total_occurrences,
        thresholds.min_success_rate,
        thresholds.max_age_days,
        thresholds.min_distinct_orgs,
        thresholds.min_distinct_sites,
    )
    return int(row["n"]) if row else 0


async def compute_tier_resolution(
    conn: asyncpg.Connection,
    site_id: str,
    client_org_id: Optional[str],
    federation_env_enabled: bool,
) -> Dict[str, Any]:
    """Aggregate tier-eligibility breakdown for one site. Used by the
    F7 diagnostic endpoint. Read-only — no enforcement, no rollout,
    no UPDATE statements anywhere in the call graph.

    Returns a dict matching the F7 endpoint's TierResolution shape:
      * tier_local_active / tier_org_active / tier_platform_active —
        booleans indicating whether each tier is currently making
        decisions (env=on AND tier.enabled AND calibrated)
      * local_eligible / org_would_be_eligible / platform_would_be_eligible —
        counts using the tier's calibrated thresholds (or seed
        thresholds if not calibrated; None if the threshold dimension
        is uncalibrated and the query can't run safely)
      * notes — human-readable context strings

    The `would_be_eligible` framing is intentional: even when a tier
    is uncalibrated/disabled, we report what eligibility WOULD look
    like under the current threshold values. This is the operator-
    visibility surface the round-table called out as "calibration
    analysis without committing to enforcement."
    """
    # Round-table P2-5: notes is Dict[str,str] keyed by tier so
    # frontend can render the org-specific note next to the org count.
    notes: Dict[str, str] = {}
    # Round-table P2-9: single query for all three tiers.
    tiers = await load_tiers(conn, ["local", "org", "platform"])
    local_t = tiers.get("local")
    org_t = tiers.get("org")
    platform_t = tiers.get("platform")

    def _tier_active(t: Optional[TierThresholds]) -> bool:
        return bool(
            federation_env_enabled
            and t is not None
            and t.enabled
            and t.calibrated
        )

    # Tier 0 — always queryable since seed thresholds are valid
    local_count: Optional[int] = None
    if local_t is not None:
        local_count = await count_tier_local_eligible(conn, site_id, local_t)

    # Tier 1 — needs client_org_id AND calibrated min_distinct_sites
    org_count: Optional[int] = None
    if org_t is not None and client_org_id:
        org_count = await count_tier_org_eligible(conn, client_org_id, org_t)
        if org_count is None:
            notes["tier_org"] = (
                "skipped: min_distinct_sites is uncalibrated (NULL). "
                "Calibration migration must populate it."
            )
    elif org_t is not None and not client_org_id:
        notes["tier_org"] = (
            "skipped: site has no client_org_id (cannot aggregate "
            "without org scope)."
        )

    # Tier 2 — needs both distinct thresholds calibrated
    platform_count: Optional[int] = None
    if platform_t is not None:
        platform_count = await count_tier_platform_eligible(conn, platform_t)
        if platform_count is None:
            notes["tier_platform"] = (
                "skipped: min_distinct_orgs and/or min_distinct_sites "
                "uncalibrated (NULL). Calibration migration must populate both."
            )

    if not federation_env_enabled:
        notes["federation"] = (
            "env flag is OFF — tier_*_active values reflect this. "
            "Counts are still computed for informational visibility."
        )

    # Round-table P2-6: consistent "would_be" naming. Tier 0 is also
    # speculative until enabled+calibrated. The *_active booleans
    # disambiguate hypothetical vs live.
    #
    # Round-table P3 (Session 214 follow-up): expose the actual
    # threshold values used per tier so an operator can see WHY a
    # count came out the way it did without cross-referencing the
    # migration. Values reflect what's currently in
    # flywheel_eligibility_tiers (seed + any calibration applied).
    return {
        "local_would_be_eligible": local_count,
        "org_would_be_eligible": org_count,
        "platform_would_be_eligible": platform_count,
        "tier_local_active": _tier_active(local_t),
        "tier_org_active": _tier_active(org_t),
        "tier_platform_active": _tier_active(platform_t),
        "tier_local_calibrated": bool(local_t and local_t.calibrated),
        "tier_org_calibrated": bool(org_t and org_t.calibrated),
        "tier_platform_calibrated": bool(platform_t and platform_t.calibrated),
        "tier_local_thresholds": _thresholds_to_dict(local_t),
        "tier_org_thresholds": _thresholds_to_dict(org_t),
        "tier_platform_thresholds": _thresholds_to_dict(platform_t),
        "client_org_id": client_org_id,
        "notes": notes,
    }


def _thresholds_to_dict(t: Optional[TierThresholds]) -> Optional[Dict[str, Any]]:
    """Round-table P3: project a TierThresholds bundle to a JSON-
    serializable dict for the F7 endpoint. Returns None when the
    tier row doesn't exist (signals to the operator that the tier
    isn't even seeded). Distinguishes that from a tier whose
    distinct_* are NULL (calibration-pending)."""
    if t is None:
        return None
    return {
        "min_total_occurrences": t.min_total_occurrences,
        "min_success_rate": t.min_success_rate,
        "min_l2_resolutions": t.min_l2_resolutions,
        "max_age_days": t.max_age_days,
        "min_distinct_orgs": t.min_distinct_orgs,
        "min_distinct_sites": t.min_distinct_sites,
    }
