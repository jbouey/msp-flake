"""Appliance Health Monitor — background loop detecting offline appliances.

Runs every 5 minutes. Detects appliances that stopped checking in,
sends notifications at 30min and 2hr thresholds, and clears flags
when appliances come back online.

Wired into main.py lifespan via health_monitor_loop().
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from dashboard_api.email_alerts import send_critical_alert

logger = logging.getLogger("health_monitor")


async def health_monitor_loop():
    """Background loop: detect offline appliances, stuck queues, and notify.

    Thresholds:
    - 15 min without checkin: mark offline_since
    - 30 min: send warning notification (once)
    - 2 hours: escalate to critical notification (once)
    - On next checkin: clear offline_since + offline_notified (done in sites.py)

    Stuck queue detection (every pass):
    - Escalation tickets open >24h without update
    - Integration syncs failed in last hour
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

        try:
            await _check_stuck_queues()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Stuck queue check error: {e}", exc_info=True)

        try:
            await _check_oauth_health()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"OAuth health check error: {e}", exc_info=True)

        try:
            await _check_device_reachability()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Device reachability check error: {e}", exc_info=True)

        try:
            await _resolve_stale_incidents()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Stale incident cleanup error: {e}", exc_info=True)

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

    # State-based dedup: update existing unread notification instead of creating duplicates
    existing = await conn.fetchval("""
        SELECT id FROM notifications
        WHERE site_id = $1 AND category = 'appliance_offline'
        AND is_read = false AND is_dismissed = false
        LIMIT 1
    """, site_id)

    if existing:
        await conn.execute("""
            UPDATE notifications
            SET message = $2, metadata = $3::jsonb, severity = $4, created_at = NOW()
            WHERE id = $1
        """, existing, message, metadata, severity)
    else:
        await conn.execute("""
            INSERT INTO notifications (site_id, appliance_id, severity, category, title, message, metadata)
            VALUES ($1, $2, $3, 'appliance_offline', $4, $5, $6::jsonb)
        """, site_id, appliance_id, severity, title, message, metadata)

    logger.info(f"[{severity}] {title}")

    # Send email alert for offline appliances
    try:
        send_critical_alert(
            title=title,
            message=message,
            site_id=site_id,
            category="appliance_offline",
            severity=severity,
            host_id=hostname,
            metadata={
                "appliance_id": appliance_id,
                "hostname": hostname,
                "last_checkin": last_checkin.isoformat(),
                "minutes_offline": minutes_offline,
                "agent_version": agent_version,
            },
            recommended_action=(
                "Check network connectivity and power status of the appliance. "
                "If the appliance is unreachable, a site visit may be required."
            ),
        )
    except Exception as e:
        logger.error(f"Failed to send offline alert email for {hostname}: {e}")


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


async def _check_stuck_queues():
    """Detect stuck escalation tickets and failed integration syncs.

    Alerts once per stuck item per 24h window (deduped via notifications table).
    """
    import json
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # --- Escalation tickets stuck >24h ---
        try:
            stuck_tickets = await conn.fetch("""
                SELECT id, site_id, title, severity, created_at,
                       EXTRACT(EPOCH FROM NOW() - created_at) / 3600 as hours_open
                FROM escalation_tickets
                WHERE status NOT IN ('resolved', 'closed')
                  AND created_at < NOW() - INTERVAL '24 hours'
                  AND NOT EXISTS (
                      SELECT 1 FROM notifications n
                      WHERE n.category = 'stuck_escalation'
                        AND n.metadata::jsonb->>'ticket_id' = escalation_tickets.id::text
                        AND n.created_at > NOW() - INTERVAL '24 hours'
                  )
            """)

            for ticket in stuck_tickets:
                hours = round(ticket["hours_open"], 1)
                title = f"Escalation ticket stuck for {hours}h: {ticket['title']}"
                message = (
                    f"Escalation ticket #{ticket['id']} has been open for {hours} hours "
                    f"without resolution. Original severity: {ticket['severity']}. "
                    f"Review and resolve at the L4 queue."
                )
                await conn.execute("""
                    INSERT INTO notifications (site_id, severity, category, title, message, metadata)
                    VALUES ($1, 'warning', 'stuck_escalation', $2, $3, $4::jsonb)
                """,
                    ticket["site_id"],
                    title,
                    message,
                    json.dumps({"ticket_id": str(ticket["id"]), "hours_open": hours}),
                )
                logger.warning(f"Stuck escalation: {title}")

                # Send email alert
                try:
                    send_critical_alert(
                        title=title,
                        message=message,
                        site_id=ticket["site_id"],
                        category="stuck_escalation",
                        severity="warning",
                        recommended_action="Check the L4 escalation queue in the admin dashboard.",
                    )
                except Exception as e:
                    logger.error(f"Failed to send stuck escalation email: {e}")

            if stuck_tickets:
                logger.warning(f"Found {len(stuck_tickets)} stuck escalation ticket(s)")
        except Exception:
            logger.exception("Stuck queue check: escalation query failed")

        # --- Integration sync failures in last hour ---
        try:
            failed_syncs = await conn.fetch("""
                SELECT id, integration_type, error_message, created_at
                FROM integration_sync_log
                WHERE status = 'failed'
                  AND created_at > NOW() - INTERVAL '1 hour'
                  AND NOT EXISTS (
                      SELECT 1 FROM notifications n
                      WHERE n.category = 'integration_sync_failure'
                        AND n.metadata::jsonb->>'sync_id' = integration_sync_log.id::text
                        AND n.created_at > NOW() - INTERVAL '1 hour'
                  )
            """)

            for sync in failed_syncs:
                title = f"Integration sync failed: {sync['integration_type']}"
                message = f"Sync error: {sync['error_message'] or 'Unknown error'}"
                await conn.execute("""
                    INSERT INTO notifications (severity, category, title, message, metadata)
                    VALUES ('warning', 'integration_sync_failure', $1, $2, $3::jsonb)
                """,
                    title,
                    message,
                    json.dumps({"sync_id": str(sync["id"]), "type": sync["integration_type"]}),
                )
                logger.warning(f"Integration sync failure: {title}")

            if failed_syncs:
                logger.warning(f"Found {len(failed_syncs)} integration sync failure(s)")
        except Exception:
            # Table may not exist yet — not critical
            logger.debug("Stuck queue check: integration sync query failed (table may not exist)")


OAUTH_PROVIDERS = ("google_workspace", "okta", "azure_ad", "microsoft_graph")


async def _check_oauth_health():
    """Check health of OAuth integrations and update health_status column.

    Evaluates three conditions for each connected OAuth integration:
    - Token expired: access_token_expires_at < now
    - Consecutive failures >= 3
    - Stale sync: last_sync_success_at is NULL or > 24h ago

    Any condition triggers a status change. Multiple bad signals → unhealthy.
    Single bad signal → degraded. All clear → healthy.
    Creates deduped notifications for degraded/unhealthy transitions.
    """
    import json
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(hours=24)

    async with admin_connection(pool) as conn:
        try:
            rows = await conn.fetch("""
                SELECT id, site_id, provider, name, health_status,
                       access_token_expires_at, consecutive_failures,
                       last_sync_success_at
                FROM integrations
                WHERE status = 'connected'
                  AND provider = ANY($1)
            """, list(OAUTH_PROVIDERS))
        except Exception:
            logger.debug("OAuth health check: integrations query failed (table may not exist)")
            return

        updated = 0
        for row in rows:
            problems = []

            # Check token expiry
            if row["access_token_expires_at"] and row["access_token_expires_at"] < now:
                problems.append("token_expired")

            # Check consecutive failures
            failures = row["consecutive_failures"] or 0
            if failures >= 3:
                problems.append(f"{failures}_consecutive_failures")

            # Check stale sync
            if row["last_sync_success_at"] is None or row["last_sync_success_at"] < stale_threshold:
                problems.append("stale_sync")

            # Determine new status
            if len(problems) >= 2:
                new_status = "unhealthy"
            elif len(problems) == 1:
                new_status = "degraded"
            else:
                new_status = "healthy"

            old_status = row["health_status"] or "unknown"
            if new_status == old_status:
                continue

            # Update health_status
            await conn.execute("""
                UPDATE integrations SET health_status = $1 WHERE id = $2
            """, new_status, row["id"])
            updated += 1

            if new_status == "healthy":
                # Recovery — log it but no notification needed
                logger.info(
                    f"OAuth integration recovered: {row['name']} ({row['provider']}) → healthy"
                )
                continue

            # Send notification for degraded/unhealthy (deduped: once per 24h per integration)
            existing = await conn.fetchval("""
                SELECT 1 FROM notifications
                WHERE category = 'oauth_health'
                  AND metadata::jsonb->>'integration_id' = $1
                  AND created_at > $2
                LIMIT 1
            """, str(row["id"]), stale_threshold)

            if existing:
                continue

            severity = "critical" if new_status == "unhealthy" else "warning"
            title = f"OAuth integration {new_status}: {row['name']}"
            message = (
                f"Integration {row['name']} ({row['provider']}) is {new_status}. "
                f"Issues: {', '.join(problems)}."
            )

            await conn.execute("""
                INSERT INTO notifications (site_id, severity, category, title, message, metadata)
                VALUES ($1, $2, 'oauth_health', $3, $4, $5::jsonb)
            """,
                str(row["site_id"]),
                severity,
                title,
                message,
                json.dumps({
                    "integration_id": str(row["id"]),
                    "provider": row["provider"],
                    "problems": problems,
                }),
            )
            logger.warning(f"[{severity}] {title}")

        if updated:
            logger.info(f"OAuth health check: updated {updated} integration(s)")


async def _check_device_reachability():
    """Roll up device_unreachable incidents into partner notifications."""
    import json
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Find sites with unreachable devices in last hour
        rows = await conn.fetch("""
            SELECT i.site_id, s.clinic_name,
                   COUNT(*) as unreachable_count,
                   array_agg(DISTINCT i.details->>'hostname') as hosts
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE i.check_type = 'device_unreachable'
              AND i.status = 'active'
              AND i.created_at > NOW() - INTERVAL '1 hour'
            GROUP BY i.site_id, s.clinic_name
            HAVING COUNT(*) >= 1
        """)

        for row in rows:
            # Check dedup: don't re-notify within 24h
            existing = await conn.fetchval("""
                SELECT 1 FROM notifications
                WHERE category = 'device_unreachable'
                  AND site_id = $1
                  AND created_at > NOW() - INTERVAL '24 hours'
                LIMIT 1
            """, row["site_id"])

            if existing:
                continue

            hosts = [h for h in (row["hosts"] or []) if h]
            await conn.execute("""
                INSERT INTO notifications (site_id, severity, category, title, message, metadata)
                VALUES ($1, 'warning', 'device_unreachable', $2, $3, $4::jsonb)
            """,
                row["site_id"],
                f"{row['unreachable_count']} device(s) unreachable at {row['clinic_name']}",
                f"Devices not responding: {', '.join(hosts[:5])}",
                json.dumps({"hosts": hosts, "count": row["unreachable_count"]}),
            )
            logger.warning(f"[health] {row['unreachable_count']} unreachable devices at {row['clinic_name']}")


async def _resolve_stale_incidents():
    """Auto-resolve incidents stuck in open/resolving/escalated for >7 days.

    These are incidents that the healing pipeline cannot fix (monitoring-only
    types, agent deploy failures, credential issues). Leaving them open
    permanently inflates the active-incident count and obscures real issues.

    Only resolves incidents whose types are NOT actively being healed — if
    execution_telemetry shows recent attempts, the pipeline is still working
    on it and we leave it alone.
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Resolve open/resolving incidents >7d with no recent healing attempts
        result = await conn.fetch("""
            UPDATE incidents SET
                status = 'resolved',
                resolved_at = NOW(),
                resolution_tier = 'monitoring'
            WHERE id IN (
                SELECT i.id FROM incidents i
                WHERE i.status IN ('open', 'resolving')
                AND i.created_at < NOW() - INTERVAL '7 days'
                AND NOT EXISTS (
                    SELECT 1 FROM execution_telemetry et
                    WHERE et.incident_type = i.incident_type
                    AND et.created_at > NOW() - INTERVAL '24 hours'
                )
            )
            RETURNING id, incident_type
        """)

        # Escalated incidents already exhausted L1/L2/L3. If stuck >7 days,
        # they won't self-heal — resolve them regardless of recent telemetry.
        escalated_result = await conn.fetch("""
            UPDATE incidents SET
                status = 'resolved',
                resolved_at = NOW(),
                resolution_tier = 'L3'
            WHERE id IN (
                SELECT i.id FROM incidents i
                WHERE i.status = 'escalated'
                AND i.created_at < NOW() - INTERVAL '7 days'
            )
            RETURNING id, incident_type
        """)
        result = list(result) + list(escalated_result)

        if result:
            for row in result:
                logger.info("Auto-resolved stale incident",
                            incident_id=str(row["id"]),
                            incident_type=row["incident_type"])
            logger.info(f"Resolved {len(result)} stale incidents (>7d, no recent healing)")

        # Transfer device ownership when owning appliance can't see the device anymore.
        # If appliance A owned a device but hasn't updated its last_seen_at in >30 min,
        # and appliance B has seen it recently, transfer ownership to B.
        try:
            transferred = await conn.fetch("""
                UPDATE discovered_devices d1
                SET owner_appliance_id = d2.appliance_id::uuid, owned_since = NOW()
                FROM discovered_devices d2
                WHERE d1.ip_address = d2.ip_address
                AND d1.site_id != d2.site_id
                AND d1.owner_appliance_id IS NOT NULL
                AND d1.owner_appliance_id != d2.appliance_id::uuid
                AND d1.last_seen_at < NOW() - INTERVAL '30 minutes'
                AND d2.last_seen_at > NOW() - INTERVAL '15 minutes'
                RETURNING d1.ip_address, d1.site_id as from_site, d2.site_id as to_site
            """)
            if transferred:
                for t in transferred:
                    logger.info("Device ownership transferred",
                                ip=t["ip_address"],
                                from_site=t["from_site"],
                                to_site=t["to_site"])
        except Exception as e:
            logger.debug(f"Device ownership transfer check: {e}")
