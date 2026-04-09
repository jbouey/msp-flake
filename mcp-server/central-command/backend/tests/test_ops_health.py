"""Tests for ops_health pure computation functions.

Validates green/yellow/red status for all 5 subsystems:
  - Evidence pipeline
  - Signing coverage
  - OTS anchoring
  - Healing pipeline
  - Fleet connectivity

No HTTP or DB required — tests import compute functions directly.
"""

import os
import sys
import types

# ---------------------------------------------------------------------------
# Stub heavy dependencies before importing ops_health (follows test_evidence_chain pattern)
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")

for mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "aiohttp",
    "dashboard_api", "dashboard_api.auth", "dashboard_api.fleet",
    "dashboard_api.partners", "dashboard_api.tenant_middleware",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# SQLAlchemy async stubs required by shared.py imports
sys.modules["sqlalchemy.ext.asyncio"].create_async_engine = lambda *a, **kw: None
sys.modules["sqlalchemy.ext.asyncio"].AsyncSession = object
sys.modules["sqlalchemy.ext.asyncio"].async_sessionmaker = lambda *a, **kw: None

_fastapi = sys.modules["fastapi"]
_fastapi.APIRouter = lambda **kw: type("FakeRouter", (), {
    "post": lambda *a, **k: lambda f: f,
    "get": lambda *a, **k: lambda f: f,
    "put": lambda *a, **k: lambda f: f,
})()
_fastapi.HTTPException = Exception
_fastapi.Depends = lambda x: x
_fastapi.Request = object
_fastapi.Cookie = lambda default=None, **kw: default
_fastapi.Query = lambda default=None, **kw: default
_fastapi.BackgroundTasks = object

# Stub auth functions
_auth = sys.modules["dashboard_api.auth"]
_auth.require_auth = lambda: None
_auth.require_partner_role = lambda *a: lambda: None

_partners = sys.modules["dashboard_api.partners"]
_partners.require_partner_role = lambda *a: lambda: None

# Stub fleet/tenant
_fleet = sys.modules["dashboard_api.fleet"]
_fleet.get_pool = lambda: None
_tenant = sys.modules["dashboard_api.tenant_middleware"]
_tenant.admin_connection = lambda p: None

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Register the backend dir as dashboard_api package so relative imports work
import importlib
_pkg = types.ModuleType("dashboard_api")
_pkg.__path__ = [backend_dir]
_pkg.__package__ = "dashboard_api"
sys.modules["dashboard_api"] = _pkg

# Now import via package path (triggers relative imports correctly)
_ops_mod = importlib.import_module("dashboard_api.ops_health")

# Re-export for test use
compute_evidence_status = _ops_mod.compute_evidence_status
compute_signing_status = _ops_mod.compute_signing_status
compute_ots_status = _ops_mod.compute_ots_status
compute_healing_status = _ops_mod.compute_healing_status
compute_fleet_status = _ops_mod.compute_fleet_status

# Also import threshold constants if the test uses them
for _name in dir(_ops_mod):
    if _name.isupper() and not _name.startswith('_'):
        globals()[_name] = getattr(_ops_mod, _name)

import pytest


# =============================================================================
# compute_evidence_status
# =============================================================================

class TestComputeEvidenceStatus:
    def test_green_healthy(self):
        result = compute_evidence_status(
            total_bundles=100,
            last_submission_minutes_ago=5.0,
            chain_gaps=0,
            signing_rate=95.0,
        )
        assert result["status"] == "green"
        assert result["total_bundles"] == 100

    def test_yellow_last_submission_over_30m(self):
        result = compute_evidence_status(
            total_bundles=50,
            last_submission_minutes_ago=45.0,
            chain_gaps=0,
            signing_rate=90.0,
        )
        assert result["status"] == "yellow"

    def test_yellow_exactly_at_threshold(self):
        """Just over the yellow threshold but under red."""
        result = compute_evidence_status(
            total_bundles=50,
            last_submission_minutes_ago=EVIDENCE_YELLOW_MINUTES + 1,
            chain_gaps=0,
            signing_rate=90.0,
        )
        assert result["status"] == "yellow"

    def test_red_last_submission_over_60m(self):
        result = compute_evidence_status(
            total_bundles=50,
            last_submission_minutes_ago=90.0,
            chain_gaps=0,
            signing_rate=90.0,
        )
        assert result["status"] == "red"

    def test_red_chain_gaps(self):
        """Chain gaps override time-based status."""
        result = compute_evidence_status(
            total_bundles=100,
            last_submission_minutes_ago=5.0,
            chain_gaps=3,
            signing_rate=95.0,
        )
        assert result["status"] == "red"
        assert "gap" in result["label"].lower()

    def test_red_no_bundles(self):
        result = compute_evidence_status(
            total_bundles=0,
            last_submission_minutes_ago=None,
            chain_gaps=0,
            signing_rate=0.0,
        )
        assert result["status"] == "red"
        assert "no evidence" in result["label"].lower()

    def test_red_none_last_submission(self):
        """None last_submission means no data — red."""
        result = compute_evidence_status(
            total_bundles=5,
            last_submission_minutes_ago=None,
            chain_gaps=0,
            signing_rate=50.0,
        )
        assert result["status"] == "red"

    def test_metrics_passthrough(self):
        """All input values appear in the returned dict."""
        result = compute_evidence_status(
            total_bundles=42,
            last_submission_minutes_ago=10.0,
            chain_gaps=0,
            signing_rate=88.0,
        )
        assert result["total_bundles"] == 42
        assert result["last_submission_minutes_ago"] == 10.0
        assert result["chain_gaps"] == 0
        assert result["signing_rate"] == 88.0


# =============================================================================
# compute_signing_status
# =============================================================================

class TestComputeSigningStatus:
    def test_green_high_rate(self):
        result = compute_signing_status(
            signing_rate=95.0,
            key_mismatches_24h=0,
            unsigned_legacy=2,
            signature_failures=0,
        )
        assert result["status"] == "green"

    def test_green_exactly_90(self):
        result = compute_signing_status(
            signing_rate=90.0,
            key_mismatches_24h=0,
            unsigned_legacy=0,
            signature_failures=0,
        )
        assert result["status"] == "green"

    def test_yellow_between_70_and_90(self):
        result = compute_signing_status(
            signing_rate=80.0,
            key_mismatches_24h=0,
            unsigned_legacy=5,
            signature_failures=0,
        )
        assert result["status"] == "yellow"

    def test_red_below_70(self):
        result = compute_signing_status(
            signing_rate=50.0,
            key_mismatches_24h=0,
            unsigned_legacy=20,
            signature_failures=5,
        )
        assert result["status"] == "red"

    def test_red_key_mismatches_override(self):
        """Active key mismatches force red regardless of rate."""
        result = compute_signing_status(
            signing_rate=99.0,
            key_mismatches_24h=2,
            unsigned_legacy=0,
            signature_failures=0,
        )
        assert result["status"] == "red"
        assert "mismatch" in result["label"].lower()

    def test_metrics_passthrough(self):
        result = compute_signing_status(
            signing_rate=85.0,
            key_mismatches_24h=0,
            unsigned_legacy=3,
            signature_failures=1,
        )
        assert result["signing_rate"] == 85.0
        assert result["unsigned_legacy"] == 3
        assert result["signature_failures"] == 1


# =============================================================================
# compute_ots_status
# =============================================================================

class TestComputeOtsStatus:
    def test_green_low_pending(self):
        result = compute_ots_status(
            anchored=500,
            pending=10,
            batching=5,
            latest_batch_age_hours=0.5,
        )
        assert result["status"] == "green"

    def test_yellow_pending_over_100(self):
        result = compute_ots_status(
            anchored=500,
            pending=150,
            batching=10,
            latest_batch_age_hours=1.0,
        )
        assert result["status"] == "yellow"

    def test_yellow_batch_over_2h(self):
        result = compute_ots_status(
            anchored=500,
            pending=50,
            batching=5,
            latest_batch_age_hours=3.0,
        )
        assert result["status"] == "yellow"

    def test_red_pending_over_500(self):
        result = compute_ots_status(
            anchored=500,
            pending=600,
            batching=10,
            latest_batch_age_hours=1.0,
        )
        assert result["status"] == "red"

    def test_red_batch_over_6h(self):
        result = compute_ots_status(
            anchored=500,
            pending=50,
            batching=5,
            latest_batch_age_hours=8.0,
        )
        assert result["status"] == "red"

    def test_green_no_batches(self):
        """None latest_batch_age_hours treated as 0 — green if pending low."""
        result = compute_ots_status(
            anchored=100,
            pending=5,
            batching=0,
            latest_batch_age_hours=None,
        )
        assert result["status"] == "green"

    def test_red_pending_takes_priority_over_batch_yellow(self):
        """High pending count (red) wins over moderate batch age (yellow)."""
        result = compute_ots_status(
            anchored=100,
            pending=600,
            batching=5,
            latest_batch_age_hours=3.0,
        )
        assert result["status"] == "red"

    def test_metrics_passthrough(self):
        result = compute_ots_status(
            anchored=42,
            pending=7,
            batching=3,
            latest_batch_age_hours=1.5,
        )
        assert result["anchored"] == 42
        assert result["pending"] == 7
        assert result["batching"] == 3
        assert result["latest_batch_age_hours"] == 1.5


# =============================================================================
# compute_healing_status
# =============================================================================

class TestComputeHealingStatus:
    def test_green_high_rate_low_exhausted(self):
        result = compute_healing_status(
            l1_heal_rate=95.0,
            exhausted_count=2,
            stuck_count=0,
        )
        assert result["status"] == "green"

    def test_green_exactly_90(self):
        result = compute_healing_status(
            l1_heal_rate=90.0,
            exhausted_count=0,
            stuck_count=0,
        )
        assert result["status"] == "green"

    def test_yellow_rate_between_70_and_90(self):
        result = compute_healing_status(
            l1_heal_rate=80.0,
            exhausted_count=2,
            stuck_count=1,
        )
        assert result["status"] == "yellow"

    def test_yellow_exhausted_over_5(self):
        result = compute_healing_status(
            l1_heal_rate=95.0,
            exhausted_count=7,
            stuck_count=0,
        )
        assert result["status"] == "yellow"

    def test_red_rate_below_70(self):
        result = compute_healing_status(
            l1_heal_rate=50.0,
            exhausted_count=0,
            stuck_count=0,
        )
        assert result["status"] == "red"

    def test_red_exhausted_over_10(self):
        result = compute_healing_status(
            l1_heal_rate=95.0,
            exhausted_count=15,
            stuck_count=0,
        )
        assert result["status"] == "red"

    def test_red_rate_takes_priority(self):
        """Low L1 rate (red) wins even if exhausted is moderate (yellow)."""
        result = compute_healing_status(
            l1_heal_rate=60.0,
            exhausted_count=7,
            stuck_count=3,
        )
        assert result["status"] == "red"

    def test_metrics_passthrough(self):
        result = compute_healing_status(
            l1_heal_rate=85.0,
            exhausted_count=3,
            stuck_count=2,
        )
        assert result["l1_heal_rate"] == 85.0
        assert result["exhausted_count"] == 3
        assert result["stuck_count"] == 2


# =============================================================================
# compute_fleet_status
# =============================================================================

class TestComputeFleetStatus:
    def test_green_all_online(self):
        result = compute_fleet_status(
            total_appliances=5,
            online_count=5,
            max_offline_minutes=None,
        )
        assert result["status"] == "green"
        assert result["offline_count"] == 0

    def test_yellow_no_appliances(self):
        result = compute_fleet_status(
            total_appliances=0,
            online_count=0,
            max_offline_minutes=None,
        )
        assert result["status"] == "yellow"
        assert "no appliances" in result["label"].lower()

    def test_yellow_offline_over_30m(self):
        result = compute_fleet_status(
            total_appliances=5,
            online_count=4,
            max_offline_minutes=45.0,
        )
        assert result["status"] == "yellow"
        assert result["offline_count"] == 1

    def test_red_offline_over_2h(self):
        result = compute_fleet_status(
            total_appliances=5,
            online_count=3,
            max_offline_minutes=150.0,
        )
        assert result["status"] == "red"
        assert result["offline_count"] == 2

    def test_green_offline_under_30m(self):
        """Appliance offline <30 min is still green."""
        result = compute_fleet_status(
            total_appliances=5,
            online_count=4,
            max_offline_minutes=10.0,
        )
        assert result["status"] == "green"

    def test_metrics_passthrough(self):
        result = compute_fleet_status(
            total_appliances=10,
            online_count=8,
            max_offline_minutes=60.0,
        )
        assert result["total_appliances"] == 10
        assert result["online_count"] == 8
        assert result["offline_count"] == 2
        assert result["max_offline_minutes"] == 60.0
