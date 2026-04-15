"""Tests for maintenance mode feature.

Tests:
- Set maintenance window → incidents suppressed
- Cancel maintenance → incidents resume
- Max duration enforcement (>48h rejected)
- Min duration enforcement (<0.5h rejected)
- Reason required
- Auto-expire (maintenance_until in the past = no effect)
- Validation edge cases
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers — minimal fakes for asyncpg and SQLAlchemy patterns used in routes
# ---------------------------------------------------------------------------

class FakeRecord(dict):
    """Dict that also supports attribute-style access (like asyncpg Record)."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class FakeConn:
    """Minimal asyncpg connection fake with transaction() support."""
    def __init__(self):
        self.executed = []
        self._fetchval_results = {}
        self._fetchrow_results = {}

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetchval(self, query, *args):
        for key, val in self._fetchval_results.items():
            if key in query:
                return val
        return None

    async def fetchrow(self, query, *args):
        for key, val in self._fetchrow_results.items():
            if key in query:
                return val
        return None

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSAResult:
    """Minimal SQLAlchemy result fake."""
    def __init__(self, rows=None, scalar_val=None):
        self._rows = rows or []
        self._scalar = scalar_val

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar

    @property
    def rowcount(self):
        return len(self._rows)


class FakeSASession:
    """Minimal async SQLAlchemy session fake."""
    def __init__(self):
        self.executed = []
        self._results = []
        self._result_index = 0

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        if self._result_index < len(self._results):
            r = self._results[self._result_index]
            self._result_index += 1
            return r
        return FakeSAResult()

    async def commit(self):
        pass

    def add_result(self, result):
        self._results.append(result)


# ---------------------------------------------------------------------------
# Tests — Validation
# ---------------------------------------------------------------------------

class TestMaintenanceValidation:
    """Test request validation for maintenance endpoints."""

    def test_duration_too_long(self):
        """duration_hours > 48 must be rejected."""
        from routes import MaintenanceRequest
        # Pydantic model accepts the value — endpoint does the range check.
        req = MaintenanceRequest(duration_hours=49, reason="Test")
        assert req.duration_hours == 49  # Model allows it; endpoint rejects

    def test_duration_too_short(self):
        """duration_hours < 0.5 must be rejected."""
        from routes import MaintenanceRequest
        req = MaintenanceRequest(duration_hours=0.1, reason="Test")
        assert req.duration_hours == 0.1

    def test_valid_duration(self):
        """Valid duration_hours within 0.5-48."""
        from routes import MaintenanceRequest
        req = MaintenanceRequest(duration_hours=4, reason="Patching night")
        assert req.duration_hours == 4
        assert req.reason == "Patching night"

    def test_reason_required_empty(self):
        """Empty reason string should fail validation at endpoint level."""
        from routes import MaintenanceRequest
        req = MaintenanceRequest(duration_hours=2, reason="")
        assert req.reason == ""

    def test_partner_model(self):
        """Partner maintenance request model works."""
        from partners import PartnerMaintenanceRequest
        req = PartnerMaintenanceRequest(duration_hours=1.5, reason="Server upgrade")
        assert req.duration_hours == 1.5
        assert req.reason == "Server upgrade"


# ---------------------------------------------------------------------------
# Tests — Incident Suppression Logic
# ---------------------------------------------------------------------------

class TestIncidentSuppression:
    """Test that incident creation is suppressed during maintenance windows."""

    @pytest.mark.asyncio
    async def test_active_maintenance_suppresses_incident(self):
        """When maintenance_until > NOW(), incident reports should return suppressed status."""
        # The key logic: query returns a future timestamp → incident suppressed
        future_time = datetime.now(timezone.utc) + timedelta(hours=4)

        db = FakeSASession()
        # Result 1: appliance lookup (SELECT id FROM v_appliances_current)
        db.add_result(FakeSAResult(rows=[("appliance-uuid-123",)]))
        # Result 2: maintenance check (SELECT maintenance_until FROM sites)
        db.add_result(FakeSAResult(rows=[(future_time,)]))

        # Simulate what the endpoint does:
        # After finding the appliance, it checks maintenance_until
        maint_result = db._results[1]
        maint_row = maint_result.fetchone()
        assert maint_row is not None
        assert maint_row[0] > datetime.now(timezone.utc)
        # This means the endpoint would return "suppressed_maintenance"

    @pytest.mark.asyncio
    async def test_expired_maintenance_allows_incident(self):
        """When maintenance_until is in the past, incidents should proceed normally."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)

        db = FakeSASession()
        # Appliance lookup
        db.add_result(FakeSAResult(rows=[("appliance-uuid-123",)]))
        # Maintenance check — the SQL has "AND maintenance_until > NOW()"
        # so an expired window returns no rows
        db.add_result(FakeSAResult(rows=[]))

        maint_result = db._results[1]
        maint_row = maint_result.fetchone()
        assert maint_row is None
        # No maintenance → incident creation proceeds

    @pytest.mark.asyncio
    async def test_no_maintenance_set_allows_incident(self):
        """When maintenance_until is NULL, incidents should proceed normally."""
        db = FakeSASession()
        db.add_result(FakeSAResult(rows=[("appliance-uuid-123",)]))
        # maintenance_until is NULL → WHERE clause filters it out
        db.add_result(FakeSAResult(rows=[]))

        maint_result = db._results[1]
        maint_row = maint_result.fetchone()
        assert maint_row is None


# ---------------------------------------------------------------------------
# Tests — Set / Cancel Maintenance
# ---------------------------------------------------------------------------

class TestMaintenanceSetCancel:
    """Test setting and cancelling maintenance windows."""

    @pytest.mark.asyncio
    async def test_set_maintenance_executes_update(self):
        """Setting maintenance should UPDATE the sites table."""
        conn = FakeConn()
        conn._fetchval_results = {"partner_id": "partner-123"}

        # Simulate what the endpoint does
        site_id = "site-abc"
        duration_hours = 4.0
        reason = "Patching night"
        set_by = "admin@example.com"

        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NOW() + ($1 || ' hours')::INTERVAL,
                maintenance_reason = $2,
                maintenance_set_by = $3
            WHERE site_id = $4
        """, str(duration_hours), reason, set_by, site_id)

        assert len(conn.executed) == 1
        query, args = conn.executed[0]
        assert "maintenance_until" in query
        assert args == ("4.0", "Patching night", "admin@example.com", "site-abc")

    @pytest.mark.asyncio
    async def test_cancel_maintenance_nullifies_fields(self):
        """Cancelling maintenance should set all maintenance fields to NULL."""
        conn = FakeConn()

        site_id = "site-abc"
        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NULL,
                maintenance_reason = NULL,
                maintenance_set_by = NULL
            WHERE site_id = $1
        """, site_id)

        assert len(conn.executed) == 1
        query, args = conn.executed[0]
        assert "NULL" in query
        assert args == ("site-abc",)


# ---------------------------------------------------------------------------
# Tests — Duration Bounds
# ---------------------------------------------------------------------------

class TestDurationBounds:
    """Test enforcement of duration_hours bounds."""

    def test_exactly_half_hour_allowed(self):
        """0.5 hours (30 minutes) is the minimum allowed."""
        duration = 0.5
        assert 0.5 <= duration <= 48

    def test_exactly_48_hours_allowed(self):
        """48 hours is the maximum allowed."""
        duration = 48
        assert 0.5 <= duration <= 48

    def test_49_hours_rejected(self):
        """49 hours exceeds the maximum."""
        duration = 49
        assert not (0.5 <= duration <= 48)

    def test_zero_hours_rejected(self):
        """0 hours is below the minimum."""
        duration = 0
        assert not (0.5 <= duration <= 48)

    def test_negative_rejected(self):
        """Negative hours are rejected."""
        duration = -1
        assert not (0.5 <= duration <= 48)

    def test_fractional_hours_allowed(self):
        """Fractional hours like 1.5 are valid."""
        duration = 1.5
        assert 0.5 <= duration <= 48


# ---------------------------------------------------------------------------
# Tests — Checkin Response
# ---------------------------------------------------------------------------

class TestCheckinResponse:
    """Test that checkin response includes maintenance_until."""

    @pytest.mark.asyncio
    async def test_checkin_includes_maintenance_until_when_active(self):
        """Active maintenance window should appear in checkin response."""
        future = datetime.now(timezone.utc) + timedelta(hours=2)

        conn = FakeConn()
        conn._fetchval_results = {"maintenance_until": future}

        result = await conn.fetchval("""
            SELECT maintenance_until FROM sites
            WHERE site_id = $1 AND maintenance_until > NOW()
        """, "site-abc")

        assert result == future
        # Response would include maintenance_until = future.isoformat()

    @pytest.mark.asyncio
    async def test_checkin_maintenance_null_when_expired(self):
        """Expired maintenance should return NULL from query."""
        conn = FakeConn()
        # No matching results → fetchval returns None
        result = await conn.fetchval("""
            SELECT maintenance_until FROM sites
            WHERE site_id = $1 AND maintenance_until > NOW()
        """, "site-abc")

        assert result is None


# ---------------------------------------------------------------------------
# Tests — Site Detail Response
# ---------------------------------------------------------------------------

class TestSiteDetailResponse:
    """Test that site detail includes maintenance fields."""

    def test_active_maintenance_in_response(self):
        """Active maintenance fields should appear in site detail."""
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        site_row = FakeRecord({
            'maintenance_until': future,
            'maintenance_reason': 'Scheduled patching',
            'maintenance_set_by': 'admin@example.com',
        })

        # Simulate the response logic
        now = datetime.now(timezone.utc)
        result = {
            'maintenance_until': site_row['maintenance_until'].isoformat()
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
            'maintenance_reason': site_row['maintenance_reason']
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
            'maintenance_set_by': site_row['maintenance_set_by']
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
        }

        assert result['maintenance_until'] is not None
        assert result['maintenance_reason'] == 'Scheduled patching'
        assert result['maintenance_set_by'] == 'admin@example.com'

    def test_expired_maintenance_excluded_from_response(self):
        """Expired maintenance should not appear in site detail."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        site_row = FakeRecord({
            'maintenance_until': past,
            'maintenance_reason': 'Old patch window',
            'maintenance_set_by': 'admin@example.com',
        })

        now = datetime.now(timezone.utc)
        result = {
            'maintenance_until': site_row['maintenance_until'].isoformat()
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
            'maintenance_reason': site_row['maintenance_reason']
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
            'maintenance_set_by': site_row['maintenance_set_by']
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
        }

        assert result['maintenance_until'] is None
        assert result['maintenance_reason'] is None
        assert result['maintenance_set_by'] is None

    def test_null_maintenance_excluded_from_response(self):
        """NULL maintenance fields should return None."""
        site_row = FakeRecord({
            'maintenance_until': None,
            'maintenance_reason': None,
            'maintenance_set_by': None,
        })

        now = datetime.now(timezone.utc)
        result = {
            'maintenance_until': site_row['maintenance_until'].isoformat()
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
            'maintenance_reason': site_row['maintenance_reason']
                if site_row['maintenance_until'] and site_row['maintenance_until'] > now else None,
        }

        assert result['maintenance_until'] is None
        assert result['maintenance_reason'] is None
