"""Email service for user management.

Sends emails for:
- User invitations
- Password resets
"""

import os
import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger(__name__)

# SMTP Configuration from environment
SMTP_HOST = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "noreply@osiriscare.net")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard.osiriscare.net")


def is_email_configured() -> bool:
    """Check if email is properly configured."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def send_invite_email(
    to_email: str,
    invite_token: str,
    inviter_name: str,
    role: str,
    display_name: Optional[str] = None
) -> bool:
    """Send user invitation email with password setup link.

    Args:
        to_email: Recipient email address
        invite_token: The invite token (plaintext, will be in URL)
        inviter_name: Name of the user who sent the invite
        role: Role being assigned (admin, operator, readonly)
        display_name: Optional display name for the new user

    Returns:
        True if email sent successfully, False otherwise
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping invite email")
        return False

    invite_url = f"{DASHBOARD_URL}/set-password?token={invite_token}"

    role_descriptions = {
        "admin": "Full administrative access including user management",
        "operator": "Can view data and execute actions (runbooks, alerts)",
        "readonly": "View-only access to dashboards and reports",
    }
    role_desc = role_descriptions.get(role, role)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "You've been invited to Central Command"
        msg["From"] = SMTP_FROM
        msg["To"] = to_email

        # Plain text version
        text_content = f"""
You've been invited to Central Command

{inviter_name} has invited you to join OsirisCare Central Command as a {role}.

Role: {role.title()}
Description: {role_desc}

Click the link below to set your password and activate your account:
{invite_url}

This link expires in 7 days.

If you didn't expect this invitation, please ignore this email.

---
OsirisCare Central Command
{DASHBOARD_URL}
"""

        # HTML version
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #4F46E5, #7C3AED); color: white; padding: 30px 20px; border-radius: 12px 12px 0 0; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
        .content {{ background: #ffffff; padding: 30px; border: 1px solid #e5e7eb; border-top: none; }}
        .intro {{ font-size: 16px; color: #374151; margin-bottom: 24px; }}
        .role-box {{ background: #f3f4f6; padding: 16px; border-radius: 8px; margin-bottom: 24px; }}
        .role-title {{ font-weight: 600; color: #111827; font-size: 18px; margin-bottom: 4px; }}
        .role-desc {{ color: #6b7280; font-size: 14px; }}
        .button {{ display: inline-block; background: #4F46E5; color: white !important; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px; }}
        .button:hover {{ background: #4338CA; }}
        .button-container {{ text-align: center; margin: 30px 0; }}
        .expiry {{ color: #9CA3AF; font-size: 14px; text-align: center; margin-top: 20px; }}
        .footer {{ padding: 20px; background: #f9fafb; border-radius: 0 0 12px 12px; font-size: 12px; color: #6b7280; text-align: center; }}
        .footer a {{ color: #4F46E5; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to Central Command</h1>
        </div>
        <div class="content">
            <p class="intro">
                <strong>{inviter_name}</strong> has invited you to join OsirisCare Central Command.
            </p>

            <div class="role-box">
                <div class="role-title">{role.title()}</div>
                <div class="role-desc">{role_desc}</div>
            </div>

            <p style="color: #374151;">Click the button below to set your password and activate your account:</p>

            <div class="button-container">
                <a href="{invite_url}" class="button">Set Your Password</a>
            </div>

            <p class="expiry">This link expires in 7 days.</p>
        </div>
        <div class="footer">
            If you didn't expect this invitation, please ignore this email.<br><br>
            <a href="{DASHBOARD_URL}">OsirisCare Central Command</a>
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
            server.sendmail(SMTP_FROM, to_email, msg.as_string())

        logger.info(f"Invite email sent to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}")
        return False


async def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a simple plain-text email.

    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Plain text body

    Returns:
        True if email sent successfully, False otherwise
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping email")
        return False

    try:
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())

        logger.info(f"Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_password_reset_email(
    to_email: str,
    reset_token: str,
    username: str
) -> bool:
    """Send password reset email.

    Args:
        to_email: Recipient email address
        reset_token: The reset token (plaintext, will be in URL)
        username: Username of the account

    Returns:
        True if email sent successfully, False otherwise
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping password reset email")
        return False

    reset_url = f"{DASHBOARD_URL}/reset-password?token={reset_token}"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Reset Your Password - Central Command"
        msg["From"] = SMTP_FROM
        msg["To"] = to_email

        text_content = f"""
Password Reset Request

A password reset was requested for your Central Command account ({username}).

Click the link below to reset your password:
{reset_url}

This link expires in 1 hour.

If you didn't request this, please ignore this email. Your password will remain unchanged.

---
OsirisCare Central Command
{DASHBOARD_URL}
"""

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #374151; color: white; padding: 30px 20px; border-radius: 12px 12px 0 0; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 24px; font-weight: 600; }}
        .content {{ background: #ffffff; padding: 30px; border: 1px solid #e5e7eb; border-top: none; }}
        .button {{ display: inline-block; background: #4F46E5; color: white !important; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; }}
        .button-container {{ text-align: center; margin: 30px 0; }}
        .expiry {{ color: #9CA3AF; font-size: 14px; text-align: center; }}
        .footer {{ padding: 20px; background: #f9fafb; border-radius: 0 0 12px 12px; font-size: 12px; color: #6b7280; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Reset Your Password</h1>
        </div>
        <div class="content">
            <p>A password reset was requested for your Central Command account (<strong>{username}</strong>).</p>

            <div class="button-container">
                <a href="{reset_url}" class="button">Reset Password</a>
            </div>

            <p class="expiry">This link expires in 1 hour.</p>

            <p style="color: #6b7280; font-size: 14px;">If you didn't request this, please ignore this email. Your password will remain unchanged.</p>
        </div>
        <div class="footer">
            OsirisCare Central Command
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())

        logger.info(f"Password reset email sent to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send password reset email to {to_email}: {e}")
        return False
