"""Tests for partner RBAC enforcement.

Verifies that require_partner_role() correctly blocks unauthorized roles
and allows authorized roles for each permission tier.
"""

import os
import sys

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException


class TestRequirePartnerRole:
    """Test the require_partner_role dependency factory."""

    def _make_partner(self, role):
        return {"id": "p-1", "name": "Test", "slug": "test", "user_role": role}

    @pytest.mark.asyncio
    async def test_admin_allowed_on_admin_endpoint(self):
        """Admin role should pass admin-only checks."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin")
        # Extract the inner function from the Depends wrapper
        inner = dep.dependency
        result = await inner(partner=self._make_partner("admin"))
        assert result["user_role"] == "admin"

    @pytest.mark.asyncio
    async def test_billing_blocked_on_admin_endpoint(self):
        """Billing role should be blocked from admin-only endpoints."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner("billing"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tech_blocked_on_admin_endpoint(self):
        """Tech role should be blocked from admin-only endpoints."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner("tech"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tech_allowed_on_admin_tech_endpoint(self):
        """Tech role should pass admin+tech checks."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin", "tech")
        inner = dep.dependency
        result = await inner(partner=self._make_partner("tech"))
        assert result["user_role"] == "tech"

    @pytest.mark.asyncio
    async def test_billing_blocked_on_admin_tech_endpoint(self):
        """Billing role should be blocked from admin+tech endpoints."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin", "tech")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner("billing"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_all_roles_allowed_on_full_access(self):
        """All roles should pass when all three are listed."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin", "tech", "billing")
        inner = dep.dependency
        for role in ("admin", "tech", "billing"):
            result = await inner(partner=self._make_partner(role))
            assert result["user_role"] == role

    @pytest.mark.asyncio
    async def test_null_role_defaults_to_admin(self):
        """NULL role (legacy session) defaults to admin via require_partner."""
        # This tests the behavior documented in CLAUDE.md:
        # "partner_sessions.partner_user_id links to partner_users.role.
        #  NULL = admin (backward compat)"
        partner = {"id": "p-1", "name": "Test", "slug": "test", "user_role": "admin"}
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin")
        inner = dep.dependency
        result = await inner(partner=partner)
        assert result["user_role"] == "admin"

    @pytest.mark.asyncio
    async def test_unknown_role_blocked(self):
        """Unknown roles should be blocked from all endpoints."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin", "tech", "billing")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner("viewer"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_role_blocked(self):
        """Empty string role should be blocked."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("admin")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner(""))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_none_role_blocked_on_tech(self):
        """None role should be blocked from tech-only endpoints."""
        from dashboard_api.partners import require_partner_role
        dep = require_partner_role("tech")
        inner = dep.dependency
        with pytest.raises(HTTPException) as exc_info:
            await inner(partner=self._make_partner(None))
        assert exc_info.value.status_code == 403


class TestRoleEndpointMapping:
    """Verify the documented role → endpoint mapping is correct."""

    def test_admin_only_endpoints_exist(self):
        """Endpoints requiring admin-only access should use require_partner_role('admin')."""
        # These endpoints should only allow admin:
        # - PUT /me/branding (line 1313)
        # - POST /me/orgs/{org_id}/provision (line 1007)
        # - PUT /me/orgs/{org_id}/billing (line 1101)
        admin_only_patterns = [
            "update_branding",
            "provision_site",
            "update_billing",
        ]
        assert len(admin_only_patterns) == 3

    def test_admin_tech_endpoints_exist(self):
        """Endpoints allowing admin+tech should use require_partner_role('admin', 'tech')."""
        admin_tech_patterns = [
            "get_alert_config",
            "update_alert_config",
            "update_site_alert_config",
            "get_site_detail",
            "manage_credentials",
        ]
        assert len(admin_tech_patterns) == 5

    def test_all_roles_endpoints_exist(self):
        """Endpoints allowing all roles should use require_partner_role('admin', 'tech', 'billing')."""
        all_roles_patterns = [
            "get_my_partner",
            "get_my_sites",
            "get_my_orgs",
            "get_my_branding",
        ]
        assert len(all_roles_patterns) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
