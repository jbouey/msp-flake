"""Tests for the billing_guard module."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
import pytest

sys.path.insert(0, "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend")


class FakeConn:
    """Fake asyncpg connection for testing billing guard."""

    def __init__(self, fetchrow_result=None, fetchrow_error=None):
        self._fetchrow_result = fetchrow_result
        self._fetchrow_error = fetchrow_error

    async def fetchrow(self, query, *args):
        if self._fetchrow_error:
            raise self._fetchrow_error
        return self._fetchrow_result


@pytest.mark.asyncio
async def test_active_subscription():
    from billing_guard import check_billing_status
    conn = FakeConn({"subscription_status": "active", "subscription_current_period_end": None})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "active"
    assert is_active is True


@pytest.mark.asyncio
async def test_trialing_subscription():
    from billing_guard import check_billing_status
    conn = FakeConn({"subscription_status": "trialing", "subscription_current_period_end": None})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "trialing"
    assert is_active is True


@pytest.mark.asyncio
async def test_none_subscription_is_free_tier():
    from billing_guard import check_billing_status
    conn = FakeConn({"subscription_status": "none", "subscription_current_period_end": None})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "none"
    assert is_active is True


@pytest.mark.asyncio
async def test_canceled_subscription_blocked():
    from billing_guard import check_billing_status
    conn = FakeConn({"subscription_status": "canceled", "subscription_current_period_end": None})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "canceled"
    assert is_active is False


@pytest.mark.asyncio
async def test_past_due_within_grace_period():
    from billing_guard import check_billing_status
    # Period ended 3 days ago — within 7-day grace
    period_end = datetime.now(timezone.utc) - timedelta(days=3)
    conn = FakeConn({"subscription_status": "past_due", "subscription_current_period_end": period_end})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "past_due"
    assert is_active is True


@pytest.mark.asyncio
async def test_past_due_past_grace_period():
    from billing_guard import check_billing_status
    # Period ended 10 days ago — past 7-day grace
    period_end = datetime.now(timezone.utc) - timedelta(days=10)
    conn = FakeConn({"subscription_status": "past_due", "subscription_current_period_end": period_end})
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "past_due"
    assert is_active is False


@pytest.mark.asyncio
async def test_no_partner_linked():
    """Site with no partner should be treated as free tier."""
    from billing_guard import check_billing_status
    conn = FakeConn(fetchrow_result=None)  # No partner row
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "none"
    assert is_active is True


@pytest.mark.asyncio
async def test_db_error_fails_open():
    """Database errors should fail open (don't block service)."""
    from billing_guard import check_billing_status
    conn = FakeConn(fetchrow_error=Exception("connection lost"))
    status, is_active = await check_billing_status(conn, "site-1")
    assert status == "unknown"
    assert is_active is True


@pytest.mark.asyncio
async def test_fleet_order_check_no_site():
    """Fleet-wide orders (no site_id) should always be allowed."""
    from billing_guard import check_billing_for_fleet_order
    conn = FakeConn()  # Won't be called
    result = await check_billing_for_fleet_order(conn, site_id=None)
    assert result is True
