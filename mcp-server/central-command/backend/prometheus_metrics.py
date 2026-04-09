"""Prometheus-compatible metrics endpoint.

Exposes platform health metrics in Prometheus text exposition format.
Requires admin authentication (cookie or Bearer token).
Generates text format manually — no prometheus_client dependency needed.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from .auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])

# =============================================================================
# Prometheus text format helpers
# =============================================================================

PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _format_metric(
    name: str,
    help_text: str,
    metric_type: str,
    values: list[tuple[dict[str, str], float]],
) -> str:
    """Format a single Prometheus metric block (HELP + TYPE + sample lines)."""
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
    ]
    for labels, value in values:
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines)


def _gauge(name: str, help_text: str, values: list[tuple[dict[str, str], float]]) -> str:
    return _format_metric(name, help_text, "gauge", values)


def _counter(name: str, help_text: str, values: list[tuple[dict[str, str], float]]) -> str:
    return _format_metric(name, help_text, "counter", values)


# =============================================================================
# Endpoint
# =============================================================================


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(user: dict = Depends(require_auth)):
    """Return platform metrics in Prometheus text exposition format.

    Queries are executed fresh on each scrape — no background loop.
    Each metric section is wrapped in its own try/except so a single
    table being absent does not break the entire response.
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    sections: list[str] = []

    try:
        pool = await get_pool()
    except Exception:
        logger.exception("Failed to get database pool for metrics")
        return PlainTextResponse(
            "# Failed to connect to database\n",
            media_type=PROM_CONTENT_TYPE,
            status_code=503,
        )

    try:
        async with admin_connection(pool) as conn:
            now = datetime.now(timezone.utc)

            # --- Appliance status (gauge) ---
            try:
                rows = await conn.fetch(
                    "SELECT last_checkin FROM site_appliances"
                )
                counts = {"online": 0, "stale": 0, "offline": 0}
                for row in rows:
                    lc = row["last_checkin"]
                    if lc is None:
                        counts["offline"] += 1
                    else:
                        age = now - lc
                        if age < timedelta(minutes=15):
                            counts["online"] += 1
                        elif age < timedelta(hours=1):
                            counts["stale"] += 1
                        else:
                            counts["offline"] += 1
                sections.append(_gauge(
                    "osiriscare_appliances_total",
                    "Number of appliances by status",
                    [({"status": s}, float(c)) for s, c in counts.items()],
                ))
            except Exception:
                logger.exception("metrics: appliance status query failed")

            # --- Open incidents by severity (gauge) ---
            try:
                rows = await conn.fetch("""
                    SELECT LOWER(COALESCE(severity, 'medium')) AS sev,
                           COUNT(*) AS cnt
                    FROM incidents
                    WHERE status != 'resolved'
                    GROUP BY LOWER(COALESCE(severity, 'medium'))
                """)
                sev_map = {r["sev"]: r["cnt"] for r in rows}
                values = [
                    ({"severity": sev}, float(sev_map.get(sev, 0)))
                    for sev in ("critical", "high", "medium", "low")
                ]
                sections.append(_gauge(
                    "osiriscare_incidents_open",
                    "Number of open incidents by severity",
                    values,
                ))
            except Exception:
                logger.exception("metrics: incidents query failed")

            # --- Healing execution telemetry (counter) ---
            try:
                rows = await conn.fetch("""
                    SELECT tier, success, COUNT(*) AS cnt
                    FROM execution_telemetry
                    GROUP BY tier, success
                """)
                values = []
                for row in rows:
                    tier = row["tier"] or "unknown"
                    success = "true" if row["success"] else "false"
                    values.append(({"tier": tier, "success": success}, float(row["cnt"])))
                # Ensure standard tiers always appear
                seen = {(v[0]["tier"], v[0]["success"]) for v in values}
                for tier in ("L1", "L2", "L3"):
                    for success in ("true", "false"):
                        if (tier, success) not in seen:
                            values.append(({"tier": tier, "success": success}, 0.0))
                sections.append(_counter(
                    "osiriscare_healing_executions_total",
                    "Total healing executions by tier and outcome",
                    values,
                ))
            except Exception:
                logger.exception("metrics: execution telemetry query failed")

            # --- Evidence bundles (counter) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM evidence_bundles"
                )
                sections.append(_counter(
                    "osiriscare_evidence_bundles_total",
                    "Total evidence bundles collected",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: evidence bundles query failed")

            # --- Fleet orders pending (gauge) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM fleet_orders WHERE status = 'active'"
                )
                sections.append(_gauge(
                    "osiriscare_fleet_orders_pending",
                    "Number of pending/active fleet orders",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: fleet orders query failed")

            # --- Checkin rate last hour (gauge) ---
            try:
                one_hour_ago = now - timedelta(hours=1)
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM site_appliances "
                    "WHERE last_checkin >= $1",
                    one_hour_ago,
                )
                sections.append(_gauge(
                    "osiriscare_checkin_rate_1h",
                    "Number of appliance checkins in the last hour",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: checkin rate query failed")

            # --- Log entries total (counter) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM log_entries"
                )
                sections.append(_counter(
                    "osiriscare_log_entries_total",
                    "Total log entries ingested",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: log entries query failed")

            # --- Learning system metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM aggregated_pattern_stats
                         WHERE promotion_eligible = true) as eligible_patterns,
                        (SELECT COUNT(*) FROM learning_promotion_candidates
                         WHERE approval_status = 'pending') as pending_promotions,
                        (SELECT COUNT(*) FROM learning_promotion_candidates
                         WHERE approval_status = 'approved'
                           AND approved_at > NOW() - INTERVAL '30 days') as recent_promotions
                """)
                sections.append(_gauge(
                    "osiriscare_learning_eligible_patterns",
                    "Patterns eligible for L2-to-L1 promotion",
                    [({}, float(row["eligible_patterns"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_learning_pending_promotions",
                    "Promotion candidates awaiting approval",
                    [({}, float(row["pending_promotions"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_learning_recent_promotions",
                    "Promotions approved in last 30 days",
                    [({}, float(row["recent_promotions"]))],
                ))
            except Exception:
                logger.exception("metrics: learning system query failed")

            # --- Escalation queue metrics (gauge) ---
            try:
                rows = await conn.fetch("""
                    SELECT status, COUNT(*) as cnt,
                           EXTRACT(EPOCH FROM AVG(NOW() - created_at)) as avg_age_secs
                    FROM escalation_tickets
                    WHERE status NOT IN ('resolved', 'closed')
                    GROUP BY status
                """)
                ticket_values = []
                age_values = []
                for row in rows:
                    ticket_values.append(({"status": row["status"]}, float(row["cnt"])))
                    age_values.append(({"status": row["status"]}, float(row["avg_age_secs"] or 0)))
                if ticket_values:
                    sections.append(_gauge(
                        "osiriscare_escalation_tickets_open",
                        "Open escalation tickets by status",
                        ticket_values,
                    ))
                    sections.append(_gauge(
                        "osiriscare_escalation_ticket_age_seconds",
                        "Average age of open escalation tickets in seconds",
                        age_values,
                    ))
            except Exception:
                logger.exception("metrics: escalation queue query failed")

            # --- Device discovery metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_devices,
                        COUNT(*) FILTER (WHERE last_seen > NOW() - INTERVAL '24 hours') as active_24h,
                        COUNT(*) FILTER (WHERE last_seen < NOW() - INTERVAL '7 days') as stale_7d
                    FROM discovered_devices
                """)
                sections.append(_gauge(
                    "osiriscare_discovered_devices_total",
                    "Total discovered devices",
                    [({}, float(row["total_devices"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_discovered_devices_active_24h",
                    "Devices seen in last 24 hours",
                    [({}, float(row["active_24h"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_discovered_devices_stale_7d",
                    "Devices not seen in over 7 days",
                    [({}, float(row["stale_7d"]))],
                ))
            except Exception:
                logger.exception("metrics: device discovery query failed")

            # --- CVE watch metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_cves,
                        COUNT(*) FILTER (WHERE severity = 'critical') as critical_cves,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as new_7d
                    FROM cve_entries
                """)
                sections.append(_gauge(
                    "osiriscare_cve_total",
                    "Total tracked CVEs",
                    [({}, float(row["total_cves"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_cve_critical",
                    "Critical severity CVEs",
                    [({}, float(row["critical_cves"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_cve_new_7d",
                    "CVEs discovered in last 7 days",
                    [({}, float(row["new_7d"]))],
                ))
            except Exception:
                logger.exception("metrics: CVE watch query failed")

            # --- Flywheel promotion pipeline health (gauge) ---
            try:
                # Candidate pipeline stages
                cand = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE approval_status = 'pending') as pending,
                        COUNT(*) FILTER (WHERE approval_status = 'approved') as approved,
                        COUNT(*) FILTER (WHERE approval_status = 'rejected') as rejected
                    FROM learning_promotion_candidates
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_candidates",
                    "Learning promotion candidates by status",
                    [
                        ({"status": "pending"}, float(cand["pending"] or 0)),
                        ({"status": "approved"}, float(cand["approved"] or 0)),
                        ({"status": "rejected"}, float(cand["rejected"] or 0)),
                    ],
                ))

                # Promoted rules by source
                pr_source = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE status = 'active') as active,
                        COUNT(*) FILTER (WHERE status = 'disabled') as disabled
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_promoted_rules",
                    "Promoted rules by status",
                    [
                        ({"status": "active"}, float(pr_source["active"] or 0)),
                        ({"status": "disabled"}, float(pr_source["disabled"] or 0)),
                    ],
                ))

                # L1 rules by source
                l1_src = await conn.fetch("""
                    SELECT source, COUNT(*) as cnt
                    FROM l1_rules WHERE enabled = true
                    GROUP BY source
                """)
                if l1_src:
                    sections.append(_gauge(
                        "osiriscare_flywheel_l1_rules_by_source",
                        "Enabled L1 rules by source (built-in vs promoted vs synced)",
                        [({"source": r["source"] or "unknown"}, float(r["cnt"])) for r in l1_src],
                    ))

                # Stuck candidates (approved but no promoted_rules row) — ALERT metric
                stuck_count = await conn.fetchval("""
                    SELECT COUNT(*) FROM learning_promotion_candidates lpc
                    LEFT JOIN promoted_rules pr
                        ON pr.pattern_signature = lpc.pattern_signature
                        AND pr.site_id = lpc.site_id
                    WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_stuck_candidates",
                    "Approved candidates with no promoted_rules row (alert if >0)",
                    [({}, float(stuck_count or 0))],
                ))

                # Eligibility pipeline: how many patterns awaiting manual approval
                eligible_waiting = await conn.fetchval("""
                    SELECT COUNT(*) FROM aggregated_pattern_stats
                    WHERE promotion_eligible = true
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_eligible_waiting",
                    "Patterns eligible for promotion but not yet promoted",
                    [({}, float(eligible_waiting or 0))],
                ))

                # Promotion rate (promotions per day last 7d)
                promo_rate = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '24 hours') as last_24h,
                        COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '7 days') as last_7d
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_promotion_rate_24h",
                    "New promotions in the last 24 hours",
                    [({}, float(promo_rate["last_24h"] or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_flywheel_promotion_rate_7d",
                    "New promotions in the last 7 days",
                    [({}, float(promo_rate["last_7d"] or 0))],
                ))

                # Pipeline stall detector — time since last promotion
                stall = await conn.fetchval("""
                    SELECT EXTRACT(EPOCH FROM (NOW() - MAX(promoted_at)))
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_last_promotion_age_seconds",
                    "Seconds since the most recent promotion (alert if >604800 = 7d)",
                    [({}, float(stall or 0))],
                ))
            except Exception:
                logger.exception("metrics: flywheel query failed")

            # --- Mesh health (gauge) ---
            try:
                # Per-site mesh state: ring size, peers, assignment coverage
                rows = await conn.fetch("""
                    SELECT
                        site_id,
                        COUNT(*) as appliance_count,
                        COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online_count,
                        AVG(
                            CASE WHEN daemon_health IS NOT NULL
                                THEN (daemon_health->>'mesh_ring_size')::int
                                ELSE NULL END
                        ) as avg_ring_size,
                        AVG(
                            CASE WHEN daemon_health IS NOT NULL
                                THEN (daemon_health->>'mesh_peer_count')::int
                                ELSE NULL END
                        ) as avg_peer_count
                    FROM site_appliances
                    WHERE last_checkin > NOW() - INTERVAL '10 minutes'
                    GROUP BY site_id
                    HAVING COUNT(*) > 1
                """)
                if rows:
                    sections.append(_gauge(
                        "osiriscare_mesh_appliance_count",
                        "Number of appliances per mesh site",
                        [({"site": r["site_id"][:40]}, float(r["appliance_count"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_online_count",
                        "Online appliances per mesh site",
                        [({"site": r["site_id"][:40]}, float(r["online_count"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_avg_ring_size",
                        "Average ring size reported by appliances (should equal online_count)",
                        [({"site": r["site_id"][:40]}, float(r["avg_ring_size"] or 0)) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_avg_peer_count",
                        "Average peer count per appliance (should equal ring_size - 1)",
                        [({"site": r["site_id"][:40]}, float(r["avg_peer_count"] or 0)) for r in rows],
                    ))

                # Assignment drift: ring_size vs online_count mismatch
                drift_row = await conn.fetchrow("""
                    SELECT COUNT(DISTINCT site_id) as drift_sites
                    FROM (
                        SELECT site_id,
                               COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online,
                               AVG((daemon_health->>'mesh_ring_size')::int) as ring
                        FROM site_appliances
                        WHERE last_checkin > NOW() - INTERVAL '10 minutes'
                          AND daemon_health IS NOT NULL
                        GROUP BY site_id
                        HAVING COUNT(*) > 1
                    ) s
                    WHERE s.online != s.ring
                """)
                sections.append(_gauge(
                    "osiriscare_mesh_drift_sites",
                    "Sites where ring size disagrees with online appliance count (alert if >0)",
                    [({}, float(drift_row["drift_sites"] or 0))],
                ))

                # Coverage gaps: targets with wrong number of assignments
                # (should be exactly 1 owner per target in a healthy mesh)
                try:
                    gap_row = await conn.fetchrow("""
                        WITH all_targets AS (
                            SELECT site_id,
                                   jsonb_array_elements_text(assigned_targets) as target
                            FROM site_appliances
                            WHERE assigned_targets IS NOT NULL
                              AND last_checkin > NOW() - INTERVAL '10 minutes'
                        )
                        SELECT
                            COUNT(*) FILTER (WHERE assignment_count > 1) as overlaps,
                            COUNT(*) FILTER (WHERE assignment_count = 0) as orphans
                        FROM (
                            SELECT site_id, target, COUNT(*) as assignment_count
                            FROM all_targets
                            GROUP BY site_id, target
                        ) t
                    """)
                    sections.append(_gauge(
                        "osiriscare_mesh_target_overlaps",
                        "Targets assigned to multiple appliances (duplicate scans, alert if >0)",
                        [({}, float(gap_row["overlaps"] or 0))],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_target_orphans",
                        "Targets with no owner (coverage hole, alert if >0)",
                        [({}, float(gap_row["orphans"] or 0))],
                    ))
                except Exception:
                    pass

                # Audit log rate (assignments changing per hour)
                audit_row = await conn.fetchrow("""
                    SELECT COUNT(*) as changes_1h
                    FROM mesh_assignment_audit
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                """)
                sections.append(_gauge(
                    "osiriscare_mesh_assignment_changes_1h",
                    "Mesh assignment changes in last hour (high rate = instability)",
                    [({}, float(audit_row["changes_1h"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: mesh query failed")

            # --- OTS proof pipeline health (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
                        COUNT(*) FILTER (WHERE status = 'pending') as pending,
                        COUNT(*) FILTER (WHERE status = 'expired') as expired,
                        COUNT(*) FILTER (WHERE status = 'verified') as verified,
                        EXTRACT(EPOCH FROM (NOW() - MIN(submitted_at))) FILTER (
                            WHERE status = 'pending'
                        ) as oldest_pending_age_seconds,
                        EXTRACT(EPOCH FROM (NOW() - MAX(anchored_at))) FILTER (
                            WHERE status = 'anchored'
                        ) as latest_anchor_age_seconds
                    FROM ots_proofs
                """)
                sections.append(_gauge(
                    "osiriscare_ots_proofs",
                    "OTS proofs by status",
                    [
                        ({"status": "anchored"}, float(row["anchored"] or 0)),
                        ({"status": "pending"}, float(row["pending"] or 0)),
                        ({"status": "expired"}, float(row["expired"] or 0)),
                        ({"status": "verified"}, float(row["verified"] or 0)),
                    ],
                ))
                # SLA metric: age of oldest pending proof (alert if > 24h)
                sections.append(_gauge(
                    "osiriscare_ots_oldest_pending_seconds",
                    "Age in seconds of the oldest pending OTS proof (alert if >86400)",
                    [({}, float(row["oldest_pending_age_seconds"] or 0))],
                ))
                # SLA metric: time since last successful anchor (alert if > 6h)
                sections.append(_gauge(
                    "osiriscare_ots_latest_anchor_age_seconds",
                    "Age in seconds since the most recent OTS anchor (alert if >21600)",
                    [({}, float(row["latest_anchor_age_seconds"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: OTS proofs query failed")

            # --- OTS calendar health (gauge) ---
            try:
                # Per-calendar anchor counts in last 24h
                rows = await conn.fetch("""
                    SELECT
                        calendar_url,
                        COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
                        COUNT(*) as total
                    FROM ots_proofs
                    WHERE submitted_at > NOW() - INTERVAL '24 hours'
                      AND calendar_url IS NOT NULL
                    GROUP BY calendar_url
                """)
                if rows:
                    sections.append(_gauge(
                        "osiriscare_ots_calendar_success_24h",
                        "Per-calendar anchor success count in last 24h",
                        [({"calendar": r["calendar_url"][:60]}, float(r["anchored"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_ots_calendar_total_24h",
                        "Per-calendar total proof count in last 24h",
                        [({"calendar": r["calendar_url"][:60]}, float(r["total"])) for r in rows],
                    ))
            except Exception:
                logger.exception("metrics: OTS calendar query failed")

            # --- Pattern sync health (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE sync_status = 'success') as success,
                        COUNT(*) FILTER (WHERE sync_status = 'partial') as partial,
                        COUNT(*) FILTER (WHERE sync_status = 'failed') as failed
                    FROM appliance_pattern_sync
                    WHERE synced_at > NOW() - INTERVAL '24 hours'
                """)
                sections.append(_gauge(
                    "osiriscare_pattern_sync_24h",
                    "Pattern sync results in last 24 hours",
                    [
                        ({"status": "success"}, float(row["success"])),
                        ({"status": "partial"}, float(row["partial"])),
                        ({"status": "failed"}, float(row["failed"])),
                    ],
                ))
            except Exception:
                logger.exception("metrics: pattern sync query failed")

    except Exception:
        logger.exception("metrics: database connection failed")
        return PlainTextResponse(
            "# Database query failed\n",
            media_type=PROM_CONTENT_TYPE,
            status_code=503,
        )

    body = "\n\n".join(sections) + "\n"
    return PlainTextResponse(body, media_type=PROM_CONTENT_TYPE)
