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

# Ensure fastapi.HTTPException works with keyword args.
# Other test files (test_production_security.py) may stub fastapi with a broken
# HTTPException that doesn't accept kwargs. We must fix this before importing auth.
def _ensure_http_exception():
    """Ensure HTTPException accepts status_code/detail kwargs."""
    _HTTPExc = type("HTTPException", (Exception,), {
        "__init__": lambda self, status_code=500, detail="": (
            setattr(self, "status_code", status_code) or
            setattr(self, "detail", detail)
        ),
    })

    if "fastapi" in sys.modules:
        # Fix existing stub/module
        sys.modules["fastapi"].HTTPException = _HTTPExc
    else:
        # Full stub for environments without fastapi
        for _mod in (
            "starlette", "starlette.middleware",
            "starlette.middleware.base", "starlette.responses",
        ):
            if _mod not in sys.modules:
                sys.modules[_mod] = types.ModuleType(_mod)

        _fastapi = types.ModuleType("fastapi")
        _fastapi.Request = type("Request", (), {})
        _fastapi.HTTPException = _HTTPExc
        _fastapi.Depends = lambda x: x
        _fastapi.Header = lambda *a, **kw: None
        _fastapi.Cookie = lambda *a, **kw: None
        _fastapi.Query = lambda *a, **kw: None
        sys.modules["fastapi"] = _fastapi

    # Stub sqlalchemy if missing
    if "sqlalchemy" not in sys.modules:
        _sa = types.ModuleType("sqlalchemy")
        _sa.text = lambda x: x
        sys.modules["sqlalchemy"] = _sa
    if "sqlalchemy.ext" not in sys.modules:
        sys.modules["sqlalchemy.ext"] = types.ModuleType("sqlalchemy.ext")
    if "sqlalchemy.ext.asyncio" not in sys.modules:
        _sa_async = types.ModuleType("sqlalchemy.ext.asyncio")
        _sa_async.create_async_engine = lambda *a, **kw: None
        _sa_async.AsyncSession = type("AsyncSession", (), {})
        _sa_async.async_sessionmaker = lambda *a, **kw: None
        sys.modules["sqlalchemy.ext.asyncio"] = _sa_async
    if "asyncpg" not in sys.modules:
        sys.modules["asyncpg"] = types.ModuleType("asyncpg")

_ensure_http_exception()

# Force-reload auth module so it picks up our fixed HTTPException.
# Without this, auth.py may have already imported a broken HTTPException
# from test_production_security.py's stubs during pytest collection.
import importlib
if "auth" in sys.modules:
    importlib.reload(sys.modules["auth"])


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
        with pytest.raises(Exception) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert exc_info.value.status_code == 404

    def test_check_org_access_returns_404_not_403(self):
        """Denied access returns 404 to prevent org enumeration."""
        from auth import _check_org_access
        with pytest.raises(Exception) as exc_info:
            _check_org_access({"org_scope": ["org-1"]}, "org-99")
        assert "not found" in exc_info.value.detail.lower()


class TestPHIBoundary:
    """Test that PHI/infrastructure data is sanitized for portal access."""

    def test_sanitize_checks_removes_raw_output(self):
        """Raw command output should be stripped from checks."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "windows_patching",
                "result": "fail",
                "hipaa_control": "164.308(a)(5)(ii)(B)",
                "raw_output": "KB5034441 missing on DC01 at 192.168.88.250",
                "details": {"missing_patches": ["KB5034441"]},
                "hostname": "DC01",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert "raw_output" not in sanitized[0]
        assert "hostname" not in sanitized[0]
        assert sanitized[0]["check_type"] == "windows_patching"
        assert sanitized[0]["result"] == "fail"
        assert sanitized[0]["hipaa_control"] == "164.308(a)(5)(ii)(B)"

    def test_sanitize_checks_preserves_compliance_fields(self):
        """Compliance-relevant fields are preserved."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "firewall_enabled",
                "result": "pass",
                "hipaa_control": "164.312(e)(1)",
                "summary": "Firewall active on all endpoints",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert sanitized[0]["check_type"] == "firewall_enabled"
        assert sanitized[0]["result"] == "pass"
        assert sanitized[0]["summary"] == "Firewall active on all endpoints"

    def test_sanitize_strips_ip_addresses_from_summary(self):
        """IP addresses in summary text should be masked."""
        from phi_boundary import sanitize_evidence_checks
        checks = [
            {
                "check_type": "logging",
                "result": "pass",
                "summary": "Syslog forwarding active to 192.168.88.50:514",
            }
        ]
        sanitized = sanitize_evidence_checks(checks)
        assert "192.168.88.50" not in sanitized[0].get("summary", "")

    def test_sanitize_empty_checks(self):
        """Empty or None checks handled gracefully."""
        from phi_boundary import sanitize_evidence_checks
        assert sanitize_evidence_checks([]) == []
        assert sanitize_evidence_checks(None) == []
