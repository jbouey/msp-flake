"""Tests for the health_monitor background loop logic."""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# We test the parsing/notification logic without real DB


class FakeConn:
    """Fake asyncpg connection for testing health monitor queries."""

    def __init__(self, fetch_results=None, fetchval_results=None):
        self._fetch_results = fetch_results or {}
        self._fetchval_results = fetchval_results or {}
        self.executed = []

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        # Return based on query substring
        for key, val in self._fetch_results.items():
            if key in query:
                return val
        return []

    async def fetchval(self, query, *args):
        self.executed.append(("fetchval", query, args))
        return None

    async def execute(self, query, *args):
        self.executed.append(("execute", query, args))

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_offline_notification_message_format():
    """Test that offline notification messages are well-formed."""
    import sys
    sys.path.insert(0, "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend")

    from health_monitor import _send_offline_notification

    conn = FakeConn()
    last_checkin = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)

    await _send_offline_notification(
        conn=conn,
        severity="warning",
        site_id="site-123",
        appliance_id="site-123-AA:BB:CC:DD:EE:FF",
        hostname="appliance-1",
        site_name="Test Clinic",
        last_checkin=last_checkin,
        minutes_offline=45,
        agent_version="0.3.20",
    )

    assert len(conn.executed) == 1
    op, query, args = conn.executed[0]
    assert op == "execute"
    assert "INSERT INTO notifications" in query
    assert args[0] == "site-123"  # site_id
    assert args[2] == "warning"  # severity
    assert "45m" in args[3]  # title contains duration
    assert "Test Clinic" in args[4]  # message contains site name
    assert "0.3.20" in args[4]  # message contains agent version


@pytest.mark.asyncio
async def test_critical_notification_format():
    """Test critical notification includes escalation language."""
    import sys
    sys.path.insert(0, "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend")

    from health_monitor import _send_offline_notification

    conn = FakeConn()
    last_checkin = datetime(2026, 3, 12, 8, 0, 0, tzinfo=timezone.utc)

    await _send_offline_notification(
        conn=conn,
        severity="critical",
        site_id="site-456",
        appliance_id="site-456-11:22:33:44:55:66",
        hostname="appliance-2",
        site_name="North Valley Medical",
        last_checkin=last_checkin,
        minutes_offline=150,
        agent_version="0.3.20",
    )

    op, query, args = conn.executed[0]
    assert "CRITICAL" in args[3]  # title
    assert "2h 30m" in args[3]  # duration in hours+minutes
    assert "Investigate immediately" in args[4]  # message urgency


@pytest.mark.asyncio
async def test_recovery_notification():
    """Test recovery notification is sent when appliance comes back."""
    import sys
    sys.path.insert(0, "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend")

    from health_monitor import _send_recovery_notification

    conn = FakeConn()

    await _send_recovery_notification(
        conn=conn,
        site_id="site-789",
        appliance_id="site-789-AA:BB:CC:DD:EE:FF",
        hostname="appliance-3",
    )

    assert len(conn.executed) == 1
    op, query, args = conn.executed[0]
    assert "INSERT INTO notifications" in query
    assert "'info'" in query  # severity is literal in query
    assert "appliance_recovery" in query  # category is literal in query
    assert "back online" in args[2]  # title ($3)
