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
