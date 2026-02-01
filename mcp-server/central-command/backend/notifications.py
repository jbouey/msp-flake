"""
Partner notification configuration and L3 escalation routing.

Provides endpoints for:
- Partner notification settings (Slack, PagerDuty, Email, Teams, Webhook)
- Site-level notification overrides
- Escalation ticket management
- SLA metrics
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr

from .fleet import get_pool
from .partners import require_partner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/partners/me/notifications", tags=["notifications"])


# =============================================================================
# MODELS
# =============================================================================

class NotificationSettings(BaseModel):
    """Partner notification channel settings."""
    # Email
    email_enabled: bool = True
    email_recipients: List[str] = []
    email_from_name: Optional[str] = None

    # Slack
    slack_enabled: bool = False
    slack_webhook_url: Optional[str] = None
    slack_channel: Optional[str] = None
    slack_username: str = "OsirisCare"
    slack_icon_emoji: str = ":warning:"

    # PagerDuty
    pagerduty_enabled: bool = False
    pagerduty_routing_key: Optional[str] = None
    pagerduty_service_id: Optional[str] = None

    # Teams
    teams_enabled: bool = False
    teams_webhook_url: Optional[str] = None

    # Generic Webhook
    webhook_enabled: bool = False
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    webhook_headers: Optional[Dict[str, str]] = None

    # Behavior
    escalation_timeout_minutes: int = 60
    auto_acknowledge: bool = False
    include_raw_data: bool = True


class SiteNotificationOverride(BaseModel):
    """Site-level notification overrides."""
    email_recipients: Optional[List[str]] = None
    slack_channel: Optional[str] = None
    pagerduty_routing_key: Optional[str] = None
    escalation_timeout_minutes: Optional[int] = None
    priority_override: Optional[str] = None


class TicketAcknowledge(BaseModel):
    """Ticket acknowledgment request."""
    acknowledged_by: str
    notes: Optional[str] = None


class TicketResolve(BaseModel):
    """Ticket resolution request."""
    resolved_by: str
    resolution_notes: str


# =============================================================================
# NOTIFICATION SETTINGS ENDPOINTS
# =============================================================================

@router.get("/settings")
async def get_notification_settings(partner=Depends(require_partner)):
    """Get partner's notification settings."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        settings = await conn.fetchrow("""
            SELECT
                email_enabled, email_recipients, email_from_name,
                slack_enabled, slack_webhook_url, slack_channel, slack_username, slack_icon_emoji,
                pagerduty_enabled, pagerduty_routing_key, pagerduty_service_id,
                teams_enabled, teams_webhook_url,
                webhook_enabled, webhook_url, webhook_secret, webhook_headers,
                escalation_timeout_minutes, auto_acknowledge, include_raw_data
            FROM partner_notification_settings
            WHERE partner_id = $1
        """, partner['id'])

        if not settings:
            # Return defaults
            return NotificationSettings().model_dump()

        result = dict(settings)
        # Parse JSON fields
        if result.get('webhook_headers'):
            result['webhook_headers'] = json.loads(result['webhook_headers'])
        return result


@router.put("/settings")
async def update_notification_settings(
    settings: NotificationSettings,
    partner=Depends(require_partner)
):
    """Update partner's notification settings."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO partner_notification_settings (
                partner_id, email_enabled, email_recipients, email_from_name,
                slack_enabled, slack_webhook_url, slack_channel, slack_username, slack_icon_emoji,
                pagerduty_enabled, pagerduty_routing_key, pagerduty_service_id,
                teams_enabled, teams_webhook_url,
                webhook_enabled, webhook_url, webhook_secret, webhook_headers,
                escalation_timeout_minutes, auto_acknowledge, include_raw_data,
                updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, NOW())
            ON CONFLICT (partner_id) DO UPDATE SET
                email_enabled = EXCLUDED.email_enabled,
                email_recipients = EXCLUDED.email_recipients,
                email_from_name = EXCLUDED.email_from_name,
                slack_enabled = EXCLUDED.slack_enabled,
                slack_webhook_url = EXCLUDED.slack_webhook_url,
                slack_channel = EXCLUDED.slack_channel,
                slack_username = EXCLUDED.slack_username,
                slack_icon_emoji = EXCLUDED.slack_icon_emoji,
                pagerduty_enabled = EXCLUDED.pagerduty_enabled,
                pagerduty_routing_key = EXCLUDED.pagerduty_routing_key,
                pagerduty_service_id = EXCLUDED.pagerduty_service_id,
                teams_enabled = EXCLUDED.teams_enabled,
                teams_webhook_url = EXCLUDED.teams_webhook_url,
                webhook_enabled = EXCLUDED.webhook_enabled,
                webhook_url = EXCLUDED.webhook_url,
                webhook_secret = EXCLUDED.webhook_secret,
                webhook_headers = EXCLUDED.webhook_headers,
                escalation_timeout_minutes = EXCLUDED.escalation_timeout_minutes,
                auto_acknowledge = EXCLUDED.auto_acknowledge,
                include_raw_data = EXCLUDED.include_raw_data,
                updated_at = NOW()
        """,
            partner['id'],
            settings.email_enabled,
            settings.email_recipients,
            settings.email_from_name,
            settings.slack_enabled,
            settings.slack_webhook_url,
            settings.slack_channel,
            settings.slack_username,
            settings.slack_icon_emoji,
            settings.pagerduty_enabled,
            settings.pagerduty_routing_key,
            settings.pagerduty_service_id,
            settings.teams_enabled,
            settings.teams_webhook_url,
            settings.webhook_enabled,
            settings.webhook_url,
            settings.webhook_secret,
            json.dumps(settings.webhook_headers) if settings.webhook_headers else None,
            settings.escalation_timeout_minutes,
            settings.auto_acknowledge,
            settings.include_raw_data
        )

    logger.info(f"Updated notification settings for partner {partner['id']}")
    return {"status": "updated"}


@router.post("/settings/test")
async def test_notification_channel(
    channel: str = Query(..., description="Channel to test: email, slack, pagerduty, teams, webhook"),
    partner=Depends(require_partner)
):
    """Send a test notification to verify channel configuration."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        settings = await conn.fetchrow("""
            SELECT * FROM partner_notification_settings
            WHERE partner_id = $1
        """, partner['id'])

    if not settings:
        raise HTTPException(400, "No notification settings configured")

    test_payload = {
        "title": "[TEST] OsirisCare Notification Test",
        "summary": "This is a test notification to verify your channel configuration.",
        "severity": "low",
        "site_name": "Test Site",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Import escalation engine for sending
    try:
        from .escalation_engine import (
            send_slack_notification,
            send_pagerduty_notification,
            send_email_notification,
            send_teams_notification,
            send_webhook_notification
        )

        if channel == "slack" and settings['slack_enabled']:
            result = await send_slack_notification(dict(settings), test_payload)
        elif channel == "pagerduty" and settings['pagerduty_enabled']:
            result = await send_pagerduty_notification(dict(settings), test_payload, is_test=True)
        elif channel == "email" and settings['email_enabled']:
            result = await send_email_notification(dict(settings), test_payload)
        elif channel == "teams" and settings['teams_enabled']:
            result = await send_teams_notification(dict(settings), test_payload)
        elif channel == "webhook" and settings['webhook_enabled']:
            result = await send_webhook_notification(dict(settings), test_payload)
        else:
            raise HTTPException(400, f"Channel '{channel}' not enabled or not recognized")

        return {"status": "sent", "result": result}
    except ImportError:
        # Escalation engine not available, just return mock success
        return {"status": "sent", "result": {"note": "Escalation engine not configured"}}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# =============================================================================
# SITE-LEVEL OVERRIDES
# =============================================================================

@router.get("/sites/{site_id}/overrides")
async def get_site_overrides(
    site_id: str,
    partner=Depends(require_partner)
):
    """Get notification overrides for a specific site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify partner owns this site (use site_id column, not id)
        site = await conn.fetchrow("""
            SELECT * FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(404, "Site not found or not owned by partner")

        overrides = await conn.fetchrow("""
            SELECT email_recipients, slack_channel, pagerduty_routing_key,
                   escalation_timeout_minutes, priority_override
            FROM site_notification_overrides
            WHERE site_id = $1
        """, site_id)

        return dict(overrides) if overrides else {}


@router.put("/sites/{site_id}/overrides")
async def update_site_overrides(
    site_id: str,
    overrides: SiteNotificationOverride,
    partner=Depends(require_partner)
):
    """Set notification overrides for a specific site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify partner owns this site (use site_id column, not id)
        site = await conn.fetchrow("""
            SELECT * FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(404, "Site not found or not owned by partner")

        await conn.execute("""
            INSERT INTO site_notification_overrides (
                site_id, partner_id, email_recipients, slack_channel,
                pagerduty_routing_key, escalation_timeout_minutes, priority_override,
                updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (site_id) DO UPDATE SET
                email_recipients = EXCLUDED.email_recipients,
                slack_channel = EXCLUDED.slack_channel,
                pagerduty_routing_key = EXCLUDED.pagerduty_routing_key,
                escalation_timeout_minutes = EXCLUDED.escalation_timeout_minutes,
                priority_override = EXCLUDED.priority_override,
                updated_at = NOW()
        """,
            site_id,
            partner['id'],
            overrides.email_recipients,
            overrides.slack_channel,
            overrides.pagerduty_routing_key,
            overrides.escalation_timeout_minutes,
            overrides.priority_override
        )

    return {"status": "updated"}


# =============================================================================
# ESCALATION TICKETS
# =============================================================================

@router.get("/tickets")
async def list_escalation_tickets(
    status: Optional[str] = Query(None, description="Filter by status: open, acknowledged, resolved, closed"),
    site_id: Optional[str] = Query(None, description="Filter by site"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    partner=Depends(require_partner)
):
    """List escalation tickets for partner."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Build query with filters
        query = """
            SELECT t.*, s.clinic_name as site_name
            FROM escalation_tickets t
            JOIN sites s ON t.site_id = s.id
            WHERE t.partner_id = $1
        """
        params = [partner['id']]
        param_idx = 2

        if status:
            query += f" AND t.status = ${param_idx}"
            params.append(status)
            param_idx += 1

        if site_id:
            query += f" AND t.site_id = ${param_idx}"
            params.append(site_id)
            param_idx += 1

        if priority:
            query += f" AND t.priority = ${param_idx}"
            params.append(priority)
            param_idx += 1

        query += f" ORDER BY t.created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        tickets = await conn.fetch(query, *params)

        # Get counts
        counts = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'open') as open_count,
                COUNT(*) FILTER (WHERE status = 'acknowledged') as acknowledged_count,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved_count,
                COUNT(*) FILTER (WHERE sla_breached = true AND status = 'open') as sla_breached_count
            FROM escalation_tickets
            WHERE partner_id = $1
        """, partner['id'])

        return {
            "tickets": [dict(t) for t in tickets],
            "counts": dict(counts),
            "pagination": {"limit": limit, "offset": offset}
        }


@router.get("/tickets/{ticket_id}")
async def get_escalation_ticket(
    ticket_id: str,
    partner=Depends(require_partner)
):
    """Get details of a specific escalation ticket."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
            SELECT t.*, s.clinic_name as site_name
            FROM escalation_tickets t
            JOIN sites s ON t.site_id = s.id
            WHERE t.id = $1 AND t.partner_id = $2
        """, ticket_id, partner['id'])

        if not ticket:
            raise HTTPException(404, "Ticket not found")

        # Get notification history
        notifications = await conn.fetch("""
            SELECT * FROM notification_deliveries
            WHERE ticket_id = $1
            ORDER BY created_at
        """, ticket_id)

        return {
            "ticket": dict(ticket),
            "notifications": [dict(n) for n in notifications]
        }


@router.post("/tickets/{ticket_id}/acknowledge")
async def acknowledge_ticket(
    ticket_id: str,
    ack: TicketAcknowledge,
    partner=Depends(require_partner)
):
    """Acknowledge an escalation ticket."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
            SELECT * FROM escalation_tickets
            WHERE id = $1 AND partner_id = $2
        """, ticket_id, partner['id'])

        if not ticket:
            raise HTTPException(404, "Ticket not found")

        if ticket['status'] != 'open':
            raise HTTPException(400, f"Ticket already {ticket['status']}")

        await conn.execute("""
            UPDATE escalation_tickets
            SET status = 'acknowledged',
                acknowledged_at = NOW(),
                acknowledged_by = $2,
                updated_at = NOW()
            WHERE id = $1
        """, ticket_id, ack.acknowledged_by)

    logger.info(f"Ticket {ticket_id} acknowledged by {ack.acknowledged_by}")
    return {"status": "acknowledged", "ticket_id": ticket_id}


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket(
    ticket_id: str,
    resolution: TicketResolve,
    partner=Depends(require_partner)
):
    """Resolve an escalation ticket."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
            SELECT * FROM escalation_tickets
            WHERE id = $1 AND partner_id = $2
        """, ticket_id, partner['id'])

        if not ticket:
            raise HTTPException(404, "Ticket not found")

        if ticket['status'] in ('resolved', 'closed'):
            raise HTTPException(400, f"Ticket already {ticket['status']}")

        await conn.execute("""
            UPDATE escalation_tickets
            SET status = 'resolved',
                resolved_at = NOW(),
                resolved_by = $2,
                resolution_notes = $3,
                updated_at = NOW()
            WHERE id = $1
        """, ticket_id, resolution.resolved_by, resolution.resolution_notes)

    logger.info(f"Ticket {ticket_id} resolved by {resolution.resolved_by}")
    return {"status": "resolved", "ticket_id": ticket_id}


# =============================================================================
# SLA METRICS
# =============================================================================

@router.get("/sla/metrics")
async def get_sla_metrics(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    partner=Depends(require_partner)
):
    """Get SLA performance metrics for partner."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        metrics = await conn.fetchrow(f"""
            SELECT
                COUNT(*) as total_tickets,
                COUNT(*) FILTER (WHERE sla_breached = true) as sla_breaches,
                COUNT(*) FILTER (WHERE status = 'resolved') as resolved_tickets,
                AVG(EXTRACT(EPOCH FROM (acknowledged_at - created_at)) / 60)
                    FILTER (WHERE acknowledged_at IS NOT NULL) as avg_response_minutes,
                AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 60)
                    FILTER (WHERE resolved_at IS NOT NULL) as avg_resolution_minutes,
                COUNT(*) FILTER (WHERE priority = 'critical') as critical_count,
                COUNT(*) FILTER (WHERE priority = 'high') as high_count,
                COUNT(*) FILTER (WHERE priority = 'medium') as medium_count,
                COUNT(*) FILTER (WHERE priority = 'low') as low_count
            FROM escalation_tickets
            WHERE partner_id = $1
            AND created_at > NOW() - ($2 * INTERVAL '1 day')
        """, partner['id'], days)

        total = metrics['total_tickets'] or 0
        breaches = metrics['sla_breaches'] or 0

        return {
            "period_days": days,
            "metrics": {
                "total_tickets": total,
                "sla_breaches": breaches,
                "sla_compliance_rate": round(100 - (breaches / max(total, 1) * 100), 1),
                "resolved_tickets": metrics['resolved_tickets'] or 0,
                "avg_response_minutes": round(metrics['avg_response_minutes'] or 0, 1),
                "avg_resolution_minutes": round(metrics['avg_resolution_minutes'] or 0, 1),
                "by_priority": {
                    "critical": metrics['critical_count'] or 0,
                    "high": metrics['high_count'] or 0,
                    "medium": metrics['medium_count'] or 0,
                    "low": metrics['low_count'] or 0
                }
            }
        }


@router.get("/sla/definitions")
async def get_sla_definitions(partner=Depends(require_partner)):
    """Get SLA definitions for partner (or defaults)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Get partner-specific or default SLAs
        slas = await conn.fetch("""
            SELECT priority, response_time_minutes, resolution_time_minutes,
                   escalate_after_minutes, escalate_to
            FROM sla_definitions
            WHERE partner_id = $1 OR partner_id IS NULL
            ORDER BY partner_id NULLS LAST, priority
        """, partner['id'])

        # Deduplicate by priority (partner-specific takes precedence)
        sla_map = {}
        for sla in slas:
            if sla['priority'] not in sla_map:
                sla_map[sla['priority']] = dict(sla)

        return {"sla_definitions": list(sla_map.values())}


# =============================================================================
# AGENT-FACING ESCALATION ENDPOINT
# =============================================================================

# Separate router for agent-originated escalations (not partner-authenticated)
escalations_router = APIRouter(prefix="/api/escalations", tags=["escalations"])


class EscalationRequest(BaseModel):
    """Escalation request from compliance agent."""
    site_id: str
    incident: dict
    attempted_actions: Optional[List[str]] = None
    recommended_action: Optional[str] = None
    priority: Optional[str] = None


@escalations_router.post("")
async def create_escalation(request: EscalationRequest):
    """
    Create an L3 escalation from a compliance agent.

    This endpoint is called by appliances when they need to escalate
    an incident that L1/L2 couldn't resolve. The escalation engine
    will route to the appropriate partner based on site ownership.
    """
    try:
        from .escalation_engine import get_escalation_engine

        engine = await get_escalation_engine()
        result = await engine.create_escalation(
            site_id=request.site_id,
            incident=request.incident,
            attempted_actions=request.attempted_actions,
            recommended_action=request.recommended_action,
            priority=request.priority
        )

        return result

    except ValueError as e:
        logger.warning(f"Escalation validation error: {e}")
        raise HTTPException(status_code=404, detail="Resource not found")
    except Exception as e:
        logger.error(f"Escalation failed: {e}")
        raise HTTPException(status_code=500, detail="Escalation failed. Please try again.")
