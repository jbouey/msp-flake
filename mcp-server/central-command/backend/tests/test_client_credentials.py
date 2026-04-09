"""Tests for POST /api/client/credentials (site-level credential entry).

Tests:
- submit_credentials_success
- invalid_type_returns_422
- missing_username_returns_422
- site_not_in_org_returns_404
"""

import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen before any dashboard_api imports
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-fernet-key-placeholder-32chars!")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Remove stub modules that may have been injected by earlier tests
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

from fastapi import HTTPException  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ORG_ID = str(uuid.uuid4())
SITE_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())

NOW = datetime.now(timezone.utc)

FAKE_USER = {
    "user_id": USER_ID,
    "email": "client@practice.com",
    "org_id": ORG_ID,
    "role": "admin",
    "partner_branding": {},
}


class FakeRecord(dict):
    """Mimics asyncpg Record — subscriptable by name."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeConn:
    """Fake asyncpg connection with configurable per-call responses."""

    def __init__(self, fetchrow_return=None, fetchval_return=0):
        self._fetchrow_return = fetchrow_return
        self._fetchval_return = fetchval_return
        self.executed = []

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        return self._fetchrow_return

    async def fetchval(self, query, *args):
        self.executed.append(("fetchval", query, args))
        return self._fetchval_return

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        return []

    async def execute(self, query, *args):
        self.executed.append(("execute", query, args))
        return "INSERT 1"

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _org_connection_patch(conn):
    """Return a context-manager patch for org_connection that yields `conn`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_org_connection(pool, *, org_id):
        yield conn

    return _fake_org_connection


def _make_request(body: dict):
    """Build a mock Request whose .json() returns `body`."""
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


# ---------------------------------------------------------------------------
# Tests: POST /client/credentials
# ---------------------------------------------------------------------------


class TestSubmitClientCredentials:
    """POST /client/credentials"""

    @pytest.mark.asyncio
    async def test_submit_credentials_success(self):
        """Valid winrm body → credential inserted, credential_id returned."""
        from dashboard_api.client_portal import submit_client_credentials

        site_record = FakeRecord(site_id=SITE_ID)
        conn = FakeConn(fetchrow_return=site_record, fetchval_return=0)

        request = _make_request({
            "site_id": SITE_ID,
            "credential_type": "winrm",
            "credential_name": "Domain Admin",
            "data": {
                "username": "DOMAIN\\Administrator",
                "password": "S3cr3t!",
                "domain": "NORTHVALLEY",
            },
        })

        fake_encrypted = b"encrypted-bytes"

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)), \
             patch("dashboard_api.credential_crypto.encrypt_credential",
                   return_value=fake_encrypted):
            result = await submit_client_credentials(request=request, user=FAKE_USER)

        assert result["status"] == "ok"
        assert "credential_id" in result
        # Verify INSERT was called
        insert_calls = [e for e in conn.executed if e[0] == "execute"]
        assert len(insert_calls) >= 1
        assert "INSERT INTO site_credentials" in insert_calls[0][1]

    @pytest.mark.asyncio
    async def test_submit_credentials_with_alert_id_inserts_approval(self):
        """When alert_id is present, client_approvals audit record is also inserted."""
        from dashboard_api.client_portal import submit_client_credentials

        site_record = FakeRecord(site_id=SITE_ID)
        conn = FakeConn(fetchrow_return=site_record, fetchval_return=0)

        request = _make_request({
            "site_id": SITE_ID,
            "credential_type": "ssh_key",
            "data": {
                "username": "sysadmin",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----...",
            },
            "alert_id": ALERT_ID,
        })

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)), \
             patch("dashboard_api.credential_crypto.encrypt_credential",
                   return_value=b"enc"):
            result = await submit_client_credentials(request=request, user=FAKE_USER)

        assert result["status"] == "ok"
        # site_credentials INSERT + client_approvals INSERT + client_audit_log INSERT (Batch 7)
        insert_calls = [e for e in conn.executed if e[0] == "execute"]
        assert len(insert_calls) == 3
        assert "INSERT INTO site_credentials" in insert_calls[0][1]
        assert "INSERT INTO client_approvals" in insert_calls[1][1]
        assert "INSERT INTO client_audit_log" in insert_calls[2][1]

    @pytest.mark.asyncio
    async def test_invalid_type_returns_422(self):
        """Unsupported credential_type raises 422."""
        from dashboard_api.client_portal import submit_client_credentials

        request = _make_request({
            "site_id": SITE_ID,
            "credential_type": "ldap",
            "data": {"username": "admin"},
        })

        async def _fake_get_pool():
            return object()

        with pytest.raises(HTTPException) as exc_info:
            with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool):
                await submit_client_credentials(request=request, user=FAKE_USER)

        assert exc_info.value.status_code == 422
        assert "credential_type" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_username_returns_422(self):
        """Missing data.username raises 422."""
        from dashboard_api.client_portal import submit_client_credentials

        request = _make_request({
            "site_id": SITE_ID,
            "credential_type": "winrm",
            "data": {},
        })

        async def _fake_get_pool():
            return object()

        with pytest.raises(HTTPException) as exc_info:
            with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool):
                await submit_client_credentials(request=request, user=FAKE_USER)

        assert exc_info.value.status_code == 422
        assert "username" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_site_not_in_org_returns_404(self):
        """Site belonging to another org returns 404."""
        from dashboard_api.client_portal import submit_client_credentials

        # fetchrow returns None — site not found for this org
        conn = FakeConn(fetchrow_return=None)

        request = _make_request({
            "site_id": str(uuid.uuid4()),  # unknown site
            "credential_type": "ssh_password",
            "data": {"username": "ubuntu", "password": "pass"},
        })

        async def _fake_get_pool():
            return object()

        with pytest.raises(HTTPException) as exc_info:
            with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
                 patch("dashboard_api.client_portal.org_connection",
                       new=_org_connection_patch(conn)), \
                 patch("dashboard_api.credential_crypto.encrypt_credential",
                       return_value=b"enc"):
                await submit_client_credentials(request=request, user=FAKE_USER)

        assert exc_info.value.status_code == 404
        assert "Site not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_site_id_returns_422(self):
        """Missing site_id raises 422 before any DB call."""
        from dashboard_api.client_portal import submit_client_credentials

        request = _make_request({
            "credential_type": "winrm",
            "data": {"username": "admin"},
        })

        async def _fake_get_pool():
            return object()

        with pytest.raises(HTTPException) as exc_info:
            with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool):
                await submit_client_credentials(request=request, user=FAKE_USER)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_rate_limit_returns_429(self):
        """Returns 429 when >= 10 credentials submitted in the last hour."""
        from dashboard_api.client_portal import submit_client_credentials

        site_record = FakeRecord(site_id=SITE_ID)
        conn = FakeConn(fetchrow_return=site_record, fetchval_return=10)

        request = _make_request({
            "site_id": SITE_ID,
            "credential_type": "winrm",
            "data": {"username": "admin", "password": "pass"},
        })

        async def _fake_get_pool():
            return object()

        with pytest.raises(HTTPException) as exc_info:
            with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
                 patch("dashboard_api.client_portal.org_connection",
                       new=_org_connection_patch(conn)), \
                 patch("dashboard_api.credential_crypto.encrypt_credential",
                       return_value=b"enc"):
                await submit_client_credentials(request=request, user=FAKE_USER)

        assert exc_info.value.status_code == 429
