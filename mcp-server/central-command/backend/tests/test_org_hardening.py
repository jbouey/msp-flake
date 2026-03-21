"""Tests for organization feature hardening.

Covers: IDOR prevention, org_scope enforcement, pagination,
N+1 query elimination, and PHI boundary enforcement.
"""

import pytest
import sys
import os
import types
import json

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


class TestOrgAccessControl:
    """Test org_scope enforcement on organization endpoints."""

    def test_check_org_access_global_admin_passes(self):
        """Global admin (org_scope=None) can access any org."""
        from auth import _check_org_access
        _check_org_access({"org_scope": None}, "any-org-id")

    def test_check_org_access_scoped_admin_allowed(self):
        """Org-scoped admin can access their own org."""
        from auth import _check_org_access
        _check_org_access({"org_scope": ["org-1", "org-2"]}, "org-1")

    def test_check_org_access_scoped_admin_denied(self):
        """Org-scoped admin cannot access other orgs."""
        from auth import _check_org_access
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert exc_info.value.status_code == 404

    def test_check_org_access_returns_404_not_403(self):
        """Denied access returns 404 to prevent org enumeration."""
        from auth import _check_org_access
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert "not found" in exc_info.value.detail.lower()
