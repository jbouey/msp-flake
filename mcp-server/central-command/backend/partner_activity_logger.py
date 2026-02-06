"""
Partner activity audit logging.

HIPAA 164.312(b) requires audit controls that record and examine
activity in systems that contain or use PHI.

This module logs all partner actions to the append-only
partner_activity_log table. All logging is fire-and-forget -
a failure to log never blocks the partner operation.

Usage:
    from .partner_activity_logger import log_partner_login, PartnerEventType

    await log_partner_login(
        partner_id=str(partner['id']),
        provider="microsoft",
        ip_address=request.client.host,
    )
"""

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .fleet import get_pool

logger = logging.getLogger(__name__)


# =============================================================================
# EVENT TYPES & CATEGORIES
# =============================================================================


class PartnerEventType(str, Enum):
    # Auth events
    OAUTH_LOGIN_STARTED = "oauth_login_started"
    OAUTH_LOGIN_SUCCESS = "oauth_login_success"
    OAUTH_LOGIN_FAILED = "oauth_login_failed"
    SESSION_CREATED = "session_created"
    LOGOUT = "logout"

    # Admin partner management
    PARTNER_CREATED = "partner_created"
    PARTNER_UPDATED = "partner_updated"
    PARTNER_APPROVED = "partner_approved"
    PARTNER_REJECTED = "partner_rejected"
    API_KEY_REGENERATED = "api_key_regenerated"

    # Self-service
    PROFILE_VIEWED = "profile_viewed"
    SITES_LISTED = "sites_listed"
    SITE_VIEWED = "site_viewed"

    # Provisions
    PROVISION_CREATED = "provision_created"
    PROVISION_REVOKED = "provision_revoked"
    PROVISION_CLAIMED = "provision_claimed"

    # Credentials
    CREDENTIAL_ADDED = "credential_added"
    CREDENTIAL_VALIDATED = "credential_validated"
    CREDENTIAL_DELETED = "credential_deleted"

    # Assets
    ASSET_UPDATED = "asset_updated"

    # Discovery
    DISCOVERY_TRIGGERED = "discovery_triggered"

    # Learning
    PATTERN_APPROVED = "pattern_approved"
    PATTERN_REJECTED = "pattern_rejected"
    RULE_STATUS_CHANGED = "rule_status_changed"


class PartnerEventCategory(str, Enum):
    AUTH = "auth"
    ADMIN = "admin"
    SITE = "site"
    PROVISION = "provision"
    CREDENTIAL = "credential"
    ASSET = "asset"
    DISCOVERY = "discovery"
    LEARNING = "learning"


EVENT_CATEGORIES: Dict[PartnerEventType, PartnerEventCategory] = {
    # Auth
    PartnerEventType.OAUTH_LOGIN_STARTED: PartnerEventCategory.AUTH,
    PartnerEventType.OAUTH_LOGIN_SUCCESS: PartnerEventCategory.AUTH,
    PartnerEventType.OAUTH_LOGIN_FAILED: PartnerEventCategory.AUTH,
    PartnerEventType.SESSION_CREATED: PartnerEventCategory.AUTH,
    PartnerEventType.LOGOUT: PartnerEventCategory.AUTH,
    # Admin
    PartnerEventType.PARTNER_CREATED: PartnerEventCategory.ADMIN,
    PartnerEventType.PARTNER_UPDATED: PartnerEventCategory.ADMIN,
    PartnerEventType.PARTNER_APPROVED: PartnerEventCategory.ADMIN,
    PartnerEventType.PARTNER_REJECTED: PartnerEventCategory.ADMIN,
    PartnerEventType.API_KEY_REGENERATED: PartnerEventCategory.ADMIN,
    # Site
    PartnerEventType.PROFILE_VIEWED: PartnerEventCategory.SITE,
    PartnerEventType.SITES_LISTED: PartnerEventCategory.SITE,
    PartnerEventType.SITE_VIEWED: PartnerEventCategory.SITE,
    # Provision
    PartnerEventType.PROVISION_CREATED: PartnerEventCategory.PROVISION,
    PartnerEventType.PROVISION_REVOKED: PartnerEventCategory.PROVISION,
    PartnerEventType.PROVISION_CLAIMED: PartnerEventCategory.PROVISION,
    # Credential
    PartnerEventType.CREDENTIAL_ADDED: PartnerEventCategory.CREDENTIAL,
    PartnerEventType.CREDENTIAL_VALIDATED: PartnerEventCategory.CREDENTIAL,
    PartnerEventType.CREDENTIAL_DELETED: PartnerEventCategory.CREDENTIAL,
    # Asset
    PartnerEventType.ASSET_UPDATED: PartnerEventCategory.ASSET,
    # Discovery
    PartnerEventType.DISCOVERY_TRIGGERED: PartnerEventCategory.DISCOVERY,
    # Learning
    PartnerEventType.PATTERN_APPROVED: PartnerEventCategory.LEARNING,
    PartnerEventType.PATTERN_REJECTED: PartnerEventCategory.LEARNING,
    PartnerEventType.RULE_STATUS_CHANGED: PartnerEventCategory.LEARNING,
}


# =============================================================================
# CORE LOGGING
# =============================================================================


async def log_partner_activity(
    partner_id: str,
    event_type: PartnerEventType,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_path: Optional[str] = None,
    request_method: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
) -> Optional[int]:
    """Log a partner activity event. Returns the log entry ID."""
    try:
        pool = await get_pool()
        category = EVENT_CATEGORIES.get(event_type, PartnerEventCategory.SITE)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO partner_activity_log (
                    partner_id, event_type, event_category,
                    event_data, target_type, target_id,
                    actor_ip, actor_user_agent,
                    request_path, request_method,
                    success, error_message
                ) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
                """,
                partner_id,
                event_type.value,
                category.value,
                json.dumps(event_data) if event_data else "{}",
                target_type,
                target_id,
                ip_address,
                (user_agent[:500] if user_agent else None),
                request_path,
                request_method,
                success,
                error_message,
            )
            return row["id"] if row else None
    except Exception as e:
        logger.warning(f"Failed to log partner activity: {e}")
        return None


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


async def log_partner_login(
    partner_id: str,
    provider: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error: Optional[str] = None,
):
    event_type = (
        PartnerEventType.OAUTH_LOGIN_SUCCESS
        if success
        else PartnerEventType.OAUTH_LOGIN_FAILED
    )
    await log_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        target_type="partner",
        target_id=partner_id,
        event_data={"provider": provider},
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        error_message=error,
    )


async def log_partner_site_action(
    partner_id: str,
    event_type: PartnerEventType,
    site_id: str,
    event_data: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_path: Optional[str] = None,
    request_method: Optional[str] = None,
):
    await log_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        target_type="site",
        target_id=site_id,
        event_data=event_data,
        ip_address=ip_address,
        user_agent=user_agent,
        request_path=request_path,
        request_method=request_method,
    )


async def log_partner_credential_action(
    partner_id: str,
    event_type: PartnerEventType,
    site_id: str,
    credential_id: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    await log_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        target_type="credential",
        target_id=credential_id,
        event_data={"site_id": site_id, **(event_data or {})},
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def log_partner_provision_action(
    partner_id: str,
    event_type: PartnerEventType,
    provision_id: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    await log_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        target_type="provision",
        target_id=provision_id,
        event_data=event_data,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def log_partner_learning_action(
    partner_id: str,
    event_type: PartnerEventType,
    pattern_id: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    await log_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        target_type="rule",
        target_id=pattern_id,
        event_data=event_data,
        ip_address=ip_address,
        user_agent=user_agent,
    )


# =============================================================================
# QUERY FUNCTIONS (for admin API)
# =============================================================================


async def get_partner_activity(
    partner_id: Optional[str] = None,
    event_type: Optional[str] = None,
    event_category: Optional[str] = None,
    target_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Query partner activity logs with optional filters."""
    pool = await get_pool()

    query = """
        SELECT
            pal.id, pal.partner_id, pal.event_type, pal.event_category,
            pal.event_data, pal.target_type, pal.target_id,
            pal.actor_ip, pal.success, pal.error_message, pal.created_at,
            p.name as partner_name, p.slug as partner_slug
        FROM partner_activity_log pal
        LEFT JOIN partners p ON p.id = pal.partner_id
        WHERE 1=1
    """
    params: list = []
    idx = 1

    if partner_id:
        query += f" AND pal.partner_id = ${idx}::uuid"
        params.append(partner_id)
        idx += 1

    if event_type:
        query += f" AND pal.event_type = ${idx}"
        params.append(event_type)
        idx += 1

    if event_category:
        query += f" AND pal.event_category = ${idx}"
        params.append(event_category)
        idx += 1

    if target_type:
        query += f" AND pal.target_type = ${idx}"
        params.append(target_type)
        idx += 1

    query += f" ORDER BY pal.created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        results = []
        for row in rows:
            r = dict(row)
            # Serialize datetime and special types
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("actor_ip"):
                r["actor_ip"] = str(r["actor_ip"])
            if r.get("partner_id"):
                r["partner_id"] = str(r["partner_id"])
            if r.get("event_data") and isinstance(r["event_data"], str):
                try:
                    r["event_data"] = json.loads(r["event_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(r)
        return results


async def get_partner_activity_stats(
    partner_id: Optional[str] = None,
    since_hours: int = 24,
) -> Dict[str, Any]:
    """Get activity statistics for dashboard cards."""
    pool = await get_pool()

    where = "WHERE 1=1"
    params: list = []
    idx = 1

    if partner_id:
        where += f" AND partner_id = ${idx}::uuid"
        params.append(partner_id)
        idx += 1

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '{since_hours} hours') as recent,
                COUNT(*) FILTER (WHERE event_category = 'auth') as auth_events,
                COUNT(DISTINCT partner_id) as unique_partners
            FROM partner_activity_log
            {where}
            """,
            *params,
        )
        return dict(row) if row else {
            "total": 0, "recent": 0, "auth_events": 0, "unique_partners": 0
        }
