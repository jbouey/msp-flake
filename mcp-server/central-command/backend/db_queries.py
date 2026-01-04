"""Database queries for dashboard API.

Provides functions to query real data from PostgreSQL.
Falls back to mock data when database is empty.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
        FROM appliances a
        LEFT JOIN incidents i ON i.appliance_id = a.id
        LEFT JOIN evidence_bundles eb ON eb.appliance_id = a.id
        WHERE a.status = 'active'
        GROUP BY a.id
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
            l1_rate = 100.0
        
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
            "overall_health": min(100, l1_rate + 10),
            "connectivity": 100.0 if row.last_checkin and row.last_checkin > datetime.now(timezone.utc) - timedelta(hours=1) else 50.0,
            "compliance": l1_rate,
            "status": status,
            "last_seen": row.last_checkin,
        })
    
    return clients


async def get_incidents_from_db(
    db: AsyncSession,
    site_id: Optional[str] = None,
    limit: int = 50,
    resolved: Optional[bool] = None
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
            i.reported_at as created_at
        FROM incidents i
        JOIN appliances a ON a.id = i.appliance_id
        WHERE 1=1
    """
    
    params = {}
    if site_id:
        query_str += " AND a.site_id = :site_id"
        params["site_id"] = site_id
    if resolved is not None:
        if resolved:
            query_str += " AND i.status = 'resolved'"
        else:
            query_str += " AND i.status != 'resolved'"
    
    query_str += " ORDER BY i.reported_at DESC LIMIT :limit"
    params["limit"] = limit
    
    result = await db.execute(text(query_str), params)
    rows = result.fetchall()
    
    return [{
        "id": row.id,
        "site_id": row.site_id,
        "hostname": "",  # Not stored in this schema
        "check_type": row.check_type or "backup",
        "severity": row.severity,
        "resolution_level": row.resolution_tier,
        "resolved": row.status == "resolved",
        "resolved_at": row.resolved_at,
        "hipaa_controls": row.hipaa_controls or [],
        "created_at": row.created_at,
    } for row in rows]


async def get_learning_status_from_db(db: AsyncSession) -> Dict[str, Any]:
    """Get learning loop statistics."""
    # Count L1 rules
    l1_result = await db.execute(text("SELECT COUNT(*) FROM l1_rules WHERE enabled = true"))
    l1_count = l1_result.scalar() or 0
    
    # Count patterns
    patterns_result = await db.execute(text("SELECT COUNT(*) FROM patterns WHERE status = 'pending'"))
    pending_patterns = patterns_result.scalar() or 0
    
    promoted_result = await db.execute(text(
        "SELECT COUNT(*) FROM patterns WHERE status = 'promoted' AND promoted_at > NOW() - INTERVAL '30 days'"
    ))
    recently_promoted = promoted_result.scalar() or 0
    
    # Count incidents by tier
    tier_result = await db.execute(text("""
        SELECT 
            resolution_tier,
            COUNT(*) as count
        FROM incidents 
        WHERE reported_at > NOW() - INTERVAL '30 days'
        AND status = 'resolved'
        GROUP BY resolution_tier
    """))
    tier_counts = {row.resolution_tier: row.count for row in tier_result.fetchall()}
    
    total_resolved = sum(tier_counts.values()) or 1
    l1_rate = (tier_counts.get("L1", 0) / total_resolved) * 100
    l2_rate = (tier_counts.get("L2", 0) / total_resolved) * 100
    
    return {
        "total_l1_rules": l1_count,
        "total_l2_decisions_30d": tier_counts.get("L2", 0),
        "patterns_awaiting_promotion": pending_patterns,
        "recently_promoted_count": recently_promoted,
        "promotion_success_rate": 95.0,  # Would need more data to calculate
        "l1_resolution_rate": round(l1_rate, 1),
        "l2_resolution_rate": round(l2_rate, 1),
    }


async def get_promotion_candidates_from_db(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get patterns eligible for promotion."""
    result = await db.execute(text("""
        SELECT 
            pattern_id,
            pattern_signature,
            description,
            incident_type,
            runbook_id,
            occurrences,
            success_rate,
            avg_resolution_time_ms,
            proposed_rule,
            first_seen,
            last_seen
        FROM patterns
        WHERE status = 'pending'
        AND occurrences >= 5
        AND success_rate >= 90
        ORDER BY success_rate DESC, occurrences DESC
    """))
    
    return [{
        "id": row.pattern_id,
        "pattern_signature": row.pattern_signature,
        "description": row.description,
        "occurrences": row.occurrences,
        "success_rate": row.success_rate,
        "avg_resolution_time_ms": row.avg_resolution_time_ms or 0,
        "proposed_rule": row.proposed_rule,
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
    } for row in result.fetchall()]


async def get_global_stats_from_db(db: AsyncSession) -> Dict[str, Any]:
    """Get global statistics."""
    # Count appliances
    appliance_result = await db.execute(text("""
        SELECT 
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '1 hour') as online
        FROM appliances WHERE status = 'active'
    """))
    appliance_row = appliance_result.fetchone()
    
    # Count incidents
    incident_result = await db.execute(text("""
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
    inc_row = incident_result.fetchone()
    
    total_resolved = inc_row.total_resolved or 1
    
    return {
        "total_clients": appliance_row.total or 0,
        "total_appliances": appliance_row.total or 0,
        "online_appliances": appliance_row.online or 0,
        "avg_compliance_score": 85.0,  # Would calculate from evidence
        "avg_connectivity_score": 95.0,
        "incidents_24h": inc_row.day or 0,
        "incidents_7d": inc_row.week or 0,
        "incidents_30d": inc_row.month or 0,
        "l1_resolution_rate": round((inc_row.l1 or 0) / total_resolved * 100, 1),
        "l2_resolution_rate": round((inc_row.l2 or 0) / total_resolved * 100, 1),
        "l3_escalation_rate": round((inc_row.l3 or 0) / total_resolved * 100, 1),
    }


async def promote_pattern_in_db(db: AsyncSession, pattern_id: str) -> Optional[str]:
    """Promote a pattern to L1 rule."""
    # Get pattern
    result = await db.execute(
        text("SELECT * FROM patterns WHERE pattern_id = :pid AND status = 'pending'"),
        {"pid": pattern_id}
    )
    pattern = result.fetchone()
    
    if not pattern:
        return None
    
    # Create L1 rule
    rule_id = f"RB-AUTO-{pattern.pattern_signature[:8].upper()}"
    
    await db.execute(text("""
        INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, promoted_from_l2, enabled)
        VALUES (:rule_id, :pattern, :runbook_id, 0.9, true, true)
        ON CONFLICT (rule_id) DO NOTHING
    """), {
        "rule_id": rule_id,
        "pattern": f'{{"incident_type": "{pattern.incident_type}"}}',
        "runbook_id": pattern.runbook_id,
    })
    
    # Update pattern
    await db.execute(text("""
        UPDATE patterns 
        SET status = 'promoted', promoted_at = NOW(), promoted_to_rule_id = :rule_id
        WHERE pattern_id = :pid
    """), {"rule_id": rule_id, "pid": pattern_id})
    
    await db.commit()
    return rule_id


async def get_compliance_scores_for_site(db: AsyncSession, site_id: str) -> Dict[str, Any]:
    """Calculate compliance scores from latest compliance bundles for a site.
    
    Maps check types to compliance categories:
    - patching: nixos_generation
    - antivirus: windows_defender, windows_windows_defender
    - backup: backup_status
    - logging: audit_logging, windows_audit_policy
    - firewall: firewall, windows_firewall_status
    - encryption: bitlocker, windows_bitlocker_status
    
    Scoring: compliant/pass=100, warning=50, non_compliant/fail=0
    """
    # Get the latest compliance bundle for this site
    result = await db.execute(text("""
        SELECT checks, summary, checked_at
        FROM compliance_bundles
        WHERE site_id = :site_id
        ORDER BY checked_at DESC
        LIMIT 50
    """), {"site_id": site_id})
    
    bundles = result.fetchall()
    
    if not bundles:
        # No compliance data - return defaults indicating unknown
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
    
    # Category mappings
    category_checks = {
        "patching": ["nixos_generation"],
        "antivirus": ["windows_defender", "windows_windows_defender"],
        "backup": ["backup_status"],
        "logging": ["audit_logging", "windows_audit_policy"],
        "firewall": ["firewall", "windows_firewall_status"],
        "encryption": ["bitlocker", "windows_bitlocker_status"],
    }
    
    # Collect scores by category
    category_scores = {cat: [] for cat in category_checks}
    
    for bundle in bundles:
        checks = bundle.checks or []
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
                continue  # Skip unknown statuses
            
            # Find which category this check belongs to
            for category, check_types in category_checks.items():
                if check_type in check_types:
                    category_scores[category].append(score)
                    break
    
    # Calculate average for each category
    result_scores = {}
    total_score = 0
    categories_with_data = 0
    
    for category, scores in category_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            result_scores[category] = round(avg)
            total_score += avg
            categories_with_data += 1
        else:
            result_scores[category] = None
    
    # Calculate overall score
    if categories_with_data > 0:
        result_scores["score"] = round(total_score / categories_with_data, 1)
    else:
        result_scores["score"] = None
    
    result_scores["has_data"] = categories_with_data > 0
    
    return result_scores


async def get_all_compliance_scores(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    """Get compliance scores for all sites that have compliance data."""
    # Get all sites with compliance data
    result = await db.execute(text("""
        SELECT DISTINCT site_id FROM compliance_bundles
    """))

    site_ids = [row.site_id for row in result.fetchall()]

    scores = {}
    for site_id in site_ids:
        scores[site_id] = await get_compliance_scores_for_site(db, site_id)

    return scores


# =============================================================================
# CLIENT PORTAL QUERIES
# =============================================================================

# Category mappings for compliance checks
CATEGORY_CHECKS = {
    "patching": ["nixos_generation"],
    "antivirus": ["windows_defender", "windows_windows_defender"],
    "backup": ["backup_status"],
    "logging": ["audit_logging", "windows_audit_policy"],
    "firewall": ["firewall", "windows_firewall_status"],
    "encryption": ["bitlocker", "windows_bitlocker_status"],
}


async def get_site_info(db: AsyncSession, site_id: str) -> Optional[Dict[str, Any]]:
    """Get site information from appliances table."""
    result = await db.execute(text("""
        SELECT
            site_id,
            host_id,
            status,
            last_checkin,
            agent_version
        FROM appliances
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
        AND checked_at > NOW() - INTERVAL :days_interval
        ORDER BY checked_at ASC
    """), {"site_id": site_id, "days_interval": f"{days} days"})

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

            # Find category
            for category, check_types in CATEGORY_CHECKS.items():
                if check_type in check_types:
                    daily_data[date_str][category].append(score)
                    break

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

    Returns signed bundles with hashes from evidence_bundles table.
    Falls back gracefully when empty.
    """
    query_str = """
        SELECT
            eb.id,
            eb.bundle_hash,
            eb.bundle_type,
            eb.created_at,
            eb.signed_at,
            eb.signature,
            a.site_id
        FROM evidence_bundles eb
        JOIN appliances a ON a.id = eb.appliance_id
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
        "bundle_id": str(row.id),
        "bundle_hash": row.bundle_hash,
        "bundle_type": row.bundle_type or "daily",
        "generated_at": row.created_at,
        "signed_at": row.signed_at,
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

            for category, check_types in CATEGORY_CHECKS.items():
                if check_type in check_types:
                    category_scores[category].append(score)
                    break

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
            COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE resolution_tier = 'L1') as l1_resolved
        FROM incidents i
        JOIN appliances a ON a.id = i.appliance_id
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
        JOIN appliances a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
        AND i.status = 'resolved'
        AND i.reported_at > NOW() - INTERVAL :days_interval
        ORDER BY i.resolved_at DESC
        LIMIT :limit
    """), {
        "site_id": site_id,
        "days_interval": f"{days} days",
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
            COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
            COUNT(*) FILTER (WHERE resolution_tier = 'L1') as l1,
            COUNT(*) FILTER (WHERE resolution_tier = 'L2') as l2,
            AVG(EXTRACT(EPOCH FROM (resolved_at - reported_at)))
                FILTER (WHERE status = 'resolved' AND check_type = 'patching') as patch_mttr_sec
        FROM incidents i
        JOIN appliances a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
        AND i.reported_at > NOW() - INTERVAL '30 days'
    """), {"site_id": site_id})

    inc_row = incident_result.fetchone()

    # Calculate pass rates from compliance history
    history = await get_compliance_history_for_site(db, site_id, days=30)

    controls_passing = 0
    controls_warning = 0
    controls_failing = 0

    if history:
        # Use latest day's data
        latest = history[-1]["scores"] if history else {}
        for cat, score in latest.items():
            if cat == "overall":
                continue
            if score is None:
                continue
            elif score >= 90:
                controls_passing += 1
            elif score >= 50:
                controls_warning += 1
            else:
                controls_failing += 1

    overall_score = scores.get("score") or 0
    patch_mttr_hours = (inc_row.patch_mttr_sec / 3600) if inc_row and inc_row.patch_mttr_sec else 0
    auto_fixes_24h = (inc_row.l1 or 0) + (inc_row.l2 or 0) if inc_row else 0

    return {
        "compliance_pct": round(overall_score, 1),
        "patch_mttr_hours": round(patch_mttr_hours, 1),
        "mfa_coverage_pct": 100.0,  # Would need separate MFA tracking
        "backup_success_rate": float(scores.get("backup") or 100),
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
        AND checked_at > NOW() - INTERVAL :days_interval
        ORDER BY checked_at DESC
    """), {"site_id": site_id, "days_interval": f"{days} days"})

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

    # Calculate pass rates
    for check_type, data in check_results.items():
        total = data["total"]
        if total > 0:
            data["pass_rate"] = round((data["pass_count"] / total) * 100, 1)
        else:
            data["pass_rate"] = None

    return check_results
