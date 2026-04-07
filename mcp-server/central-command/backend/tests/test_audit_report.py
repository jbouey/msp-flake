"""Tests for audit_report pure computation functions.

Covers compute_audit_readiness (badge logic, checks, blockers)
and compute_audit_countdown (urgency levels, edge cases).
No HTTP or DB — imports pure functions directly.
"""

import sys
import os
import types
from datetime import date

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies so audit_report.py can be imported without them
# ---------------------------------------------------------------------------

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

# Minimal stubs for fastapi / pydantic / sqlalchemy / asyncpg
for _mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "asyncpg",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

_fastapi = sys.modules["fastapi"]
_fastapi.APIRouter = lambda **kw: type(
    "FakeRouter", (),
    {"get": lambda *a, **k: lambda f: f, "put": lambda *a, **k: lambda f: f},
)()
_fastapi.HTTPException = Exception
_fastapi.Depends = lambda x: x

_pydantic = sys.modules["pydantic"]
_pydantic.BaseModel = type("BaseModel", (), {})
_pydantic.Field = lambda *a, **kw: None

_sa = sys.modules["sqlalchemy"]
_sa.text = lambda x: x
_sa_async = sys.modules["sqlalchemy.ext.asyncio"]
_sa_async.create_async_engine = lambda *a, **kw: None
        _sa_async.AsyncSession = type("AsyncSession", (), {})
        _sa_async.async_sessionmaker = lambda *a, **kw: None

# Stub the relative imports that audit_report.py uses
_dashboard_api = types.ModuleType("dashboard_api")
sys.modules["dashboard_api"] = _dashboard_api

_auth_mod = types.ModuleType("dashboard_api.auth")
_auth_mod.require_auth = lambda: None
_auth_mod.require_partner_role = lambda *a: None
sys.modules["dashboard_api.auth"] = _auth_mod

_fleet_mod = types.ModuleType("dashboard_api.fleet")
_fleet_mod.get_pool = None
sys.modules["dashboard_api.fleet"] = _fleet_mod

_tenant_mod = types.ModuleType("dashboard_api.tenant_middleware")
_tenant_mod.admin_connection = None
sys.modules["dashboard_api.tenant_middleware"] = _tenant_mod

# Register backend dir as dashboard_api package so relative imports work
_pkg = sys.modules["dashboard_api"]
_pkg.__path__ = [backend_dir]
_pkg.__package__ = "dashboard_api"

import importlib
_mod = importlib.import_module("dashboard_api.audit_report")
compute_audit_readiness = _mod.compute_audit_readiness
compute_audit_countdown = _mod.compute_audit_countdown


# =============================================================================
# compute_audit_readiness
# =============================================================================

class TestComputeAuditReadiness:
    """Test badge, checks, and blockers for various audit states."""

    def test_green_all_pass(self):
        """All checks pass => green badge, ready=True, no blockers."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=95.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "green"
        assert result["ready"] is True
        assert result["blockers"] == []
        assert result["passed_count"] == 6
        assert result["total_checks"] == 6

    def test_yellow_baa_missing(self):
        """BAA missing but no red conditions => yellow badge."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=95.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=False,
            packet_downloadable=True,
        )
        assert result["badge"] == "yellow"
        assert result["ready"] is False
        assert result["passed_count"] == 5
        assert any("Business Associate Agreement" in b for b in result["blockers"])

    def test_yellow_packet_not_downloadable(self):
        """Packet not downloadable, no red conditions => yellow."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=95.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=False,
        )
        assert result["badge"] == "yellow"
        assert result["ready"] is False
        assert any("packet" in b.lower() for b in result["blockers"])

    def test_yellow_low_signing_rate(self):
        """Signing rate below 90% but no red conditions => yellow."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=85.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "yellow"
        assert result["ready"] is False
        assert any("90%" in b for b in result["blockers"])

    def test_red_chain_broken(self):
        """Broken chain => red badge regardless of other checks."""
        result = compute_audit_readiness(
            chain_unbroken=False,
            signing_rate=99.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "red"
        assert result["ready"] is False
        assert any("chain" in b.lower() for b in result["blockers"])

    def test_red_critical_incidents(self):
        """Open critical incidents => red badge."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=95.0,
            ots_current=True,
            critical_unresolved=3,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "red"
        assert result["ready"] is False
        assert any("3" in b and "critical" in b for b in result["blockers"])

    def test_red_ots_stalled(self):
        """OTS stalled => red badge."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=95.0,
            ots_current=False,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "red"
        assert result["ready"] is False
        assert any("timestamp" in b.lower() or "ots" in b.lower() for b in result["blockers"])

    def test_red_multiple_failures(self):
        """Multiple red conditions stack blockers but still one red badge."""
        result = compute_audit_readiness(
            chain_unbroken=False,
            signing_rate=50.0,
            ots_current=False,
            critical_unresolved=2,
            baa_on_file=False,
            packet_downloadable=False,
        )
        assert result["badge"] == "red"
        assert result["ready"] is False
        assert result["passed_count"] == 0
        assert len(result["blockers"]) == 6

    def test_checks_list_always_has_six_entries(self):
        """Checks list has exactly 6 entries regardless of state."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=100.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert len(result["checks"]) == 6
        for check in result["checks"]:
            assert "name" in check
            assert "passed" in check
            assert "detail" in check

    def test_signing_rate_boundary_exactly_90(self):
        """Signing rate of exactly 90.0 does NOT pass (need >90)."""
        result = compute_audit_readiness(
            chain_unbroken=True,
            signing_rate=90.0,
            ots_current=True,
            critical_unresolved=0,
            baa_on_file=True,
            packet_downloadable=True,
        )
        assert result["badge"] == "yellow"
        assert result["passed_count"] == 5


# =============================================================================
# compute_audit_countdown
# =============================================================================

class TestComputeAuditCountdown:
    """Test countdown urgency and edge cases."""

    def test_normal_more_than_30_days(self):
        """More than 30 days out => normal urgency."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 6, 1),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 61
        assert result["urgency"] == "normal"
        assert result["next_audit_date"] == "2026-06-01"

    def test_urgent_within_30_days(self):
        """15-30 days out => urgent."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 4, 25),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 24
        assert result["urgency"] == "urgent"

    def test_critical_within_14_days(self):
        """1-14 days out => critical."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 4, 10),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 9
        assert result["urgency"] == "critical"

    def test_critical_exactly_14_days(self):
        """Exactly 14 days => critical (boundary)."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 4, 15),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 14
        assert result["urgency"] == "critical"

    def test_urgent_exactly_30_days(self):
        """Exactly 30 days => urgent (boundary)."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 5, 1),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 30
        assert result["urgency"] == "urgent"

    def test_overdue_negative_days(self):
        """Past date => overdue."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 3, 15),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == -17
        assert result["urgency"] == "overdue"

    def test_overdue_yesterday(self):
        """One day past => overdue with -1."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 3, 31),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == -1
        assert result["urgency"] == "overdue"

    def test_today_is_audit_day(self):
        """Audit day is today => 0 days remaining, critical."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 4, 1),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 0
        assert result["urgency"] == "critical"

    def test_none_when_no_date(self):
        """No next_audit_date => returns None."""
        result = compute_audit_countdown(next_audit_date=None, today=date(2026, 4, 1))
        assert result is None

    def test_normal_exactly_31_days(self):
        """31 days out => normal (just past the urgent boundary)."""
        result = compute_audit_countdown(
            next_audit_date=date(2026, 5, 2),
            today=date(2026, 4, 1),
        )
        assert result is not None
        assert result["days_remaining"] == 31
        assert result["urgency"] == "normal"
