"""Appliance Health Monitor — background loop detecting offline appliances.

Runs every 5 minutes. Detects appliances that stopped checking in,
sends notifications at 30min and 2hr thresholds, and clears flags
when appliances come back online.

Wired into main.py lifespan via health_monitor_loop().
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("health_monitor")


async def health_monitor_loop():
    """Background loop: detect offline appliances and notify.

    Thresholds:
    - 15 min without checkin: mark offline_since
    - 30 min: send warning notification (once)
    - 2 hours: escalate to critical notification (once)
    - On next checkin: clear offline_since + offline_notified (done in sites.py)
    """
    await asyncio.sleep(180)  # Wait 3 min after startup for pool to be ready
    logger.info("Health monitor started")

    while True:
        try:
            await _check_appliance_health()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Health monitor error: {e}", exc_info=True)

        await asyncio.sleep(300)  # Every 5 minutes


async def _check_appliance_health():
    """Single pass: detect newly offline, notify at thresholds, clear recovered."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    now = datetime.now(timezone.utc)
    threshold_offline = now - timedelta(minutes=15)
    threshold_warn = now - timedelta(minutes=30)
    threshold_critical = now - timedelta(hours=2)

    async with admin_connection(pool) as conn:
        # --- Step 1: Mark newly offline appliances ---
        newly_offline = await conn.fetch("""
            UPDATE site_appliances
            SET offline_since = last_checkin
            WHERE last_checkin < $1
              AND last_checkin IS NOT NULL
              AND offline_since IS NULL
              AND status != 'pending'
            RETURNING appliance_id, site_id, hostname, last_checkin
        """, threshold_offline)

        if newly_offline:
            logger.info(f"Detected {len(newly_offline)} newly offline appliance(s)")

        # --- Step 2: Send warning for 30min+ offline (not yet notified) ---
        warn_appliances = await conn.fetch("""
            SELECT sa.appliance_id, sa.site_id, sa.hostname, sa.last_checkin,
                   sa.offline_since, sa.agent_version, sa.ip_addresses,
                   s.clinic_name as site_name
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            WHERE sa.offline_since IS NOT NULL
              AND sa.offline_since < $1
              AND sa.offline_notified = false
        """, threshold_warn)

        for row in warn_appliances:
            minutes_offline = int((now - row["offline_since"]).total_seconds() / 60)
            await _send_offline_notification(
                conn=conn,
                severity="warning",
                site_id=row["site_id"],
                appliance_id=row["appliance_id"],
                hostname=row["hostname"],
                site_name=row["site_name"],
                last_checkin=row["last_checkin"],
                minutes_offline=minutes_offline,
                agent_version=row["agent_version"],
            )
            # Mark as notified (warning sent)
            await conn.execute("""
                UPDATE site_appliances
                SET offline_notified = true
                WHERE appliance_id = $1
            """, row["appliance_id"])

        if warn_appliances:
            logger.warning(f"Sent {len(warn_appliances)} offline warning(s)")

        # --- Step 3: Escalate to critical for 2hr+ offline ---
        # Only escalate if not already escalated (check notifications table)
        critical_appliances = await conn.fetch("""
            SELECT sa.appliance_id, sa.site_id, sa.hostname, sa.last_checkin,
                   sa.offline_since, sa.agent_version, sa.ip_addresses,
                   s.clinic_name as site_name
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            WHERE sa.offline_since IS NOT NULL
              AND sa.offline_since < $1
              AND sa.offline_notified = true
              AND NOT EXISTS (
                  SELECT 1 FROM notifications n
                  WHERE n.site_id = sa.site_id
                    AND n.severity = 'critical'
                    AND n.category = 'appliance_offline'
                    AND n.created_at > sa.offline_since
              )
        """, threshold_critical)

        for row in critical_appliances:
            hours_offline = round((now - row["offline_since"]).total_seconds() / 3600, 1)
            await _send_offline_notification(
                conn=conn,
                severity="critical",
                site_id=row["site_id"],
                appliance_id=row["appliance_id"],
                hostname=row["hostname"],
                site_name=row["site_name"],
                last_checkin=row["last_checkin"],
                minutes_offline=int(hours_offline * 60),
                agent_version=row["agent_version"],
            )

        if critical_appliances:
            logger.critical(f"Sent {len(critical_appliances)} critical offline escalation(s)")

        # --- Step 4: Clear recovered appliances ---
        # (Appliances that came back online since we last checked)
        recovered = await conn.fetch("""
            UPDATE site_appliances
            SET offline_since = NULL,
                offline_notified = false
            WHERE offline_since IS NOT NULL
              AND last_checkin > offline_since
            RETURNING appliance_id, site_id, hostname
        """)

        for row in recovered:
            logger.info(f"Appliance recovered: {row['hostname']} ({row['appliance_id']})")
            # Send recovery notification
            await _send_recovery_notification(
                conn=conn,
                site_id=row["site_id"],
                appliance_id=row["appliance_id"],
                hostname=row["hostname"],
            )


async def _send_offline_notification(
    conn,
    severity: str,
    site_id: str,
    appliance_id: str,
    hostname: str,
    site_name: str,
    last_checkin: datetime,
    minutes_offline: int,
    agent_version: str,
):
    """Insert an offline notification into the notifications table."""
    import json

    if minutes_offline >= 120:
        duration_str = f"{minutes_offline // 60}h {minutes_offline % 60}m"
    else:
        duration_str = f"{minutes_offline}m"

    site_label = site_name or site_id

    if severity == "critical":
        title = f"CRITICAL: {hostname} offline for {duration_str}"
        message = (
            f"Appliance {hostname} at site {site_label} has been offline for {duration_str}. "
            f"Last check-in at {last_checkin.strftime('%Y-%m-%d %H:%M UTC')}. "
            f"Agent version: {agent_version}. "
            f"Compliance monitoring and healing are suspended. Investigate immediately."
        )
    else:
        title = f"Appliance {hostname} offline for {duration_str}"
        message = (
            f"Appliance {hostname} at site {site_label} has not checked in for {duration_str}. "
            f"Last check-in at {last_checkin.strftime('%Y-%m-%d %H:%M UTC')}. "
            f"Agent version: {agent_version}. "
            f"If the appliance does not recover within 2 hours, this will escalate to critical."
        )

    metadata = json.dumps({
        "appliance_id": appliance_id,
        "hostname": hostname,
        "last_checkin": last_checkin.isoformat(),
        "minutes_offline": minutes_offline,
        "agent_version": agent_version,
    })

    await conn.execute("""
        INSERT INTO notifications (site_id, appliance_id, severity, category, title, message, metadata)
        VALUES ($1, $2, $3, 'appliance_offline', $4, $5, $6::jsonb)
    """, site_id, appliance_id, severity, title, message, metadata)

    logger.info(f"[{severity}] {title}")


async def _send_recovery_notification(conn, site_id: str, appliance_id: str, hostname: str):
    """Send a notification when an appliance comes back online."""
    import json

    await conn.execute("""
        INSERT INTO notifications (site_id, appliance_id, severity, category, title, message, metadata)
        VALUES ($1, $2, 'info', 'appliance_recovery', $3, $4, $5::jsonb)
    """,
        site_id,
        appliance_id,
        f"Appliance {hostname} back online",
        f"Appliance {hostname} has recovered and is checking in again.",
        json.dumps({"appliance_id": appliance_id, "hostname": hostname}),
    )
