"""Tests for alert_router module.

Tests alert mode resolution, incident classification, digest email rendering,
and silent-mode suppression logic.

No HTTP or DB required — all functions are pure or take a mock connection.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup (must happen before any app imports)
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

# ---------------------------------------------------------------------------
# Stub heavy dependencies so we can import alert_router without a full stack
# ---------------------------------------------------------------------------

for mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "aiohttp", "structlog",
    "nacl", "nacl.signing", "nacl.encoding", "minio",
    "redis", "redis.asyncio",
    "dashboard_api.email_alerts",
    "dashboard_api.fleet",
    "dashboard_api.tenant_middleware",
    "dashboard_api.shared",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# SQLAlchemy async stubs required by shared.py imports
sys.modules["sqlalchemy.ext.asyncio"].create_async_engine = lambda *a, **kw: None
sys.modules["sqlalchemy.ext.asyncio"].AsyncSession = object
sys.modules["sqlalchemy.ext.asyncio"].async_sessionmaker = lambda *a, **kw: None

# Stub email_alerts with the functions alert_router will call
_email_mod = sys.modules["dashboard_api.email_alerts"]
_email_mod.send_digest_email = MagicMock(return_value=True)
_email_mod.is_email_configured = MagicMock(return_value=True)

# Stub fleet / tenant middleware
_fleet_mod = sys.modules["dashboard_api.fleet"]
_fleet_mod.get_pool = AsyncMock(return_value=None)

_tenant_mod = sys.modules["dashboard_api.tenant_middleware"]

class _FakeAdminConn:
    """Async context manager that yields a fake asyncpg-style connection."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass

_tenant_mod.admin_connection = lambda pool: _FakeAdminConn(MagicMock())
_tenant_mod.admin_transaction = lambda pool: _FakeAdminConn(MagicMock())

# Ensure backend dir is importable as dashboard_api package
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import importlib

_pkg = types.ModuleType("dashboard_api")
_pkg.__path__ = [backend_dir]
_pkg.__package__ = "dashboard_api"
# Only set if not already a real package
if not hasattr(sys.modules.get("dashboard_api", None), "__file__") or \
        sys.modules.get("dashboard_api", None).__file__ is None:
    sys.modules["dashboard_api"] = _pkg

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

_alert_router_mod = importlib.import_module("dashboard_api.alert_router")

get_effective_alert_mode = _alert_router_mod.get_effective_alert_mode
classify_alert = _alert_router_mod.classify_alert
render_digest_email = _alert_router_mod.render_digest_email
maybe_enqueue_alert = _alert_router_mod.maybe_enqueue_alert


# =============================================================================
# TestAlertModeResolution
# =============================================================================

class TestAlertModeResolution:
    def test_site_override_wins(self):
        """Site-level mode wins over org-level mode."""
        result = get_effective_alert_mode(site_mode="self_service", org_mode="silent")
        assert result == "self_service"

    def test_null_site_inherits_org(self):
        """Null site mode falls back to org mode."""
        result = get_effective_alert_mode(site_mode=None, org_mode="silent")
        assert result == "silent"

    def test_both_null_defaults_informed(self):
        """Both null → default 'informed' mode."""
        result = get_effective_alert_mode(site_mode=None, org_mode=None)
        assert result == "informed"


# =============================================================================
# TestAlertClassification
# =============================================================================

class TestAlertClassification:
    def test_drift_firewall_classified_as_client(self):
        result = classify_alert("drift:windows_firewall", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "firewall_off"

    def test_patch_drift_classified(self):
        result = classify_alert("drift:windows_update", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "patch_available"

    def test_service_stopped_classified(self):
        result = classify_alert("drift:service_stopped", "medium")
        assert result["tier"] == "client"
        assert result["alert_type"] == "service_stopped"

    def test_unknown_type_defaults_to_admin(self):
        result = classify_alert("unknown:thing", "low")
        assert result["tier"] == "admin"
        # alert_type key should still be present
        assert "alert_type" in result


# =============================================================================
# TestDigestEmailContent
# =============================================================================

class TestDigestEmailContent:
    def _sample_alerts(self):
        return [
            {"alert_type": "patch_available", "site_name": "North Valley Clinic", "count": 3},
            {"alert_type": "firewall_off", "site_name": "North Valley Clinic", "count": 1},
        ]

    def test_digest_body_has_no_ips(self):
        """Digest email must be PHI-free: no IP addresses."""
        html, text = render_digest_email(
            org_name="North Valley Health",
            alerts_list=self._sample_alerts(),
            mode="informed",
        )
        assert "192.168" not in text
        assert "192.168" not in html
        # Site names must appear
        assert "North Valley Clinic" in text or "North Valley Clinic" in html

    def test_informed_mode_no_action_language(self):
        """Informed mode footer signals monitoring, not action."""
        html, text = render_digest_email(
            org_name="North Valley Health",
            alerts_list=self._sample_alerts(),
            mode="informed",
        )
        combined = (text + html).lower()
        assert "no action required" in combined or "monitoring" in combined

    def test_self_service_mode_has_action_cta(self):
        """Self-service mode CTA should include action or review language."""
        html, text = render_digest_email(
            org_name="North Valley Health",
            alerts_list=self._sample_alerts(),
            mode="self_service",
        )
        combined = (text + html).lower()
        assert "action" in combined or "review" in combined


# =============================================================================
# TestSilentModeSuppression
# =============================================================================

class TestSilentModeSuppression:
    @pytest.mark.asyncio
    async def test_silent_mode_skips_enqueue(self):
        """Silent mode: maybe_enqueue_alert returns None, DB not touched."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        result = await maybe_enqueue_alert(
            conn=mock_conn,
            org_id="org-123",
            site_id="site-456",
            incident_id="inc-789",
            incident_type="drift:windows_firewall",
            severity="medium",
            site_mode="silent",
            org_mode="silent",
        )

        assert result is None
        mock_conn.execute.assert_not_called()
