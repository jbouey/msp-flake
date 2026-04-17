"""Email alerts for critical notifications.

Sends email alerts to administrators when critical notifications are created.
Uses SMTP with TLS for secure email delivery.
"""

import html
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
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")


def is_email_configured() -> bool:
    """Check if email is properly configured."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def _send_smtp_with_retry(
    msg: MIMEMultipart,
    recipients: list[str],
    label: str = "email",
    max_retries: int = 3,
) -> bool:
    """Send an email via SMTP with exponential backoff retry.

    Centralizes the retry logic used by all email-sending functions.
    Returns True on success, False on failure (after exhausting retries).
    """
    import time as _time

    for attempt in range(max_retries):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, recipients, msg.as_string())
            logger.info(f"{label} sent successfully")
            return True
        except (smtplib.SMTPException, OSError) as smtp_err:
            if attempt < max_retries - 1:
                logger.warning(f"SMTP attempt {attempt + 1}/{max_retries} failed for {label}: {smtp_err}")
                _time.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to send {label} after {max_retries} attempts: {smtp_err}")
                return False
    return False


def _build_details_section(details: dict) -> str:
    """Build HTML for incident details (expected vs actual, drift info)."""
    if not details:
        return ""

    rows = []
    # Prioritize expected/actual for drift visibility
    if "expected" in details and "actual" in details:
        rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">Expected</td>'
                     f'<td style="padding:6px 12px;font-family:monospace;color:#059669;">{html.escape(details["expected"])}</td></tr>')
        rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">Actual</td>'
                     f'<td style="padding:6px 12px;font-family:monospace;color:#dc2626;">{html.escape(details["actual"])}</td></tr>')

    # Show other detail fields (format nested values as JSON)
    for key, val in details.items():
        if key in ("expected", "actual"):
            continue
        if isinstance(val, (dict, list)):
            import json
            formatted = json.dumps(val, indent=2, default=str)
            rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;vertical-align:top;">{html.escape(key)}</td>'
                         f'<td style="padding:6px 12px;"><pre style="margin:0;font-size:12px;white-space:pre-wrap;">{html.escape(formatted)}</pre></td></tr>')
        else:
            rows.append(f'<tr><td style="padding:6px 12px;color:#6b7280;">{html.escape(key)}</td>'
                         f'<td style="padding:6px 12px;">{html.escape(val)}</td></tr>')

    if not rows:
        return ""

    return f"""
            <div style="margin-top:16px;">
                <div class="field-label">Drift Details</div>
                <table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;margin-top:4px;">
                    {"".join(rows)}
                </table>
            </div>"""


def _build_controls_section(controls: list, framework: str = "HIPAA") -> str:
    """Build HTML for compliance controls (any framework)."""
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
        label = f"<strong>{html.escape(c)}</strong>"
        if desc:
            label += f" &mdash; {html.escape(desc)}"
        items.append(f"<li style='margin-bottom:4px;'>{label}</li>")

    framework_upper = framework.upper()
    return f"""
            <div style="margin-top:16px;">
                <div class="field-label">{framework_upper} Controls Affected</div>
                <ul style="margin:4px 0 0 0;padding-left:20px;">{"".join(items)}</ul>
            </div>"""


# Backward-compatible alias
def _build_hipaa_section(controls: list) -> str:
    """Build HTML for HIPAA controls. Deprecated: use _build_controls_section."""
    return _build_controls_section(controls, framework="HIPAA")


def _build_actions_section(attempted: list) -> str:
    """Build HTML for attempted remediation actions."""
    if not attempted:
        return ""

    items = "".join(f"<li style='margin-bottom:4px;'>{html.escape(a)}</li>" for a in attempted[:5])
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
                <div style="color:#111827;margin-top:4px;">{html.escape(rec)}</div>
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
    controls: Optional[list] = None,
    framework: str = "hipaa",
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
        hipaa_controls: List of control IDs affected (legacy name, still accepted)
        attempted_actions: List of L1/L2 actions tried before escalation
        recommended_action: Suggested next step for the partner
        controls: List of control IDs affected (preferred over hipaa_controls)
        framework: Compliance framework label (hipaa, soc2, pci_dss, etc.)

    Returns:
        True if email sent successfully, False otherwise
    """
    # Accept either kwarg; controls takes precedence
    effective_controls = controls if controls is not None else hipaa_controls
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

        if effective_controls:
            text_parts.append(f"Compliance Controls: {', '.join(effective_controls)}")
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
            f"Dashboard: {FRONTEND_URL}",
        ])
        text_content = "\n".join(text_parts)

        # --- Dynamic HTML sections ---
        details_html = _build_details_section(details or {})
        hipaa_html = _build_controls_section(effective_controls or [], framework=framework)
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
                <div class="field-value">{html.escape(host_id)}</div>
            </div>"""

        check_row = ""
        if check_type:
            check_row = f"""
            <div class="field">
                <div class="field-label">Check Type</div>
                <div class="field-value">{html.escape(check_type)}</div>
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
                <div class="field-value" style="font-size: 18px; font-weight: 600;">{html.escape(title)}</div>
            </div>
            <div class="field">
                <div class="field-label">Category</div>
                <div class="field-value">{html.escape(category)}</div>
            </div>
            <div class="field">
                <div class="field-label">Site</div>
                <div class="field-value">{html.escape(site_id or 'System-wide')}</div>
            </div>{host_row}{check_row}
            <div class="field">
                <div class="field-label">Time</div>
                <div class="field-value">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
            </div>
            <div class="message-box">
                <div class="field-label">Message</div>
                <div class="field-value">{html.escape(message)}</div>
            </div>{details_html}{hipaa_html}{actions_html}{recommendation_html}
            <a href="{FRONTEND_URL}/notifications" class="button">View in Dashboard</a>
        </div>
        <div class="footer">
            This is an automated alert from OsirisCare Central Command.<br>
            <a href="{FRONTEND_URL}">{FRONTEND_URL.replace('https://', '').replace('http://', '')}</a>
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        return _send_smtp_with_retry(msg, [ALERT_EMAIL], f"critical alert: {title}")

    except Exception as e:
        logger.error(f"Failed to build critical alert email: {e}")
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
    controls: Optional[list] = None,
    framework: str = "hipaa",
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
        hipaa_controls: Control IDs affected (legacy name, still accepted)
        controls: Control IDs affected (preferred over hipaa_controls)
        framework: Compliance framework (hipaa, soc2, etc.)

    Returns:
        Notification ID
    """
    # Accept either kwarg for backward compat
    effective_controls = controls if controls is not None else hipaa_controls
    from sqlalchemy import text
    import json

    # Insert notification
    from .shared import execute_with_retry
    result = await execute_with_retry(db, text("""
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
            controls=effective_controls,
            framework=framework,
        )

    return notification_id


def send_digest_email(
    to_email: str,
    cc_email: Optional[str],
    subject: str,
    html_body: str,
    text_body: str,
) -> bool:
    """Send a digest email with optional CC.

    Uses same SMTP pattern as send_critical_alert: 3 retries with backoff.

    Args:
        to_email: Primary recipient
        cc_email: Optional CC recipient (None = omit CC header)
        subject: Email subject line
        html_body: HTML part
        text_body: Plain text part

    Returns:
        True if sent successfully, False otherwise
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping digest email")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        if cc_email:
            msg["Cc"] = cc_email

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        recipients = [to_email]
        if cc_email:
            recipients.append(cc_email)

        return _send_smtp_with_retry(msg, recipients, f"digest to {to_email}: {subject}")

    except Exception as e:
        logger.error(f"Failed to build digest email: {e}")
        return False


async def send_companion_alert_email(
    to_email: str,
    companion_name: str,
    org_name: str,
    module_label: str,
    expected_status: str,
    current_status: str,
    target_date: str,
    description: str = None,
) -> bool:
    """Send a compliance alert email to a companion user.

    Uses teal branding to match the companion portal aesthetic.
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping companion alert email")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[OsirisCare] Compliance Alert: {module_label} overdue for {org_name}"
        msg["From"] = SMTP_FROM
        msg["To"] = to_email

        desc_line = f"\nNote: {description}" if description else ""

        text_content = f"""Compliance Alert - OsirisCare
{'=' * 40}

Hi {companion_name},

The following compliance module has not met its expected status by the target date:

  Client:          {org_name}
  Module:          {module_label}
  Expected Status: {expected_status.replace('_', ' ').title()}
  Current Status:  {current_status.replace('_', ' ').title()}
  Target Date:     {target_date}{desc_line}

Please review this client's progress in the Companion Portal.

---
This is an automated alert from OsirisCare Central Command.
{FRONTEND_URL}/companion
"""

        desc_html = ""
        if description:
            desc_html = f"""
            <div class="field">
                <div class="field-label">Note</div>
                <div class="field-value">{html.escape(description)}</div>
            </div>"""

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0D7377, #095456); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 22px; }}
        .header .badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 2px 10px; border-radius: 12px; font-size: 13px; margin-top: 6px; }}
        .content {{ background: #FAFAF8; padding: 20px; border: 1px solid #E8E5E1; }}
        .field {{ margin-bottom: 12px; }}
        .field-label {{ font-weight: 600; color: #6B6B66; font-size: 12px; text-transform: uppercase; }}
        .field-value {{ color: #1A1A18; }}
        .status-box {{ display: flex; gap: 24px; background: white; padding: 16px; border-radius: 8px; margin-top: 16px; border: 1px solid #E8E5E1; }}
        .status-item {{ text-align: center; }}
        .status-label {{ font-size: 11px; color: #6B6B66; text-transform: uppercase; font-weight: 600; }}
        .status-expected {{ font-size: 16px; font-weight: 600; color: #2D8A4E; }}
        .status-current {{ font-size: 16px; font-weight: 600; color: #DC2626; }}
        .footer {{ padding: 16px 20px; background: #F5F3F0; border-radius: 0 0 8px 8px; font-size: 12px; color: #6B6B66; }}
        .button {{ display: inline-block; background: #0D7377; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Compliance Alert</h1>
            <span class="badge">Overdue</span>
        </div>
        <div class="content">
            <p style="margin:0 0 16px;color:#1A1A18;">Hi {html.escape(companion_name)},</p>
            <p style="margin:0 0 16px;color:#6B6B66;">A compliance module has not reached its expected status by the target date.</p>

            <div class="field">
                <div class="field-label">Client</div>
                <div class="field-value" style="font-size:16px;font-weight:600;">{html.escape(org_name)}</div>
            </div>
            <div class="field">
                <div class="field-label">Module</div>
                <div class="field-value">{html.escape(module_label)}</div>
            </div>
            <div class="field">
                <div class="field-label">Target Date</div>
                <div class="field-value">{html.escape(target_date)}</div>
            </div>{desc_html}

            <div class="status-box">
                <div class="status-item" style="flex:1;">
                    <div class="status-label">Expected</div>
                    <div class="status-expected">{html.escape(expected_status.replace('_', ' ').title())}</div>
                </div>
                <div class="status-item" style="flex:1;">
                    <div class="status-label">Current</div>
                    <div class="status-current">{html.escape(current_status.replace('_', ' ').title())}</div>
                </div>
            </div>

            <a href="{FRONTEND_URL}/companion" class="button">Open Companion Portal</a>
        </div>
        <div class="footer">
            This is an automated alert from OsirisCare Central Command.<br>
            <a href="{FRONTEND_URL}/companion" style="color:#0D7377;">{FRONTEND_URL.replace('https://', '').replace('http://', '')}/companion</a>
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        return _send_smtp_with_retry(msg, [to_email], f"companion alert to {to_email}: {module_label}")

    except Exception as e:
        logger.error(f"Failed to build companion alert email: {e}")
        return False


async def send_sra_overdue_email(
    to_email: str,
    user_name: str,
    org_name: str,
    overdue_items: list[dict],
) -> bool:
    """Send SRA remediation overdue reminder email.

    overdue_items: list of {"question_key": str, "plan": str, "due": str}
    """
    if not is_email_configured():
        logger.warning("[sra-email] SMTP not configured, skipping overdue reminder")
        return False

    count = len(overdue_items)
    subject = f"[OsirisCare] {count} SRA remediation item{'s' if count != 1 else ''} overdue for {org_name}"

    items_html = ""
    items_text = ""
    for item in overdue_items[:20]:  # cap at 20
        items_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;">{item['question_key']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;">{item['plan'][:120]}{'...' if len(item['plan']) > 120 else ''}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;color:#dc2626;">{item['due']}</td>
        </tr>"""
        items_text += f"  - {item['question_key']}: {item['plan'][:80]} (due: {item['due']})\n"

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#0d9488;padding:20px;border-radius:8px 8px 0 0;">
        <h1 style="color:white;margin:0;font-size:20px;">SRA Remediation Overdue</h1>
      </div>
      <div style="padding:20px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;">
        <p>Hi {user_name},</p>
        <p>The following SRA remediation items for <strong>{org_name}</strong> are past their due date:</p>
        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
          <thead>
            <tr style="background:#f3f4f6;">
              <th style="padding:8px 12px;text-align:left;font-size:13px;">Item</th>
              <th style="padding:8px 12px;text-align:left;font-size:13px;">Plan</th>
              <th style="padding:8px 12px;text-align:left;font-size:13px;">Due Date</th>
            </tr>
          </thead>
          <tbody>{items_html}</tbody>
        </table>
        {f'<p style="color:#6b7280;font-size:13px;">...and {count - 20} more items</p>' if count > 20 else ''}
        <p>Please update the remediation plans in your Security Risk Assessment or mark them as completed.</p>
        <p style="color:#6b7280;font-size:13px;">This is an automated reminder from OsirisCare compliance monitoring.</p>
      </div>
    </div>"""

    text = f"""SRA Remediation Overdue - {org_name}

Hi {user_name},

The following SRA remediation items are past their due date:

{items_text}
Please update the remediation plans in your Security Risk Assessment or mark them as completed.
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    return _send_smtp_with_retry(msg, [to_email], f"SRA overdue reminder to {to_email} ({count} items)")


# ─── Session 206 round-table P2: partner weekly digest ─────────────

def send_partner_weekly_digest(
    *,
    to_email: str,
    partner_brand: str,
    partner_logo_url: Optional[str],
    primary_color: str,
    week_label: str,
    stats: dict,
    attention_sites: list[dict],
    activity_highlights: list[dict],
) -> bool:
    """Friday morning partner digest — week in review.

    `stats`: {clients, self_heal_pct, incidents, chronic_broken, l3_count}
    `attention_sites`: top 5 sites needing attention next week (dicts w/
                       clinic_name, risk_score, reason)
    `activity_highlights`: up to 5 notable events this week
                           (dicts with when, clinic_name, incident_type, outcome)
    """
    if not is_email_configured():
        logger.warning("SMTP not configured — skipping partner weekly digest")
        return False

    safe_brand = html.escape(partner_brand or "OsirisCare")
    logo_html = (
        f'<img src="{html.escape(partner_logo_url)}" alt="{safe_brand}" style="height:32px;" />'
        if partner_logo_url else f'<span style="font-weight:700;font-size:18px;color:{primary_color};">{safe_brand}</span>'
    )

    def _attn_row(s: dict) -> str:
        return (
            f'<tr>'
            f'<td style="padding:8px 12px;">{html.escape(s.get("clinic_name") or s.get("site_id") or "")}</td>'
            f'<td style="padding:8px 12px;text-align:right;font-variant-numeric:tabular-nums;">{int(s.get("risk_score", 0))}</td>'
            f'<td style="padding:8px 12px;color:#64748b;font-size:12px;">{html.escape(str(s.get("reason") or ""))}</td>'
            f'</tr>'
        )

    def _activity_row(a: dict) -> str:
        return (
            f'<tr>'
            f'<td style="padding:6px 12px;color:#64748b;white-space:nowrap;">{html.escape(str(a.get("when") or ""))}</td>'
            f'<td style="padding:6px 12px;">{html.escape(a.get("clinic_name") or a.get("site_id") or "")}</td>'
            f'<td style="padding:6px 12px;color:#334155;">{html.escape(str(a.get("incident_type") or ""))}</td>'
            f'<td style="padding:6px 12px;color:#10b981;">{html.escape(str(a.get("outcome") or ""))}</td>'
            f'</tr>'
        )

    attention_rows = "".join(_attn_row(s) for s in attention_sites[:5]) or (
        '<tr><td colspan="3" style="padding:12px;color:#64748b;text-align:center;">'
        '✓ No sites need urgent attention this week.</td></tr>'
    )
    activity_rows = "".join(_activity_row(a) for a in activity_highlights[:5]) or (
        '<tr><td colspan="4" style="padding:12px;color:#64748b;text-align:center;">'
        'Quiet week — no notable events.</td></tr>'
    )

    self_heal = float(stats.get("self_heal_pct") or 0)
    heal_tone = "#047857" if self_heal >= 95 else "#b45309" if self_heal >= 85 else "#b91c1c"

    html_body = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{safe_brand} · Week in review · {html.escape(week_label)}</title></head>
<body style="font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;background:#f8fafc;margin:0;padding:20px;">
<div style="max-width:640px;margin:0 auto;background:white;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.05);">

<div style="padding:18px 24px;border-bottom:3px solid {primary_color};display:flex;align-items:center;justify-content:space-between;">
  <div>{logo_html}</div>
  <div style="font-size:13px;color:#64748b;text-align:right;">Week in review<br/><b>{html.escape(week_label)}</b></div>
</div>

<div style="padding:24px;">
  <h1 style="margin:0 0 4px 0;font-size:20px;color:#0f172a;">Your book-of-business this week</h1>
  <p style="margin:0 0 18px 0;font-size:13px;color:#64748b;">
    A summary of what the platform did on your behalf across all your clients.
  </p>

  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:18px;margin-bottom:16px;">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#64748b;">Self-heal rate</div>
    <div style="font-size:40px;font-weight:700;color:{heal_tone};">{self_heal:.1f}%</div>
    <div style="font-size:12px;color:#64748b;">
      {stats.get('incidents', 0):,} issues detected across {stats.get('clients', 0)} clients · {stats.get('l1_count', 0):,} auto-healed without your touch
    </div>
  </div>

  <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:0.05em;color:{primary_color};margin:18px 0 8px 0;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
    Needs attention next week
  </h2>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
      <tr style="background:#f1f5f9;font-size:11px;text-transform:uppercase;color:#475569;">
        <th style="padding:8px 12px;text-align:left;">Client</th>
        <th style="padding:8px 12px;text-align:right;">Risk</th>
        <th style="padding:8px 12px;text-align:left;">Why</th>
      </tr>
    </thead>
    <tbody>{attention_rows}</tbody>
  </table>

  <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:0.05em;color:{primary_color};margin:18px 0 8px 0;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
    Activity highlights
  </h2>
  <table style="width:100%;border-collapse:collapse;font-size:12px;">
    <thead>
      <tr style="background:#f1f5f9;font-size:11px;text-transform:uppercase;color:#475569;">
        <th style="padding:6px 12px;text-align:left;">When</th>
        <th style="padding:6px 12px;text-align:left;">Client</th>
        <th style="padding:6px 12px;text-align:left;">Event</th>
        <th style="padding:6px 12px;text-align:left;">Outcome</th>
      </tr>
    </thead>
    <tbody>{activity_rows}</tbody>
  </table>

  <div style="margin-top:22px;padding:14px;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;font-size:13px;color:#065f46;">
    <b>{stats.get('chronic_broken', 0)}</b> recurring pattern(s) were permanently broken this week.
    <b>{stats.get('l3_count', 0)}</b> incidents needed your hands-on attention.
  </div>

  <p style="margin-top:20px;font-size:11px;color:#94a3b8;">
    This digest summarizes monitoring and remediation activity only. It is not a HIPAA compliance
    attestation. Monthly compliance packets (signed + OTS-anchored) remain the authoritative record.
  </p>
</div>

<div style="padding:14px 24px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b;">
  Delivered by {safe_brand} · Powered by OsirisCare.
</div>

</div>
</body></html>"""

    text_body = (
        f"{partner_brand or 'OsirisCare'} — Week in review — {week_label}\n\n"
        f"Self-heal rate: {self_heal:.1f}%\n"
        f"Clients: {stats.get('clients', 0)} | "
        f"Issues: {stats.get('incidents', 0)} | "
        f"Auto-healed: {stats.get('l1_count', 0)} | "
        f"Needed you: {stats.get('l3_count', 0)}\n\n"
        f"Top attention for next week:\n"
        + "\n".join(
            f"  - {s.get('clinic_name') or s.get('site_id')}: risk {int(s.get('risk_score', 0))} ({s.get('reason') or ''})"
            for s in attention_sites[:5]
        )
        + "\n\n"
        f"This digest summarizes activity only; not a HIPAA attestation.\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{partner_brand or 'OsirisCare'} · Week in review · {week_label}"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return _send_smtp_with_retry(msg, [to_email], f"partner weekly digest to {to_email}")


# ─── Migration 184 Phase 4 — consent-request magic-link email ────

def send_consent_request_email(
    *,
    to_email: str,
    raw_token: str,
    site_id: str,
    class_display_name: str,
    class_description: str,
    class_risk_level: str,
    partner_brand: str,
    partner_logo_url: Optional[str],
    primary_color: str,
    partner_contact_email: str,
    ttl_days: int,
) -> bool:
    """Send the magic-link consent-approval email to the practice manager.

    The raw token is NEVER persisted server-side (only the SHA256 is).
    This is the one and only place it appears in cleartext.
    """
    if not is_email_configured():
        logger.warning("SMTP not configured — skipping consent request email")
        return False

    safe_brand = html.escape(partner_brand or "OsirisCare")
    safe_class = html.escape(class_display_name)
    safe_desc = html.escape(class_description)
    safe_site = html.escape(site_id)
    portal_origin = os.getenv("PORTAL_ORIGIN", "https://app.osiriscare.net")
    # URL path the frontend routes: /consent/approve/{token}
    magic_link = f"{portal_origin}/consent/approve/{raw_token}"
    risk_color = {"low": "#047857", "medium": "#b45309", "high": "#b91c1c"}.get(
        class_risk_level, "#475569"
    )
    logo_html = (
        f'<img src="{html.escape(partner_logo_url)}" alt="{safe_brand}" style="height:32px;" />'
        if partner_logo_url else f'<b style="color:{primary_color};font-size:18px;">{safe_brand}</b>'
    )

    html_body = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;background:#f8fafc;margin:0;padding:20px;">
<div style="max-width:600px;margin:0 auto;background:white;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.05);">

<div style="padding:18px 24px;border-bottom:3px solid {primary_color};">
  {logo_html}
</div>

<div style="padding:24px;">
  <h1 style="font-size:20px;margin:0 0 4px 0;color:#0f172a;">Authorization requested</h1>
  <p style="margin:0 0 16px 0;font-size:13px;color:#64748b;">
    Your IT partner <b>{safe_brand}</b> is asking for your authorization to run automated
    remediation in the following category on <b>{safe_site}</b>.
  </p>

  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:16px;">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#64748b;">Category</div>
    <div style="font-size:18px;font-weight:600;color:#0f172a;margin-top:4px;">{safe_class}</div>
    <div style="font-size:12px;color:#475569;margin-top:6px;">{safe_desc}</div>
    <div style="margin-top:10px;font-size:11px;">
      <span style="background:{risk_color};color:white;padding:2px 8px;border-radius:10px;">
        Risk: {html.escape(class_risk_level)}
      </span>
    </div>
  </div>

  <p style="font-size:13px;color:#334155;margin:0 0 20px 0;">
    <b>By approving, you authorize</b> {safe_brand} to run OsirisCare-verified
    remediations in this specific category for up to <b>{ttl_days} days</b>. You can revoke
    this consent at any time from your portal — revocation takes effect at the next
    check-in (≤15 min) and cancels any pending remediation.
  </p>

  <div style="text-align:center;margin:28px 0;">
    <a href="{magic_link}"
       style="display:inline-block;background:{primary_color};color:white;font-weight:600;font-size:14px;
              padding:12px 28px;border-radius:8px;text-decoration:none;">
      Review and approve
    </a>
  </div>

  <div style="font-size:11px;color:#64748b;background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:10px;">
    <b>Security note:</b> this link is single-use and expires in 72 hours. It authenticates
    via the link itself — we also verify that the approval email matches this address
    ({html.escape(to_email)}). If you didn't expect this request, ignore the email or
    contact <a href="mailto:{html.escape(partner_contact_email)}">{html.escape(partner_contact_email)}</a>.
  </div>

  <p style="margin-top:16px;font-size:11px;color:#94a3b8;">
    A cryptographic record of this consent is created when you approve and stored
    in your compliance packet chain for 7 years per HIPAA §164.316(b)(2)(i).
  </p>
</div>

<div style="padding:12px 24px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b;">
  Delivered by {safe_brand} · Powered by OsirisCare.
</div>

</div>
</body></html>"""

    text_body = (
        f"{partner_brand or 'OsirisCare'} is requesting your authorization to run "
        f"automated remediation in the '{class_display_name}' category "
        f"on site {site_id}.\n\n"
        f"Risk level: {class_risk_level}\n"
        f"TTL: {ttl_days} days (revocable at any time)\n\n"
        f"Review and approve: {magic_link}\n\n"
        f"This link is single-use and expires in 72 hours.\n"
        f"Questions: {partner_contact_email}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{partner_brand or 'OsirisCare'} · Consent requested for {class_display_name}"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return _send_smtp_with_retry(msg, [to_email], f"consent request to {to_email}")
