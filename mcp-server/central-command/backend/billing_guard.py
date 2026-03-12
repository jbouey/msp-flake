"""Billing Enforcement Guard — check partner billing status before operations.

Targeted checks at specific integration points (NOT global middleware).
Partners with canceled subscriptions get degraded service:
- Checkin returns billing_hold=true (appliance keeps L1, stops L2/evidence)
- Fleet order creation blocked
- Dashboard shows billing status badge

Grace periods:
- 'none' = free tier / not yet billed → allowed
- 'active' / 'trialing' → allowed
- 'past_due' → 7-day grace from period end, then blocked
- 'canceled' / 'canceling' → blocked
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional

logger = logging.getLogger("billing_guard")

# Statuses that allow full service
_ACTIVE_STATUSES = {"active", "trialing", "none"}

# Grace period for past_due before blocking
_PAST_DUE_GRACE_DAYS = 7


async def check_billing_status(
    conn, site_id: str
) -> Tuple[str, bool]:
    """Check billing status for the partner that owns a site.

    Args:
        conn: asyncpg connection (already inside tenant or admin context)
        site_id: The site to check

    Returns:
        (status, is_active) where:
        - status: 'active', 'trialing', 'none', 'past_due', 'canceled', 'canceling', 'unknown'
        - is_active: True if the partner should receive full service
    """
    try:
        row = await conn.fetchrow("""
            SELECT p.subscription_status, p.subscription_current_period_end
            FROM sites s
            JOIN partners p ON p.id = s.partner_id
            WHERE s.site_id = $1
        """, site_id)
    except Exception as e:
        logger.warning(f"Billing check failed for site {site_id}: {e}")
        # Fail open — don't block service on billing query errors
        return ("unknown", True)

    if not row:
        # No partner linked, or partner doesn't exist — treat as free tier
        return ("none", True)

    status = row["subscription_status"] or "none"

    if status in _ACTIVE_STATUSES:
        return (status, True)

    if status == "past_due":
        period_end = row["subscription_current_period_end"]
        if period_end:
            grace_deadline = period_end + timedelta(days=_PAST_DUE_GRACE_DAYS)
            now = datetime.now(timezone.utc)
            if now < grace_deadline:
                logger.debug(f"Site {site_id}: past_due but within grace period (until {grace_deadline})")
                return (status, True)
        # Past grace period
        logger.info(f"Site {site_id}: past_due and past grace period — billing hold")
        return (status, False)

    # canceled, canceling, or anything unexpected
    logger.info(f"Site {site_id}: billing status '{status}' — billing hold")
    return (status, False)


async def check_billing_for_fleet_order(conn, site_id: Optional[str] = None) -> bool:
    """Check if a fleet order should be allowed based on billing.

    For fleet-wide orders (no site_id), always allow.
    For site-specific orders, check the partner billing status.

    Returns True if allowed, False if blocked.
    """
    if not site_id:
        return True  # Fleet-wide orders always allowed

    status, is_active = await check_billing_status(conn, site_id)
    if not is_active:
        logger.warning(f"Fleet order blocked for site {site_id}: billing status '{status}'")
    return is_active
