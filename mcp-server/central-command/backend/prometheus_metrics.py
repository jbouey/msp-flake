"""Prometheus-compatible metrics endpoint.

Exposes platform health metrics in Prometheus text exposition format.
No authentication required (standard for Prometheus scraping).
Generates text format manually — no prometheus_client dependency needed.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

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
async def prometheus_metrics():
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

    except Exception:
        logger.exception("metrics: database connection failed")
        return PlainTextResponse(
            "# Database query failed\n",
            media_type=PROM_CONTENT_TYPE,
            status_code=503,
        )

    body = "\n\n".join(sections) + "\n"
    return PlainTextResponse(body, media_type=PROM_CONTENT_TYPE)
