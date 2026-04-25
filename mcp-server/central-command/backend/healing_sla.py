"""Per-site healing rate SLA tracking — background loop + query helpers.

Runs hourly. For each active site, queries execution_telemetry for the
last hour, calculates healing rate, upserts into site_healing_sla,
and sends an alert if the rate falls below the SLA target.

Wired into main.py lifespan via healing_sla_loop().
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger("healing_sla")


async def healing_sla_loop():
    """Background loop: compute hourly healing SLA per site.

    Startup delay: 5 minutes (after pool is ready).
    Interval: 1 hour.
    """
    await asyncio.sleep(300)  # Wait 5 min after startup for pool to be ready
    logger.info("Healing SLA tracker started")

    while True:
        try:
            await _compute_hourly_sla()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Healing SLA computation error: {e}", exc_info=True)

        await asyncio.sleep(3600)  # Every hour


async def _compute_hourly_sla():
    """Single pass: compute healing rate for the last hour per active site."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection
    from dashboard_api.email_alerts import send_critical_alert

    pool = await get_pool()
    now = datetime.now(timezone.utc)
    period_end = now.replace(minute=0, second=0, microsecond=0)
    period_start = period_end - timedelta(hours=1)

    async with admin_connection(pool) as conn:
        # Get all active sites
        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name
            FROM sites s
            WHERE s.status = 'active'
        """)

        if not sites:
            logger.debug("No active sites for SLA computation")
            return

        for site in sites:
            site_id = site["site_id"]
            clinic_name = site["clinic_name"] or site_id

            # Query execution_telemetry for this site in the last hour
            stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE success = true) AS succeeded
                FROM execution_telemetry
                WHERE site_id = $1
                  AND created_at >= $2
                  AND created_at < $3
            """, site_id, period_start, period_end)

            total = stats["total"] or 0
            succeeded = stats["succeeded"] or 0

            if total == 0:
                # No healing attempts — skip (don't pollute SLA with zero-data periods)
                continue

            rate = round(succeeded / total * 100, 2)

            # Get the SLA target (from most recent row, or default 90.0)
            target_row = await conn.fetchrow("""
                SELECT sla_target FROM site_healing_sla
                WHERE site_id = $1
                ORDER BY period_start DESC LIMIT 1
            """, site_id)
            sla_target = float(target_row["sla_target"]) if target_row else 90.0

            sla_met = rate >= sla_target

            # Upsert the SLA record
            await conn.execute("""
                INSERT INTO site_healing_sla
                    (site_id, period_start, period_end, total_attempts,
                     successful_heals, healing_rate, sla_target, sla_met)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (site_id, period_start)
                DO UPDATE SET
                    total_attempts = EXCLUDED.total_attempts,
                    successful_heals = EXCLUDED.successful_heals,
                    healing_rate = EXCLUDED.healing_rate,
                    sla_target = EXCLUDED.sla_target,
                    sla_met = EXCLUDED.sla_met
            """, site_id, period_start, period_end,
                total, succeeded, rate, sla_target, sla_met)

            if not sla_met:
                logger.warning(
                    f"SLA breach: {clinic_name} ({site_id}) "
                    f"rate={rate}% target={sla_target}% "
                    f"({succeeded}/{total} heals)"
                )
                # Send alert via existing notification + email pattern
                await conn.execute("""
                    INSERT INTO notifications
                        (id, title, message, severity, category, site_id, created_at)
                    VALUES (gen_random_uuid(),
                            'Healing SLA Breach',
                            $1, 'warning', 'healing_sla', $2, NOW())
                """,
                    f"Site {clinic_name} healing rate {rate}% is below "
                    f"SLA target {sla_target}% ({succeeded}/{total} successful "
                    f"heals in the last hour)",
                    site_id,
                )
                # Also send email alert
                send_critical_alert(
                    title=f"Healing SLA Breach — {clinic_name}",
                    message=(
                        f"Healing rate for {clinic_name} ({site_id}) dropped to "
                        f"{rate}% (target: {sla_target}%).\n\n"
                        f"Period: {period_start.isoformat()} — {period_end.isoformat()}\n"
                        f"Attempts: {total}, Successful: {succeeded}"
                    ),
                    site_id=site_id,
                    category="healing_sla",
                    severity="warning",
                )

        logger.info(
            f"SLA computation complete for {len(sites)} sites, "
            f"period {period_start.isoformat()} — {period_end.isoformat()}"
        )


async def get_sla_overview(conn) -> List[Dict[str, Any]]:
    """Query SLA overview for all sites — used by the admin endpoint.

    Returns per-site: site_id, clinic_name, current_rate, sla_target,
    sla_met, trend (last 7 periods).

    Args:
        conn: asyncpg connection (from admin_connection context manager).
    """
    # Get the latest SLA row per site + site name
    latest_rows = await conn.fetch("""
        SELECT DISTINCT ON (sla.site_id)
            sla.site_id,
            s.clinic_name,
            sla.healing_rate AS current_rate,
            sla.sla_target,
            sla.sla_met,
            sla.period_start,
            sla.period_end,
            sla.total_attempts,
            sla.successful_heals
        FROM site_healing_sla sla
        JOIN sites s ON s.site_id = sla.site_id
        ORDER BY sla.site_id, sla.period_start DESC
    """)

    if not latest_rows:
        return []

    # Batch-fetch trend data (last 7 periods per site)
    site_ids = [r["site_id"] for r in latest_rows]
    trend_rows = await conn.fetch("""
        SELECT site_id, period_start, healing_rate, sla_met,
               total_attempts, successful_heals
        FROM site_healing_sla
        WHERE site_id = ANY($1)
          AND period_start >= NOW() - INTERVAL '7 hours'
        ORDER BY site_id, period_start ASC
    """, site_ids)

    # Group trends by site_id
    trends_by_site: Dict[str, list] = {}
    for row in trend_rows:
        sid = row["site_id"]
        trends_by_site.setdefault(sid, []).append({
            "period_start": row["period_start"].isoformat(),
            "healing_rate": float(row["healing_rate"]),
            "sla_met": row["sla_met"],
            "total_attempts": row["total_attempts"],
            "successful_heals": row["successful_heals"],
        })

    results = []
    for row in latest_rows:
        sid = row["site_id"]
        results.append({
            "site_id": sid,
            "clinic_name": row["clinic_name"] or sid,
            "current_rate": float(row["current_rate"]),
            "sla_target": float(row["sla_target"]),
            "sla_met": row["sla_met"],
            "period_start": row["period_start"].isoformat(),
            "period_end": row["period_end"].isoformat(),
            "total_attempts": row["total_attempts"],
            "successful_heals": row["successful_heals"],
            "trend": trends_by_site.get(sid, []),
        })

    return results
