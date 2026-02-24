"""
L3 Escalation Engine - Routes incidents to partner notification channels.

This module handles the actual delivery of escalation notifications to
partners via their configured channels (Slack, PagerDuty, Email, Teams, Webhook).

Usage:
    from .escalation_engine import EscalationEngine

    engine = EscalationEngine()
    ticket = await engine.create_escalation(
        site_id="clinic-abc123",
        incident=incident_data,
        attempted_actions=["L1 restart failed", "L2 plan failed"]
    )
"""

import asyncio
import json
import hmac
import hashlib
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from functools import partial
from typing import Optional, Dict, Any, List
from uuid import uuid4

import aiohttp

from .fleet import get_pool

logger = logging.getLogger(__name__)


# =============================================================================
# EMAIL CONFIGURATION (from environment)
# =============================================================================

import os

SMTP_HOST = os.getenv("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASSWORD", "")


# =============================================================================
# NOTIFICATION SENDER FUNCTIONS
# =============================================================================

async def send_slack_notification(settings: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a Slack notification via webhook.

    Args:
        settings: Partner notification settings with slack_webhook_url, slack_channel, etc.
        payload: Notification payload with title, summary, severity, etc.

    Returns:
        Dict with status and any response details
    """
    webhook_url = settings.get('slack_webhook_url')
    if not webhook_url:
        return {"status": "skipped", "reason": "No Slack webhook configured"}

    priority_emoji = {
        "critical": ":rotating_light:",
        "high": ":red_circle:",
        "medium": ":large_yellow_circle:",
        "low": ":large_blue_circle:"
    }

    severity = payload.get('severity', 'medium').lower()
    emoji = priority_emoji.get(severity, ":warning:")

    message = {
        "channel": settings.get('slack_channel', '#incidents'),
        "username": settings.get('slack_username', 'OsirisCare'),
        "icon_emoji": settings.get('slack_icon_emoji', ':warning:'),
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {payload.get('title', 'Escalation Alert')}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity.upper()}"},
                    {"type": "mrkdwn", "text": f"*Site:*\n{payload.get('site_name', 'Unknown')}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Summary:*\n{payload.get('summary', 'No details available')}"
                }
            }
        ]
    }

    # Add HIPAA controls if present
    if payload.get('hipaa_controls'):
        controls_text = ", ".join(payload['hipaa_controls'])
        message["blocks"].append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":shield: *HIPAA Controls:* {controls_text}"}
            ]
        })

    # Add recommended action if present
    if payload.get('recommended_action'):
        message["blocks"].append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":bulb: *Recommended Action:*\n`{payload['recommended_action']}`"
            }
        })

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=message, timeout=10) as resp:
                if resp.status == 200:
                    return {"status": "sent", "channel": "slack"}
                else:
                    text = await resp.text()
                    return {"status": "failed", "code": resp.status, "error": text}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def send_pagerduty_notification(
    settings: Dict[str, Any],
    payload: Dict[str, Any],
    is_test: bool = False
) -> Dict[str, Any]:
    """
    Send a PagerDuty alert via Events API v2.

    Args:
        settings: Partner settings with pagerduty_routing_key
        payload: Alert payload
        is_test: If True, include test marker in dedup_key
    """
    routing_key = settings.get('pagerduty_routing_key')
    if not routing_key:
        return {"status": "skipped", "reason": "No PagerDuty routing key configured"}

    severity_map = {
        "critical": "critical",
        "high": "error",
        "medium": "warning",
        "low": "info"
    }

    severity = payload.get('severity', 'medium').lower()
    pd_severity = severity_map.get(severity, "info")

    dedup_key = payload.get('ticket_id', str(uuid4()))
    if is_test:
        dedup_key = f"test-{dedup_key}"

    event = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup_key,
        "payload": {
            "summary": payload.get('title', 'OsirisCare Escalation'),
            "severity": pd_severity,
            "source": payload.get('site_name', 'Unknown Site'),
            "component": payload.get('incident_type', 'compliance'),
            "group": payload.get('site_id', 'unknown'),
            "class": "hipaa_compliance",
            "custom_details": {
                "summary": payload.get('summary'),
                "hipaa_controls": payload.get('hipaa_controls', []),
                "recommended_action": payload.get('recommended_action'),
                "attempted_actions": payload.get('attempted_actions', [])
            }
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=event,
                timeout=10
            ) as resp:
                if resp.status in (200, 202):
                    body = await resp.json()
                    return {
                        "status": "sent",
                        "channel": "pagerduty",
                        "dedup_key": dedup_key,
                        "message": body.get('message')
                    }
                else:
                    text = await resp.text()
                    return {"status": "failed", "code": resp.status, "error": text}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def send_email_notification(settings: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send email notification via SMTP.

    Args:
        settings: Partner settings with email_recipients, email_from_name
        payload: Notification payload
    """
    recipients = settings.get('email_recipients', [])
    if not recipients:
        return {"status": "skipped", "reason": "No email recipients configured"}

    from_name = settings.get('email_from_name', 'OsirisCare Alerts')
    severity = payload.get('severity', 'medium').upper()

    # Build email
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"[{severity}] {payload.get('title', 'Escalation Alert')}"
    msg['From'] = f"{from_name} <{SMTP_USER}>"
    msg['To'] = ", ".join(recipients)

    # Plain text version
    text_body = f"""
OsirisCare Escalation Alert
============================

Title: {payload.get('title', 'Unknown')}
Severity: {severity}
Site: {payload.get('site_name', 'Unknown')}

Summary:
{payload.get('summary', 'No details available')}

HIPAA Controls: {', '.join(payload.get('hipaa_controls', []))}

Recommended Action:
{payload.get('recommended_action', 'Review and assess')}

---
This is an automated notification from OsirisCare Compliance Platform.
"""

    # HTML version
    html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background-color: {'#dc3545' if severity == 'CRITICAL' else '#ffc107' if severity in ['HIGH', 'MEDIUM'] else '#17a2b8'}; color: white; padding: 15px; border-radius: 5px 5px 0 0;">
        <h2 style="margin: 0;">{payload.get('title', 'Escalation Alert')}</h2>
        <p style="margin: 5px 0 0 0;">Severity: {severity}</p>
    </div>
    <div style="border: 1px solid #ddd; border-top: none; padding: 20px;">
        <p><strong>Site:</strong> {payload.get('site_name', 'Unknown')}</p>
        <h3>Summary</h3>
        <p>{payload.get('summary', 'No details available')}</p>

        <h3>HIPAA Controls</h3>
        <p>{', '.join(payload.get('hipaa_controls', [])) or 'None specified'}</p>

        <h3>Recommended Action</h3>
        <p style="background-color: #f8f9fa; padding: 10px; border-radius: 5px;">
            {payload.get('recommended_action', 'Review and assess')}
        </p>
    </div>
    <div style="padding: 10px; color: #666; font-size: 12px; text-align: center;">
        OsirisCare Compliance Platform - Automated Notification
    </div>
</body>
</html>
"""

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        if not SMTP_PASS:
            logger.warning("SMTP password not configured, email skipped")
            return {"status": "skipped", "reason": "SMTP not configured"}

        def _send_smtp(message):
            ctx = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls(context=ctx)
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(message)

        await asyncio.get_event_loop().run_in_executor(None, partial(_send_smtp, msg))

        return {"status": "sent", "channel": "email", "recipients": len(recipients)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def send_teams_notification(settings: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send Microsoft Teams notification via webhook connector.

    Args:
        settings: Partner settings with teams_webhook_url
        payload: Notification payload
    """
    webhook_url = settings.get('teams_webhook_url')
    if not webhook_url:
        return {"status": "skipped", "reason": "No Teams webhook configured"}

    severity = payload.get('severity', 'medium').lower()
    theme_colors = {
        "critical": "FF0000",
        "high": "FF6600",
        "medium": "FFCC00",
        "low": "0078D4"
    }

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme_colors.get(severity, "808080"),
        "summary": payload.get('title', 'OsirisCare Escalation'),
        "sections": [{
            "activityTitle": payload.get('title', 'Escalation Alert'),
            "activitySubtitle": f"Severity: {severity.upper()}",
            "facts": [
                {"name": "Site", "value": payload.get('site_name', 'Unknown')},
                {"name": "Incident Type", "value": payload.get('incident_type', 'compliance')},
                {"name": "HIPAA Controls", "value": ', '.join(payload.get('hipaa_controls', [])) or 'N/A'}
            ],
            "text": payload.get('summary', 'No details available'),
            "markdown": True
        }]
    }

    if payload.get('recommended_action'):
        card["sections"].append({
            "title": "Recommended Action",
            "text": f"`{payload['recommended_action']}`"
        })

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=card, timeout=10) as resp:
                if resp.status == 200:
                    return {"status": "sent", "channel": "teams"}
                else:
                    text = await resp.text()
                    return {"status": "failed", "code": resp.status, "error": text}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def send_webhook_notification(settings: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send generic webhook notification with optional HMAC signature.

    Args:
        settings: Partner settings with webhook_url, webhook_secret, webhook_headers
        payload: Notification payload
    """
    webhook_url = settings.get('webhook_url')
    if not webhook_url:
        return {"status": "skipped", "reason": "No webhook URL configured"}

    # Build webhook payload
    body = {
        "event": "escalation.created",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "ticket_id": payload.get('ticket_id'),
            "title": payload.get('title'),
            "severity": payload.get('severity'),
            "site_id": payload.get('site_id'),
            "site_name": payload.get('site_name'),
            "incident_type": payload.get('incident_type'),
            "summary": payload.get('summary'),
            "hipaa_controls": payload.get('hipaa_controls', []),
            "recommended_action": payload.get('recommended_action'),
            "attempted_actions": payload.get('attempted_actions', [])
        }
    }

    # Include raw data if configured
    if settings.get('include_raw_data') and payload.get('raw_data'):
        body['data']['raw_data'] = payload['raw_data']

    body_json = json.dumps(body, separators=(',', ':'))

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "OsirisCare-Escalation/1.0"
    }

    # Add custom headers if configured
    custom_headers = settings.get('webhook_headers')
    if custom_headers:
        if isinstance(custom_headers, str):
            try:
                custom_headers = json.loads(custom_headers)
            except json.JSONDecodeError:
                pass
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)

    # Add HMAC signature if secret is configured
    webhook_secret = settings.get('webhook_secret')
    if webhook_secret:
        signature = hmac.new(
            webhook_secret.encode('utf-8'),
            body_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers["X-OsirisCare-Signature"] = f"sha256={signature}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                data=body_json,
                headers=headers,
                timeout=30
            ) as resp:
                if resp.status in (200, 201, 202, 204):
                    return {"status": "sent", "channel": "webhook", "code": resp.status}
                else:
                    text = await resp.text()
                    return {"status": "failed", "code": resp.status, "error": text[:500]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =============================================================================
# ESCALATION ENGINE CLASS
# =============================================================================

class EscalationEngine:
    """
    L3 Escalation Engine for Central Command.

    Routes incidents from appliances to the appropriate partner
    based on site ownership, creates tickets, and sends notifications.
    """

    def __init__(self):
        self.pool = None

    async def _get_pool(self):
        """Get database connection pool."""
        if self.pool is None:
            self.pool = await get_pool()
        return self.pool

    async def create_escalation(
        self,
        site_id: str,
        incident: Dict[str, Any],
        attempted_actions: List[str] = None,
        recommended_action: Optional[str] = None,
        severity: Optional[str] = None,
        priority: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an escalation ticket and notify the partner.

        Args:
            site_id: Site where incident occurred
            incident: Incident data from appliance
            attempted_actions: List of L1/L2 actions that were attempted
            recommended_action: Suggested remediation
            severity: Incident severity (critical, high, medium, low)
            priority: Override priority for routing

        Returns:
            Created ticket details including notification status
        """
        pool = await self._get_pool()

        async with pool.acquire() as conn:
            # Get site and partner info
            site = await conn.fetchrow("""
                SELECT s.id, s.site_id, s.clinic_name, s.partner_id, s.status,
                       p.company_name as partner_name
                FROM sites s
                LEFT JOIN partners p ON s.partner_id = p.id
                WHERE s.site_id = $1
            """, site_id)

            if not site:
                raise ValueError(f"Site {site_id} not found")

            partner_id = site['partner_id']
            if not partner_id:
                logger.warning(f"Site {site_id} has no partner, using internal escalation")
                return await self._create_internal_escalation(incident, site)

            # Get partner notification settings
            settings = await conn.fetchrow("""
                SELECT id, partner_id, email_enabled, email_recipients,
                       slack_enabled, slack_webhook_url, slack_channel,
                       pagerduty_enabled, pagerduty_routing_key,
                       teams_enabled, teams_webhook_url,
                       webhook_enabled, webhook_url, webhook_secret,
                       min_severity, escalation_timeout_minutes
                FROM partner_notification_settings
                WHERE partner_id = $1
            """, partner_id)

            # Get site-level overrides
            overrides = await conn.fetchrow("""
                SELECT site_id, email_recipients, slack_channel,
                       pagerduty_routing_key, escalation_timeout_minutes,
                       priority_override
                FROM site_notification_overrides
                WHERE site_id = $1
            """, site_id)

            # Merge settings with overrides
            effective_settings = dict(settings) if settings else {}
            if overrides:
                for key in ['email_recipients', 'slack_channel', 'pagerduty_routing_key', 'escalation_timeout_minutes']:
                    if overrides.get(key):
                        effective_settings[key] = overrides[key]
                if overrides.get('priority_override'):
                    priority = overrides['priority_override']

            # Determine severity and priority
            incident_severity = severity or incident.get('severity', 'medium')
            incident_priority = priority or self._determine_priority(incident_severity, incident)

            # Get SLA target
            sla = await conn.fetchrow("""
                SELECT response_time_minutes FROM sla_definitions
                WHERE (partner_id = $1 OR partner_id IS NULL) AND priority = $2
                ORDER BY partner_id NULLS LAST
                LIMIT 1
            """, partner_id, incident_priority)

            sla_minutes = sla['response_time_minutes'] if sla else 60
            sla_target = datetime.now(timezone.utc) + timedelta(minutes=sla_minutes)

            # Generate ticket ID
            ticket_id = f"ESC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"

            # Build ticket payload
            ticket_data = {
                "ticket_id": ticket_id,
                "title": self._generate_title(incident),
                "summary": self._generate_summary(incident, attempted_actions),
                "severity": incident_severity,
                "priority": incident_priority,
                "site_id": site_id,
                "site_name": site['clinic_name'],
                "incident_id": incident.get('id', str(uuid4())),
                "incident_type": incident.get('type', incident.get('incident_type', 'unknown')),
                "hipaa_controls": self._get_hipaa_controls(incident),
                "attempted_actions": attempted_actions or [],
                "recommended_action": recommended_action or self._suggest_action(incident),
                "raw_data": incident.get('raw_data', incident)
            }

            # Create ticket in database
            await conn.execute("""
                INSERT INTO escalation_tickets (
                    id, partner_id, site_id, incident_id, incident_type,
                    severity, priority, title, summary, raw_data,
                    hipaa_controls, attempted_actions, recommended_action,
                    sla_target_at, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, NOW())
            """,
                ticket_id,
                partner_id,
                site_id,
                ticket_data['incident_id'],
                ticket_data['incident_type'],
                incident_severity,
                incident_priority,
                ticket_data['title'],
                ticket_data['summary'],
                json.dumps(ticket_data['raw_data']),
                ticket_data['hipaa_controls'],
                json.dumps(attempted_actions or []),
                recommended_action,
                sla_target
            )

            logger.info(f"Created escalation ticket {ticket_id} for site {site_id}")

            # Send notifications
            notification_results = await self._send_all_notifications(
                conn, ticket_id, effective_settings, ticket_data, incident_priority
            )

            return {
                "ticket_id": ticket_id,
                "partner_id": partner_id,
                "site_id": site_id,
                "priority": incident_priority,
                "sla_target": sla_target.isoformat(),
                "notifications": notification_results
            }

    async def _send_all_notifications(
        self,
        conn,
        ticket_id: str,
        settings: Dict[str, Any],
        payload: Dict[str, Any],
        priority: str
    ) -> List[Dict[str, Any]]:
        """Send notifications to all appropriate channels based on priority."""
        channels = self._get_channels_for_priority(settings, priority)
        results = []

        for channel in channels:
            result = await self._send_and_log(conn, ticket_id, channel, settings, payload)
            results.append(result)

        return results

    def _get_channels_for_priority(self, settings: Dict[str, Any], priority: str) -> List[str]:
        """Determine which channels to notify based on priority."""
        channels = []

        # Critical: All enabled channels
        if priority == "critical":
            if settings.get('pagerduty_enabled'):
                channels.append('pagerduty')
            if settings.get('slack_enabled'):
                channels.append('slack')
            if settings.get('teams_enabled'):
                channels.append('teams')
            if settings.get('email_enabled'):
                channels.append('email')
            if settings.get('webhook_enabled'):
                channels.append('webhook')

        # High: PagerDuty + Slack/Teams
        elif priority == "high":
            if settings.get('pagerduty_enabled'):
                channels.append('pagerduty')
            if settings.get('slack_enabled'):
                channels.append('slack')
            elif settings.get('teams_enabled'):
                channels.append('teams')

        # Medium: Slack/Teams + Email
        elif priority == "medium":
            if settings.get('slack_enabled'):
                channels.append('slack')
            elif settings.get('teams_enabled'):
                channels.append('teams')
            if settings.get('email_enabled'):
                channels.append('email')

        # Low: Email only
        else:
            if settings.get('email_enabled'):
                channels.append('email')

        # Always send to webhook if enabled (PSA/RMM integration)
        if settings.get('webhook_enabled') and 'webhook' not in channels:
            channels.append('webhook')

        return channels

    async def _send_and_log(
        self,
        conn,
        ticket_id: str,
        channel: str,
        settings: Dict[str, Any],
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send notification to a channel and log the attempt."""
        send_funcs = {
            'slack': send_slack_notification,
            'pagerduty': send_pagerduty_notification,
            'email': send_email_notification,
            'teams': send_teams_notification,
            'webhook': send_webhook_notification
        }

        send_func = send_funcs.get(channel)
        if not send_func:
            return {"channel": channel, "status": "error", "error": "Unknown channel"}

        try:
            result = await send_func(settings, payload)
        except Exception as e:
            result = {"status": "error", "error": str(e)}

        # Log delivery attempt
        recipient = self._get_recipient_for_channel(settings, channel)
        await conn.execute("""
            INSERT INTO notification_deliveries (
                ticket_id, channel, recipient, status,
                response_code, error_message, sent_at
            ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
        """,
            ticket_id,
            channel,
            recipient,
            result.get('status', 'unknown'),
            result.get('code'),
            result.get('error')
        )

        return {"channel": channel, **result}

    def _get_recipient_for_channel(self, settings: Dict[str, Any], channel: str) -> str:
        """Get a display-friendly recipient string for logging."""
        if channel == 'slack':
            return settings.get('slack_channel', '#unknown')
        elif channel == 'pagerduty':
            return settings.get('pagerduty_service_id', 'default-service')
        elif channel == 'email':
            recipients = settings.get('email_recipients', [])
            return ', '.join(recipients[:3]) if recipients else 'no-recipients'
        elif channel == 'teams':
            return 'teams-channel'
        elif channel == 'webhook':
            return settings.get('webhook_url', 'unknown')[:50]
        return 'unknown'

    def _determine_priority(self, severity: str, incident: Dict[str, Any]) -> str:
        """Determine priority from severity and incident details."""
        severity = severity.lower()

        # Certain incident types are always high priority
        high_priority_types = ['encryption', 'ransomware', 'data_breach', 'unauthorized_access']
        incident_type = incident.get('type', incident.get('incident_type', '')).lower()

        if any(t in incident_type for t in high_priority_types):
            return 'critical'

        return {
            'critical': 'critical',
            'high': 'high',
            'medium': 'medium',
            'low': 'low'
        }.get(severity, 'medium')

    def _generate_title(self, incident: Dict[str, Any]) -> str:
        """Generate a concise ticket title."""
        severity = incident.get('severity', 'unknown').upper()
        incident_type = incident.get('type', incident.get('incident_type', 'Unknown'))
        host = incident.get('host', incident.get('host_id', ''))

        title = f"[{severity}] {incident_type}"
        if host:
            title += f" - {host}"
        return title[:200]  # Limit length

    def _generate_summary(self, incident: Dict[str, Any], attempted_actions: List[str] = None) -> str:
        """Generate a summary description."""
        parts = []

        if incident.get('description'):
            parts.append(incident['description'])
        elif incident.get('message'):
            parts.append(incident['message'])

        if attempted_actions:
            parts.append("\nAttempted remediation:")
            for action in attempted_actions[:5]:
                parts.append(f"  - {action}")

        return "\n".join(parts) or "Incident requires human review."

    def _get_hipaa_controls(self, incident: Dict[str, Any]) -> List[str]:
        """Map incident type to HIPAA controls."""
        control_map = {
            "patching": ["164.308(a)(5)(ii)(B)"],
            "av_edr": ["164.308(a)(5)(ii)(B)"],
            "backup": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
            "logging": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "firewall": ["164.312(e)(1)", "164.312(a)(1)"],
            "encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
            "access_control": ["164.312(d)", "164.312(a)(1)"],
            "audit": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "ntp": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "time": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
        }

        incident_type = incident.get('type', incident.get('incident_type', '')).lower()

        for key, controls in control_map.items():
            if key in incident_type:
                return controls

        return []

    def _suggest_action(self, incident: Dict[str, Any]) -> str:
        """Suggest a remediation action based on incident type."""
        suggestions = {
            "patching": "Review patch status and manually apply critical updates",
            "av_edr": "Check endpoint protection status and scan for threats",
            "backup": "Verify backup integrity and investigate failure cause",
            "logging": "Check log service status and disk space",
            "firewall": "Review firewall rules and network segmentation",
            "encryption": "Verify encryption at rest and in transit",
            "service_down": "Restart service and check for resource constraints",
            "ntp": "Verify NTP service is running and reachable. Check chrony/systemd-timesyncd status and network connectivity to NTP servers",
            "time": "Check system clock synchronization. Evidence timestamps require accurate clocks for HIPAA audit trails",
        }

        incident_type = incident.get('type', incident.get('incident_type', '')).lower()

        for key, suggestion in suggestions.items():
            if key in incident_type:
                return suggestion

        return "Review incident details and assess risk level"

    async def _create_internal_escalation(
        self,
        incident: Dict[str, Any],
        site: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle escalation for sites without a partner."""
        ticket_id = f"INT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"

        logger.warning(
            f"Internal escalation created: {ticket_id} for site {site['id']} "
            f"(no partner configured)"
        )

        return {
            "ticket_id": ticket_id,
            "partner_id": None,
            "site_id": site['id'],
            "priority": "medium",
            "notifications": [],
            "note": "Site has no partner - escalated to internal queue"
        }

    async def check_sla_breaches(self) -> List[Dict[str, Any]]:
        """Check for SLA breaches and update tickets."""
        pool = await self._get_pool()
        breached = []

        async with pool.acquire() as conn:
            # Find tickets that have breached SLA
            tickets = await conn.fetch("""
                UPDATE escalation_tickets
                SET sla_breached = true, updated_at = NOW()
                WHERE status = 'open'
                AND sla_breached = false
                AND sla_target_at < NOW()
                RETURNING id, partner_id, site_id, title, priority, sla_target_at
            """)

            for ticket in tickets:
                breached.append(dict(ticket))
                logger.warning(f"SLA breach: ticket {ticket['id']} exceeded target")

        return breached


# Singleton instance for easy import
_engine: Optional[EscalationEngine] = None


async def get_escalation_engine() -> EscalationEngine:
    """Get or create the escalation engine singleton."""
    global _engine
    if _engine is None:
        _engine = EscalationEngine()
    return _engine
