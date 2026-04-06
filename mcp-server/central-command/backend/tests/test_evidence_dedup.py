"""Unit tests for evidence bundle deduplication in submit_evidence.

Covers the 15-minute window dedup that prevents duplicate rows during
mesh failover grace periods when two appliances scan the same target.

These tests import via the dashboard_api package so that relative imports
in evidence_chain.py resolve correctly (avoiding the Depends(None) problem
that occurs when the module is imported standalone).
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment / path setup (must be before any project imports)
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

# Restore real fastapi/sqlalchemy/pydantic/aiohttp if a pure-function test
# file (test_evidence_chain.py) ran first and stubbed them.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

# Import via the package so relative imports inside evidence_chain.py resolve.
from dashboard_api import evidence_chain  # noqa: E402


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

SITE_ID = "test-site-dedup-001"
BUNDLE_HASH = "a" * 64  # pre-computed hash injected directly into bundle
AGENT_PUB_KEY = "b" * 64
AGENT_SIG = "c" * 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRow:
    """Minimal SQLAlchemy row stand-in."""

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
    """Minimal SQLAlchemy execute() result stand-in."""

    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _make_bundle(**overrides):
    """Return an EvidenceBundleSubmit with sensible defaults for dedup tests."""
    now = datetime.now(timezone.utc)
    kwargs = dict(
        site_id=SITE_ID,
        bundle_id="CB-dedup-test-001",
        bundle_hash=BUNDLE_HASH,  # pre-set so dedup query uses this value
        check_type="patching",
        check_result="pass",
        checked_at=now,
        checks=[{"check": "patching", "status": "pass"}],
        summary={"total": 1, "passed": 1, "failed": 0},
        agent_signature=AGENT_SIG,
        agent_public_key=AGENT_PUB_KEY,
        signed_data=json.dumps(
            {"site_id": SITE_ID, "checked_at": now.isoformat()},
            sort_keys=True,
        ),
        ntp_verification=None,
    )
    kwargs.update(overrides)
    return evidence_chain.EvidenceBundleSubmit(**kwargs)


def _make_db(dedup_hit: bool):
    """
    Build a mock AsyncSession that responds to the DB query sequence inside
    submit_evidence, routing dedup SELECT to either a hit or miss result.

    Query order inside submit_evidence up to the dedup check:
      1. SELECT site_id, agent_public_key FROM sites WHERE site_id = …
      2. SELECT appliance_id, agent_public_key FROM site_appliances WHERE … (key lookup)
      3. UPDATE site_appliances … (auto-register / heartbeat — optional, ignored)
      4. SELECT 1 FROM compliance_bundles WHERE … bundle_hash … (dedup check)
    """
    site_row = FakeRow(
        [SITE_ID, AGENT_PUB_KEY],
        names=["site_id", "agent_public_key"],
    )
    appliance_row = FakeRow(
        ["appliance-uuid-001", AGENT_PUB_KEY],
        names=["appliance_id", "agent_public_key"],
    )
    dedup_row = FakeRow([1]) if dedup_hit else None

    async def mock_execute(query, params=None):
        query_str = str(query)

        if "FROM sites WHERE site_id" in query_str:
            return FakeResult([site_row])
        if (
            "FROM site_appliances" in query_str
            and "agent_public_key" in query_str
            and "SELECT" in query_str
        ):
            return FakeResult([appliance_row])
        if (
            "compliance_bundles" in query_str
            and "bundle_hash" in query_str
            and "SELECT" in query_str
        ):
            return FakeResult([dedup_row] if dedup_row else [])
        # UPDATEs, advisory lock, prev-bundle SELECT, INSERT — empty success
        return FakeResult()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=mock_execute)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvidenceDedup:
    """Verify the 15-minute dedup window in evidence_chain.submit_evidence."""

    @pytest.mark.asyncio
    async def test_dedup_returns_accepted_without_inserting(self):
        """
        When an identical bundle_hash exists within 15 minutes,
        submit_evidence must return status=accepted + deduplicated=True
        without executing an INSERT INTO compliance_bundles.
        """
        db = _make_db(dedup_hit=True)
        bundle = _make_bundle()
        background_tasks = MagicMock()

        with patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            result = await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        assert result["status"] == "accepted", f"unexpected status: {result}"
        assert result.get("deduplicated") is True, "deduplicated flag not set"
        assert result["bundle_id"] == bundle.bundle_id
        assert "15-minute" in result.get("message", ""), (
            f"expected '15-minute' in message, got: {result.get('message')}"
        )

        insert_calls = [
            call
            for call in db.execute.call_args_list
            if "INSERT INTO compliance_bundles" in str(call.args[0])
        ]
        assert insert_calls == [], (
            f"INSERT must NOT run when dedup match is found; got {len(insert_calls)} call(s)"
        )

    @pytest.mark.asyncio
    async def test_no_dedup_proceeds_to_insert(self):
        """
        When no matching bundle_hash exists in the 15-minute window,
        submit_evidence must proceed normally and execute the INSERT.
        """
        db = _make_db(dedup_hit=False)
        bundle = _make_bundle()
        background_tasks = MagicMock()

        with patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            result = await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        # result may be a Pydantic model or dict depending on code path
        deduped = (
            result.get("deduplicated")
            if isinstance(result, dict)
            else getattr(result, "deduplicated", None)
        )
        assert deduped is not True, (
            "deduplicated should not be set when no dedup match exists"
        )
        insert_calls = [
            call
            for call in db.execute.call_args_list
            if "INSERT INTO compliance_bundles" in str(call.args[0])
        ]
        assert len(insert_calls) == 1, (
            f"INSERT must execute exactly once when no dedup match; got {len(insert_calls)}"
        )

    @pytest.mark.asyncio
    async def test_dedup_skip_does_not_call_commit(self):
        """
        A dedup-skipped submission must not call db.commit() because no write
        occurred — early return happens before the advisory lock and INSERT.
        """
        db = _make_db(dedup_hit=True)
        bundle = _make_bundle()
        background_tasks = MagicMock()

        with patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_check_failure_does_not_block_submission(self):
        """
        A transient DB error during the dedup SELECT (e.g., connection glitch)
        must be swallowed and allow the submission to proceed normally.
        """
        site_row = FakeRow(
            [SITE_ID, AGENT_PUB_KEY],
            names=["site_id", "agent_public_key"],
        )
        appliance_row = FakeRow(
            ["appliance-uuid-002", AGENT_PUB_KEY],
            names=["appliance_id", "agent_public_key"],
        )

        async def mock_execute_dedup_error(query, params=None):
            query_str = str(query)
            if "FROM sites WHERE site_id" in query_str:
                return FakeResult([site_row])
            if (
                "FROM site_appliances" in query_str
                and "agent_public_key" in query_str
                and "SELECT" in query_str
            ):
                return FakeResult([appliance_row])
            # Only raise on the dedup-specific query, which has INTERVAL '15 minutes'
            if "INTERVAL" in query_str and "bundle_hash" in query_str:
                raise RuntimeError("Simulated transient DB error in dedup check")
            return FakeResult()

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=mock_execute_dedup_error)
        db.commit = AsyncMock()

        bundle = _make_bundle()
        background_tasks = MagicMock()

        with patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            result = await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        deduped = (
            result.get("deduplicated")
            if isinstance(result, dict)
            else getattr(result, "deduplicated", None)
        )
        assert deduped is not True, (
            "Submission must not be deduplicated when dedup check itself fails"
        )
        insert_calls = [
            call
            for call in db.execute.call_args_list
            if "INSERT INTO compliance_bundles" in str(call.args[0])
        ]
        assert len(insert_calls) == 1, (
            f"Submission must proceed to INSERT when dedup check fails; got {len(insert_calls)}"
        )
