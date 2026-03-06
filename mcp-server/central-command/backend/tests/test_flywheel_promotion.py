"""Integration tests for the learning flywheel promotion system.

Tests the flow:
  POST /learning/promote/{pattern_id} -> L1 rule creation

Two promotion paths:
  1. Legacy `patterns` table via promote_pattern_in_db()
  2. Fallback to `aggregated_pattern_stats` table

These tests mock the database layer (SQLAlchemy AsyncSession) so we can
exercise the FastAPI endpoint and db_queries logic without a real Postgres.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
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

# Ensure the backend package is importable
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

# Pre-import main at module level so it's cached before other test files
# can corrupt sys.modules further.
import main as _main_module  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Helpers: mock DB result rows
# ---------------------------------------------------------------------------

class FakeRow:
    """Simulate a SQLAlchemy result row with index and attribute access."""

    def __init__(self, values, columns=None):
        self._values = values
        self._columns = columns or []

    def __getitem__(self, idx):
        return self._values[idx]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        # Named column access
        if self._columns:
            for i, col in enumerate(self._columns):
                if col == name and i < len(self._values):
                    return self._values[i]
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
    """Create a mock AsyncSession that returns configurable results."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests: POST /learning/promote/{pattern_id} endpoint
# ---------------------------------------------------------------------------

class TestPromotePatternEndpoint:
    """Test the promote_pattern endpoint via httpx AsyncClient."""

    @pytest.mark.asyncio
    async def test_promote_from_legacy_patterns_table(self):
        """When pattern exists in legacy patterns table, promote it and return new rule."""
        # Import from _routes_impl (routes.py loaded via package __init__)
        from dashboard_api._routes_impl import promote_pattern as promote_fn

        pattern_id = "pat-legacy-001"
        expected_rule_id = "RB-AUTO-SERVICE_RESTART"

        mock_db = make_mock_db()

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=expected_rule_id):
            result = await promote_fn(pattern_id=pattern_id, db=mock_db)

        assert result["status"] == "promoted"
        assert result["pattern_id"] == pattern_id
        assert result["new_rule_id"] == expected_rule_id

    @pytest.mark.asyncio
    async def test_promote_from_aggregated_pattern_stats(self):
        """When legacy table returns None, fall back to aggregated_pattern_stats."""
        from dashboard_api._routes_impl import promote_pattern as promote_fn

        pattern_id = "42"
        mock_db = make_mock_db()

        aps_row = FakeRow(
            [42, "service_stopped:RB-AUTO-SERVICE_RESTART", "site-abc", "restart_service"],
            columns=["id", "pattern_signature", "site_id", "recommended_action"],
        )

        async def mock_execute(query, params=None):
            query_str = str(query)

            # APS lookup (first db.execute after promote_pattern_in_db returns None)
            if "aggregated_pattern_stats" in query_str and "SELECT" in query_str:
                return FakeResult([aps_row])
            # INSERT INTO l1_rules
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([], rowcount=1)
            # UPDATE aggregated_pattern_stats
            if "UPDATE aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=1)
            # INSERT INTO learning_promotion_candidates
            if "learning_promotion_candidates" in query_str:
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=None):
            result = await promote_fn(pattern_id=pattern_id, db=mock_db)

        assert result["status"] == "promoted"
        assert result["pattern_id"] == pattern_id
        # Rule ID derived from incident_type portion of pattern_signature
        assert result["new_rule_id"] == "L1-AUTO-SERVICE-STOPPED"

    @pytest.mark.asyncio
    async def test_pattern_not_found_returns_404(self):
        """When pattern is not in legacy table or APS, raise 404."""
        from dashboard_api._routes_impl import promote_pattern as promote_fn
        from fastapi.exceptions import HTTPException

        pattern_id = "999"
        mock_db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query)
            if "aggregated_pattern_stats" in query_str:
                return FakeResult([])  # Not found
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await promote_fn(pattern_id=pattern_id, db=mock_db)

        assert exc_info.value.status_code == 404
        assert "999" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Tests: promote_pattern_in_db() direct unit tests
# ---------------------------------------------------------------------------

class TestPromotePatternInDb:
    """Test the db_queries.promote_pattern_in_db function directly."""

    @pytest.mark.asyncio
    async def test_pending_pattern_creates_rule_and_updates_status(self):
        """A pending pattern should be promoted: L1 rule created, status updated."""
        from dashboard_api.db_queries import promote_pattern_in_db

        pattern_id = "pat-pending-001"
        mock_db = make_mock_db()

        # Simulate a pending pattern row
        pattern_row = FakeRow(
            [pattern_id, "pending", "svc_restart", "service_stopped", "RB-AUTO-SVC"],
            columns=["pattern_id", "status", "pattern_signature", "incident_type", "runbook_id"],
        )

        executed_queries = []

        async def mock_execute(query, params=None):
            query_str = str(query)
            executed_queries.append(query_str)

            # SELECT FOR UPDATE on patterns
            if "SELECT * FROM patterns" in query_str and "FOR UPDATE" in query_str:
                return FakeResult([pattern_row])
            # INSERT INTO l1_rules
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([], rowcount=1)
            # UPDATE patterns SET status = 'promoted'
            if "UPDATE patterns" in query_str:
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.websocket_manager.broadcast_event", new_callable=AsyncMock) as mock_broadcast:
            rule_id = await promote_pattern_in_db(mock_db, pattern_id)

        # Should return rule_id derived from pattern_signature
        assert rule_id == "RB-AUTO-SVC_RESTART"

        # Verify commit was called (not rollback)
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

        # Verify the right queries were executed
        assert any("SELECT * FROM patterns" in q and "FOR UPDATE" in q for q in executed_queries)
        assert any("INSERT INTO l1_rules" in q for q in executed_queries)
        assert any("UPDATE patterns" in q and "promoted" in q for q in executed_queries)

        # Verify websocket broadcast was attempted
        mock_broadcast.assert_awaited_once()
        broadcast_args = mock_broadcast.call_args
        assert broadcast_args[0][0] == "pattern_promoted"
        assert broadcast_args[0][1]["pattern_id"] == pattern_id
        assert broadcast_args[0][1]["rule_id"] == "RB-AUTO-SVC_RESTART"

    @pytest.mark.asyncio
    async def test_non_pending_pattern_returns_none(self):
        """A pattern that is not in 'pending' status should return None (no promotion)."""
        from dashboard_api.db_queries import promote_pattern_in_db

        pattern_id = "pat-already-promoted"
        mock_db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query)
            # SELECT FOR UPDATE finds nothing (status != 'pending')
            if "SELECT * FROM patterns" in query_str and "FOR UPDATE" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.websocket_manager.broadcast_event", new_callable=AsyncMock) as mock_broadcast:
            rule_id = await promote_pattern_in_db(mock_db, pattern_id)

        assert rule_id is None

        # No commit, no broadcast
        mock_db.commit.assert_not_awaited()
        mock_broadcast.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
