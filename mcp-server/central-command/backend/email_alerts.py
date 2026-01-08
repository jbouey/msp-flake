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
from datetime import datetime
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


def send_critical_alert(
    title: str,
    message: str,
    site_id: Optional[str] = None,
    category: str = "system",
    metadata: Optional[dict] = None
) -> bool:
    """Send an email alert for a critical notification.

    Args:
        title: Alert title
        message: Alert message
        site_id: Optional site ID related to the alert
        category: Alert category
        metadata: Optional additional metadata

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

        # Plain text version
        text_content = f"""
CRITICAL ALERT - OsirisCare Central Command

Title: {title}
Category: {category}
Site: {site_id or 'System-wide'}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

Message:
{message}

---
This is an automated alert from OsirisCare Central Command.
Dashboard: https://dashboard.osiriscare.net
"""

        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #dc2626, #ea580c); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
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
        </div>
        <div class="content">
            <div class="field">
                <div class="field-label">Title</div>
                <div class="field-value" style="font-size: 18px; font-weight: 600;">{title}</div>
            </div>
            <div class="field">
                <div class="field-label">Category</div>
                <div class="field-value">{category}</div>
            </div>
            <div class="field">
                <div class="field-label">Site</div>
                <div class="field-value">{site_id or 'System-wide'}</div>
            </div>
            <div class="field">
                <div class="field-label">Time</div>
                <div class="field-value">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
            </div>
            <div class="message-box">
                <div class="field-label">Message</div>
                <div class="field-value">{message}</div>
            </div>
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
    metadata: Optional[dict] = None
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
            metadata=metadata
        )

    return notification_id
