"""
Level 3: Human Escalation Handler.

Handles 5-10% of incidents that require human intervention:
- Rich ticket generation with full context
- Multiple notification channels (email, Slack, PagerDuty)
- Central Command integration for partner routing
- Feedback collection for learning loop
- Approval workflow for sensitive actions
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

import aiohttp

from .incident_db import (
    IncidentDatabase, Incident,
    ResolutionLevel, IncidentOutcome
)


logger = logging.getLogger(__name__)


class EscalationChannel(str, Enum):
    """Notification channels for escalation."""
    EMAIL = "email"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    TEAMS = "teams"
    WEBHOOK = "webhook"


class EscalationPriority(str, Enum):
    """Priority levels for escalation."""
    LOW = "low"           # Email only, within 4 hours
    MEDIUM = "medium"     # Slack + Email, within 1 hour
    HIGH = "high"         # PagerDuty + Slack, within 15 minutes
    CRITICAL = "critical" # PagerDuty immediate, phone call


@dataclass
class EscalationConfig:
    """Configuration for escalation handler."""
    # Central Command integration (preferred)
    central_command_enabled: bool = False
    central_command_url: Optional[str] = None  # e.g. "https://api.osiriscare.net"
    site_id: Optional[str] = None
    api_key: Optional[str] = None

    # Local notification settings (fallback if Central Command disabled)
    email_enabled: bool = True
    email_recipients: List[str] = field(default_factory=list)
    email_smtp_server: Optional[str] = None

    slack_enabled: bool = False
    slack_webhook_url: Optional[str] = None
    slack_channel: str = "#incidents"

    pagerduty_enabled: bool = False
    pagerduty_routing_key: Optional[str] = None

    teams_enabled: bool = False
    teams_webhook_url: Optional[str] = None

    webhook_enabled: bool = False
    webhook_url: Optional[str] = None

    # Escalation policy
    auto_assign: bool = True
    default_assignee: Optional[str] = None
    escalation_timeout_minutes: int = 60


@dataclass
class EscalationTicket:
    """Rich ticket for human escalation."""
    id: str
    incident_id: str
    title: str
    description: str
    priority: EscalationPriority
    site_id: str
    host_id: str

    # Rich context
    incident_type: str
    severity: str
    raw_data: Dict[str, Any]
    historical_context: Dict[str, Any]
    similar_incidents: List[Dict[str, Any]]
    attempted_actions: List[Dict[str, Any]]

    # Metadata
    created_at: str
    escalation_reason: str
    recommended_action: Optional[str] = None
    hipaa_controls: List[str] = field(default_factory=list)
    assigned_to: Optional[str] = None

    # Workflow
    status: str = "open"
    resolution: Optional[str] = None
    resolved_at: Optional[str] = None
    feedback: Optional[Dict[str, Any]] = None


class EscalationHandler:
    """
    Level 3 Human Escalation Handler.

    Creates rich tickets and manages human workflow for
    incidents that can't be auto-resolved.
    """

    def __init__(
        self,
        config: EscalationConfig,
        incident_db: IncidentDatabase
    ):
        self.config = config
        self.incident_db = incident_db
        self.tickets: Dict[str, EscalationTicket] = {}
        self.notification_handlers: Dict[EscalationChannel, Callable] = {}

        self._setup_notification_handlers()

    def _setup_notification_handlers(self):
        """Setup notification channel handlers."""
        if self.config.email_enabled:
            self.notification_handlers[EscalationChannel.EMAIL] = self._send_email

        if self.config.slack_enabled:
            self.notification_handlers[EscalationChannel.SLACK] = self._send_slack

        if self.config.pagerduty_enabled:
            self.notification_handlers[EscalationChannel.PAGERDUTY] = self._send_pagerduty

        if self.config.teams_enabled:
            self.notification_handlers[EscalationChannel.TEAMS] = self._send_teams

        if self.config.webhook_enabled:
            self.notification_handlers[EscalationChannel.WEBHOOK] = self._send_webhook

    async def escalate(
        self,
        incident: Incident,
        reason: str,
        context: Dict[str, Any],
        attempted_actions: List[Dict[str, Any]] = None,
        recommended_action: Optional[str] = None,
        priority: Optional[EscalationPriority] = None
    ) -> EscalationTicket:
        """
        Create an escalation ticket for human intervention.

        If Central Command is enabled, routes to the partner's configured
        notification channels. Otherwise, uses local notification handlers.
        """
        # Determine priority if not specified
        if priority is None:
            priority = self._determine_priority(incident, reason)

        # Create ticket ID
        ticket_id = f"ESC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{incident.id[-8:]}"

        # Build ticket
        ticket = EscalationTicket(
            id=ticket_id,
            incident_id=incident.id,
            title=self._generate_title(incident, reason),
            description=self._generate_description(incident, reason, context),
            priority=priority,
            site_id=incident.site_id,
            host_id=incident.host_id,
            incident_type=incident.incident_type,
            severity=incident.severity,
            raw_data=incident.raw_data,
            historical_context=context.get("historical", {}),
            similar_incidents=context.get("similar_incidents", []),
            attempted_actions=attempted_actions or [],
            created_at=datetime.now(timezone.utc).isoformat(),
            escalation_reason=reason,
            recommended_action=recommended_action,
            hipaa_controls=self._get_hipaa_controls(incident),
            assigned_to=self.config.default_assignee if self.config.auto_assign else None
        )

        # Store ticket locally
        self.tickets[ticket_id] = ticket

        # Try Central Command first if enabled
        if self.config.central_command_enabled:
            cc_result = await self._escalate_to_central_command(ticket, attempted_actions)
            if cc_result.get("success"):
                ticket.id = cc_result.get("ticket_id", ticket_id)  # Use CC ticket ID
                logger.info(f"Escalated to Central Command: {ticket.id}")
            else:
                logger.warning(f"Central Command escalation failed: {cc_result.get('error')}, using local notifications")
                await self._send_notifications(ticket)
        else:
            # Use local notification handlers
            await self._send_notifications(ticket)

        # Update incident database
        self.incident_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL3_HUMAN,
            resolution_action="escalated",
            outcome=IncidentOutcome.ESCALATED,
            resolution_time_ms=0  # Will be updated on resolution
        )

        logger.info(f"Created escalation ticket {ticket_id} for incident {incident.id}")

        return ticket

    async def _escalate_to_central_command(
        self,
        ticket: 'EscalationTicket',
        attempted_actions: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send escalation to Central Command for partner routing.

        Central Command will:
        1. Look up the partner for this site
        2. Get their notification settings
        3. Send to configured channels (Slack, PagerDuty, etc.)
        4. Create and track the ticket
        """
        if not self.config.central_command_url or not self.config.site_id:
            return {"success": False, "error": "Central Command not configured"}

        url = f"{self.config.central_command_url.rstrip('/')}/api/escalations"

        # Format attempted actions as strings
        actions_list = []
        if attempted_actions:
            for action in attempted_actions:
                if isinstance(action, dict):
                    actions_list.append(action.get('action', str(action)))
                else:
                    actions_list.append(str(action))

        payload = {
            "site_id": self.config.site_id,
            "incident": {
                "id": ticket.incident_id,
                "type": ticket.incident_type,
                "severity": ticket.severity,
                "host": ticket.host_id,
                "description": ticket.escalation_reason,
                "raw_data": ticket.raw_data
            },
            "attempted_actions": actions_list,
            "recommended_action": ticket.recommended_action,
            "priority": ticket.priority.value if isinstance(ticket.priority, EscalationPriority) else ticket.priority
        }

        headers = {
            "Content-Type": "application/json"
        }
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        return {
                            "success": True,
                            "ticket_id": result.get("ticket_id"),
                            "notifications": result.get("notifications", [])
                        }
                    else:
                        text = await resp.text()
                        return {"success": False, "error": f"HTTP {resp.status}: {text[:200]}"}
        except aiohttp.ClientError as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _determine_priority(self, incident: Incident, reason: str) -> EscalationPriority:
        """Determine escalation priority based on incident details."""
        severity = incident.severity.lower()

        if severity == "critical" or "encryption" in reason.lower():
            return EscalationPriority.CRITICAL
        elif severity == "high" or "security" in reason.lower():
            return EscalationPriority.HIGH
        elif severity == "medium":
            return EscalationPriority.MEDIUM
        else:
            return EscalationPriority.LOW

    def _generate_title(self, incident: Incident, reason: str) -> str:
        """Generate ticket title."""
        return f"[{incident.severity.upper()}] {incident.incident_type} - {incident.host_id}"

    def _generate_description(
        self,
        incident: Incident,
        reason: str,
        context: Dict[str, Any]
    ) -> str:
        """Generate rich ticket description."""
        similar_count = len(context.get("similar_incidents", []))
        historical = context.get("historical", {})

        return f"""## Escalation Summary

**Reason:** {reason}

**Incident Details:**
- Type: {incident.incident_type}
- Severity: {incident.severity}
- Site: {incident.site_id}
- Host: {incident.host_id}
- Created: {incident.created_at}

## Historical Context

This pattern has been seen {historical.get('total_occurrences', 0)} times before.
- L1 Resolutions: {historical.get('l1_resolutions', 0)}
- L2 Resolutions: {historical.get('l2_resolutions', 0)}
- L3 Escalations: {historical.get('l3_resolutions', 0)}

{similar_count} similar incidents were found for context.

## Raw Data

```json
{json.dumps(incident.raw_data, indent=2)}
```

## Recommended Actions

Based on historical data, the following actions have been successful:
{self._format_successful_actions(context.get('successful_actions', []))}

## HIPAA Compliance Notes

This incident may affect the following HIPAA controls:
{self._format_hipaa_controls(incident)}

---
*Generated by MSP Compliance Agent - Level 3 Escalation*
"""

    def _format_successful_actions(self, actions: List[Dict[str, Any]]) -> str:
        """Format successful actions for display."""
        if not actions:
            return "- No historical data available"

        lines = []
        for action in actions[:5]:
            lines.append(f"- {action.get('resolution_action', 'unknown')} ({action.get('count', 0)} times)")

        return "\n".join(lines)

    def _format_hipaa_controls(self, incident: Incident) -> str:
        """Format HIPAA controls based on incident type."""
        control_map = {
            "patching": ["164.308(a)(5)(ii)(B)"],
            "av_edr": ["164.308(a)(5)(ii)(B)"],
            "backup": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
            "logging": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "firewall": ["164.312(e)(1)", "164.312(a)(1)"],
            "encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
        }

        controls = control_map.get(incident.incident_type, [])

        if not controls:
            return "- Review applicable controls based on incident details"

        return "\n".join([f"- {c}" for c in controls])

    def _get_hipaa_controls(self, incident: Incident) -> List[str]:
        """Get HIPAA controls for incident."""
        control_map = {
            "patching": ["164.308(a)(5)(ii)(B)"],
            "av_edr": ["164.308(a)(5)(ii)(B)"],
            "backup": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
            "logging": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
            "firewall": ["164.312(e)(1)", "164.312(a)(1)"],
            "encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
        }

        return control_map.get(incident.incident_type, [])

    async def _send_notifications(self, ticket: EscalationTicket):
        """Send notifications to appropriate channels based on priority."""
        channels = self._get_channels_for_priority(ticket.priority)

        for channel in channels:
            handler = self.notification_handlers.get(channel)
            if handler:
                try:
                    await handler(ticket)
                    logger.info(f"Sent {channel.value} notification for ticket {ticket.id}")
                except Exception as e:
                    logger.error(f"Failed to send {channel.value} notification: {e}")

    def _get_channels_for_priority(self, priority: EscalationPriority) -> List[EscalationChannel]:
        """Get notification channels based on priority."""
        if priority == EscalationPriority.CRITICAL:
            return [
                EscalationChannel.PAGERDUTY,
                EscalationChannel.SLACK,
                EscalationChannel.EMAIL
            ]
        elif priority == EscalationPriority.HIGH:
            return [EscalationChannel.PAGERDUTY, EscalationChannel.SLACK]
        elif priority == EscalationPriority.MEDIUM:
            return [EscalationChannel.SLACK, EscalationChannel.EMAIL]
        else:
            return [EscalationChannel.EMAIL]

    async def _send_email(self, ticket: EscalationTicket):
        """Send email notification."""
        if not self.config.email_recipients:
            logger.warning("No email recipients configured")
            return

        # In production, use aiosmtplib or similar
        logger.info(f"Would send email to {self.config.email_recipients} for ticket {ticket.id}")

    async def _send_slack(self, ticket: EscalationTicket):
        """Send Slack notification."""
        if not self.config.slack_webhook_url:
            logger.warning("No Slack webhook configured")
            return

        import aiohttp

        priority_emoji = {
            EscalationPriority.CRITICAL: "🚨",
            EscalationPriority.HIGH: "🔴",
            EscalationPriority.MEDIUM: "🟡",
            EscalationPriority.LOW: "🔵"
        }

        message = {
            "channel": self.config.slack_channel,
            "username": "MSP Compliance Agent",
            "icon_emoji": ":robot_face:",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{priority_emoji.get(ticket.priority, '⚪')} Escalation: {ticket.title}"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Ticket:*\n{ticket.id}"},
                        {"type": "mrkdwn", "text": f"*Priority:*\n{ticket.priority.value}"},
                        {"type": "mrkdwn", "text": f"*Site:*\n{ticket.site_id}"},
                        {"type": "mrkdwn", "text": f"*Host:*\n{ticket.host_id}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Reason:* {ticket.escalation_reason}"
                    }
                }
            ]
        }

        if ticket.recommended_action:
            message["blocks"].append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recommended Action:* `{ticket.recommended_action}`"
                }
            })

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.slack_webhook_url,
                    json=message
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Slack notification failed: {resp.status}")
        except Exception as e:
            logger.error(f"Slack notification error: {e}")

    async def _send_pagerduty(self, ticket: EscalationTicket):
        """Send PagerDuty alert."""
        if not self.config.pagerduty_routing_key:
            logger.warning("No PagerDuty routing key configured")
            return

        import aiohttp

        severity_map = {
            EscalationPriority.CRITICAL: "critical",
            EscalationPriority.HIGH: "error",
            EscalationPriority.MEDIUM: "warning",
            EscalationPriority.LOW: "info"
        }

        payload = {
            "routing_key": self.config.pagerduty_routing_key,
            "event_action": "trigger",
            "dedup_key": ticket.id,
            "payload": {
                "summary": ticket.title,
                "severity": severity_map.get(ticket.priority, "info"),
                "source": f"{ticket.site_id}/{ticket.host_id}",
                "component": ticket.incident_type,
                "group": ticket.site_id,
                "class": "compliance",
                "custom_details": {
                    "ticket_id": ticket.id,
                    "incident_id": ticket.incident_id,
                    "escalation_reason": ticket.escalation_reason,
                    "recommended_action": ticket.recommended_action,
                    "hipaa_controls": ticket.hipaa_controls
                }
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload
                ) as resp:
                    if resp.status not in (200, 202):
                        logger.error(f"PagerDuty notification failed: {resp.status}")
        except Exception as e:
            logger.error(f"PagerDuty notification error: {e}")

    async def _send_teams(self, ticket: EscalationTicket):
        """Send Microsoft Teams notification."""
        if not self.config.teams_webhook_url:
            logger.warning("No Teams webhook configured")
            return

        import aiohttp

        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": self._get_theme_color(ticket.priority),
            "summary": ticket.title,
            "sections": [{
                "activityTitle": f"🔔 {ticket.title}",
                "facts": [
                    {"name": "Ticket ID", "value": ticket.id},
                    {"name": "Priority", "value": ticket.priority.value},
                    {"name": "Site", "value": ticket.site_id},
                    {"name": "Host", "value": ticket.host_id},
                    {"name": "Reason", "value": ticket.escalation_reason}
                ],
                "markdown": True
            }]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.teams_webhook_url,
                    json=card
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Teams notification failed: {resp.status}")
        except Exception as e:
            logger.error(f"Teams notification error: {e}")

    async def _send_webhook(self, ticket: EscalationTicket):
        """Send generic webhook notification."""
        if not self.config.webhook_url:
            return

        import aiohttp

        payload = {
            "event": "escalation",
            "ticket": {
                "id": ticket.id,
                "incident_id": ticket.incident_id,
                "title": ticket.title,
                "priority": ticket.priority.value,
                "site_id": ticket.site_id,
                "host_id": ticket.host_id,
                "incident_type": ticket.incident_type,
                "severity": ticket.severity,
                "escalation_reason": ticket.escalation_reason,
                "recommended_action": ticket.recommended_action,
                "hipaa_controls": ticket.hipaa_controls,
                "created_at": ticket.created_at
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.webhook_url,
                    json=payload
                ) as resp:
                    if resp.status not in (200, 201, 202):
                        logger.error(f"Webhook notification failed: {resp.status}")
        except Exception as e:
            logger.error(f"Webhook notification error: {e}")

    def _get_theme_color(self, priority: EscalationPriority) -> str:
        """Get theme color for Teams card."""
        return {
            EscalationPriority.CRITICAL: "FF0000",
            EscalationPriority.HIGH: "FF6600",
            EscalationPriority.MEDIUM: "FFCC00",
            EscalationPriority.LOW: "0078D4"
        }.get(priority, "808080")

    async def resolve_ticket(
        self,
        ticket_id: str,
        resolution: str,
        action_taken: Optional[str] = None,
        feedback: Optional[Dict[str, Any]] = None
    ):
        """
        Resolve an escalation ticket and collect feedback.

        This feeds back into the learning loop for L1 rule promotion.
        """
        ticket = self.tickets.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        ticket.status = "resolved"
        ticket.resolution = resolution
        ticket.resolved_at = datetime.now(timezone.utc).isoformat()
        ticket.feedback = feedback

        # Record feedback in incident database for learning
        if feedback:
            self.incident_db.add_human_feedback(
                incident_id=ticket.incident_id,
                feedback_type="escalation_resolution",
                feedback_data={
                    "ticket_id": ticket_id,
                    "resolution": resolution,
                    "action_taken": action_taken,
                    "feedback": feedback
                }
            )

        logger.info(f"Resolved ticket {ticket_id}: {resolution}")

    def get_open_tickets(self) -> List[EscalationTicket]:
        """Get all open escalation tickets."""
        return [t for t in self.tickets.values() if t.status == "open"]

    def get_ticket(self, ticket_id: str) -> Optional[EscalationTicket]:
        """Get a specific ticket."""
        return self.tickets.get(ticket_id)
