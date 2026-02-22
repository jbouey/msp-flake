"""Email alerts for critical notifications.

Sends email alerts to administrators when critical notifications are created.
Uses SMTP with TLS for secure email delivery.
"""

import os
import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# SMTP Configuration from environment
SMTP_HOST = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "alerts@osiriscare.net")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "administrator@osiriscare.net")


def is_email_configured() -> bool:
    """Check if email is properly configured."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _build_details_section(details: dict) -> str:
    """Build HTML for incident details (expected vs actual, drift info)."""
    if not details:
        return ""

    rows = []
    # Prioritize expected/actual for drift visibility
    if "expected" in details and "actual" in details:
        rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">Expected</td>'
                     f'<td style="padding:6px 12px;font-family:monospace;color:#059669;">{_escape_html(details["expected"])}</td></tr>')
        rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">Actual</td>'
                     f'<td style="padding:6px 12px;font-family:monospace;color:#dc2626;">{_escape_html(details["actual"])}</td></tr>')

    # Show other detail fields (format nested values as JSON)
    for key, val in details.items():
        if key in ("expected", "actual"):
            continue
        if isinstance(val, (dict, list)):
            import json
            formatted = json.dumps(val, indent=2, default=str)
            rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;vertical-align:top;">{_escape_html(key)}</td>'
                         f'<td style="padding:6px 12px;"><pre style="margin:0;font-size:12px;white-space:pre-wrap;">{_escape_html(formatted)}</pre></td></tr>')
        else:
            rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">{_escape_html(key)}</td>'
                         f'<td style="padding:6px 12px;">{_escape_html(val)}</td></tr>')

    if not rows:
        return ""

    return f"""
            <div style="margin-top:16px;">
                <div class="field-label">Drift Details</div>
                <table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;margin-top:4px;">
                    {"".join(rows)}
                </table>
            </div>"""


def _build_hipaa_section(controls: list) -> str:
    """Build HTML for HIPAA controls."""
    if not controls:
        return ""

    control_descriptions = {
        "164.308(a)(5)(ii)(B)": "Security awareness & training - Protection from malicious software",
        "164.308(a)(7)(ii)(A)": "Contingency plan - Data backup plan",
        "164.308(a)(1)(ii)(D)": "Security management - Information system activity review",
        "164.310(d)(2)(iv)": "Device & media controls - Data backup and storage",
        "164.312(a)(1)": "Access control - Unique user identification",
        "164.312(a)(2)(iv)": "Access control - Encryption and decryption",
        "164.312(b)": "Audit controls",
        "164.312(d)": "Person or entity authentication",
        "164.312(e)(1)": "Transmission security - Integrity controls",
        "164.312(e)(2)(ii)": "Transmission security - Encryption",
    }

    items = []
    for c in controls:
        desc = control_descriptions.get(c, "")
        label = f"<strong>{_escape_html(c)}</strong>"
        if desc:
            label += f" &mdash; {_escape_html(desc)}"
        items.append(f"<li style='margin-bottom:4px;'>{label}</li>")

    return f"""
            <div style="margin-top:16px;">
                <div class="field-label">HIPAA Controls Affected</div>
                <ul style="margin:4px 0 0 0;padding-left:20px;">{"".join(items)}</ul>
            </div>"""


def _build_actions_section(attempted: list) -> str:
    """Build HTML for attempted remediation actions."""
    if not attempted:
        return ""

    items = "".join(f"<li style='margin-bottom:4px;'>{_escape_html(a)}</li>" for a in attempted[:5])
    return f"""
            <div style="margin-top:16px;">
                <div class="field-label">Remediation Attempted</div>
                <ul style="margin:4px 0 0 0;padding-left:20px;">{items}</ul>
            </div>"""


def _build_recommendation_section(rec: str) -> str:
    """Build HTML for recommended action."""
    if not rec:
        return ""

    return f"""
            <div style="margin-top:16px;background:white;padding:12px 16px;border-radius:8px;border-left:4px solid #3b82f6;">
                <div class="field-label">Recommended Action</div>
                <div style="color:#111827;margin-top:4px;">{_escape_html(rec)}</div>
            </div>"""


def send_critical_alert(
    title: str,
    message: str,
    site_id: Optional[str] = None,
    category: str = "system",
    metadata: Optional[dict] = None,
    host_id: Optional[str] = None,
    severity: Optional[str] = None,
    check_type: Optional[str] = None,
    details: Optional[dict] = None,
    hipaa_controls: Optional[list] = None,
    attempted_actions: Optional[list] = None,
    recommended_action: Optional[str] = None,
) -> bool:
    """Send an email alert for a critical notification.

    Args:
        title: Alert title
        message: Alert message
        site_id: Optional site ID related to the alert
        category: Alert category
        metadata: Optional additional metadata
        host_id: Host/endpoint that triggered the incident
        severity: Incident severity (critical, high, medium, low)
        check_type: Type of compliance check that failed
        details: Drift details dict (expected, actual, etc.)
        hipaa_controls: List of HIPAA control IDs affected
        attempted_actions: List of L1/L2 actions tried before escalation
        recommended_action: Suggested next step for the partner

    Returns:
        True if email sent successfully, False otherwise
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping critical alert email")
        return False

    try:
        # Build email content
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[CRITICAL] {title}"
        msg["From"] = SMTP_FROM
        msg["To"] = ALERT_EMAIL

        # --- Plain text version ---
        text_parts = [
            "CRITICAL ALERT - OsirisCare Central Command",
            "=" * 44,
            "",
            f"Title: {title}",
            f"Severity: {(severity or 'unknown').upper()}",
            f"Category: {category}",
            f"Site: {site_id or 'System-wide'}",
        ]
        if host_id:
            text_parts.append(f"Host: {host_id}")
        if check_type:
            text_parts.append(f"Check: {check_type}")
        text_parts.append(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        text_parts.append("")

        if details:
            text_parts.append("Drift Details:")
            if "expected" in details and "actual" in details:
                text_parts.append(f"  Expected: {details['expected']}")
                text_parts.append(f"  Actual:   {details['actual']}")
            for k, v in details.items():
                if k not in ("expected", "actual") and not isinstance(v, (dict, list)):
                    text_parts.append(f"  {k}: {v}")
            text_parts.append("")

        if hipaa_controls:
            text_parts.append(f"HIPAA Controls: {', '.join(hipaa_controls)}")
            text_parts.append("")

        if attempted_actions:
            text_parts.append("Remediation Attempted:")
            for a in attempted_actions[:5]:
                text_parts.append(f"  - {a}")
            text_parts.append("")

        if recommended_action:
            text_parts.append(f"Recommended Action: {recommended_action}")
            text_parts.append("")

        text_parts.extend([
            f"Message: {message}",
            "",
            "---",
            "This is an automated alert from OsirisCare Central Command.",
            "Dashboard: https://dashboard.osiriscare.net",
        ])
        text_content = "\n".join(text_parts)

        # --- Dynamic HTML sections ---
        details_html = _build_details_section(details or {})
        hipaa_html = _build_hipaa_section(hipaa_controls or [])
        actions_html = _build_actions_section(attempted_actions or [])
        recommendation_html = _build_recommendation_section(recommended_action or "")

        severity_upper = (severity or "unknown").upper()
        severity_colors = {
            "CRITICAL": ("#dc2626", "#ea580c"),
            "HIGH": ("#ea580c", "#f59e0b"),
            "MEDIUM": ("#f59e0b", "#eab308"),
            "LOW": ("#3b82f6", "#6366f1"),
        }
        color_start, color_end = severity_colors.get(severity_upper, ("#dc2626", "#ea580c"))

        # Optional host/check row
        host_row = ""
        if host_id:
            host_row = f"""
            <div class="field">
                <div class="field-label">Host</div>
                <div class="field-value">{_escape_html(host_id)}</div>
            </div>"""

        check_row = ""
        if check_type:
            check_row = f"""
            <div class="field">
                <div class="field-label">Check Type</div>
                <div class="field-value">{_escape_html(check_type)}</div>
            </div>"""

        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, {color_start}, {color_end}); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header .severity-badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 12px; font-size: 13px; margin-top: 6px; }}
        .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; }}
        .field {{ margin-bottom: 12px; }}
        .field-label {{ font-weight: 600; color: #6b7280; font-size: 12px; text-transform: uppercase; }}
        .field-value {{ color: #111827; }}
        .message-box {{ background: white; padding: 16px; border-radius: 8px; border-left: 4px solid #dc2626; margin-top: 16px; }}
        .footer {{ padding: 16px 20px; background: #f3f4f6; border-radius: 0 0 8px 8px; font-size: 12px; color: #6b7280; }}
        .button {{ display: inline-block; background: #3b82f6; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Critical Alert</h1>
            <span class="severity-badge">{severity_upper}</span>
        </div>
        <div class="content">
            <div class="field">
                <div class="field-label">Title</div>
                <div class="field-value" style="font-size: 18px; font-weight: 600;">{_escape_html(title)}</div>
            </div>
            <div class="field">
                <div class="field-label">Category</div>
                <div class="field-value">{_escape_html(category)}</div>
            </div>
            <div class="field">
                <div class="field-label">Site</div>
                <div class="field-value">{_escape_html(site_id or 'System-wide')}</div>
            </div>{host_row}{check_row}
            <div class="field">
                <div class="field-label">Time</div>
                <div class="field-value">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
            </div>
            <div class="message-box">
                <div class="field-label">Message</div>
                <div class="field-value">{_escape_html(message)}</div>
            </div>{details_html}{hipaa_html}{actions_html}{recommendation_html}
            <a href="https://dashboard.osiriscare.net/notifications" class="button">View in Dashboard</a>
        </div>
        <div class="footer">
            This is an automated alert from OsirisCare Central Command.<br>
            <a href="https://dashboard.osiriscare.net">dashboard.osiriscare.net</a>
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Send email
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, ALERT_EMAIL, msg.as_string())

        logger.info(f"Critical alert email sent: {title}")
        return True

    except Exception as e:
        logger.error(f"Failed to send critical alert email: {e}")
        return False


async def create_notification_with_email(
    db,
    severity: str,
    category: str,
    title: str,
    message: str,
    site_id: Optional[str] = None,
    appliance_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    host_id: Optional[str] = None,
    incident_severity: Optional[str] = None,
    check_type: Optional[str] = None,
    details: Optional[dict] = None,
    hipaa_controls: Optional[list] = None,
) -> str:
    """Create a notification and send email if critical.

    Args:
        db: Database session
        severity: Notification severity (critical, warning, info, success)
        category: Notification category
        title: Notification title
        message: Notification message
        site_id: Optional site ID
        appliance_id: Optional appliance ID
        metadata: Optional metadata dict
        host_id: Host that triggered the incident
        incident_severity: Incident severity for email display
        check_type: Compliance check type
        details: Drift details (expected/actual)
        hipaa_controls: HIPAA control IDs affected

    Returns:
        Notification ID
    """
    from sqlalchemy import text
    import json

    # Insert notification
    result = await db.execute(text("""
        INSERT INTO notifications (site_id, appliance_id, severity, category, title, message, metadata)
        VALUES (:site_id, :appliance_id, :severity, :category, :title, :message, :metadata)
        RETURNING id
    """), {
        "site_id": site_id,
        "appliance_id": appliance_id,
        "severity": severity,
        "category": category,
        "title": title,
        "message": message,
        "metadata": json.dumps(metadata or {}),
    })
    await db.commit()

    notification_id = str(result.fetchone()[0])

    # Send email for critical alerts
    if severity == "critical":
        send_critical_alert(
            title=title,
            message=message,
            site_id=site_id,
            category=category,
            metadata=metadata,
            host_id=host_id,
            severity=incident_severity,
            check_type=check_type,
            details=details,
            hipaa_controls=hipaa_controls,
        )

    return notification_id
