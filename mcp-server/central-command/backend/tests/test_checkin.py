"""Integration tests for the appliance checkin endpoints.

Tests the legacy /checkin endpoint (main.py) and the /api/appliances/checkin
endpoint, verifying:
- Successful registration/update of appliances
- Pending orders returned in response
- Windows targets (credentials) returned in response
- site_appliances upsert
- Rate limiting behaviour
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

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

# Restore real fastapi/sqlalchemy/pydantic if earlier tests stubbed them.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

import main as _main_module  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRow:
    """Simulate a SQLAlchemy/asyncpg result row with index access."""

    def __init__(self, values, names=None):
        self._values = values
        self._names = names or []

    def __getitem__(self, idx):
        if isinstance(idx, str) and self._names:
            return self._values[self._names.index(idx)]
        return self._values[idx]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._names and name in self._names:
            return self._values[self._names.index(name)]
        raise AttributeError(name)


class FakeResult:
    """Simulate SQLAlchemy execute() result."""

    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def make_mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests for legacy /checkin endpoint
# ---------------------------------------------------------------------------

class TestLegacyCheckin:
    """Test the /checkin endpoint in main.py."""

    @pytest.mark.asyncio
    async def test_new_appliance_registration(self):
        """A new appliance should be registered and returned with 'registered' action."""
        import main

        db = make_mock_db()
        now = datetime.now(timezone.utc)

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            # Appliance lookup - not found (new)
            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([])
            # Pending orders lookup
            if "SELECT order_id" in query_str and "orders o" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.CheckinRequest(
            site_id="new-site-001",
            host_id="host-01",
            deployment_mode="direct",
            policy_version="1.0",
            nixos_version="24.11",
            agent_version="0.3.17",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "get_public_key_hex", return_value="ab" * 32):
                result = await main.checkin(req, mock_request, db)

        assert result["status"] == "ok"
        assert result["action"] == "registered"
        assert result["server_public_key"] == "ab" * 32
        assert isinstance(result["pending_orders"], list)
        assert len(result["pending_orders"]) == 0

    @pytest.mark.asyncio
    async def test_existing_appliance_update(self):
        """An existing appliance should be updated and returned with 'updated' action."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT order_id" in query_str and "orders o" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.CheckinRequest(
            site_id="existing-site-001",
            host_id="host-01",
            deployment_mode="direct",
            policy_version="2.0",
            agent_version="0.3.18",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "get_public_key_hex", return_value="ab" * 32):
                result = await main.checkin(req, mock_request, db)

        assert result["status"] == "ok"
        assert result["action"] == "updated"

    @pytest.mark.asyncio
    async def test_checkin_returns_pending_orders(self):
        """Checkin should return pending healing orders for the appliance."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=15)

        order_row = FakeRow([
            "order-abc123",           # order_id
            "RB-AUTO-SERVICE_RESTART", # runbook_id
            json.dumps({"runbook_id": "RB-AUTO-SERVICE_RESTART"}),  # parameters
            "nonce123",               # nonce
            "sig" * 42 + "ab",       # signature
            900,                      # ttl_seconds
            now,                      # issued_at
            expires,                  # expires_at
            '{"order_id":"order-abc123"}',  # signed_payload
        ])

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT order_id" in query_str and "orders o" in query_str:
                return FakeResult([order_row])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.CheckinRequest(
            site_id="site-with-orders",
            host_id="host-01",
            deployment_mode="direct",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "get_public_key_hex", return_value="ab" * 32):
                result = await main.checkin(req, mock_request, db)

        assert result["status"] == "ok"
        assert len(result["pending_orders"]) == 1
        order = result["pending_orders"][0]
        assert order["order_id"] == "order-abc123"
        assert order["runbook_id"] == "RB-AUTO-SERVICE_RESTART"
        assert order["order_type"] == "healing"

    @pytest.mark.asyncio
    async def test_checkin_rate_limited(self):
        """Checkin should return 429 when rate limited."""
        import main
        from fastapi.exceptions import HTTPException

        db = make_mock_db()

        req = main.CheckinRequest(
            site_id="rate-limited-site",
            host_id="host-01",
            deployment_mode="direct",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(False, 120)):
            with pytest.raises(HTTPException) as exc_info:
                await main.checkin(req, mock_request, db)
            assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# Tests for /api/appliances/checkin endpoint
# ---------------------------------------------------------------------------

class TestAppliancesCheckin:
    """Test the /api/appliances/checkin endpoint (site_appliances table)."""

    @pytest.mark.asyncio
    async def test_upserts_site_appliance(self):
        """Checkin should upsert into site_appliances and return ok."""
        import main

        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            # site_credentials query
            if "SELECT credential_type" in query_str and "site_credentials" in query_str:
                return FakeResult([])
            # org_credentials query
            if "SELECT oc.credential_type" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.ApplianceCheckinRequest(
            site_id="api-site-001",
            hostname="nixos-appliance",
            mac_address="aa:bb:cc:dd:ee:ff",
            ip_addresses=["192.168.88.241"],
            agent_version="0.3.17",
            nixos_version="24.11",
            uptime_seconds=86400,
            queue_depth=0,
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.88.241"

        result = await main.appliances_checkin(req, mock_request, db)

        assert result["status"] == "ok"
        assert result["appliance_id"] == "api-site-001-aa:bb:cc:dd:ee:ff"
        assert "server_time" in result
        assert isinstance(result["windows_targets"], list)

    @pytest.mark.asyncio
    async def test_returns_windows_targets(self):
        """Checkin should return decrypted Windows targets from site_credentials."""
        import main

        db = make_mock_db()

        cred_data = json.dumps({
            "host": "192.168.88.250",
            "username": "Administrator",
            "password": "TestPass123!",
            "domain": "NORTHVALLEY",
            "use_ssl": False,
        })

        cred_row = FakeRow(
            ["domain_admin", "DC Credential", cred_data],
            names=["credential_type", "credential_name", "encrypted_data"],
        )

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT credential_type" in query_str and "site_credentials" in query_str:
                return FakeResult([cred_row])
            if "SELECT oc.credential_type" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.ApplianceCheckinRequest(
            site_id="cred-site-001",
            hostname="nixos-appliance",
            mac_address="aa:bb:cc:dd:ee:ff",
            ip_addresses=["192.168.88.241"],
            agent_version="0.3.17",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.88.241"

        result = await main.appliances_checkin(req, mock_request, db)

        assert len(result["windows_targets"]) == 1
        target = result["windows_targets"][0]
        assert target["hostname"] == "192.168.88.250"
        assert target["username"] == "NORTHVALLEY\\Administrator"
        assert target["password"] == "TestPass123!"
        assert target["role"] == "domain_admin"
        assert target["use_ssl"] is False

    @pytest.mark.asyncio
    async def test_default_mac_when_missing(self):
        """When mac_address is None, use default 00:00:00:00:00:00."""
        import main

        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query
            if "SELECT credential_type" in query_str:
                return FakeResult([])
            if "SELECT oc.credential_type" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.ApplianceCheckinRequest(
            site_id="no-mac-site",
            hostname="test-host",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"

        result = await main.appliances_checkin(req, mock_request, db)

        assert result["appliance_id"] == "no-mac-site-00:00:00:00:00:00"

    @pytest.mark.asyncio
    async def test_site_creds_take_precedence_over_org_creds(self):
        """Site-level credentials should override org-level for same host."""
        import main

        db = make_mock_db()

        site_cred_data = json.dumps({
            "host": "192.168.88.251",
            "username": "localadmin",
            "password": "SitePassword!",
            "domain": "",
        })
        org_cred_data = json.dumps({
            "host": "192.168.88.251",
            "username": "orgadmin",
            "password": "OrgPassword!",
            "domain": "NORTHVALLEY",
        })

        site_cred = FakeRow(
            ["local_admin", "WS01 Local", site_cred_data],
            names=["credential_type", "credential_name", "encrypted_data"],
        )
        org_cred = FakeRow(
            ["domain_admin", "Org DC", org_cred_data],
            names=["credential_type", "credential_name", "encrypted_data"],
        )

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT credential_type" in query_str and "site_credentials" in query_str:
                return FakeResult([site_cred])
            if "SELECT oc.credential_type" in query_str:
                return FakeResult([org_cred])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        req = main.ApplianceCheckinRequest(
            site_id="precedence-site",
            hostname="test-host",
            mac_address="11:22:33:44:55:66",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"

        result = await main.appliances_checkin(req, mock_request, db)

        # Only one target for host 192.168.88.251 - site cred wins
        targets_for_host = [t for t in result["windows_targets"] if t["hostname"] == "192.168.88.251"]
        assert len(targets_for_host) == 1
        assert targets_for_host[0]["username"] == "localadmin"
        assert targets_for_host[0]["password"] == "SitePassword!"


# ---------------------------------------------------------------------------
# Tests for the CheckinRequest model validation
# ---------------------------------------------------------------------------

class TestCheckinRequestValidation:
    """Test Pydantic validation on CheckinRequest."""

    def test_valid_direct_mode(self):
        import main
        req = main.CheckinRequest(
            site_id="site-001",
            host_id="host-01",
            deployment_mode="direct",
        )
        assert req.deployment_mode == "direct"

    def test_valid_reseller_mode(self):
        import main
        req = main.CheckinRequest(
            site_id="site-002",
            host_id="host-01",
            deployment_mode="reseller",
            reseller_id="partner-123",
        )
        assert req.deployment_mode == "reseller"

    def test_invalid_deployment_mode_rejected(self):
        import main
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            main.CheckinRequest(
                site_id="site-003",
                host_id="host-01",
                deployment_mode="invalid_mode",
            )

    def test_empty_site_id_rejected(self):
        import main
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            main.CheckinRequest(
                site_id="",
                host_id="host-01",
                deployment_mode="direct",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
