"""Database queries for dashboard API.

Provides functions to query real data from PostgreSQL.
Falls back to mock data when database is empty.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Cache TTL (seconds) — configurable via environment
CACHE_TTL_SCORES = int(os.environ.get("CACHE_TTL_SCORES", "120"))
CACHE_TTL_METRICS = int(os.environ.get("CACHE_TTL_METRICS", "120"))


async def _get_redis():
    """Get Redis client if available."""
    try:
        from main import redis_client
        return redis_client
    except (ImportError, AttributeError):
        return None


async def _cache_get(key: str):
    """Get value from Redis cache. Returns None on miss or error."""
    r = await _get_redis()
    if not r:
        return None
    try:
        data = await r.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.debug(f"Redis cache GET failed for {key}: {e}")
    return None


async def _cache_set(key: str, value, ttl_seconds: int = 60):
    """Set value in Redis cache with TTL."""
    r = await _get_redis()
    if not r:
        return
    try:
        await r.setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception as e:
        logger.debug(f"Redis cache SET failed for {key}: {e}")


async def get_fleet_from_db(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get all appliances with health data."""
    query = text("""
        SELECT 
            a.site_id,
            a.host_id as name,
            a.status,
            a.last_checkin,
            COUNT(DISTINCT i.id) FILTER (WHERE i.status = 'open') as open_incidents,
            COUNT(DISTINCT i.id) FILTER (WHERE i.status = 'resolved' AND i.resolution_tier = 'L1') as l1_resolved,
            COUNT(DISTINCT i.id) FILTER (WHERE i.status = 'resolved' AND i.resolution_tier = 'L2') as l2_resolved,
            COUNT(DISTINCT i.id) FILTER (WHERE i.status = 'resolved') as total_resolved,
            COUNT(DISTINCT eb.id) as evidence_count
        FROM v_appliances_current a
        LEFT JOIN incidents i ON i.appliance_id = a.id
        LEFT JOIN evidence_bundles eb ON eb.appliance_id = a.id
        WHERE a.status = 'active'
        GROUP BY a.id, a.site_id, a.host_id, a.status, a.last_checkin
        ORDER BY a.last_checkin DESC NULLS LAST
    """)
    
    result = await db.execute(query)
    rows = result.fetchall()
    
    clients = []
    for row in rows:
        total = row.total_resolved or 0
        l1 = row.l1_resolved or 0
        
        # Calculate health score based on incident resolution
        if total > 0:
            l1_rate = (l1 / total) * 100
        else:
            l1_rate = None  # No incidents — no data to compute from
        
        # Determine status based on open incidents
        if row.open_incidents and row.open_incidents > 3:
            status = "critical"
        elif row.open_incidents and row.open_incidents > 0:
            status = "warning"
        else:
            status = "healthy"
        
        clients.append({
            "site_id": row.site_id,
            "name": row.name or row.site_id,
            "appliance_count": 1,  # Each row is one appliance
            "overall_health": round(l1_rate, 1) if l1_rate is not None else None,
            "connectivity": 100.0 if row.last_checkin and row.last_checkin > datetime.now(timezone.utc) - timedelta(hours=1) else (0.0 if not row.last_checkin else 50.0),
            "compliance": round(l1_rate, 1) if l1_rate is not None else None,
            "status": status,
            "last_seen": row.last_checkin,
        })
    
    return clients


async def get_incidents_from_db(
    db: AsyncSession,
    site_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    resolved: Optional[bool] = None,
    level: Optional[str] = None,
    org_scope: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Get incidents from database."""
    query_str = """
        SELECT
            i.id,
            a.site_id,
            i.incident_type,
            i.severity,
            i.check_type,
            i.resolution_tier,
            i.status,
            i.resolved_at,
            i.hipaa_controls,
            i.reported_at as created_at,
            COALESCE(i.remediation_attempts, 0) as remediation_attempts,
            COALESCE(i.remediation_exhausted, false) as remediation_exhausted
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        WHERE 1=1
    """

    params = {}
    if org_scope is not None:
        query_str += " AND a.site_id IN (SELECT site_id FROM sites WHERE client_org_id = ANY(:org_scope_ids))"
        params["org_scope_ids"] = org_scope
    if site_id:
        query_str += " AND a.site_id = :site_id"
        params["site_id"] = site_id
    if resolved is not None:
        if resolved:
            query_str += " AND i.status = 'resolved'"
        else:
            query_str += " AND i.status != 'resolved'"
    if level and level in ("L1", "L2", "L3"):
        query_str += " AND i.resolution_tier = :level"
        params["level"] = level
    
    query_str += " ORDER BY i.reported_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    
    result = await db.execute(text(query_str), params)
    rows = result.fetchall()
    
    return [{
        "id": row.id,
        "site_id": row.site_id,
        "hostname": "",  # Not stored in this schema
        "check_type": row.check_type or row.incident_type or "unknown",
        "severity": row.severity,
        "resolution_level": row.resolution_tier,
        "resolved": row.status == "resolved",
        "resolved_at": row.resolved_at,
        "hipaa_controls": row.hipaa_controls or [],
        "created_at": row.created_at,
        "remediation_attempts": row.remediation_attempts,
        "remediation_exhausted": row.remediation_exhausted,
    } for row in rows]


async def get_events_from_db(
    db: AsyncSession,
    site_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    org_scope: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Get recent events from compliance_bundles.

    This includes drift detections and other compliance checks.
    Provides visibility into appliance activity when no incidents exist.
    """
    query_str = """
        SELECT
            cb.bundle_id as id,
            cb.site_id,
            cb.check_type,
            cb.check_result,
            cb.summary,
            cb.created_at
        FROM compliance_bundles cb
        WHERE 1=1
    """

    params = {}
    if org_scope is not None:
        query_str += " AND cb.site_id IN (SELECT site_id FROM sites WHERE client_org_id = ANY(:org_scope_ids))"
        params["org_scope_ids"] = org_scope
    if site_id:
        query_str += " AND cb.site_id = :site_id"
        params["site_id"] = site_id

    query_str += " ORDER BY cb.created_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    result = await db.execute(text(query_str), params)
    rows = result.fetchall()

    events = []
    for row in rows:
        # Determine severity from check_result
        outcome = row.check_result or "unknown"
        summary = row.summary or {}

        if outcome == "fail" or outcome == "warn":
            severity = "medium"
        elif outcome == "error":
            severity = "critical"
        else:
            severity = "low"

        events.append({
            "id": row.id,
            "site_id": row.site_id,
            "hostname": "",
            "check_type": row.check_type or "drift",
            "check_name": row.check_type,
            "outcome": outcome,
            "severity": severity,
            "resolution_level": None,
            "resolved": outcome == "pass",
            "resolved_at": row.created_at if outcome == "pass" else None,
            "hipaa_controls": [],
            "created_at": row.created_at,
            "source": "compliance_bundle",
        })

    return events


async def get_learning_status_from_db(db: AsyncSession) -> Dict[str, Any]:
    """Get learning loop statistics.

    NOTE: Queries run sequentially because SQLAlchemy AsyncSession does not
    support concurrent operations on the same session (causes InvalidStateError).
    """
    # L1 rules count
    result = await db.execute(text("SELECT COUNT(*) FROM l1_rules WHERE enabled = true"))
    l1_count = result.scalar() or 0

    # Pending patterns (from aggregated_pattern_stats, not legacy patterns table)
    result = await db.execute(text("""
        SELECT COUNT(*) FROM aggregated_pattern_stats aps
        LEFT JOIN learning_promotion_candidates lpc
            ON lpc.pattern_signature::text = aps.pattern_signature::text
            AND lpc.site_id::text = aps.site_id::text
        WHERE aps.promotion_eligible = true
          AND COALESCE(lpc.approval_status, 'not_submitted') NOT IN ('approved', 'rejected')
          AND aps.last_seen > NOW() - INTERVAL '14 days'
    """))
    pending_patterns = result.scalar() or 0

    # Recently promoted (from learning_promotion_candidates, not legacy patterns table)
    result = await db.execute(text(
        "SELECT COUNT(*) FROM learning_promotion_candidates WHERE approval_status = 'approved' AND approved_at > NOW() - INTERVAL '30 days'"
    ))
    recently_promoted = result.scalar() or 0

    # Last promotion timestamp — "Last rule promoted 3d ago" on the dashboard
    # Learning Loop card. Proves the flywheel is alive without forcing the
    # customer success team to dig into the Learning page.
    result = await db.execute(text(
        "SELECT MAX(approved_at) FROM learning_promotion_candidates WHERE approval_status = 'approved'"
    ))
    last_promotion_at = result.scalar()

    # Tier counts
    result = await db.execute(text("""
        WITH combined_tiers AS (
            SELECT resolution_tier as tier FROM incidents
            WHERE reported_at > NOW() - INTERVAL '30 days'
            AND resolution_tier IS NOT NULL
            UNION ALL
            SELECT resolution_level as tier FROM execution_telemetry
            WHERE created_at > NOW() - INTERVAL '30 days'
            AND resolution_level IS NOT NULL
        )
        SELECT tier, COUNT(*) as count
        FROM combined_tiers
        GROUP BY tier
    """))
    tier_counts = {row.tier: row.count for row in result.fetchall()}

    # Success stats
    result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE success = true) as successful
        FROM execution_telemetry
        WHERE created_at > NOW() - INTERVAL '30 days'
    """))
    success_row = result.fetchone()

    # Return None (not 0.0) when no denominator exists. A 0.0 with no
    # incidents/executions renders as "we succeeded at 0%" on the dashboard,
    # which is a credibility-hitting empty-state lie — the rate is genuinely
    # undefined with no denominator.
    total_resolved = sum(tier_counts.values())
    if total_resolved > 0:
        l1_rate: Optional[float] = round((tier_counts.get("L1", 0) / total_resolved) * 100, 1)
        l2_rate: Optional[float] = round((tier_counts.get("L2", 0) / total_resolved) * 100, 1)
    else:
        l1_rate = None
        l2_rate = None

    total_executions = success_row.total or 0
    if total_executions > 0:
        success_rate: Optional[float] = round((success_row.successful / total_executions) * 100, 1)
    else:
        success_rate = None

    return {
        "total_l1_rules": l1_count,
        "total_l2_decisions_30d": tier_counts.get("L2", 0),
        "patterns_awaiting_promotion": pending_patterns,
        "recently_promoted_count": recently_promoted,
        "promotion_success_rate": success_rate,
        "l1_resolution_rate": l1_rate,
        "l2_resolution_rate": l2_rate,
        "last_promotion_at": last_promotion_at.isoformat() if last_promotion_at else None,
    }


async def get_promotion_candidates_from_db(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get patterns eligible for promotion from aggregated_pattern_stats.

    The legacy `patterns` table only tracked L2 decisions. The current system
    aggregates L1+L2 telemetry into aggregated_pattern_stats, which is the
    authoritative source for promotion candidates.
    """
    result = await db.execute(text("""
        SELECT
            aps.id::text as id,
            aps.pattern_signature,
            aps.site_id,
            s.clinic_name as site_name,
            aps.total_occurrences,
            CASE WHEN aps.success_rate <= 1
                THEN aps.success_rate * 100
                ELSE aps.success_rate END as success_rate,
            aps.avg_resolution_time_ms,
            aps.recommended_action,
            aps.first_seen,
            aps.last_seen,
            COALESCE(impact.cnt, 0) as impact_count_7d
        FROM aggregated_pattern_stats aps
        JOIN sites s ON s.site_id::text = aps.site_id::text
        LEFT JOIN learning_promotion_candidates lpc
            ON lpc.pattern_signature::text = aps.pattern_signature::text
            AND lpc.site_id::text = aps.site_id::text
        LEFT JOIN LATERAL (
            SELECT COUNT(*) as cnt
            FROM execution_telemetry et
            WHERE et.incident_type = split_part(aps.pattern_signature, ':', 1)
              AND et.site_id::text = aps.site_id::text
              AND et.created_at > NOW() - INTERVAL '7 days'
        ) impact ON true
        WHERE aps.promotion_eligible = true
          AND COALESCE(lpc.approval_status, 'not_submitted') NOT IN ('approved', 'rejected')
          AND aps.last_seen > NOW() - INTERVAL '14 days'
        ORDER BY aps.total_occurrences DESC
    """))

    return [{
        "id": row.id,
        "pattern_signature": row.pattern_signature,
        "site_id": row.site_id,
        "site_name": row.site_name,
        "occurrences": row.total_occurrences,
        "success_rate": float(row.success_rate or 0),
        "avg_resolution_time_ms": float(row.avg_resolution_time_ms or 0),
        "proposed_rule": row.recommended_action,
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
        "impact_count_7d": int(row.impact_count_7d or 0),
    } for row in result.fetchall()]


async def get_coverage_gaps_from_db(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get check_types seen in telemetry that lack L1 rules."""
    result = await db.execute(text("""
        SELECT
            et.incident_type as check_type,
            COUNT(*) as incident_count_30d,
            MAX(et.created_at) as last_seen,
            EXISTS(
                SELECT 1 FROM l1_rules lr
                WHERE lr.enabled = true
                  AND (
                    lr.incident_pattern->>'check_type' = et.incident_type
                    OR lr.incident_pattern->>'incident_type' = et.incident_type
                    OR lr.rule_id ILIKE '%' || REPLACE(et.incident_type, '_', '-') || '%'
                    OR lr.rule_id ILIKE '%' || et.incident_type || '%'
                  )
            ) as has_l1_rule
        FROM execution_telemetry et
        WHERE et.created_at > NOW() - INTERVAL '30 days'
          AND et.incident_type IS NOT NULL
          AND et.incident_type != ''
        GROUP BY et.incident_type
        ORDER BY incident_count_30d DESC
    """))
    return [{
        "check_type": row.check_type,
        "incident_count_30d": row.incident_count_30d,
        "last_seen": row.last_seen,
        "has_l1_rule": row.has_l1_rule,
    } for row in result.fetchall()]


async def get_global_stats_from_db(db: AsyncSession) -> Dict[str, Any]:
    """Get global statistics using parallel queries."""

    # NOTE: Queries run sequentially - SQLAlchemy AsyncSession does not
    # support concurrent operations on the same session.
    site_row = await db.execute(text("SELECT COUNT(*) as total FROM sites"))
    site_row = site_row.fetchone()

    appliance_row = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '15 minutes') as online
        FROM site_appliances
    """))
    appliance_row = appliance_row.fetchone()

    inc_row = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE reported_at > NOW() - INTERVAL '24 hours') as day,
            COUNT(*) FILTER (WHERE reported_at > NOW() - INTERVAL '7 days') as week,
            COUNT(*) FILTER (WHERE reported_at > NOW() - INTERVAL '30 days') as month,
            COUNT(*) FILTER (WHERE resolution_tier = 'L1' AND status = 'resolved') as l1,
            COUNT(*) FILTER (WHERE resolution_tier = 'L2' AND status = 'resolved') as l2,
            COUNT(*) FILTER (WHERE resolution_tier = 'L3' AND status = 'resolved') as l3,
            COUNT(*) FILTER (WHERE status = 'resolved') as total_resolved
        FROM incidents
        WHERE reported_at > NOW() - INTERVAL '30 days'
    """))
    inc_row = inc_row.fetchone()

    # Fetch disabled drift checks per site for compliance score filtering
    # Includes both disabled and not_applicable checks
    global_disabled_by_site: Dict[str, set] = {}
    global_default_disabled: set = set()
    try:
        dc_result = await db.execute(text(
            "SELECT site_id, check_type FROM site_drift_config WHERE enabled = false OR status = 'not_applicable'"
        ))
        dc_rows = dc_result.fetchall()
        for r in dc_rows:
            global_disabled_by_site.setdefault(r.site_id, set()).add(r.check_type)
        global_default_disabled = global_disabled_by_site.get('__defaults__', set())
    except Exception:
        # If table doesn't exist yet, skip — log for visibility.
        logger.error("site_drift_config_load_failed_global", exc_info=True)

    comp_result = await db.execute(text("""
        SELECT cb.site_id, c->>'check' as check_type, c->>'status' as check_status
        FROM compliance_bundles cb,
             jsonb_array_elements(cb.checks) as c
        WHERE cb.created_at > NOW() - INTERVAL '24 hours'
          AND jsonb_array_length(cb.checks) > 0
    """))
    comp_rows = comp_result.fetchall()

    comp_passed = 0
    comp_total = 0
    for cr in comp_rows:
        ct = cr.check_type or ""
        site_disabled = global_disabled_by_site.get(cr.site_id, global_default_disabled)
        if ct in site_disabled:
            continue
        status = (cr.check_status or "").lower()
        if status in ("pass", "compliant"):
            comp_passed += 1
            comp_total += 1
        elif status in ("fail", "non_compliant", "warning"):
            comp_total += 1

    compliance_score = round(comp_passed / max(comp_total, 1) * 100, 1)

    # Calculate connectivity score from appliance checkins
    total_appliances = appliance_row.total or 0
    online_appliances = appliance_row.online or 0
    connectivity_score = round(online_appliances / max(total_appliances, 1) * 100, 1)

    # Same empty-denominator rule as get_learning_status_from_db: if no
    # incidents were resolved in the window, the resolution rates are
    # undefined — return None so the dashboard renders "—" instead of a
    # misleading "0%". total_resolved is the denominator for all three rates.
    total_resolved = inc_row.total_resolved or 0
    if total_resolved > 0:
        l1_res_rate: Optional[float] = round((inc_row.l1 or 0) / total_resolved * 100, 1)
        l2_res_rate: Optional[float] = round((inc_row.l2 or 0) / total_resolved * 100, 1)
        l3_esc_rate: Optional[float] = round((inc_row.l3 or 0) / total_resolved * 100, 1)
    else:
        l1_res_rate = None
        l2_res_rate = None
        l3_esc_rate = None

    return {
        "total_clients": site_row.total or 0,
        "total_appliances": appliance_row.total or 0,
        "online_appliances": appliance_row.online or 0,
        "avg_compliance_score": compliance_score,
        "avg_connectivity_score": connectivity_score,
        "incidents_24h": inc_row.day or 0,
        "incidents_7d": inc_row.week or 0,
        "incidents_30d": inc_row.month or 0,
        "l1_resolution_rate": l1_res_rate,
        "l2_resolution_rate": l2_res_rate,
        "l3_escalation_rate": l3_esc_rate,
    }


async def promote_pattern_in_db(db: AsyncSession, pattern_id: str) -> Optional[str]:
    """Promote a pattern to L1 rule. Uses SELECT FOR UPDATE to prevent duplicate promotions."""
    import json as _json
    from .websocket_manager import broadcast_event

    # Get pattern with row lock to prevent concurrent promotions
    result = await db.execute(
        text("SELECT * FROM patterns WHERE pattern_id = :pid AND status = 'pending' FOR UPDATE"),
        {"pid": pattern_id}
    )
    pattern = result.fetchone()

    if not pattern:
        return None

    # Create L1 rule
    rule_id = f"RB-AUTO-{pattern.pattern_signature.upper()}"

    try:
        await db.execute(text("""
            INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, promoted_from_l2, enabled)
            VALUES (:rule_id, :pattern, :runbook_id, 0.9, true, true)
            ON CONFLICT (rule_id) DO NOTHING
        """), {
            "rule_id": rule_id,
            "pattern": _json.dumps({"incident_type": pattern.incident_type}),
            "runbook_id": pattern.runbook_id,
        })

        # Cross-site learning: create a synced version available to all appliances
        synced_rule_id = f"SYNC-{rule_id}"
        await db.execute(text("""
            INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, promoted_from_l2, enabled, source)
            VALUES (:rule_id, :pattern, :runbook_id, 0.9, true, true, 'synced')
            ON CONFLICT (rule_id) DO NOTHING
        """), {
            "rule_id": synced_rule_id,
            "pattern": _json.dumps({"incident_type": pattern.incident_type}),
            "runbook_id": pattern.runbook_id,
        })

        # Update pattern status
        await db.execute(text("""
            UPDATE patterns
            SET status = 'promoted', promoted_at = NOW(), promoted_to_rule_id = :rule_id
            WHERE pattern_id = :pid
        """), {"rule_id": rule_id, "pid": pattern_id})

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # Broadcast promotion event
    try:
        await broadcast_event("pattern_promoted", {
            "pattern_id": pattern_id,
            "rule_id": rule_id,
            "incident_type": pattern.incident_type,
            "runbook_id": pattern.runbook_id,
            "pattern_signature": pattern.pattern_signature,
        })
    except Exception as e:
        logger.warning(f"Pattern promotion broadcast failed: {e}")

    return rule_id


async def get_compliance_scores_for_site(db: AsyncSession, site_id: str) -> Dict[str, Any]:
    """Calculate compliance scores from latest compliance bundles for a site.
    
    Maps check types to compliance categories:
    - patching: nixos_generation
    - antivirus: windows_defender
    - backup: backup_status
    - logging: audit_logging, windows_audit_policy
    - firewall: firewall, windows_firewall_status
    - encryption: bitlocker, windows_bitlocker_status
    
    Scoring: compliant/pass=100, warning=50, non_compliant/fail=0
    """
    # Fetch disabled drift checks for this site (includes disabled + not_applicable)
    disabled_checks = set()
    try:
        dc_result = await db.execute(text(
            "SELECT check_type FROM site_drift_config WHERE site_id = :site_id AND (enabled = false OR status = 'not_applicable')"
        ), {"site_id": site_id})
        dc_rows = dc_result.fetchall()
        if not dc_rows:
            dc_result = await db.execute(text(
                "SELECT check_type FROM site_drift_config WHERE site_id = '__defaults__' AND (enabled = false OR status = 'not_applicable')"
            ))
            dc_rows = dc_result.fetchall()
        disabled_checks = {r.check_type for r in dc_rows}
    except Exception:
        # If table doesn't exist yet, skip — log for visibility.
        logger.error("site_drift_config_load_failed_per_site", extra={"site_id": site_id}, exc_info=True)

    # Get compliance bundles from last 24 hours. Staleness cutoff ensures
    # offline hosts don't carry stale "pass" results indefinitely.
    # LIMIT 200 covers 10+ appliances with 5+ scan types each.
    # Deduplication to latest-per-check happens below.
    result = await db.execute(text("""
        SELECT checks, summary, checked_at
        FROM compliance_bundles
        WHERE site_id = :site_id
          AND checked_at > NOW() - INTERVAL '24 hours'
        ORDER BY checked_at DESC
        LIMIT 200
    """), {"site_id": site_id})

    bundles = result.fetchall()

    if not bundles:
        return {
            "patching": None,
            "antivirus": None,
            "backup": None,
            "logging": None,
            "firewall": None,
            "encryption": None,
            "score": None,
            "has_data": False,
        }

    # LATEST-PER-CHECK scoring: for each unique (check_type, hostname),
    # take only the most recent result. This produces a stable point-in-time
    # score that doesn't oscillate based on which bundles landed in the window.
    #
    # Round Table: "The same underlying state was producing scores between
    # 50-90% because the ratio of Windows-to-Linux bundles in the last 50
    # changed on every page load. One vote per check per host."
    latest_check: Dict[str, str] = {}  # (check_type, hostname) -> status

    for bundle in bundles:
        checks = bundle.checks or []
        for check in checks:
            check_type = check.get("check", "")
            if check_type in disabled_checks:
                continue
            hostname = check.get("hostname", "unknown")
            key = f"{check_type}:{hostname}"
            # First occurrence = most recent (bundles ordered DESC)
            if key not in latest_check:
                latest_check[key] = check.get("status", "").lower()

    # Count pass/warn/fail per category from latest-per-check results
    cat_pass: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}
    cat_warn: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}
    cat_fail: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}

    for key, status in latest_check.items():
        check_type = key.split(":")[0]
        category = _CHECK_TYPE_TO_CATEGORY.get(check_type)
        if not category:
            continue

        if status in ("compliant", "pass"):
            cat_pass[category] += 1
        elif status == "warning":
            cat_warn[category] += 1
        elif status in ("non_compliant", "fail"):
            cat_fail[category] += 1

    # Unified formula: score = (passes + 0.5 * warnings) / total * 100
    # Overall uses HIPAA-weighted average (encryption/access_control weighted higher)
    result_scores = {}
    weighted_sum = 0.0
    weight_sum = 0.0

    for category in CATEGORY_CHECKS:
        total = cat_pass[category] + cat_warn[category] + cat_fail[category]
        if total > 0:
            avg = ((cat_pass[category] + 0.5 * cat_warn[category]) / total) * 100
            result_scores[category] = round(avg)
            weight = HIPAA_CATEGORY_WEIGHTS.get(category, 0.06)
            weighted_sum += avg * weight
            weight_sum += weight
        else:
            result_scores[category] = None

    # Overall score = HIPAA-weighted average of category scores
    if weight_sum > 0:
        result_scores["score"] = round(weighted_sum / weight_sum, 1)
    else:
        result_scores["score"] = None

    result_scores["has_data"] = weight_sum > 0

    return result_scores


async def get_per_device_compliance(
    db: AsyncSession, site_id: str, window_days: int = 30
) -> Dict[str, str]:
    """Per-device compliance status derived live from compliance_bundles.

    BUG 3 round-table 2026-05-01 (fork a48dd10968aaf583c, Path C):
    the deprecated compliance_status column on discovered_devices is
    a denormalized cache that was NEVER wired to bundle-ingest; every device shows 'unknown'
    forever even when Go agents are actively submitting passing bundles.
    Site-level score read 94% from compliance_bundles, but per-device
    "Managed Fleet" read 0% from the stale cache — same writer/reader
    divergence class as BUG 2 / mig 268.

    Per consensus: source-of-truth is `compliance_bundles` (Ed25519 +
    OTS-anchored chain). Compute per-device status live; deprecate
    the cache column.

    Returns: dict mapping hostname → status, where status ∈
    {'compliant', 'drifted', 'warning', 'unknown'}. Aggregation rule
    matches the writer at evidence_chain.py:1135-1146:
      - any 'fail' or 'non_compliant' per-host check → 'drifted'
      - any 'warning' per-host check → 'warning'  (passing-but-flagged)
      - all 'pass'/'compliant' → 'compliant'
      - none of the above → 'unknown'

    Window: 30 days default. Hosts not seen in window → not in result;
    caller treats absence as 'unknown'.

    Performance: covered by `idx_cb_site_created` (mig 260). One query
    per device-list page load (cacheable; staleTime). NOT a hot-path
    impact like Path A's writer-cache approach would have been.
    """
    # Same shape as get_compliance_scores_for_site:1135-1146 — fetch
    # bundles, dedup latest-per (hostname, check) inside, aggregate.
    result = await db.execute(text("""
        SELECT checks, checked_at
          FROM compliance_bundles
         WHERE site_id = :site_id
           AND checked_at > NOW() - make_interval(days => :window_days)
         ORDER BY checked_at DESC
         LIMIT 200
    """), {"site_id": site_id, "window_days": window_days})
    bundles = result.fetchall()

    if not bundles:
        return {}

    # latest-per-(host, check_type) — bundles ordered DESC, first occurrence is most-recent
    PASSING = {"pass", "compliant"}
    WARNING = {"warning", "warn"}
    FAILING = {"fail", "non_compliant"}

    # hostname -> latest aggregate state across all checks
    # We compute per-host: any FAIL → drifted; any WARNING (no FAIL) → warning;
    # any PASS (no FAIL/WARNING) → compliant; none → unknown.
    seen_keys: set[str] = set()
    host_has_fail: Dict[str, bool] = {}
    host_has_warn: Dict[str, bool] = {}
    host_has_pass: Dict[str, bool] = {}

    for bundle in bundles:
        checks = bundle.checks or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                continue
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_type = check.get("check") or check.get("check_type") or ""
            hostname = check.get("hostname")
            status = (check.get("status") or "").lower()
            if not hostname or not check_type:
                continue
            key = f"{hostname}::{check_type}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if status in FAILING:
                host_has_fail[hostname] = True
            elif status in WARNING:
                host_has_warn[hostname] = True
            elif status in PASSING:
                host_has_pass[hostname] = True

    out: Dict[str, str] = {}
    all_hosts = (
        set(host_has_fail.keys())
        | set(host_has_warn.keys())
        | set(host_has_pass.keys())
    )
    for host in all_hosts:
        if host_has_fail.get(host):
            out[host] = "drifted"
        elif host_has_warn.get(host):
            out[host] = "warning"
        elif host_has_pass.get(host):
            out[host] = "compliant"
    return out


async def get_all_compliance_scores(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    """Get compliance scores for all sites in a single query (replaces N+1 loop).

    Uses a window function to fetch the latest 50 bundles per site, then
    aggregates scores in Python using the pre-computed category lookup.
    """
    # SECURITY: admin-only cache — callers MUST be behind require_auth (admin)
    cached = await _cache_get("admin:compliance:all_scores")
    if cached is not None:
        return cached

    # Fetch all disabled drift checks in one query
    # Includes both disabled and not_applicable checks
    disabled_by_site: Dict[str, set] = {}
    default_disabled: set = set()
    try:
        dc_result = await db.execute(text(
            "SELECT site_id, check_type FROM site_drift_config WHERE enabled = false OR status = 'not_applicable'"
        ))
        dc_rows = dc_result.fetchall()
        for r in dc_rows:
            disabled_by_site.setdefault(r.site_id, set()).add(r.check_type)
        default_disabled = disabled_by_site.get('__defaults__', set())
    except Exception:
        # If table doesn't exist yet, skip — log for visibility.
        logger.error("site_drift_config_load_failed_all_sites", exc_info=True)

    result = await db.execute(text("""
        SELECT site_id, checks FROM (
            SELECT site_id, checks,
                   ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY checked_at DESC) as rn
            FROM compliance_bundles
            WHERE checked_at > NOW() - INTERVAL '24 hours'
        ) ranked
        WHERE rn <= 200
    """))
    rows = result.fetchall()

    if not rows:
        return {}

    # Group by site_id
    site_bundles: Dict[str, list] = {}
    for row in rows:
        site_bundles.setdefault(row.site_id, []).append(row.checks)

    # Score each site using LATEST-PER-CHECK (same logic as single-site version).
    # For each unique (check_type, hostname), take only the most recent result.
    # Bundles are already ordered DESC by checked_at from the window function.
    scores = {}
    for site_id, bundles_checks in site_bundles.items():
        site_disabled = disabled_by_site.get(site_id, default_disabled)
        latest_check: Dict[str, str] = {}  # (check_type:hostname) -> status
        bundle_check_count = 0

        for checks in bundles_checks:
            if not checks:
                continue
            for check in checks:
                check_type = check.get("check", "")
                if check_type in site_disabled:
                    continue
                hostname = check.get("hostname", "unknown")
                key = f"{check_type}:{hostname}"
                if key not in latest_check:
                    latest_check[key] = check.get("status", "").lower()

        cat_pass: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}
        cat_warn: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}
        cat_fail: Dict[str, int] = {cat: 0 for cat in CATEGORY_CHECKS}

        for key, status in latest_check.items():
            check_type = key.split(":")[0]
            category = _CHECK_TYPE_TO_CATEGORY.get(check_type)
            if not category:
                continue
            bundle_check_count += 1
            if status in ("compliant", "pass"):
                cat_pass[category] += 1
            elif status == "warning":
                cat_warn[category] += 1
            elif status in ("non_compliant", "fail"):
                cat_fail[category] += 1

        result_scores = {}
        weighted_sum = 0.0
        weight_sum = 0.0

        for category in CATEGORY_CHECKS:
            total = cat_pass[category] + cat_warn[category] + cat_fail[category]
            if total > 0:
                avg = ((cat_pass[category] + 0.5 * cat_warn[category]) / total) * 100
                result_scores[category] = round(avg)
                weight = HIPAA_CATEGORY_WEIGHTS.get(category, 0.06)
                weighted_sum += avg * weight
                weight_sum += weight
            else:
                result_scores[category] = None

        if weight_sum > 0:
            result_scores["score"] = round(weighted_sum / weight_sum, 1)
        else:
            result_scores["score"] = None

        result_scores["has_data"] = weight_sum > 0
        result_scores["_bundle_check_count"] = bundle_check_count
        scores[site_id] = result_scores

    # Fetch Go agent compliance summaries for all sites and blend into scores
    try:
        ga_result = await db.execute(text("""
            SELECT site_id,
                   COALESCE(total_agents, 0) as total_agents,
                   COALESCE(active_agents, 0) as active_agents,
                   COALESCE(overall_compliance_rate, 0) as compliance_rate
            FROM site_go_agent_summaries
            WHERE active_agents > 0
        """))
        ga_rows = ga_result.fetchall()

        for ga in ga_rows:
            agent_score = float(ga.compliance_rate)
            sid = ga.site_id

            if sid in scores and scores[sid].get("has_data") and scores[sid]["score"] is not None:
                # Blend: weight by check count (bundles) vs agent count
                bundle_count = scores[sid].get("_bundle_check_count", 0)
                agent_count = ga.active_agents
                if bundle_count > 0:
                    total_weight = bundle_count + agent_count
                    scores[sid]["score"] = round(
                        (scores[sid]["score"] * bundle_count + agent_score * agent_count) / total_weight,
                        1,
                    )
                # Add agent metadata
                scores[sid]["agent_count"] = ga.total_agents
                scores[sid]["agent_compliance"] = round(agent_score, 1)
            elif ga.active_agents > 0:
                # Site has Go agent data but no bundle data — create entry
                if sid not in scores:
                    scores[sid] = {cat: None for cat in CATEGORY_CHECKS}
                scores[sid]["score"] = round(agent_score, 1)
                scores[sid]["has_data"] = True
                scores[sid]["agent_count"] = ga.total_agents
                scores[sid]["agent_compliance"] = round(agent_score, 1)
                scores[sid]["_bundle_check_count"] = 0
    except Exception:
        # If table doesn't exist yet, skip gracefully — log for visibility.
        logger.error("site_go_agent_summaries_blend_failed", exc_info=True)

    # Clean up internal fields before caching
    for sid in scores:
        scores[sid].pop("_bundle_check_count", None)

    await _cache_set("admin:compliance:all_scores", scores, ttl_seconds=CACHE_TTL_SCORES)
    return scores


# =============================================================================
# CLIENT PORTAL QUERIES
# =============================================================================

# Category mappings for compliance checks
# HIPAA-weighted category importance for overall score calculation.
# Encryption and access control carry the most weight per HIPAA Security Rule:
#   §164.312(a)(2)(iv) encryption, §164.312(a)(1) access control,
#   §164.308(a)(7) backup/DR, §164.312(b) audit logging.
HIPAA_CATEGORY_WEIGHTS = {
    "encryption":      0.18,  # PHI protection at rest/transit
    "access_control":  0.18,  # Authentication, RBAC, screen lock
    "backup":          0.14,  # Disaster recovery, business continuity
    "logging":         0.14,  # Audit controls, log forwarding
    "patching":        0.14,  # Vulnerability management
    "firewall":        0.10,  # Network perimeter security
    "antivirus":       0.06,  # Endpoint protection
    "services":        0.06,  # Service hardening
}
# Sum = 1.0

CATEGORY_CHECKS = {
    "patching": [
        "nixos_generation", "windows_update", "linux_patching",
    ],
    "antivirus": [
        "windows_defender",
        "windows_defender_exclusions", "defender_exclusions",
    ],
    "backup": [
        "backup_status", "windows_backup_status",
    ],
    "logging": [
        "audit_logging", "windows_audit_policy",
        "linux_audit", "linux_logging",
        "linux_audit_logging",
    ],
    "firewall": [
        "firewall", "windows_firewall_status", "firewall_status",
        "linux_firewall",
    ],
    "encryption": [
        "bitlocker", "windows_bitlocker_status", "bitlocker_status",
        "linux_crypto", "windows_smb_signing", "smb_signing",
    ],
    "access_control": [
        "rogue_admin_users", "linux_accounts",
        "windows_password_policy", "password_policy",
        "linux_permissions",
        "linux_ssh_config", "windows_screen_lock_policy", "screen_lock_policy",
        "guest_account", "rdp_nla",
        "linux_user_accounts", "linux_file_permissions",
    ],
    "services": [
        "critical_services", "linux_services",
        "windows_service_dns", "service_dns",
        "windows_service_netlogon", "service_netlogon",
        "windows_service_spooler", "windows_service_w32time",
        "windows_service_wuauserv", "agent_status",
        "linux_failed_services",
    ],
    "network": [
        "network_posture_windows",
        "windows_network_profile", "network_profile",
        "windows_dns_config", "dns_config",
        "linux_network", "ntp_sync", "linux_time_sync", "linux_ntp_sync",
        "windows_smb1_protocol", "smb1_protocol",
        "linux_open_ports",
        # Operational monitoring types EXCLUDED from compliance scoring:
        # "network" — netscan aggregate findings (host reachability, not attestation)
        # "net_unexpected_ports", "net_expected_service", "net_host_reachability"
        # These flow through the healing pipeline but are NOT compliance evidence.
    ],
    "system_integrity": [
        "disk_space", "linux_disk_space",
        "linux_kernel", "linux_kernel_params",
        "linux_boot", "linux_integrity", "linux_mac",
        "rogue_scheduled_tasks",
        "windows_registry_run_persistence",
        "windows_scheduled_task_persistence",
        "windows_wmi_event_persistence",
        "linux_cron", "linux_cron_review",
        "linux_banner",
        "linux_incident_response",
        "linux_log_forwarding",
        "linux_cert_expiry",
        "linux_unattended_upgrades",
    ],
}

# Pre-computed reverse lookup: check_type -> category (O(1) instead of O(categories))
# Initialized from hardcoded CATEGORY_CHECKS, then overridden by check_type_registry DB table.
_CHECK_TYPE_TO_CATEGORY: Dict[str, str] = {}
_MONITORING_ONLY_FROM_REGISTRY: set = set()
_REGISTRY_LOADED = False

def _rebuild_category_lookup():
    """Rebuild from hardcoded CATEGORY_CHECKS (fallback)."""
    _CHECK_TYPE_TO_CATEGORY.clear()
    for _cat, _types in CATEGORY_CHECKS.items():
        for _ct in _types:
            _CHECK_TYPE_TO_CATEGORY[_ct] = _cat

_rebuild_category_lookup()  # Initialize from hardcoded values


async def load_check_registry(db) -> None:
    """Load check_type_registry from DB and override the hardcoded mappings.

    Called once at startup and can be called to refresh. Falls back to
    hardcoded CATEGORY_CHECKS if the table doesn't exist yet.

    This is the single source of truth fix: the Go daemon defines check
    names, this table maps them to categories, and the scoring engine
    reads from here. No more naming mismatches.
    """
    global _REGISTRY_LOADED
    try:
        result = await db.execute(text(
            "SELECT check_name, category, is_monitoring_only FROM check_type_registry WHERE is_scored = true"
        ))
        rows = result.fetchall()
        if rows:
            _CHECK_TYPE_TO_CATEGORY.clear()
            _MONITORING_ONLY_FROM_REGISTRY.clear()
            for row in rows:
                if row.category:
                    _CHECK_TYPE_TO_CATEGORY[row.check_name] = row.category
            # Also load monitoring-only checks
            mon_result = await db.execute(text(
                "SELECT check_name FROM check_type_registry WHERE is_monitoring_only = true"
            ))
            for row in mon_result.fetchall():
                _MONITORING_ONLY_FROM_REGISTRY.add(row.check_name)
            _REGISTRY_LOADED = True
            logger.info(f"Check registry loaded: {len(_CHECK_TYPE_TO_CATEGORY)} scored checks, "
                        f"{len(_MONITORING_ONLY_FROM_REGISTRY)} monitoring-only")
    except Exception:
        # Table doesn't exist yet — use hardcoded fallback
        _rebuild_category_lookup()


async def get_site_info(db: AsyncSession, site_id: str) -> Optional[Dict[str, Any]]:
    """Get site information from appliances table."""
    result = await db.execute(text("""
        SELECT
            site_id,
            host_id,
            status,
            last_checkin,
            agent_version
        FROM v_appliances_current
        WHERE site_id = :site_id
        ORDER BY last_checkin DESC NULLS LAST
        LIMIT 1
    """), {"site_id": site_id})

    row = result.fetchone()
    if not row:
        return None

    return {
        "site_id": row.site_id,
        "name": row.host_id or row.site_id.replace("-", " ").title(),
        "status": "online" if row.last_checkin and row.last_checkin > datetime.now(timezone.utc) - timedelta(hours=1) else "offline",
        "last_checkin": row.last_checkin,
        "agent_version": row.agent_version,
    }


async def get_compliance_history_for_site(
    db: AsyncSession,
    site_id: str,
    days: int = 90
) -> List[Dict[str, Any]]:
    """Get daily compliance scores for trending.

    Returns list of daily aggregated scores:
    [{date: "2025-01-01", scores: {patching: 85, antivirus: 100, ...}}, ...]
    """
    result = await db.execute(text("""
        SELECT
            DATE(checked_at) as check_date,
            checks,
            summary
        FROM compliance_bundles
        WHERE site_id = :site_id
        AND checked_at > NOW() - make_interval(days => :days_param)
        ORDER BY checked_at ASC
    """), {"site_id": site_id, "days_param": days})

    rows = result.fetchall()

    # Group by date
    daily_data: Dict[str, Dict[str, List[int]]] = {}

    for row in rows:
        date_str = row.check_date.isoformat() if row.check_date else "unknown"
        if date_str not in daily_data:
            daily_data[date_str] = {cat: [] for cat in CATEGORY_CHECKS}

        checks = row.checks or []
        for check in checks:
            check_type = check.get("check", "")
            status = check.get("status", "").lower()

            # Map status to score
            if status in ("compliant", "pass"):
                score = 100
            elif status == "warning":
                score = 50
            elif status in ("non_compliant", "fail"):
                score = 0
            else:
                continue

            # Find category via O(1) reverse lookup
            category = _CHECK_TYPE_TO_CATEGORY.get(check_type)
            if category:
                daily_data[date_str][category].append(score)

    # Calculate daily averages
    history = []
    for date_str, categories in sorted(daily_data.items()):
        day_scores = {}
        total = 0
        count = 0

        for category, scores in categories.items():
            if scores:
                avg = sum(scores) / len(scores)
                day_scores[category] = round(avg)
                total += avg
                count += 1
            else:
                day_scores[category] = None

        day_scores["overall"] = round(total / count) if count > 0 else None

        history.append({
            "date": date_str,
            "scores": day_scores,
        })

    return history


async def get_evidence_bundles_for_site(
    db: AsyncSession,
    site_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Get signed evidence bundles for audit packets.

    Returns bundles from evidence_bundles table with OTS anchoring info.
    Falls back gracefully when empty.
    """
    query_str = """
        SELECT
            eb.id,
            eb.bundle_id,
            eb.check_type,
            eb.outcome,
            eb.created_at,
            eb.signature,
            eb.s3_uri,
            eb.ots_status,
            eb.ots_bitcoin_block,
            eb.ots_anchored_at,
            a.site_id
        FROM evidence_bundles eb
        JOIN v_appliances_current a ON a.id = eb.appliance_id
        WHERE a.site_id = :site_id
    """

    params: Dict[str, Any] = {"site_id": site_id}

    if start_date:
        query_str += " AND eb.created_at >= :start_date"
        params["start_date"] = start_date

    if end_date:
        query_str += " AND eb.created_at <= :end_date"
        params["end_date"] = end_date

    query_str += " ORDER BY eb.created_at DESC LIMIT :limit"
    params["limit"] = limit

    result = await db.execute(text(query_str), params)
    rows = result.fetchall()

    return [{
        "bundle_id": row.bundle_id or str(row.id),
        "bundle_type": row.check_type or "compliance",
        "generated_at": row.created_at,
        "size_bytes": 0,
        "has_signature": row.signature is not None,
    } for row in rows]


async def get_monthly_compliance_report(
    db: AsyncSession,
    site_id: str,
    year: int,
    month: int
) -> Dict[str, Any]:
    """Generate monthly compliance report from historical data.

    Aggregates compliance_bundles for the month and returns board-ready summary.
    """
    # Calculate date range
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    # Get all compliance bundles for the month
    result = await db.execute(text("""
        SELECT checks, summary, checked_at
        FROM compliance_bundles
        WHERE site_id = :site_id
        AND checked_at >= :start_date
        AND checked_at < :end_date
        ORDER BY checked_at ASC
    """), {
        "site_id": site_id,
        "start_date": start_date,
        "end_date": end_date,
    })

    rows = result.fetchall()

    if not rows:
        return {
            "site_id": site_id,
            "year": year,
            "month": month,
            "has_data": False,
            "total_checks": 0,
            "category_scores": {},
            "overall_score": None,
            "trend": "unknown",
        }

    # Aggregate scores by category
    category_scores: Dict[str, List[int]] = {cat: [] for cat in CATEGORY_CHECKS}

    for row in rows:
        checks = row.checks or []
        for check in checks:
            check_type = check.get("check", "")
            status = check.get("status", "").lower()

            if status in ("compliant", "pass"):
                score = 100
            elif status == "warning":
                score = 50
            elif status in ("non_compliant", "fail"):
                score = 0
            else:
                continue

            category = _CHECK_TYPE_TO_CATEGORY.get(check_type)
            if category:
                category_scores[category].append(score)

    # Calculate monthly averages
    monthly_scores = {}
    total = 0
    count = 0

    for category, scores in category_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            monthly_scores[category] = round(avg, 1)
            total += avg
            count += 1
        else:
            monthly_scores[category] = None

    overall = round(total / count, 1) if count > 0 else None

    # Get incident count for month
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1_resolved
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
        AND i.reported_at >= :start_date
        AND i.reported_at < :end_date
    """), {
        "site_id": site_id,
        "start_date": start_date,
        "end_date": end_date,
    })

    inc_row = incident_result.fetchone()

    # Calculate trend (compare first half vs second half of month)
    mid_point = len(rows) // 2
    if mid_point > 0:
        first_half_scores = []
        second_half_scores = []

        for i, row in enumerate(rows):
            checks = row.checks or []
            row_score = 0
            row_count = 0

            for check in checks:
                status = check.get("status", "").lower()
                if status in ("compliant", "pass"):
                    row_score += 100
                    row_count += 1
                elif status == "warning":
                    row_score += 50
                    row_count += 1
                elif status in ("non_compliant", "fail"):
                    row_count += 1

            if row_count > 0:
                avg = row_score / row_count
                if i < mid_point:
                    first_half_scores.append(avg)
                else:
                    second_half_scores.append(avg)

        first_avg = sum(first_half_scores) / len(first_half_scores) if first_half_scores else 0
        second_avg = sum(second_half_scores) / len(second_half_scores) if second_half_scores else 0

        if second_avg > first_avg + 5:
            trend = "improving"
        elif second_avg < first_avg - 5:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"

    return {
        "site_id": site_id,
        "year": year,
        "month": month,
        "has_data": True,
        "total_checks": len(rows),
        "category_scores": monthly_scores,
        "overall_score": overall,
        "trend": trend,
        "incidents_total": inc_row.total if inc_row else 0,
        "incidents_resolved": inc_row.resolved if inc_row else 0,
        "incidents_auto_healed": inc_row.l1_resolved if inc_row else 0,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
    }


async def get_resolved_incidents_for_site(
    db: AsyncSession,
    site_id: str,
    days: int = 30,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """Get resolved incidents for portal display.

    Portal only shows resolved incidents (outcomes, not operations).
    """
    result = await db.execute(text("""
        SELECT
            i.id,
            i.incident_type,
            i.severity,
            i.check_type,
            i.resolution_tier,
            i.reported_at,
            i.resolved_at,
            EXTRACT(EPOCH FROM (i.resolved_at - i.reported_at)) as resolution_time_sec
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
        AND i.status = 'resolved'
        AND i.reported_at > NOW() - make_interval(days => :days_param)
        ORDER BY i.resolved_at DESC
        LIMIT :limit
    """), {
        "site_id": site_id,
        "days_param": days,
        "limit": limit,
    })

    rows = result.fetchall()

    return [{
        "incident_id": str(row.id),
        "incident_type": row.incident_type,
        "severity": row.severity,
        "check_type": row.check_type,
        "auto_fixed": row.resolution_tier in ("L1", "L2"),
        "resolution_tier": row.resolution_tier,
        "resolution_time_sec": int(row.resolution_time_sec) if row.resolution_time_sec else None,
        "created_at": row.reported_at,
        "resolved_at": row.resolved_at,
    } for row in rows]


async def get_portal_kpis(db: AsyncSession, site_id: str) -> Dict[str, Any]:
    """Calculate KPIs for client portal display.

    Shows outcomes and historical averages, not real-time operations.
    """
    # Get compliance scores
    scores = await get_compliance_scores_for_site(db, site_id)

    # Get incident stats for last 30 days
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
            AVG(EXTRACT(EPOCH FROM (i.resolved_at - i.reported_at)))
                FILTER (WHERE i.status = 'resolved' AND i.check_type = 'patching') as patch_mttr_sec
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
        AND i.reported_at > NOW() - INTERVAL '30 days'
    """), {"site_id": site_id})

    inc_row = incident_result.fetchone()

    # Calculate pass/warn/fail counts from 8 portal controls
    # Uses same multi-check aggregation as portal endpoint
    control_results = await get_control_results_for_site(db, site_id, days=30)
    portal_check_mapping = {
        "endpoint_drift": ["nixos_generation", "linux_kernel", "linux_integrity"],
        "patch_freshness": ["windows_update", "linux_patching"],
        "backup_success": ["backup_status", "windows_backup_status"],
        "mfa_coverage": ["windows_password_policy", "windows_screen_lock_policy"],
        "privileged_access": ["rogue_admin_users", "linux_accounts", "linux_permissions"],
        "git_protections": [],
        "secrets_hygiene": [],
        "storage_posture": ["windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
    }

    controls_passing = 0
    controls_warning = 0
    controls_failing = 0

    for _rule_id, check_types in portal_check_mapping.items():
        pass_rates = []
        for ct in check_types:
            r = control_results.get(ct, {})
            if r.get("pass_rate") is not None:
                pass_rates.append(r["pass_rate"])
        if not pass_rates:
            continue  # No data = skip (don't count as passing or failing)
        else:
            avg_rate = sum(pass_rates) / len(pass_rates)
            if avg_rate >= 90:
                controls_passing += 1
            elif avg_rate >= 50:
                controls_warning += 1
            else:
                controls_failing += 1

    overall_score = scores.get("score") or 0
    patch_mttr_hours = (inc_row.patch_mttr_sec / 3600) if inc_row and inc_row.patch_mttr_sec else 0
    auto_fixes_24h = (inc_row.l1 or 0) + (inc_row.l2 or 0) if inc_row else 0

    return {
        "compliance_pct": round(overall_score, 1),
        "patch_mttr_hours": round(patch_mttr_hours, 1),
        "mfa_coverage_pct": None,  # Not yet tracked — requires separate MFA audit
        "backup_success_rate": float(scores.get("backup")) if scores.get("backup") is not None else None,
        "auto_fixes_24h": auto_fixes_24h,
        "controls_passing": controls_passing,
        "controls_warning": controls_warning,
        "controls_failing": controls_failing,
        "health_score": round(overall_score, 1),
    }


async def get_control_results_for_site(
    db: AsyncSession,
    site_id: str,
    days: int = 30
) -> Dict[str, Dict[str, Any]]:
    """Get control check results aggregated over time.

    Returns historical pass rates per control, not just latest status.
    """
    result = await db.execute(text("""
        SELECT checks, checked_at
        FROM compliance_bundles
        WHERE site_id = :site_id
        AND checked_at > NOW() - make_interval(days => :days_param)
        ORDER BY checked_at DESC
        LIMIT 500
    """), {"site_id": site_id, "days_param": days})

    rows = result.fetchall()

    # Track results per check type
    check_results: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        checks = row.checks or []
        for check in checks:
            check_type = check.get("check", "")
            status = check.get("status", "").lower()

            if check_type not in check_results:
                check_results[check_type] = {
                    "pass_count": 0,
                    "warn_count": 0,
                    "fail_count": 0,
                    "total": 0,
                    "last_checked": None,
                    "last_status": None,
                }

            check_results[check_type]["total"] += 1

            if status in ("compliant", "pass"):
                check_results[check_type]["pass_count"] += 1
            elif status == "warning":
                check_results[check_type]["warn_count"] += 1
            elif status in ("non_compliant", "fail"):
                check_results[check_type]["fail_count"] += 1

            # Track latest
            if check_results[check_type]["last_checked"] is None:
                check_results[check_type]["last_checked"] = row.checked_at
                check_results[check_type]["last_status"] = status

    # Calculate pass rates and detect flapping
    for check_type, data in check_results.items():
        total = data["total"]
        if total > 0:
            data["pass_rate"] = round((data["pass_count"] / total) * 100, 1)
        else:
            data["pass_rate"] = None

        # Detect flapping: count state transitions in chronological order
        # A check is flapping if it changes state frequently relative to
        # total checks (>40% of checks are transitions)
        data["flapping"] = False
        if total >= 4:
            transitions = 0
            fail_pct = (data["fail_count"] / total) * 100
            pass_pct = (data["pass_count"] / total) * 100
            # Flapping = both pass and fail are significant (neither dominates)
            # and pass rate is between 20-80% (not a consistent state)
            if 20 < pass_pct < 80:
                data["flapping"] = True
                # Dampen flapping scores: cap at "warn" level (50) instead of
                # raw average which can be very low due to interleaved failures
                if data["pass_rate"] is not None and data["pass_rate"] < 50:
                    data["pass_rate_raw"] = data["pass_rate"]
                    data["pass_rate"] = 50.0  # Floor at warn, not fail

    return check_results


# =============================================================================
# RUNBOOK QUERIES
# =============================================================================

async def get_runbooks_from_db(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get all runbooks with execution statistics.

    Calculates execution_count, success_rate, avg_execution_time from both
    orders table (server-initiated) and execution_telemetry (agent L1/L2 healing).
    """
    # Get runbooks with stats from orders table AND execution_telemetry
    # Uses runbook_id_mapping table to translate L1 rule IDs to runbook IDs
    result = await db.execute(text("""
        WITH order_stats AS (
            SELECT
                runbook_id,
                COUNT(*) FILTER (WHERE status IN ('completed', 'failed')) as exec_count,
                COUNT(*) FILTER (WHERE status = 'completed') as success_count,
                AVG(EXTRACT(EPOCH FROM (completed_at - acknowledged_at)) * 1000)
                    FILTER (WHERE status = 'completed' AND completed_at IS NOT NULL AND acknowledged_at IS NOT NULL)
                    as avg_time_ms
            FROM orders
            GROUP BY runbook_id
        ),
        telemetry_stats AS (
            -- Join through mapping table to get proper runbook_id
            SELECT
                COALESCE(m.runbook_id, et.runbook_id) as runbook_id,
                COUNT(*) as exec_count,
                COUNT(*) FILTER (WHERE et.success = true) as success_count,
                AVG(et.duration_seconds * 1000) FILTER (WHERE et.duration_seconds IS NOT NULL) as avg_time_ms
            FROM execution_telemetry et
            LEFT JOIN runbook_id_mapping m ON m.l1_rule_id = et.runbook_id
            GROUP BY COALESCE(m.runbook_id, et.runbook_id)
        )
        SELECT
            r.runbook_id,
            r.name,
            r.description,
            r.category,
            r.severity,
            r.hipaa_controls,
            r.steps,
            r.enabled,
            r.created_at,
            r.updated_at,
            COALESCE(os.exec_count, 0) + COALESCE(ts.exec_count, 0) as execution_count,
            COALESCE(os.success_count, 0) + COALESCE(ts.success_count, 0) as success_count,
            COALESCE(os.avg_time_ms, ts.avg_time_ms, 0) as avg_execution_time_ms
        FROM runbooks r
        LEFT JOIN order_stats os ON os.runbook_id = r.runbook_id
        LEFT JOIN telemetry_stats ts ON ts.runbook_id = r.runbook_id
        WHERE r.enabled = true
        ORDER BY r.category, r.name
    """))

    rows = result.fetchall()

    # Get total stats from execution_telemetry (since runbook IDs don't match)
    # This captures L1/L2 healing executions that use different ID formats
    telemetry_totals = await db.execute(text("""
        SELECT
            COUNT(*) as total_execs,
            COUNT(*) FILTER (WHERE success = true) as total_success,
            AVG(duration_seconds * 1000) FILTER (WHERE duration_seconds IS NOT NULL) as avg_time_ms
        FROM execution_telemetry
    """))
    totals = telemetry_totals.fetchone()
    total_exec_count = totals.total_execs or 0
    total_success_count = totals.total_success or 0
    total_success_rate = (total_success_count / total_exec_count * 100) if total_exec_count > 0 else 0.0
    total_avg_time = totals.avg_time_ms or 0

    runbooks = []
    for row in rows:
        exec_count = row.execution_count or 0
        success_count = row.success_count or 0
        success_rate = (success_count / exec_count * 100) if exec_count > 0 else 0.0

        # Map severity to level (all are L1 deterministic)
        level = "L1"

        # Determine if disruptive based on category or severity
        disruptive_categories = ["patching", "encryption", "drift"]
        is_disruptive = row.category in disruptive_categories or row.severity in ("critical", "high")

        runbooks.append({
            "id": row.runbook_id,
            "name": row.name,
            "description": row.description or "",
            "level": level,
            "hipaa_controls": row.hipaa_controls or [],
            "is_disruptive": is_disruptive,
            "execution_count": exec_count,
            "success_rate": round(success_rate, 1),
            "avg_execution_time_ms": int(row.avg_execution_time_ms or 0),
            "category": row.category,
            "steps": row.steps or [],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        })

    return runbooks


async def get_runbook_detail_from_db(db: AsyncSession, runbook_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed runbook information including steps and parameters."""
    result = await db.execute(text("""
        WITH order_stats AS (
            SELECT
                runbook_id,
                COUNT(*) FILTER (WHERE status IN ('completed', 'failed')) as exec_count,
                COUNT(*) FILTER (WHERE status = 'completed') as success_count,
                AVG(EXTRACT(EPOCH FROM (completed_at - acknowledged_at)) * 1000)
                    FILTER (WHERE status = 'completed' AND completed_at IS NOT NULL AND acknowledged_at IS NOT NULL)
                    as avg_time_ms
            FROM orders
            WHERE runbook_id = :runbook_id
            GROUP BY runbook_id
        ),
        telemetry_stats AS (
            SELECT
                runbook_id,
                COUNT(*) as exec_count,
                COUNT(*) FILTER (WHERE success = true) as success_count,
                AVG(duration_seconds * 1000) FILTER (WHERE duration_seconds IS NOT NULL) as avg_time_ms
            FROM execution_telemetry
            WHERE runbook_id = :runbook_id
            GROUP BY runbook_id
        )
        SELECT
            r.runbook_id,
            r.name,
            r.description,
            r.category,
            r.severity,
            r.hipaa_controls,
            r.steps,
            r.parameters_schema,
            r.enabled,
            r.version,
            r.created_at,
            r.updated_at,
            COALESCE(os.exec_count, 0) + COALESCE(ts.exec_count, 0) as execution_count,
            COALESCE(os.success_count, 0) + COALESCE(ts.success_count, 0) as success_count,
            COALESCE(os.avg_time_ms, ts.avg_time_ms, 0) as avg_execution_time_ms
        FROM runbooks r
        LEFT JOIN order_stats os ON os.runbook_id = r.runbook_id
        LEFT JOIN telemetry_stats ts ON ts.runbook_id = r.runbook_id
        WHERE r.runbook_id = :runbook_id
    """), {"runbook_id": runbook_id})

    row = result.fetchone()

    if not row:
        return None

    exec_count = row.execution_count or 0
    success_count = row.success_count or 0
    success_rate = (success_count / exec_count * 100) if exec_count > 0 else 0.0

    disruptive_categories = ["patching", "encryption", "drift"]
    is_disruptive = row.category in disruptive_categories or row.severity in ("critical", "high")

    return {
        "id": row.runbook_id,
        "name": row.name,
        "description": row.description or "",
        "level": "L1",
        "hipaa_controls": row.hipaa_controls or [],
        "is_disruptive": is_disruptive,
        "steps": row.steps or [],
        "parameters": row.parameters_schema or {},
        "execution_count": exec_count,
        "success_rate": round(success_rate, 1),
        "avg_execution_time_ms": int(row.avg_execution_time_ms or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_healing_metrics_for_site(db: AsyncSession, site_id: str) -> Dict[str, Any]:
    """Calculate healing success rate and order execution rate for a site.

    Returns:
        healing_success_rate: % of incidents resolved vs total
        order_execution_rate: % of orders completed vs total
        incidents_24h: count of incidents in last 24 hours
        last_incident: most recent incident timestamp
    """
    # Get healing success rate from incidents (via site_appliances -> appliances join)
    # First try to get incidents linked to appliances registered for this site
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE i.reported_at > NOW() - INTERVAL '24 hours') as last_24h,
            MAX(i.reported_at) as last_incident
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
    """), {"site_id": site_id})
    inc_row = incident_result.fetchone()

    # Get order execution rate from admin_orders
    order_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'completed') as completed
        FROM admin_orders
        WHERE site_id = :site_id
    """), {"site_id": site_id})
    ord_row = order_result.fetchone()

    # Calculate rates
    total_incidents = inc_row.total if inc_row else 0
    resolved_incidents = inc_row.resolved if inc_row else 0
    healing_rate = (resolved_incidents / total_incidents * 100) if total_incidents > 0 else None

    total_orders = ord_row.total if ord_row else 0
    completed_orders = ord_row.completed if ord_row else 0
    order_rate = (completed_orders / total_orders * 100) if total_orders > 0 else None

    return {
        "healing_success_rate": round(healing_rate, 1),
        "order_execution_rate": round(order_rate, 1) if order_rate is not None else 0.0,
        "incidents_24h": inc_row.last_24h if inc_row else 0,
        "last_incident": inc_row.last_incident if inc_row else None,
    }


async def get_all_healing_metrics(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    """Batch-fetch healing metrics for ALL sites in 2 queries (replaces N+1 per-site calls).

    Returns dict keyed by site_id with same shape as get_healing_metrics_for_site().
    """
    # SECURITY: admin-only cache — callers MUST be behind require_auth (admin)
    cached = await _cache_get("admin:healing:all_metrics")
    if cached is not None:
        return cached

    # Get incident stats grouped by site
    incident_result = await db.execute(text("""
        SELECT
            a.site_id,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE i.reported_at > NOW() - INTERVAL '24 hours') as last_24h,
            MAX(i.reported_at) as last_incident
        FROM incidents i
        JOIN v_appliances_current a ON a.id = i.appliance_id
        GROUP BY a.site_id
    """))
    incident_by_site = {row.site_id: row for row in incident_result.fetchall()}

    # Get order stats grouped by site
    order_result = await db.execute(text("""
        SELECT
            site_id,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'completed') as completed
        FROM admin_orders
        GROUP BY site_id
    """))
    order_by_site = {row.site_id: row for row in order_result.fetchall()}

    # Merge into per-site dicts
    all_site_ids = set(incident_by_site.keys()) | set(order_by_site.keys())
    results: Dict[str, Dict[str, Any]] = {}

    for site_id in all_site_ids:
        inc = incident_by_site.get(site_id)
        ord_ = order_by_site.get(site_id)

        total_incidents = inc.total if inc else 0
        resolved = inc.resolved if inc else 0
        healing_rate = (resolved / total_incidents * 100) if total_incidents > 0 else None

        total_orders = ord_.total if ord_ else 0
        completed = ord_.completed if ord_ else 0
        order_rate = (completed / total_orders * 100) if total_orders > 0 else None

        results[site_id] = {
            "healing_success_rate": round(healing_rate, 1),
            "order_execution_rate": round(order_rate, 1) if order_rate is not None else 0.0,
            "incidents_24h": inc.last_24h if inc else 0,
            "last_incident": inc.last_incident if inc else None,
        }

    await _cache_set("admin:healing:all_metrics", results, ttl_seconds=CACHE_TTL_METRICS)
    return results


async def get_global_healing_metrics(db: AsyncSession) -> Dict[str, Any]:
    """Calculate global healing metrics across all sites."""
    # Get overall incident stats
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE reported_at > NOW() - INTERVAL '24 hours') as last_24h
        FROM incidents
    """))
    inc_row = incident_result.fetchone()

    # Get overall order stats
    order_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'completed') as completed
        FROM admin_orders
    """))
    ord_row = order_result.fetchone()

    total_incidents = inc_row.total if inc_row else 0
    resolved_incidents = inc_row.resolved if inc_row else 0
    healing_rate = (resolved_incidents / total_incidents * 100) if total_incidents > 0 else None

    total_orders = ord_row.total if ord_row else 0
    completed_orders = ord_row.completed if ord_row else 0
    order_rate = (completed_orders / total_orders * 100) if total_orders > 0 else None

    return {
        "healing_success_rate": round(healing_rate, 1),
        "order_execution_rate": round(order_rate, 1) if order_rate is not None else 0.0,
        "incidents_24h": inc_row.last_24h if inc_row else 0,
        "total_incidents": total_incidents,
        "total_orders": total_orders,
    }


async def get_runbook_executions_from_db(
    db: AsyncSession,
    runbook_id: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get recent executions of a specific runbook from orders table."""
    result = await db.execute(text("""
        SELECT
            o.id,
            o.order_id,
            o.runbook_id,
            a.site_id,
            a.host_id as hostname,
            o.status,
            o.result,
            o.issued_at,
            o.acknowledged_at,
            o.completed_at,
            EXTRACT(EPOCH FROM (o.completed_at - o.acknowledged_at)) * 1000 as execution_time_ms
        FROM orders o
        JOIN v_appliances_current a ON a.id = o.appliance_id
        WHERE o.runbook_id = :runbook_id
        AND o.status IN ('completed', 'failed')
        ORDER BY o.completed_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """), {"runbook_id": runbook_id, "limit": limit, "offset": offset})

    rows = result.fetchall()

    return [{
        "id": str(row.id),
        "runbook_id": row.runbook_id,
        "site_id": row.site_id,
        "hostname": row.hostname or "",
        "incident_id": None,  # Orders aren't always linked to incidents
        "success": row.status == "completed",
        "execution_time_ms": int(row.execution_time_ms) if row.execution_time_ms else 0,
        "output": row.result.get("message", "") if row.result else "",
        "executed_at": row.completed_at or row.issued_at,
    } for row in rows]
