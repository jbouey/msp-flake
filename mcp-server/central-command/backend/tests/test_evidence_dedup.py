"""Unit tests for evidence bundle deduplication in submit_evidence.

Covers both the 15-minute hash-window dedup AND the bundle_id dedup under
the per-site advisory lock. Since Session 209 the write path runs under
tenant_connection(site_id) with asyncpg (not SQLAlchemy), so this file
mocks get_pool() + tenant_connection() to observe the asyncpg call
sequence. The pre-write phase (site + appliance lookup) still uses
SQLAlchemy and is mocked via `db.execute` as before.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
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
# SQLAlchemy-side fakes (pre-write phase: site lookup + appliance lookup)
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


def _make_sqla_db():
    """SQLAlchemy mock for the pre-write phase (site + appliance lookup + heartbeat UPDATEs)."""
    site_row = FakeRow(
        [SITE_ID, AGENT_PUB_KEY],
        names=["site_id", "agent_public_key"],
    )
    appliance_row = FakeRow(
        ["appliance-uuid-001", AGENT_PUB_KEY],
        names=["appliance_id", "agent_public_key"],
    )

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
        # UPDATE site_appliances etc. — empty success
        return FakeResult()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=mock_execute)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# asyncpg-side fake (write phase via tenant_connection)
# ---------------------------------------------------------------------------


class FakeAsyncpgConn:
    """Minimal asyncpg Connection stand-in capturing calls."""

    def __init__(self, hash_hit=False, id_hit=False, prev_bundle=None):
        self.hash_hit = hash_hit
        self.id_hit = id_hit
        self.prev_bundle = prev_bundle
        self.execute_calls = []
        self.fetchval_calls = []
        self.fetchrow_calls = []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "OK"

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if "bundle_hash" in query and "INTERVAL" in query:
            return 1 if self.hash_hit else None
        if "WHERE bundle_id = $1 LIMIT 1" in query:
            return 1 if self.id_hit else None
        return None

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "ORDER BY chain_position DESC" in query:
            return self.prev_bundle
        return None

    def inserts(self):
        return [
            call for call in self.execute_calls
            if "INSERT INTO compliance_bundles" in call[0]
        ]

    def advisory_locks(self):
        return [
            call for call in self.execute_calls
            if "pg_advisory_xact_lock" in call[0]
        ]


def _patch_pool_and_tenant(conn):
    """Return (get_pool_patcher, tenant_connection_patcher) that route the
    deferred `from .fleet import get_pool` + `from .tenant_middleware import
    tenant_connection` inside submit_evidence to our fakes."""

    @asynccontextmanager
    async def _fake_tc(pool, site_id=None, is_admin=False, actor_appliance_id=None):
        yield conn

    gp = patch(
        "dashboard_api.fleet.get_pool",
        AsyncMock(return_value=MagicMock()),
    )
    tc = patch(
        "dashboard_api.tenant_middleware.tenant_connection",
        _fake_tc,
    )
    return gp, tc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvidenceDedup:
    """Verify hash-window + bundle_id dedup in evidence_chain.submit_evidence."""

    @pytest.mark.asyncio
    async def test_dedup_hash_window_returns_accepted_without_inserting(self):
        """
        When an identical bundle_hash exists within 15 minutes,
        submit_evidence must return status=accepted + deduplicated=True
        without executing the INSERT or acquiring the advisory lock.
        """
        conn = FakeAsyncpgConn(hash_hit=True)
        db = _make_sqla_db()
        bundle = _make_bundle()
        background_tasks = MagicMock()

        gp, tc = _patch_pool_and_tenant(conn)
        with gp, tc, patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
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
        assert conn.inserts() == [], (
            f"INSERT must NOT run when hash-window dedup match is found; got {len(conn.inserts())}"
        )
        assert conn.advisory_locks() == [], (
            "Advisory lock must NOT be acquired when hash-window dedup returns early"
        )

    @pytest.mark.asyncio
    async def test_no_dedup_proceeds_to_insert(self):
        """
        When no matching bundle_hash OR bundle_id exists, submit_evidence
        must acquire the advisory lock and execute the INSERT.
        """
        conn = FakeAsyncpgConn(hash_hit=False, id_hit=False, prev_bundle=None)
        db = _make_sqla_db()
        bundle = _make_bundle()
        background_tasks = MagicMock()

        gp, tc = _patch_pool_and_tenant(conn)
        with gp, tc, patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            result = await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        # result is an EvidenceSubmitResponse Pydantic model on the happy path
        deduped = (
            result.get("deduplicated")
            if isinstance(result, dict)
            else getattr(result, "deduplicated", None)
        )
        assert deduped is not True, (
            "deduplicated should not be set when no dedup match exists"
        )
        assert len(conn.inserts()) == 1, (
            f"INSERT must execute exactly once when no dedup match; got {len(conn.inserts())}"
        )
        assert len(conn.advisory_locks()) == 1, (
            f"Advisory lock must be acquired exactly once on the write path; got {len(conn.advisory_locks())}"
        )

    @pytest.mark.asyncio
    async def test_bundle_id_duplicate_returns_accepted_without_inserting(self):
        """
        Second-line dedup: bundle_hash window misses but bundle_id already
        exists (e.g., hash-window expired but ID still collides). Must
        return deduplicated=True with the bundle_id-specific message and
        skip the INSERT, while STILL having acquired the advisory lock.
        """
        conn = FakeAsyncpgConn(hash_hit=False, id_hit=True, prev_bundle=None)
        db = _make_sqla_db()
        bundle = _make_bundle()
        background_tasks = MagicMock()

        gp, tc = _patch_pool_and_tenant(conn)
        with gp, tc, patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            result = await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        assert result["status"] == "accepted"
        assert result.get("deduplicated") is True
        assert "bundle_id" in result.get("message", "").lower()
        assert conn.inserts() == [], (
            "INSERT must not run when bundle_id already exists"
        )
        assert len(conn.advisory_locks()) == 1, (
            "Advisory lock IS acquired for the bundle_id check (tighter race window)"
        )

    @pytest.mark.asyncio
    async def test_dedup_skip_does_not_call_sqla_commit(self):
        """
        A dedup-skipped submission must not call the SQLAlchemy db.commit()
        because no write happened — early return fires before tenant_connection
        even opens a transaction on the asyncpg side.
        """
        conn = FakeAsyncpgConn(hash_hit=True)
        db = _make_sqla_db()
        bundle = _make_bundle()
        background_tasks = MagicMock()

        gp, tc = _patch_pool_and_tenant(conn)
        with gp, tc, patch.object(evidence_chain, "verify_ed25519_signature", return_value=True):
            await evidence_chain.submit_evidence(
                site_id=SITE_ID,
                bundle=bundle,
                background_tasks=background_tasks,
                auth_site_id=SITE_ID,
                db=db,
            )

        db.commit.assert_not_called()
