"""Canonical client-facing compliance score computation.

Round-table 2026-05-05 Stage 2
(.agent/plans/25-client-portal-data-display-roundtable-2026-05-05.md).

PROBLEM SOLVED: pre-Stage-2, three different client-portal endpoints
computed "compliance_score" three different ways with three different
windows + three different defaults:

  /api/client/dashboard          → 24h window, blends bundle 70% +
                                   agent 30%, fallback 100% if no data
  /api/client/sites/{id}/health  → last 50 bundles, per-category
                                   averaged (pass + 0.5*warn / total)
                                   then mean of categories
  /api/client/reports/current    → all-time latest-per-check,
                                   passed/total*100, 100% if 0/0

Customer saw 20.8% / 93% / 100.0% for the same org. **Stage 1 fixed
RLS so each surface gets data; Stage 2 makes them agree on the math.**

CANONICAL ALGORITHM:
  For each (site_id, check_type, hostname) triple, take the most
  recent check result. Aggregate pass/fail/warning counts. Score is
  passed / total * 100 when total > 0; otherwise None + status='no_data'.

  Why latest-per-check (not 24h moving avg):
    - Honest about staleness — a check that hasn't run in 30d still
      counts but its last_check_at shows that explicitly.
    - Doesn't drop a check that runs daily but happens to miss the
      24h window (e.g. nightly backups checked at 23:55Z).
    - Compliance is a state, not a moving average.
    - Aligns with Steve's chain-of-custody framing — every check has
      a single authoritative latest signed bundle.

PER-CATEGORY breakdown remains available on the per-site endpoint as
supplementary metadata (Encryption 100% / Firewall 100% / etc.) but the
HEADLINE number on every surface is the unified score.

AGENT COMPLIANCE is a separate sibling metric. Pre-Stage-2 the dashboard
blended bundle * 0.7 + agent * 0.3 to a single number — this masked
both signals (a 95% bundle score + 0% agent → 67% blended that means
"you can't tell"). Post-Stage-2, agent compliance is its own field
the frontend renders alongside the unified score.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ComplianceScore:
    """Canonical client-facing compliance score result.

    Always returned by `compute_compliance_score()` — the API endpoints
    that wrap it convert to dict via `to_response()` for JSON output.

    `overall_score` is None when source set is empty (no checks for any
    site) — never 100.0 (Maya P0 anti-fake-data). Frontend distinguishes
    null from 0.0 to render "—" / "Awaiting first scan".

    `status` decision tree:
      - no_data     → total = 0 (no checks at all)
      - partial     → total > 0 BUT some checks are stale (>7d old)
      - healthy     → total > 0 and all checks fresh
    """
    overall_score: Optional[float]
    status: str
    counts: Dict[str, int]                # passed, failed, warnings, total
    last_check_at: Optional[datetime]
    stale_check_count: int = 0
    by_site: List[Dict[str, Any]] = field(default_factory=list)
    window_description: str = "Latest result per (site, check_type, hostname)"

    def to_response(self) -> Dict[str, Any]:
        """Serialize for API response. Datetimes → ISO strings."""
        d = asdict(self)
        if self.last_check_at is not None:
            d["last_check_at"] = self.last_check_at.isoformat()
        for s in d["by_site"]:
            if s.get("last_check_at") is not None and not isinstance(
                s["last_check_at"], str
            ):
                s["last_check_at"] = s["last_check_at"].isoformat()
        return d


# Configurable: a check older than this is "stale" — we still count it
# but flag the site/org as 'partial' freshness so the customer knows.
STALE_THRESHOLD_DAYS = 7


async def compute_compliance_score(
    conn,
    site_ids: List[str],
    *,
    include_incidents: bool = False,
) -> ComplianceScore:
    """Canonical compliance score for an org or single site.

    Args:
        conn: asyncpg connection (already in the appropriate RLS
              context — admin / org / tenant — caller decides).
        site_ids: site ids to aggregate over. Empty list → no_data.
        include_incidents: when true, fold active incidents into
                           the failure count (matches the per-site
                           compliance-health endpoint shape).

    Returns:
        ComplianceScore — see class docstring.

    Implementation notes:
      - Uses DISTINCT ON to take the latest result per
        (site, check_type, hostname). This is the same dedup
        logic /api/client/reports/current already used and is now
        canonical.
      - The query is RLS-aware — caller must have set
        app.current_org or app.current_tenant before calling.
        compliance_bundles has both site-scoped and org-scoped
        policies (mig 278).
      - status='partial' triggers when ANY check is older than
        STALE_THRESHOLD_DAYS. The customer-facing copy explains
        "X checks haven't been run since <date>" so they can
        diagnose stale appliances themselves.
    """
    if not site_ids:
        return ComplianceScore(
            overall_score=None,
            status="no_data",
            counts={"passed": 0, "failed": 0, "warnings": 0, "total": 0},
            last_check_at=None,
            stale_check_count=0,
            by_site=[],
        )

    # Latest result per (site, check_type, hostname) across all bundles
    # the caller can see under their RLS context. Identical algorithm
    # to /api/client/reports/current pre-Stage-2; promoted to canonical.
    rows = await conn.fetch(
        """
        WITH unnested AS (
            SELECT
                cb.site_id,
                cb.checked_at,
                c->>'check'    AS check_type,
                c->>'status'   AS check_status,
                COALESCE(c->>'hostname', c->>'host', '') AS hostname
              FROM compliance_bundles cb,
                   jsonb_array_elements(cb.checks) AS c
             WHERE cb.site_id = ANY($1)
        ),
        latest AS (
            SELECT DISTINCT ON (site_id, check_type, hostname)
                site_id, check_type, check_status, hostname, checked_at
              FROM unnested
             ORDER BY site_id, check_type, hostname, checked_at DESC
        )
        SELECT site_id, check_status, checked_at FROM latest
        """,
        site_ids,
    )

    incident_rows: list = []
    if include_incidents:
        # Fold open incidents in as "fail" votes. This is the per-site
        # endpoint's existing shape — preserved for that surface.
        # Distinct (check_type, appliance) so device count drives the
        # weight, not raw alert volume.
        incident_rows = await conn.fetch(
            """
            SELECT i.check_type, COUNT(DISTINCT i.appliance_id) AS devices,
                   a.site_id
              FROM incidents i
              JOIN v_appliances_current a ON a.id = i.appliance_id
             WHERE a.site_id = ANY($1)
               AND i.resolved_at IS NULL
             GROUP BY i.check_type, a.site_id
            """,
            site_ids,
        )

    # Per-site aggregation
    per_site: Dict[str, Dict[str, Any]] = {
        sid: {
            "site_id": sid,
            "passed": 0,
            "failed": 0,
            "warnings": 0,
            "total": 0,
            "last_check_at": None,
            "stale_count": 0,
        }
        for sid in site_ids
    }

    now = datetime.now(timezone.utc)
    stale_threshold = STALE_THRESHOLD_DAYS

    total_passed = 0
    total_failed = 0
    total_warnings = 0
    overall_last: Optional[datetime] = None
    total_stale = 0

    for r in rows:
        sid = r["site_id"]
        if sid not in per_site:
            continue  # defensive: RLS may surface unexpected sites
        bucket = per_site[sid]
        status = (r["check_status"] or "").lower()
        bucket["total"] += 1
        if status in ("pass", "compliant"):
            bucket["passed"] += 1
            total_passed += 1
        elif status in ("fail", "non_compliant"):
            bucket["failed"] += 1
            total_failed += 1
        elif status in ("warn", "warning"):
            bucket["warnings"] += 1
            total_warnings += 1

        if r["checked_at"] is not None:
            ca = r["checked_at"]
            if bucket["last_check_at"] is None or ca > bucket["last_check_at"]:
                bucket["last_check_at"] = ca
            if overall_last is None or ca > overall_last:
                overall_last = ca
            age_days = (now - ca).total_seconds() / 86400.0
            if age_days > stale_threshold:
                bucket["stale_count"] += 1
                total_stale += 1

    for r in incident_rows:
        sid = r["site_id"]
        if sid not in per_site:
            continue
        bucket = per_site[sid]
        bucket["failed"] += r["devices"]
        bucket["total"] += r["devices"]
        total_failed += r["devices"]

    total = total_passed + total_failed + total_warnings

    if total == 0:
        return ComplianceScore(
            overall_score=None,
            status="no_data",
            counts={"passed": 0, "failed": 0, "warnings": 0, "total": 0},
            last_check_at=None,
            stale_check_count=0,
            by_site=[
                {
                    **bucket,
                    "score": None,
                    "status": "no_data",
                }
                for bucket in per_site.values()
            ],
        )

    overall_score = round(total_passed / total * 100, 1)

    # Status: partial if any check is stale, else healthy.
    overall_status = "partial" if total_stale > 0 else "healthy"

    by_site_serialized: List[Dict[str, Any]] = []
    for bucket in per_site.values():
        site_total = bucket["total"]
        if site_total == 0:
            site_score = None
            site_status = "no_data"
        else:
            site_score = round(bucket["passed"] / site_total * 100, 1)
            site_status = "partial" if bucket["stale_count"] > 0 else "healthy"
        by_site_serialized.append({
            "site_id": bucket["site_id"],
            "score": site_score,
            "status": site_status,
            "passed": bucket["passed"],
            "failed": bucket["failed"],
            "warnings": bucket["warnings"],
            "total": site_total,
            "last_check_at": bucket["last_check_at"],
            "stale_count": bucket["stale_count"],
        })

    return ComplianceScore(
        overall_score=overall_score,
        status=overall_status,
        counts={
            "passed": total_passed,
            "failed": total_failed,
            "warnings": total_warnings,
            "total": total,
        },
        last_check_at=overall_last,
        stale_check_count=total_stale,
        by_site=by_site_serialized,
    )
