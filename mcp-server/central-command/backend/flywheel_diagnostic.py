"""GET /api/admin/sites/{site_id}/flywheel-diagnostic — operator
diagnostic endpoint for the flywheel system.

Session 213 F7 P3 from 2026-04-29 round-table follow-up. Aggregates
the four flywheel signals into a single panel:

  1. Canonical aliasing — does this site have inbound or outbound
     site_canonical_mapping rows? (mig 256)

  2. Operational health — live appliance count, evidence chain
     activity, orphan classification.

  3. Telemetry recency — execution_telemetry / incidents / l2_decisions
     row counts in the last 24h, plus the orphan-telemetry signal
     (mig 258 / flywheel_orphan_telemetry invariant).

  4. Flywheel state — promotion candidates, promoted_rules by
     lifecycle_state (proposed / shadow / approved / rolling_out /
     active / regime_warning / auto_disabled / graduated / retired),
     ledger event counts.

  5. Substrate signals — open substrate_violations rows for this
     site_id (any severity).

Auth: require_admin. Per-site lookup; returns 404 if site doesn't
exist (canonicalizes input through canonical_site_id() so an
operator passing the OLD orphan site_id gets the new diagnostic).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from auth import require_admin
    from tenant_middleware import admin_transaction
    from shared import check_rate_limit, parse_bool_env
    from flywheel_eligibility_queries import compute_tier_resolution
except ImportError:
    from .auth import require_admin
    from .tenant_middleware import admin_transaction
    from .shared import check_rate_limit, parse_bool_env
    from .flywheel_eligibility_queries import compute_tier_resolution

# `fleet` has its own relative imports and only loads cleanly under the
# packaged runtime (production). Tests don't exercise the DB path
# without a fixture override.
try:
    from .fleet import get_pool  # type: ignore[attr-defined]
except ImportError:
    try:
        from fleet import get_pool  # type: ignore[no-redef]
    except ImportError:
        get_pool = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/sites", tags=["admin", "flywheel"])


class CanonicalAliasing(BaseModel):
    """Site's place in the site_canonical_mapping graph."""
    canonical_site_id: str  # what canonical_site_id() resolves this to
    is_canonical: bool      # this is itself a canonical (not aliased forward)
    inbound_aliases: List[Dict[str, Any]]   # other sites that alias TO this one
    outbound_alias: Optional[Dict[str, Any]]  # this site aliases TO another (chain hop)


class OperationalHealth(BaseModel):
    live_appliances_count: int
    relocating_appliances_count: int
    site_exists: bool
    is_orphan: bool  # zero live appliances + zero canonical mapping


class TelemetryRecency(BaseModel):
    execution_telemetry_24h: int
    incidents_24h: int
    l2_decisions_24h: int
    orphan_telemetry_24h: int   # rows under this site_id with no live appliance
    aggregated_pattern_stats_count: int


class FlywheelStateSummary(BaseModel):
    promotion_candidates: int       # learning_promotion_candidates rows
    promoted_rules_by_lifecycle: Dict[str, int]  # lifecycle_state → count
    flywheel_events_24h: int        # promoted_rule_events rows in last 24h


class SubstrateSignal(BaseModel):
    invariant_name: str
    severity: str
    detected_at: str
    last_seen_at: str
    details: Dict[str, Any]


class PendingFleetOrders(BaseModel):
    """Pending fleet orders targeted at this site_id (per
    `parameters->>'site_id'` in fleet_orders). Round-table Angle 3
    P1 — operators investigating flywheel incidents need to see
    orders that promoted but didn't get acked."""
    sync_promoted_rule_pending: int
    update_daemon_pending: int
    other_pending: int
    oldest_pending_at: Optional[str]


class RecentAdminEvent(BaseModel):
    """Most-recent admin_audit_log event touching this site_id."""
    action: str
    username: str
    created_at: str
    details_summary: Optional[str] = None


class Recommendation(BaseModel):
    """Structured recommendation surfaced to the operator. Round-table
    Angle 2 P1 — replaces List[str] so the frontend can drive badges,
    severity colors, and click-through links from a stable contract."""
    code: str          # ORPHAN_TELEMETRY, PHANTOM_PROMOTION_RISK, SITE_MISSING, ...
    severity: str      # info | warn | critical
    message: str       # human-readable
    action_hint: Optional[str] = None  # operator's runbook ("call rename_site(...)")


class TierThresholdsView(BaseModel):
    """The threshold values currently configured for one tier. NULL
    distinct fields indicate calibration-pending. Round-table P3
    exposure for operator transparency — see WHY a count came out
    the way it did without cross-referencing the migration."""
    min_total_occurrences: int
    min_success_rate: float
    min_l2_resolutions: int
    max_age_days: int
    min_distinct_orgs: Optional[int] = None
    min_distinct_sites: Optional[int] = None


class TierResolution(BaseModel):
    """F6 phase 2 read-only tier-eligibility breakdown. Counts are
    diagnostic; no enforcement, no rollout. `*_active` reports whether
    each tier is currently making decisions (env-on AND
    tier.enabled AND calibrated). All three counts use the
    `would_be_eligible` framing for consistency — local is also
    speculative until enabled+calibrated. Round-table 2026-04-30.

    `*_thresholds` (round-table P3) exposes the actual threshold
    bundle used to compute the count — operator transparency. NULL
    when the tier row doesn't exist; populated even when calibration
    is pending (so an operator can see seed values vs calibrated)."""
    local_would_be_eligible: Optional[int] = None
    org_would_be_eligible: Optional[int] = None
    platform_would_be_eligible: Optional[int] = None
    tier_local_active: bool = False
    tier_org_active: bool = False
    tier_platform_active: bool = False
    tier_local_calibrated: bool = False
    tier_org_calibrated: bool = False
    tier_platform_calibrated: bool = False
    tier_local_thresholds: Optional[TierThresholdsView] = None
    tier_org_thresholds: Optional[TierThresholdsView] = None
    tier_platform_thresholds: Optional[TierThresholdsView] = None
    client_org_id: Optional[str] = None
    # Dict keyed by tier name — frontend can render the org-specific
    # note next to the org count badge (round-table P2-5).
    notes: Dict[str, str] = {}


class FlywheelDiagnostic(BaseModel):
    site_id_input: str
    canonical_site_id: str
    canonical_aliasing: CanonicalAliasing
    operational_health: OperationalHealth
    telemetry_recency: TelemetryRecency
    flywheel_state: FlywheelStateSummary
    pending_fleet_orders: PendingFleetOrders
    substrate_signals: List[SubstrateSignal]
    recent_admin_events: List[RecentAdminEvent]
    tier_resolution: TierResolution
    recommendations: List[Recommendation]
    notes: Dict[str, str]


def _build_recommendations(
    canonical: CanonicalAliasing,
    health: OperationalHealth,
    telemetry: TelemetryRecency,
    flywheel: FlywheelStateSummary,
    signals: List[SubstrateSignal],
    aged_promotion_candidates: int,
) -> List[Recommendation]:
    """Heuristic recommendations the operator can act on. Read-only;
    the endpoint never takes action itself.

    Each recommendation is independent — multiple may surface for one
    site. Returned as structured Recommendation objects so the
    frontend can drive severity badges + click-through links.
    """
    recs: List[Recommendation] = []

    if not health.site_exists:
        recs.append(Recommendation(
            code="SITE_MISSING",
            severity="warn",
            message=(
                "Site does not exist in `sites` table. Either it was never "
                "provisioned, was hard-deleted (rare — should be soft via "
                "delete_protection), or the input is a stale reference."
            ),
            action_hint=(
                "Check site_canonical_mapping for an alias before assuming "
                "it's gone — see canonical_aliasing.outbound_alias."
            ),
        ))

    if telemetry.orphan_telemetry_24h > 10:
        recs.append(Recommendation(
            code="ORPHAN_TELEMETRY",
            severity="critical",
            message=(
                f"flywheel_orphan_telemetry signal: "
                f"{telemetry.orphan_telemetry_24h} orphan rows in last 24h. "
                f"Telemetry is landing under this site_id but no live "
                f"appliance carries it."
            ),
            action_hint=(
                f"If this site was renamed/decom'd, call "
                f"rename_site('{canonical.canonical_site_id}', '<new>', "
                f"'<your-email>', '<reason ≥20 chars>') to alias forward. "
                f"Otherwise investigate which appliance is misconfigured."
            ),
        ))

    # Round-table Angle 3 P2: reassuring rec when site is the canonical
    # FOR something — orphan-but-aliased-FROM is a normal post-relocate
    # state, not a problem.
    if (
        health.is_orphan
        and canonical.inbound_aliases
        and not canonical.outbound_alias
    ):
        n = len(canonical.inbound_aliases)
        recs.append(Recommendation(
            code="ORPHAN_BUT_CANONICAL_TARGET",
            severity="info",
            message=(
                f"This site is the canonical target for {n} aliased "
                f"site_id(s); zero live appliances is expected if all "
                f"aliased sources have been migrated."
            ),
            action_hint=None,
        ))
    elif health.is_orphan and not canonical.outbound_alias:
        recs.append(Recommendation(
            code="ORPHAN_NO_MAPPING",
            severity="warn",
            message=(
                "Site has zero live appliances AND no canonical mapping "
                "forward. Either this is a newly-created site awaiting "
                "first checkin, OR the site has been retired without a "
                "mapping."
            ),
            action_hint=(
                "Check admin_audit_log for 'appliance.relocate' events "
                "on this site_id (see recent_admin_events below)."
            ),
        ))

    # Round-table Angle 2 P1: PHANTOM PROMOTION false-positive gate.
    # A brand-new site can briefly carry a candidate row before its
    # `sites` row materializes (cross-org promotion writes async-ahead
    # of provisioning). Use the aged candidate count (created_at >
    # 1h ago) to suppress the false positive.
    if (
        aged_promotion_candidates > 0
        and not health.site_exists
        and not canonical.inbound_aliases
    ):
        recs.append(Recommendation(
            code="PHANTOM_PROMOTION_RISK",
            severity="critical",
            message=(
                f"{aged_promotion_candidates} promotion candidate(s) "
                f"older than 1h queued for a site_id that doesn't exist "
                f"and isn't an alias. PhantomSiteRolloutError "
                f"(Session 213 F2) will reject any rollout attempt."
            ),
            action_hint=(
                "Investigate the source of these candidates. Check "
                "learning_promotion_candidates.partner_id + created_at."
            ),
        ))

    if signals:
        # Surface highest severity prominently
        sev_rank = {"sev1": 3, "sev2": 2, "sev3": 1}
        sorted_signals = sorted(
            signals, key=lambda s: sev_rank.get(s.severity, 0), reverse=True
        )
        top = sorted_signals[0]
        recs.append(Recommendation(
            code="SUBSTRATE_VIOLATION",
            severity="critical" if top.severity == "sev1" else "warn",
            message=(
                f"{len(signals)} substrate violation(s) open. Highest "
                f"severity: {top.invariant_name} ({top.severity})."
            ),
            action_hint=(
                f"GET /api/admin/substrate/runbook/{top.invariant_name} "
                f"for the operator runbook. Full panel at "
                f"/admin/substrate-health."
            ),
        ))

    return recs


@router.get(
    "/{site_id}/flywheel-diagnostic",
    response_model=FlywheelDiagnostic,
    summary="Operator diagnostic — flywheel signals for one site",
)
async def get_flywheel_diagnostic(
    site_id: str,
    user: Dict[str, Any] = Depends(require_admin),
):
    """Read-only aggregation of flywheel state for one site_id.

    Resolves the input through `canonical_site_id()` so an operator
    can pass either an orphan site_id or its canonical and get the
    same diagnostic — the response distinguishes the input from the
    canonical.

    Auth: admin only.
    """
    # Rate limit per actor — admin-only auth alone isn't enough; a
    # buggy dashboard tab polling auto-refresh would still cost 11
    # PgBouncer transactions per call. 20/min is generous for real
    # operator use during an incident.
    actor_email = (user.get("email") or user.get("username") or "admin").lower()
    if not check_rate_limit(actor_email, "flywheel_diagnostic",
                            window_seconds=60, max_requests=20):
        raise HTTPException(
            status_code=429,
            detail="flywheel_diagnostic rate limit exceeded (20/min/actor)",
            headers={"Retry-After": "60"},
        )

    pool = await get_pool()
    # Round-table P0 (Session 213 F7): MUST be admin_transaction, not
    # admin_connection. This handler runs 11 queries; under PgBouncer
    # transaction-pool, the SET LOCAL app.is_admin and any subsequent
    # fetches can route to different backends — RLS would then hide
    # every row and the diagnostic returns silent zeros at exactly the
    # moment the operator needs ground truth. Same class as Session 212
    # sigauth bug (commit 303421cc). admin_transaction pins the SET +
    # all fetches to a single backend via an explicit transaction.
    async with admin_transaction(pool) as conn:
        # 0. Resolve input → canonical
        canonical_site_id = await conn.fetchval(
            "SELECT canonical_site_id($1)", site_id
        )

        # 1. Canonical aliasing — both directions of the mapping graph
        outbound_row = await conn.fetchrow(
            """
            SELECT to_site_id, actor, reason, effective_at, related_migration
              FROM site_canonical_mapping
             WHERE from_site_id = $1
            """,
            site_id,
        )
        inbound_rows = await conn.fetch(
            """
            SELECT from_site_id, actor, reason, effective_at, related_migration
              FROM site_canonical_mapping
             WHERE to_site_id = $1
             ORDER BY effective_at DESC
            """,
            canonical_site_id,
        )

        outbound: Optional[Dict[str, Any]] = None
        if outbound_row:
            outbound = {
                "to_site_id": outbound_row["to_site_id"],
                "actor": outbound_row["actor"],
                "reason": outbound_row["reason"],
                "effective_at": outbound_row["effective_at"].isoformat() if outbound_row["effective_at"] else None,
                "related_migration": outbound_row["related_migration"],
            }
        inbound = [
            {
                "from_site_id": r["from_site_id"],
                "actor": r["actor"],
                "reason": r["reason"],
                "effective_at": r["effective_at"].isoformat() if r["effective_at"] else None,
                "related_migration": r["related_migration"],
            }
            for r in inbound_rows
        ]

        canonical_aliasing = CanonicalAliasing(
            canonical_site_id=canonical_site_id,
            is_canonical=(outbound is None),
            inbound_aliases=inbound,
            outbound_alias=outbound,
        )

        # 2. Operational health (against canonical site)
        site_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM sites WHERE site_id = $1)",
            canonical_site_id,
        )
        live_appliances = await conn.fetchval(
            """
            SELECT COUNT(*) FROM site_appliances
             WHERE site_id = $1
               AND deleted_at IS NULL
               AND status NOT IN ('relocating', 'relocated', 'decommissioned')
            """,
            canonical_site_id,
        )
        relocating_appliances = await conn.fetchval(
            """
            SELECT COUNT(*) FROM site_appliances
             WHERE site_id = $1
               AND deleted_at IS NULL
               AND status = 'relocating'
            """,
            canonical_site_id,
        )
        is_orphan = (
            int(live_appliances or 0) == 0
            and int(relocating_appliances or 0) == 0
        )

        operational_health = OperationalHealth(
            live_appliances_count=int(live_appliances or 0),
            relocating_appliances_count=int(relocating_appliances or 0),
            site_exists=bool(site_exists),
            is_orphan=is_orphan,
        )

        # 3. Telemetry recency — count 24h activity under the INPUT
        # site_id (not canonical) so an operator can see where data is
        # actually landing physically. Aggregator output is keyed on
        # canonical, but raw rows still carry their physical site_id.
        et_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM execution_telemetry
             WHERE site_id = $1
               AND created_at > NOW() - INTERVAL '24 hours'
            """,
            site_id,
        )
        incidents_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM incidents
             WHERE site_id = $1
               AND created_at > NOW() - INTERVAL '24 hours'
            """,
            site_id,
        )
        l2_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM l2_decisions
             WHERE site_id = $1
               AND created_at > NOW() - INTERVAL '24 hours'
            """,
            site_id,
        )
        orphan_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM execution_telemetry et
             WHERE et.site_id = $1
               AND et.created_at > NOW() - INTERVAL '24 hours'
               AND et.site_id NOT IN (
                   SELECT DISTINCT site_id FROM site_appliances
                    WHERE deleted_at IS NULL
               )
            """,
            site_id,
        )
        aps_count = await conn.fetchval(
            "SELECT COUNT(*) FROM aggregated_pattern_stats WHERE site_id = $1",
            canonical_site_id,
        )

        telemetry_recency = TelemetryRecency(
            execution_telemetry_24h=int(et_24h or 0),
            incidents_24h=int(incidents_24h or 0),
            l2_decisions_24h=int(l2_24h or 0),
            orphan_telemetry_24h=int(orphan_24h or 0),
            aggregated_pattern_stats_count=int(aps_count or 0),
        )

        # 4. Flywheel state — promotion candidates + promoted_rules by
        # lifecycle. Both keyed on canonical (post-mig 256). Track
        # aged candidates (>1h old) separately so PHANTOM_PROMOTION_RISK
        # doesn't false-positive on a freshly-seeded candidate awaiting
        # provisioning catch-up.
        candidates_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM learning_promotion_candidates
             WHERE site_id = $1
               AND approval_status = 'pending'
            """,
            canonical_site_id,
        )
        aged_candidates_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM learning_promotion_candidates
             WHERE site_id = $1
               AND approval_status = 'pending'
               AND created_at < NOW() - INTERVAL '1 hour'
            """,
            canonical_site_id,
        )
        lifecycle_rows = await conn.fetch(
            """
            SELECT lifecycle_state, COUNT(*) AS n
              FROM promoted_rules
             WHERE site_id = $1
             GROUP BY lifecycle_state
            """,
            canonical_site_id,
        )
        lifecycle_breakdown = {r["lifecycle_state"]: int(r["n"]) for r in lifecycle_rows}

        ledger_24h = await conn.fetchval(
            """
            SELECT COUNT(*) FROM promoted_rule_events
             WHERE site_id = $1
               AND created_at > NOW() - INTERVAL '24 hours'
            """,
            canonical_site_id,
        )

        flywheel_state = FlywheelStateSummary(
            promotion_candidates=int(candidates_count or 0),
            promoted_rules_by_lifecycle=lifecycle_breakdown,
            flywheel_events_24h=int(ledger_24h or 0),
        )

        # 5. Pending fleet orders — round-table Angle 3 P1. The flywheel
        # push goes promotion → safe_rollout → fleet_order(sync_promoted_rule).
        # Pending orders that aren't being acked are the smoking gun for
        # phantom promotions manifesting at the fleet-order layer. Use
        # parameters->>'site_id' (unsigned, but fine for diagnostic
        # READ — actual order routing uses the signed payload).
        fleet_rows = await conn.fetch(
            """
            SELECT order_type,
                   COUNT(*) AS n,
                   MIN(created_at) AS oldest
              FROM fleet_orders
             WHERE status = 'active'
               AND (parameters->>'site_id' = $1
                    OR parameters->>'site_id' = $2)
             GROUP BY order_type
            """,
            site_id, canonical_site_id,
        )
        sync_promoted_pending = 0
        update_daemon_pending = 0
        other_pending = 0
        oldest_pending: Optional[Any] = None
        for r in fleet_rows:
            n = int(r["n"])
            if r["order_type"] == "sync_promoted_rule":
                sync_promoted_pending = n
            elif r["order_type"] == "update_daemon":
                update_daemon_pending = n
            else:
                other_pending += n
            if oldest_pending is None or (r["oldest"] and r["oldest"] < oldest_pending):
                oldest_pending = r["oldest"]

        pending_fleet_orders = PendingFleetOrders(
            sync_promoted_rule_pending=sync_promoted_pending,
            update_daemon_pending=update_daemon_pending,
            other_pending=other_pending,
            oldest_pending_at=oldest_pending.isoformat() if oldest_pending else None,
        )

        # 6. Recent admin_audit_log activity — Angle 3 P2. Last 5 admin
        # actions touching this site (or its canonical). Operators
        # investigating "why is this site weird" need to see the last
        # appliance.relocate / api_key.deactivate / site.rename.
        admin_rows = await conn.fetch(
            """
            SELECT action, username, created_at, details
              FROM admin_audit_log
             WHERE target = $1 OR target = $2
                OR (details::text LIKE '%' || $3 || '%')
                OR (details::text LIKE '%' || $4 || '%')
             ORDER BY created_at DESC
             LIMIT 5
            """,
            f"site:{site_id}", f"site:{canonical_site_id}",
            site_id, canonical_site_id,
        )
        recent_admin_events = [
            RecentAdminEvent(
                action=r["action"],
                username=r["username"] or "unknown",
                created_at=r["created_at"].isoformat(),
                details_summary=(
                    str(r["details"])[:200] if r["details"] else None
                ),
            )
            for r in admin_rows
        ]

        # 7. Substrate signals — open violations for this site_id
        signal_rows = await conn.fetch(
            """
            SELECT invariant_name, severity, detected_at, last_seen_at, details
              FROM substrate_violations
             WHERE (site_id = $1 OR site_id = $2)
               AND resolved_at IS NULL
             ORDER BY detected_at DESC
            """,
            site_id, canonical_site_id,
        )
        signals = [
            SubstrateSignal(
                invariant_name=r["invariant_name"],
                severity=r["severity"],
                detected_at=r["detected_at"].isoformat(),
                last_seen_at=r["last_seen_at"].isoformat(),
                details=r["details"] or {},
            )
            for r in signal_rows
        ]

        # 8. F6 phase 2 tier_resolution — read-only eligibility
        # breakdown across local/org/platform tiers. Diagnostic only;
        # no enforcement. The compute helper handles the cross-org
        # boundary by taking client_org_id as a scoped parameter
        # (NEVER aggregates without an explicit org filter).
        client_org_id_row = await conn.fetchval(
            "SELECT client_org_id FROM sites WHERE site_id = $1",
            canonical_site_id,
        )
        federation_env_enabled = parse_bool_env("FLYWHEEL_FEDERATION_ENABLED")
        tier_resolution_data = await compute_tier_resolution(
            conn,
            site_id=canonical_site_id,
            client_org_id=client_org_id_row,
            federation_env_enabled=federation_env_enabled,
        )

    tier_resolution = TierResolution(**tier_resolution_data)

    recommendations = _build_recommendations(
        canonical_aliasing,
        operational_health,
        telemetry_recency,
        flywheel_state,
        signals,
        int(aged_candidates_count or 0),
    )

    # Round-table Angle 2 P2: surface the input/canonical asymmetry
    # in the response so consumers don't have to read source.
    notes = {
        "telemetry_recency": (
            "counts keyed on raw input site_id (where data physically lands)"
        ),
        "flywheel_state": (
            "counts keyed on canonical site_id (post-mig 256 aggregator)"
        ),
        "substrate_signals": (
            "matches both input and canonical site_id"
        ),
        "pending_fleet_orders": (
            "matches both input and canonical site_id via parameters->>'site_id'"
        ),
    }

    return FlywheelDiagnostic(
        site_id_input=site_id,
        canonical_site_id=canonical_site_id,
        canonical_aliasing=canonical_aliasing,
        operational_health=operational_health,
        telemetry_recency=telemetry_recency,
        flywheel_state=flywheel_state,
        pending_fleet_orders=pending_fleet_orders,
        substrate_signals=signals,
        recent_admin_events=recent_admin_events,
        tier_resolution=tier_resolution,
        recommendations=recommendations,
        notes=notes,
    )
