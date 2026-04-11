"""Alert Router — classify, enqueue, and digest client-facing alerts.

Handles:
- Alert mode resolution (site > org > default)
- Incident type classification to alert_type + routing tier
- PHI-free digest email rendering
- pending_alerts DB enqueue (silent mode suppression)
- Digest sender loop (4-hour batched email per org)
- One-time welcome email
"""

import asyncio
import html
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("alert_router")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORTAL_URL = os.getenv("CLIENT_PORTAL_URL", "https://portal.osiriscare.net")
DIGEST_INTERVAL_HOURS = int(os.getenv("ALERT_DIGEST_INTERVAL_HOURS", "4"))
NON_ENGAGEMENT_HOURS = int(os.getenv("NON_ENGAGEMENT_HOURS", "48"))

# ---------------------------------------------------------------------------
# Classification maps
# ---------------------------------------------------------------------------

ALERT_TYPE_MAP: dict[str, str] = {
    "drift:windows_update": "patch_available",
    "drift:linux_patching": "patch_available",
    "drift:nixos_generation": "patch_available",
    "drift:windows_firewall": "firewall_off",
    "drift:linux_firewall": "firewall_off",
    "drift:macos_firewall": "firewall_off",
    "drift:service_stopped": "service_stopped",
    "drift:windows_encryption": "encryption_off",
    "drift:linux_encryption": "encryption_off",
    "drift:macos_filevault": "encryption_off",
    "netscan:rogue_device": "rogue_device",
}

ALERT_SUMMARIES: dict[str, str] = {
    "patch_available": "{count} device(s) have patch updates available",
    "firewall_off": "{count} device(s) have firewall disabled",
    "service_stopped": "{count} device(s) have stopped services",
    "encryption_off": "{count} device(s) have encryption disabled",
    "rogue_device": "{count} unrecognized device(s) detected",
    "credential_needed": "{count} device(s) need credentials configured",
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_effective_alert_mode(
    site_mode: Optional[str],
    org_mode: Optional[str],
) -> str:
    """Resolve effective alert mode using site > org > default precedence.

    Args:
        site_mode: Site-level override (may be None to inherit)
        org_mode: Org-level default (may be None)

    Returns:
        One of: "silent", "informed", "self_service"
    """
    return site_mode or org_mode or "informed"


def classify_alert(incident_type: str, severity: str) -> dict:
    """Classify an incident into an alert_type and routing tier.

    Mapped drift/netscan types → client tier.
    Unknown types → admin tier for manual review.

    Args:
        incident_type: e.g. "drift:windows_firewall"
        severity: e.g. "medium"

    Returns:
        dict with keys: tier, alert_type, severity
    """
    alert_type = ALERT_TYPE_MAP.get(incident_type)

    if alert_type is not None:
        return {"tier": "client", "alert_type": alert_type, "severity": severity}

    # Unmapped drift: prefixes get a generic client-tier classification
    if incident_type.startswith("drift:"):
        return {"tier": "client", "alert_type": "service_stopped", "severity": severity}

    # Everything else escalates to admin
    return {"tier": "admin", "alert_type": incident_type, "severity": severity}


async def maybe_enqueue_alert(
    conn,
    org_id: str,
    site_id: str,
    incident_id: Optional[str],
    incident_type: str,
    severity: str,
    site_mode: Optional[str],
    org_mode: Optional[str],
) -> Optional[str]:
    """Enqueue an alert into pending_alerts if mode and tier allow it.

    Args:
        conn: asyncpg connection (or compatible mock)
        org_id: client org UUID
        site_id: site UUID
        incident_id: source incident UUID (may be None)
        incident_type: e.g. "drift:windows_firewall"
        severity: e.g. "medium"
        site_mode: site-level alert mode override
        org_mode: org-level alert mode

    Returns:
        alert_id (str UUID) if enqueued, None if suppressed
    """
    mode = get_effective_alert_mode(site_mode, org_mode)
    if mode == "silent":
        return None

    classification = classify_alert(incident_type, severity)
    if classification["tier"] != "client":
        return None

    alert_type = classification["alert_type"]
    summary_template = ALERT_SUMMARIES.get(alert_type, "1 compliance issue detected")
    summary = summary_template.format(count=1)

    alert_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO pending_alerts (id, org_id, site_id, alert_type, severity, summary, incident_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        alert_id,
        org_id,
        site_id,
        alert_type,
        severity,
        summary,
        incident_id,
    )
    logger.info(
        "Enqueued alert",
        extra={"alert_id": alert_id, "org_id": org_id, "alert_type": alert_type},
    )
    return alert_id


def render_digest_email(
    org_name: str,
    alerts_list: list[dict],
    mode: str,
) -> tuple[str, str]:
    """Render a PHI-free digest email as (html, text).

    No IP addresses, hostnames, or raw data — site names only.

    Args:
        org_name: e.g. "North Valley Health"
        alerts_list: list of {alert_type, site_name, count}
        mode: "informed" or "self_service"

    Returns:
        (html_body, text_body) tuple
    """
    is_self_service = mode == "self_service"

    # Build per-alert summary lines
    summary_lines = []
    for alert in alerts_list:
        alert_type = alert.get("alert_type", "unknown")
        site_name = alert.get("site_name", "Unknown site")
        count = alert.get("count", 1)
        template = ALERT_SUMMARIES.get(alert_type, "{count} issue(s) detected")
        summary = template.format(count=count)
        summary_lines.append(f"{site_name}: {summary}")

    # Footer language based on mode
    if is_self_service:
        footer_text = "Review and take action in the client portal."
        footer_html = (
            f'Review and take action in your '
            f'<a href="{PORTAL_URL}">client portal</a>.'
        )
        action_text = "Action required: please review the items above."
    else:
        footer_text = "No action required — your OsirisCare team is monitoring these items."
        footer_html = (
            "No action required — your OsirisCare team is monitoring these items."
        )
        action_text = "Your compliance team is monitoring these items."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- Plain text ----
    text_lines = [
        f"OsirisCare Compliance Digest — {org_name}",
        "=" * 50,
        f"Generated: {now_str}",
        "",
        "Compliance Summary:",
        "",
    ]
    text_lines.extend(f"  • {line}" for line in summary_lines)
    text_lines.extend([
        "",
        action_text,
        footer_text,
        "",
        "---",
        f"OsirisCare | {PORTAL_URL}",
    ])
    text_body = "\n".join(text_lines)

    # ---- HTML ----
    rows_html = ""
    for alert in alerts_list:
        alert_type = alert.get("alert_type", "unknown")
        site_name = alert.get("site_name", "Unknown site")
        count = alert.get("count", 1)
        template = ALERT_SUMMARIES.get(alert_type, "{count} issue(s) detected")
        summary = template.format(count=count)
        rows_html += (
            f"<tr>"
            f'<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">'
            f"{html.escape(site_name)}</td>"
            f'<td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">'
            f"{html.escape(summary)}</td>"
            f"</tr>"
        )

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color:#333; line-height:1.6; }}
    .container {{ max-width:600px; margin:0 auto; padding:20px; }}
    .header {{ background:linear-gradient(135deg,#0d9488,#0891b2); color:white; padding:20px; border-radius:8px 8px 0 0; }}
    .header h1 {{ margin:0; font-size:20px; }}
    .content {{ background:#f9fafb; padding:20px; border:1px solid #e5e7eb; border-top:none; }}
    table {{ width:100%; border-collapse:collapse; margin:16px 0; }}
    th {{ padding:8px 12px; text-align:left; background:#f3f4f6; font-size:13px; }}
    .footer {{ padding:16px 20px; background:#f3f4f6; border-radius:0 0 8px 8px; font-size:12px; color:#6b7280; }}
    .action-box {{ background:white; padding:14px 16px; border-radius:8px; border-left:4px solid #0d9488; margin-top:16px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Compliance Digest</h1>
      <div style="margin-top:4px;font-size:13px;opacity:0.85;">{html.escape(org_name)} &mdash; {html.escape(now_str)}</div>
    </div>
    <div class="content">
      <p style="margin:0 0 12px;">Your compliance monitoring summary:</p>
      <table>
        <thead>
          <tr>
            <th>Location</th>
            <th>Finding</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div class="action-box">{footer_html}</div>
    </div>
    <div class="footer">
      OsirisCare Central Command &mdash; <a href="{PORTAL_URL}">{PORTAL_URL}</a>
    </div>
  </div>
</body>
</html>"""

    return html_body, text_body


async def send_digest_for_org(
    conn,
    org_id: str,
    org_name: str,
    alert_email: str,
    cc_email: Optional[str],
    org_mode: str,
) -> int:
    """Fetch unsent pending_alerts for org, render digest, send, mark sent.

    Args:
        conn: asyncpg connection
        org_id: client org UUID
        org_name: display name for email header
        alert_email: primary recipient
        cc_email: optional CC recipient
        org_mode: "informed" or "self_service" (silent orgs never reach here)

    Returns:
        Number of alert rows marked as sent
    """
    from dashboard_api.email_alerts import send_digest_email

    rows = await conn.fetch(
        """
        SELECT id, alert_type, severity, summary, site_id
        FROM pending_alerts
        WHERE org_id = $1
          AND sent_at IS NULL
          AND dismissed_at IS NULL
        ORDER BY created_at ASC
        """,
        org_id,
    )

    if not rows:
        return 0

    # Group by alert_type + site_id → aggregate counts
    site_names: dict[str, str] = {}
    site_rows = await conn.fetch(
        "SELECT site_id, clinic_name FROM sites WHERE site_id = ANY($1::text[])",
        [str(r["site_id"]) for r in rows],
    )
    for sr in site_rows:
        site_names[str(sr["site_id"])] = sr["clinic_name"]

    # Build alerts_list collapsing by (alert_type, site_id)
    buckets: dict[tuple, dict] = {}
    for row in rows:
        key = (row["alert_type"], str(row["site_id"]))
        if key not in buckets:
            buckets[key] = {
                "alert_type": row["alert_type"],
                "site_name": site_names.get(str(row["site_id"]), "Unknown site"),
                "count": 0,
            }
        buckets[key]["count"] += 1

    alerts_list = list(buckets.values())
    html_body, text_body = render_digest_email(org_name, alerts_list, org_mode)
    subject = f"[OsirisCare] Compliance digest — {org_name}"

    sent = send_digest_email(
        to_email=alert_email,
        cc_email=cc_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )

    if sent:
        alert_ids = [r["id"] for r in rows]
        await conn.execute(
            "UPDATE pending_alerts SET sent_at = NOW() WHERE id = ANY($1::uuid[])",
            [str(aid) for aid in alert_ids],
        )
        logger.info(
            "Digest sent",
            extra={"org_id": org_id, "count": len(alert_ids)},
        )
        return len(alert_ids)

    logger.warning("Digest email failed to send", extra={"org_id": org_id})
    return 0


async def send_welcome_email_if_needed(
    conn,
    org_id: str,
    org_name: str,
    alert_email: str,
    device_count: int,
    site_count: int,
) -> bool:
    """Send one-time welcome email when an org's first devices are discovered.

    Args:
        conn: asyncpg connection
        org_id: client org UUID
        org_name: display name
        alert_email: recipient
        device_count: number of discovered devices
        site_count: number of enrolled sites

    Returns:
        True if email was sent, False if already sent or not configured
    """
    from dashboard_api.email_alerts import send_digest_email

    row = await conn.fetchrow(
        "SELECT welcome_email_sent_at FROM client_orgs WHERE id = $1",
        org_id,
    )
    if not row or row["welcome_email_sent_at"] is not None:
        return False

    subject = f"[OsirisCare] Your compliance monitoring is active — {org_name}"
    text_body = (
        f"Welcome to OsirisCare, {org_name}!\n\n"
        f"Your compliance monitoring is now active.\n"
        f"  Sites enrolled: {site_count}\n"
        f"  Devices discovered: {device_count}\n\n"
        f"You will receive digest updates as our system monitors for HIPAA drift.\n\n"
        f"Portal: {PORTAL_URL}\n"
        f"---\nOsirisCare Central Command\n"
    )
    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <div style="background:linear-gradient(135deg,#0d9488,#0891b2);color:white;padding:20px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:20px;">Welcome to OsirisCare</h1>
  </div>
  <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;">
    <p>Hi {html.escape(org_name)},</p>
    <p>Your HIPAA compliance monitoring is now active.</p>
    <ul>
      <li><strong>Sites enrolled:</strong> {site_count}</li>
      <li><strong>Devices discovered:</strong> {device_count}</li>
    </ul>
    <p>You will receive digest updates as our system monitors for compliance drift.</p>
    <p><a href="{PORTAL_URL}" style="display:inline-block;background:#0d9488;color:white;
       padding:10px 20px;text-decoration:none;border-radius:6px;">Open Portal</a></p>
  </div>
  <div style="padding:12px 20px;font-size:12px;color:#6b7280;">
    OsirisCare Central Command &mdash; <a href="{PORTAL_URL}">{PORTAL_URL}</a>
  </div>
</body>
</html>"""

    sent = send_digest_email(
        to_email=alert_email,
        cc_email=None,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )

    if sent:
        await conn.execute(
            "UPDATE client_orgs SET welcome_email_sent_at = NOW() WHERE id = $1",
            org_id,
        )
        logger.info("Welcome email sent", extra={"org_id": org_id})

    return sent


async def digest_sender_loop() -> None:
    """Background loop: send batched digest emails every DIGEST_INTERVAL_HOURS.

    - Critical/high alerts are flushed immediately before each regular digest pass.
    - Regular digest aggregates all unsent alerts per org.
    - Silent-mode orgs have no pending_alerts (suppressed at enqueue time).
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    # Wait for pool startup
    await asyncio.sleep(120)
    logger.info("Digest sender loop started", extra={"interval_hours": DIGEST_INTERVAL_HOURS})

    while True:
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                await _flush_critical_alerts(conn)
                await _send_all_org_digests(conn)
                await _check_non_engagement(conn)
                # Audit log retention: HIPAA requires 7-year minimum (2555 days).
                # DELETE triggers on admin_audit_log enforce immutability —
                # retention cleanup requires manual DBA intervention with
                # documented justification. The 3-year automated purge was
                # removed as non-compliant with HIPAA 164.316(b)(2)(i).
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Digest cycle error: {e}", exc_info=True)

        await asyncio.sleep(DIGEST_INTERVAL_HOURS * 3600)


async def _flush_critical_alerts(conn) -> None:
    """Immediately send emails for critical/high pending alerts (don't batch)."""
    rows = await conn.fetch(
        """
        SELECT pa.id, pa.org_id, pa.alert_type, pa.severity, pa.summary,
               co.name AS org_name, co.alert_email, co.client_alert_mode,
               s.clinic_name AS site_name
        FROM pending_alerts pa
        JOIN client_orgs co ON co.id = pa.org_id
        LEFT JOIN sites s ON s.site_id = pa.site_id
        WHERE pa.sent_at IS NULL
          AND pa.dismissed_at IS NULL
          AND pa.severity IN ('critical', 'high')
        ORDER BY pa.created_at ASC
        """,
    )
    if not rows:
        return

    from dashboard_api.email_alerts import send_digest_email

    for row in rows:
        org_mode = row["client_alert_mode"] or "informed"
        alerts_list = [{
            "alert_type": row["alert_type"],
            "site_name": row.get("site_name") or "Your site",
            "count": 1,
        }]
        html_body, text_body = render_digest_email(row["org_name"], alerts_list, org_mode)
        subject = f"[OsirisCare] {row['severity'].upper()} alert — {row['org_name']}"
        sent = send_digest_email(
            to_email=row["alert_email"],
            cc_email=None,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        if sent:
            await conn.execute(
                "UPDATE pending_alerts SET sent_at = NOW() WHERE id = $1",
                row["id"],
            )


async def _check_non_engagement(conn) -> None:
    """Escalate to partner when client alerts go unacted for NON_ENGAGEMENT_HOURS."""
    # Find orgs with unacted alerts past threshold
    rows = await conn.fetch(
        """
        SELECT pa.org_id, co.name as org_name, co.current_partner_id,
               COUNT(*) as unacted_count,
               MIN(pa.created_at) as oldest_alert
        FROM pending_alerts pa
        JOIN client_orgs co ON co.id = pa.org_id
        WHERE pa.sent_at IS NOT NULL
          AND pa.dismissed_at IS NULL
          AND pa.created_at < NOW() - INTERVAL '1 hour' * $1
        GROUP BY pa.org_id, co.name, co.current_partner_id
        HAVING COUNT(*) >= 1
        """,
        NON_ENGAGEMENT_HOURS,
    )

    if not rows:
        return

    for row in rows:
        partner_id = row["current_partner_id"]
        if not partner_id:
            continue

        # Dedup: don't re-escalate same org within 7 days
        existing = await conn.fetchrow(
            """SELECT id FROM partner_notifications
               WHERE partner_id = $1 AND org_id = $2
                 AND notification_type = 'non_engagement'
                 AND created_at > NOW() - INTERVAL '7 days'""",
            partner_id, row["org_id"],
        )
        if existing:
            continue

        summary = f"{row['org_name']} has {row['unacted_count']} unacted alert(s) ({NON_ENGAGEMENT_HOURS}h+)"

        await conn.execute(
            """INSERT INTO partner_notifications (id, partner_id, org_id, notification_type, summary)
               VALUES (gen_random_uuid(), $1, $2, 'non_engagement', $3)""",
            partner_id, row["org_id"], summary,
        )

        # Send email to partner
        partner = await conn.fetchrow(
            "SELECT name, email FROM partners WHERE id = $1", partner_id
        )
        if partner and partner["email"]:
            try:
                from dashboard_api.email_alerts import send_digest_email
                safe_org_name = html.escape(row["org_name"])
                html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;">
<div style="background:linear-gradient(135deg,#7c3aed,#4f46e5);padding:24px;border-radius:8px 8px 0 0;">
  <h1 style="color:#fff;margin:0;font-size:20px;">Partner Alert</h1>
  <p style="color:#c4b5fd;margin:4px 0 0;">Client Non-Engagement</p>
</div>
<div style="padding:20px;background:#fff;border:1px solid #e2e8f0;border-top:none;">
  <p><strong>{safe_org_name}</strong> has <strong>{row['unacted_count']}</strong> unacted compliance alert(s) that have been pending for over {NON_ENGAGEMENT_HOURS} hours.</p>
  <p style="color:#64748b;">The client was notified but has not taken action. Please follow up.</p>
  <div style="margin-top:16px;text-align:center;">
    <a href="https://dashboard.osiriscare.net/partners/orgs/{row['org_id']}" style="display:inline-block;background:#4f46e5;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;">View in Dashboard</a>
  </div>
</div>
<div style="padding:12px;text-align:center;color:#94a3b8;font-size:11px;">OsirisCare Partner Alert</div>
</body></html>"""
                text_body = f"{row['org_name']} has {row['unacted_count']} unacted compliance alert(s) pending for over {NON_ENGAGEMENT_HOURS} hours. Please follow up."
                send_digest_email(
                    to_email=partner["email"],
                    cc_email=None,
                    subject=f"[OsirisCare Partner] Client non-engagement — {row['org_name']}",
                    html_body=html_body,
                    text_body=text_body,
                )
            except Exception as e:
                logger.error(f"Failed to send non-engagement email for org {row['org_id']}: {e}")

        logger.info(
            "Non-engagement escalation",
            extra={
                "org_id": str(row["org_id"]),
                "partner_id": str(partner_id),
                "count": row["unacted_count"],
            },
        )


async def _send_all_org_digests(conn) -> None:
    """Send digest emails to all orgs that have unsent pending alerts."""
    orgs = await conn.fetch(
        """
        SELECT DISTINCT co.id, co.name, co.alert_email, co.client_alert_mode
        FROM pending_alerts pa
        JOIN client_orgs co ON co.id = pa.org_id
        WHERE pa.sent_at IS NULL
          AND pa.dismissed_at IS NULL
          AND co.alert_email IS NOT NULL
        """
    )

    for org in orgs:
        try:
            org_mode = org["client_alert_mode"] or "informed"
            await send_digest_for_org(
                conn=conn,
                org_id=str(org["id"]),
                org_name=org["name"],
                alert_email=org["alert_email"],
                cc_email=None,
                org_mode=org_mode,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                f"Digest failed for org {org['id']}: {e}",
                exc_info=True,
            )
