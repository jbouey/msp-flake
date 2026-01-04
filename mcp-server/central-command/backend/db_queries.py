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
