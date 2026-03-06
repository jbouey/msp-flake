"""Integration tests for evidence bundle submission and retrieval.

Tests the /evidence endpoint flow:
- POST /evidence with a signed evidence bundle
- Verify bundle storage and order status update
- Verify MinIO upload attempt
- GET /evidence/{site_id} retrieval
- Outcome validation
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from io import BytesIO

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
    """Simulate a SQLAlchemy result row."""

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


def make_evidence_payload(**overrides):
    """Build a valid EvidenceSubmission dict."""
    now = datetime.now(timezone.utc)
    defaults = {
        "bundle_id": f"bundle-{uuid.uuid4().hex[:8]}",
        "site_id": "test-site-001",
        "host_id": "appliance-01",
        "order_id": None,
        "check_type": "service_monitor",
        "outcome": "success",
        "pre_state": {"service": "stopped"},
        "post_state": {"service": "running"},
        "actions_taken": [{"action": "restart_service", "result": "ok"}],
        "hipaa_controls": ["164.312(a)(1)"],
        "rollback_available": False,
        "rollback_generation": None,
        "timestamp_start": (now - timedelta(seconds=5)).isoformat(),
        "timestamp_end": now.isoformat(),
        "policy_version": "1.0",
        "nixos_revision": "abc123",
        "ntp_offset_ms": 12,
        "signature": "a" * 128,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests for POST /evidence
# ---------------------------------------------------------------------------

class TestEvidenceSubmission:
    """Test evidence bundle submission."""

    @pytest.mark.asyncio
    async def test_successful_evidence_submission(self):
        """POST /evidence with valid payload stores the bundle and returns success."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            # Appliance lookup
            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-test-001",
            site_id="test-site-001",
            host_id="appliance-01",
            check_type="service_monitor",
            outcome="success",
            pre_state={"service": "stopped"},
            post_state={"service": "running"},
            actions_taken=[{"action": "restart", "result": "ok"}],
            hipaa_controls=["164.312(a)(1)"],
            rollback_available=False,
            timestamp_start=now - timedelta(seconds=5),
            timestamp_end=now,
            signature="a" * 128,
        )

        # Mock MinIO client
        mock_minio = MagicMock()
        mock_minio.put_object = MagicMock()
        mock_minio.set_object_retention = MagicMock()

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "minio_client", mock_minio):
                result = await main.submit_evidence(evidence, db)

        assert result["status"] == "received"
        assert result["bundle_id"] == "bundle-test-001"
        assert result["evidence_id"] is not None
        assert result["s3_uri"] is not None
        assert result["s3_uri"].startswith("s3://evidence/")

        # Verify MinIO was called
        mock_minio.put_object.assert_called_once()
        call_args = mock_minio.put_object.call_args
        assert call_args[0][0] == "evidence"  # bucket name
        assert "test-site-001" in call_args[0][1]  # object path includes site_id

    @pytest.mark.asyncio
    async def test_evidence_with_order_id_updates_order(self):
        """Evidence linked to an order should update the order status."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())
        order_uuid = str(uuid.uuid4())
        update_calls = []

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT id FROM orders WHERE order_id" in query_str:
                return FakeResult([FakeRow([order_uuid])])
            if "UPDATE orders SET" in query_str:
                update_calls.append(params)
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-order-001",
            site_id="test-site-002",
            host_id="appliance-02",
            order_id="order-abc123",
            check_type="service_monitor",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=now - timedelta(seconds=3),
            timestamp_end=now,
            signature="b" * 128,
        )

        mock_minio = MagicMock()

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "minio_client", mock_minio):
                result = await main.submit_evidence(evidence, db)

        assert result["status"] == "received"
        # Verify order update was called with completed status
        assert len(update_calls) == 1
        assert update_calls[0]["status"] == "completed"
        assert update_calls[0]["order_id"] == "order-abc123"

    @pytest.mark.asyncio
    async def test_failed_evidence_updates_order_as_failed(self):
        """Evidence with outcome='failed' should mark the order as failed."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())
        order_uuid = str(uuid.uuid4())
        update_calls = []

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT id FROM orders WHERE order_id" in query_str:
                return FakeResult([FakeRow([order_uuid])])
            if "UPDATE orders SET" in query_str:
                update_calls.append(params)
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-fail-001",
            site_id="test-site-003",
            host_id="appliance-03",
            order_id="order-fail-123",
            check_type="firewall",
            outcome="failed",
            error="Service failed to restart after 3 attempts",
            pre_state={"firewall": "disabled"},
            post_state={"firewall": "disabled"},
            timestamp_start=now - timedelta(seconds=10),
            timestamp_end=now,
            signature="c" * 128,
        )

        mock_minio = MagicMock()

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "minio_client", mock_minio):
                result = await main.submit_evidence(evidence, db)

        assert result["status"] == "received"
        assert len(update_calls) == 1
        assert update_calls[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_unregistered_appliance_returns_404(self):
        """Evidence from an unregistered appliance should raise 404."""
        import main
        from fastapi.exceptions import HTTPException

        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=0)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-ghost-001",
            site_id="nonexistent-site",
            host_id="ghost-host",
            check_type="service_monitor",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=now - timedelta(seconds=2),
            timestamp_end=now,
            signature="d" * 128,
        )

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with pytest.raises(HTTPException) as exc_info:
                await main.submit_evidence(evidence, db)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rate_limited_evidence_returns_429(self):
        """Evidence submission should return 429 when rate limited."""
        import main
        from fastapi.exceptions import HTTPException

        db = make_mock_db()

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-rl-001",
            site_id="rate-limited-site",
            host_id="host-01",
            check_type="patching",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=now - timedelta(seconds=1),
            timestamp_end=now,
            signature="e" * 128,
        )

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(False, 60)):
            with pytest.raises(HTTPException) as exc_info:
                await main.submit_evidence(evidence, db)
            assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_minio_failure_does_not_block_storage(self):
        """If MinIO upload fails, the evidence should still be stored in DB."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-minio-fail-001",
            site_id="test-site-minio",
            host_id="appliance-minio",
            check_type="backup",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=now - timedelta(seconds=3),
            timestamp_end=now,
            signature="f" * 128,
        )

        # MinIO raises an exception
        mock_minio = MagicMock()
        mock_minio.put_object = MagicMock(side_effect=Exception("MinIO connection refused"))

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "minio_client", mock_minio):
                result = await main.submit_evidence(evidence, db)

        # Should still succeed (DB storage worked)
        assert result["status"] == "received"
        assert result["bundle_id"] == "bundle-minio-fail-001"
        assert result["s3_uri"] is None  # MinIO failed


# ---------------------------------------------------------------------------
# Tests for GET /evidence/{site_id}
# ---------------------------------------------------------------------------

class TestEvidenceRetrieval:
    """Test evidence bundle listing."""

    @pytest.mark.asyncio
    async def test_list_evidence_returns_bundles(self):
        """GET /evidence/{site_id} should return stored bundles."""
        import main

        db = make_mock_db()
        now = datetime.now(timezone.utc)

        bundle_rows = [
            FakeRow([
                "bundle-001",                   # bundle_id
                "service_monitor",               # check_type
                "success",                       # outcome
                now - timedelta(hours=1),        # timestamp_start
                now - timedelta(hours=1, seconds=-5),  # timestamp_end
                ["164.312(a)(1)"],               # hipaa_controls
                "s3://evidence/test-site/2026/03/06/bundle-001.json",  # s3_uri
                "abcdef1234567890" * 8,          # signature
            ]),
            FakeRow([
                "bundle-002",
                "firewall",
                "failed",
                now - timedelta(hours=2),
                now - timedelta(hours=2, seconds=-10),
                ["164.312(e)(1)"],
                "s3://evidence/test-site/2026/03/06/bundle-002.json",
                "0987654321fedcba" * 8,
            ]),
        ]

        db.execute = AsyncMock(return_value=FakeResult(bundle_rows))

        result = await main.list_evidence("test-site-001", limit=50, offset=0, db=db)

        assert result["site_id"] == "test-site-001"
        assert result["count"] == 2
        assert len(result["evidence"]) == 2
        assert result["evidence"][0]["bundle_id"] == "bundle-001"
        assert result["evidence"][0]["outcome"] == "success"
        assert result["evidence"][1]["bundle_id"] == "bundle-002"
        assert result["evidence"][1]["outcome"] == "failed"

    @pytest.mark.asyncio
    async def test_list_evidence_empty_site(self):
        """GET /evidence/{site_id} for a site with no bundles returns empty list."""
        import main

        db = make_mock_db()
        db.execute = AsyncMock(return_value=FakeResult([]))

        result = await main.list_evidence("empty-site", limit=50, offset=0, db=db)

        assert result["site_id"] == "empty-site"
        assert result["count"] == 0
        assert result["evidence"] == []

    @pytest.mark.asyncio
    async def test_list_evidence_signature_truncated(self):
        """Signatures in list response should be truncated for readability."""
        import main

        db = make_mock_db()
        now = datetime.now(timezone.utc)

        full_sig = "a" * 128
        bundle_row = FakeRow([
            "bundle-trunc",
            "patching",
            "success",
            now,
            now + timedelta(seconds=5),
            None,
            None,
            full_sig,
        ])

        db.execute = AsyncMock(return_value=FakeResult([bundle_row]))

        result = await main.list_evidence("sig-site", limit=50, offset=0, db=db)

        sig = result["evidence"][0]["signature"]
        assert sig.endswith("...")
        assert len(sig) == 35  # 32 chars + "..."


# ---------------------------------------------------------------------------
# Tests for EvidenceSubmission model validation
# ---------------------------------------------------------------------------

class TestEvidenceModelValidation:
    """Test Pydantic validation on EvidenceSubmission."""

    def test_valid_outcomes(self):
        """All valid outcomes should be accepted."""
        import main

        now = datetime.now(timezone.utc)
        valid_outcomes = ["success", "failed", "reverted", "deferred", "alert", "rejected", "expired"]

        for outcome in valid_outcomes:
            ev = main.EvidenceSubmission(
                bundle_id=f"bundle-{outcome}",
                site_id="test-site",
                host_id="host-01",
                check_type="test",
                outcome=outcome,
                timestamp_start=now - timedelta(seconds=1),
                timestamp_end=now,
                signature="a" * 128,
            )
            assert ev.outcome == outcome

    def test_invalid_outcome_rejected(self):
        """Invalid outcome should raise ValidationError."""
        import main
        from pydantic import ValidationError

        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError) as exc_info:
            main.EvidenceSubmission(
                bundle_id="bundle-bad",
                site_id="test-site",
                host_id="host-01",
                check_type="test",
                outcome="invalid_outcome",
                timestamp_start=now - timedelta(seconds=1),
                timestamp_end=now,
                signature="a" * 128,
            )
        assert "outcome" in str(exc_info.value)

    def test_optional_fields_default_correctly(self):
        """Optional fields should default to None or empty."""
        import main

        now = datetime.now(timezone.utc)
        ev = main.EvidenceSubmission(
            bundle_id="bundle-minimal",
            site_id="test-site",
            host_id="host-01",
            check_type="test",
            outcome="success",
            timestamp_start=now - timedelta(seconds=1),
            timestamp_end=now,
            signature="a" * 128,
        )
        assert ev.order_id is None
        assert ev.pre_state == {}
        assert ev.post_state == {}
        assert ev.actions_taken == []
        assert ev.hipaa_controls is None
        assert ev.rollback_available is False
        assert ev.error is None


# ---------------------------------------------------------------------------
# Tests for WORM retention
# ---------------------------------------------------------------------------

class TestWORMRetention:
    """Test that WORM (Write-Once Read-Many) retention is set on evidence."""

    @pytest.mark.asyncio
    async def test_worm_retention_set_on_upload(self):
        """MinIO set_object_retention should be called after put_object."""
        import main

        db = make_mock_db()
        appliance_uuid = str(uuid.uuid4())

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances WHERE site_id" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        now = datetime.now(timezone.utc)
        evidence = main.EvidenceSubmission(
            bundle_id="bundle-worm-001",
            site_id="worm-site",
            host_id="host-worm",
            check_type="encryption",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=now - timedelta(seconds=2),
            timestamp_end=now,
            signature="g" * 128,
        )

        mock_minio = MagicMock()

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "minio_client", mock_minio):
                result = await main.submit_evidence(evidence, db)

        # Verify WORM retention was attempted
        mock_minio.set_object_retention.assert_called_once()
        retention_args = mock_minio.set_object_retention.call_args
        assert retention_args[0][0] == "evidence"  # bucket


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
