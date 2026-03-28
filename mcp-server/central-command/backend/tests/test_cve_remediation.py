"""Unit tests for CVE-to-Runbook auto-remediation engine.

Tests the core functions:
  - generate_cve_runbook: creates runbooks from CVE data, idempotent
  - auto_remediate_cve: creates L1 rules for full-coverage sites only
  - process_new_cve_matches: batch processor for unprocessed matches
  - _build_runbook_steps: step generation heuristics
  - _make_runbook_id / _make_l1_rule_id: ID derivation
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

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Restore real fastapi/sqlalchemy if earlier tests stubbed them.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

import main as _main_module  # noqa: E402, F401

from dashboard_api.cve_remediation import (
    _build_runbook_steps,
    _make_l1_rule_id,
    _make_runbook_id,
    auto_remediate_cve,
    generate_cve_runbook,
    process_new_cve_matches,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_conn():
    """Create a mock asyncpg connection with configurable return values."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    return conn


def make_cve_row(cve_id="CVE-2024-12345", severity="high", cvss_score=8.5,
                 description="A remote code execution vulnerability in the update mechanism"):
    """Create a mock CVE entry row."""
    return {
        "id": uuid.uuid4(),
        "cve_id": cve_id,
        "severity": severity,
        "cvss_score": cvss_score,
        "description": description,
        "affected_cpes": json.dumps([{"criteria": "cpe:2.3:a:vendor:product:*"}]),
    }


def make_fleet_match_row(cve_id_str="CVE-2024-12345", site_id="site-001",
                         severity="high", remediation_status=None):
    """Create a mock fleet match row (as returned by the JOIN query)."""
    cve_uuid = uuid.uuid4()
    return {
        "id": uuid.uuid4(),
        "cve_id": cve_uuid,
        "site_id": site_id,
        "appliance_id": "appliance-001",
        "cve_id_str": cve_id_str,
        "severity": severity,
        "remediation_status": remediation_status,
    }


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

class TestRunbookIdDerivation:
    def test_make_runbook_id(self):
        assert _make_runbook_id("CVE-2024-12345") == "RB-CVE-2024-12345"

    def test_make_runbook_id_preserves_format(self):
        assert _make_runbook_id("CVE-2023-0001") == "RB-CVE-2023-0001"

    def test_make_l1_rule_id(self):
        assert _make_l1_rule_id("CVE-2024-12345") == "L1-CVE-2024-12345"

    def test_make_l1_rule_id_preserves_format(self):
        assert _make_l1_rule_id("CVE-2023-0001") == "L1-CVE-2023-0001"


class TestBuildRunbookSteps:
    def test_patch_keywords_detected(self):
        steps = _build_runbook_steps("Update to version 3.1 to fix the vulnerability", "high")
        assert len(steps) == 3
        assert steps[0]["action"] == "detect"
        assert "version" in steps[0]["description"].lower() or "software" in steps[0]["description"].lower()
        assert steps[1]["action"] == "remediate"
        assert "update" in steps[1]["description"].lower() or "patch" in steps[1]["description"].lower()
        assert steps[2]["action"] == "verify"

    def test_config_keywords_detected(self):
        steps = _build_runbook_steps("A misconfiguration in default settings allows bypass", "medium")
        assert len(steps) == 3
        assert "configuration" in steps[0]["description"].lower() or "config" in steps[0]["description"].lower()

    def test_service_keywords_detected(self):
        steps = _build_runbook_steps("The daemon process can be exploited to restart services", "high")
        assert len(steps) == 3
        assert "service" in steps[0]["description"].lower()

    def test_generic_fallback(self):
        steps = _build_runbook_steps("An unspecified vulnerability exists", "low")
        assert len(steps) == 3
        assert steps[0]["action"] == "detect"
        assert steps[1]["action"] == "remediate"
        assert steps[2]["action"] == "verify"

    def test_empty_description(self):
        steps = _build_runbook_steps("", "unknown")
        assert len(steps) == 3  # Falls through to generic

    def test_none_description(self):
        steps = _build_runbook_steps(None, "critical")
        assert len(steps) == 3


# ---------------------------------------------------------------------------
# generate_cve_runbook tests
# ---------------------------------------------------------------------------

class TestGenerateCveRunbook:
    @pytest.mark.asyncio
    async def test_creates_runbook_successfully(self):
        conn = make_mock_conn()
        cve_row = make_cve_row()

        # fetchval for existing check returns None (no existing runbook)
        conn.fetchval = AsyncMock(return_value=None)
        # fetchrow returns CVE data
        conn.fetchrow = AsyncMock(return_value=cve_row)

        result = await generate_cve_runbook(conn, "CVE-2024-12345", str(uuid.uuid4()))

        assert result == "RB-CVE-2024-12345"
        # Verify INSERT was called
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args
        assert "INSERT INTO runbooks" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_idempotent_returns_existing(self):
        conn = make_mock_conn()
        # fetchval returns existing runbook_id
        conn.fetchval = AsyncMock(return_value="RB-CVE-2024-12345")

        result = await generate_cve_runbook(conn, "CVE-2024-12345", str(uuid.uuid4()))

        assert result == "RB-CVE-2024-12345"
        # Should NOT have called execute (no INSERT needed)
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cve_not_found_returns_none(self):
        conn = make_mock_conn()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=None)  # CVE not found

        result = await generate_cve_runbook(conn, "CVE-9999-99999", str(uuid.uuid4()))

        assert result is None

    @pytest.mark.asyncio
    async def test_insert_failure_returns_none(self):
        conn = make_mock_conn()
        cve_row = make_cve_row()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=cve_row)
        conn.execute = AsyncMock(side_effect=Exception("DB write failed"))

        result = await generate_cve_runbook(conn, "CVE-2024-12345", str(uuid.uuid4()))

        assert result is None

    @pytest.mark.asyncio
    async def test_critical_severity_maps_to_security_category(self):
        conn = make_mock_conn()
        cve_row = make_cve_row(severity="critical")
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=cve_row)

        result = await generate_cve_runbook(conn, "CVE-2024-12345", str(uuid.uuid4()))

        assert result == "RB-CVE-2024-12345"
        call_args = conn.execute.call_args[0]
        # category is the 4th positional arg after the SQL
        assert call_args[4] == "security"  # category

    @pytest.mark.asyncio
    async def test_low_severity_maps_to_patching_category(self):
        conn = make_mock_conn()
        cve_row = make_cve_row(severity="low")
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=cve_row)

        result = await generate_cve_runbook(conn, "CVE-2024-12345", str(uuid.uuid4()))

        assert result == "RB-CVE-2024-12345"
        call_args = conn.execute.call_args[0]
        assert call_args[4] == "patching"  # category


# ---------------------------------------------------------------------------
# auto_remediate_cve tests
# ---------------------------------------------------------------------------

class TestAutoRemediateCve:
    @pytest.mark.asyncio
    async def test_full_coverage_creates_rule(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"healing_tier": "full_coverage"})
        conn.fetchval = AsyncMock(return_value=None)  # No existing rule

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-001", "RB-CVE-2024-12345"
        )

        assert result["action"] == "created"
        assert result["rule_id"] == "L1-CVE-2024-12345"
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_standard_tier_skipped(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"healing_tier": "standard"})

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-001", "RB-CVE-2024-12345"
        )

        assert result["action"] == "skipped"
        assert "standard" in result["reason"]
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_tier_defaults_to_standard(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"healing_tier": None})

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-001", "RB-CVE-2024-12345"
        )

        assert result["action"] == "skipped"

    @pytest.mark.asyncio
    async def test_site_not_found_skipped(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value=None)  # Site not found

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-nonexistent", "RB-CVE-2024-12345"
        )

        assert result["action"] == "skipped"

    @pytest.mark.asyncio
    async def test_idempotent_existing_rule(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"healing_tier": "full_coverage"})
        conn.fetchval = AsyncMock(return_value="L1-CVE-2024-12345")  # Already exists

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-001", "RB-CVE-2024-12345"
        )

        assert result["action"] == "already_exists"
        assert result["rule_id"] == "L1-CVE-2024-12345"
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_error_returns_failed(self):
        conn = make_mock_conn()
        conn.fetchrow = AsyncMock(return_value={"healing_tier": "full_coverage"})
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock(side_effect=Exception("Connection refused"))

        result = await auto_remediate_cve(
            conn, "CVE-2024-12345", "site-001", "RB-CVE-2024-12345"
        )

        assert result["action"] == "failed"
        assert "Connection refused" in result["reason"]


# ---------------------------------------------------------------------------
# process_new_cve_matches tests
# ---------------------------------------------------------------------------

class TestProcessNewCveMatches:
    @pytest.mark.asyncio
    async def test_empty_matches_returns_zero_stats(self):
        conn = make_mock_conn()
        conn.fetch = AsyncMock(return_value=[])

        stats = await process_new_cve_matches(conn)

        assert stats["processed"] == 0
        assert stats["runbooks_generated"] == 0

    @pytest.mark.asyncio
    async def test_processes_single_match(self):
        conn = make_mock_conn()
        match = make_fleet_match_row()

        # First call: fetch matches. Subsequent calls: other queries.
        call_count = 0

        async def mock_fetch(query, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [match]
            return []

        conn.fetch = mock_fetch

        # generate_cve_runbook needs: fetchval (no existing), fetchrow (cve data)
        cve_row = make_cve_row()
        fetchval_calls = 0

        async def mock_fetchval(query, *args):
            nonlocal fetchval_calls
            fetchval_calls += 1
            return None  # No existing runbook or rule

        conn.fetchval = mock_fetchval

        fetchrow_calls = 0

        async def mock_fetchrow(query, *args):
            nonlocal fetchrow_calls
            fetchrow_calls += 1
            if "cve_entries" in query:
                return cve_row
            if "sites" in query:
                return {"healing_tier": "standard"}
            return None

        conn.fetchrow = mock_fetchrow

        stats = await process_new_cve_matches(conn)

        assert stats["processed"] == 1
        assert stats["runbooks_generated"] == 1
        # standard tier = skipped auto-remediation
        assert stats["skipped"] == 1

    @pytest.mark.asyncio
    async def test_full_coverage_site_auto_remediates(self):
        conn = make_mock_conn()
        match = make_fleet_match_row()

        call_count = 0

        async def mock_fetch(query, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [match]
            return []

        conn.fetch = mock_fetch

        cve_row = make_cve_row()

        async def mock_fetchval(query, *args):
            return None

        conn.fetchval = mock_fetchval

        async def mock_fetchrow(query, *args):
            if "cve_entries" in query:
                return cve_row
            if "sites" in query:
                return {"healing_tier": "full_coverage"}
            return None

        conn.fetchrow = mock_fetchrow

        stats = await process_new_cve_matches(conn)

        assert stats["processed"] == 1
        assert stats["runbooks_generated"] == 1
        assert stats["auto_remediated"] == 1

    @pytest.mark.asyncio
    async def test_failed_runbook_generation_recorded(self):
        conn = make_mock_conn()
        match = make_fleet_match_row()

        call_count = 0

        async def mock_fetch(query, *args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [match]
            return []

        conn.fetch = mock_fetch

        # Simulate CVE not found
        async def mock_fetchval(query, *args):
            return None

        conn.fetchval = mock_fetchval

        async def mock_fetchrow(query, *args):
            return None  # CVE not found

        conn.fetchrow = mock_fetchrow

        stats = await process_new_cve_matches(conn)

        assert stats["processed"] == 1
        assert stats["errors"] == 1
        assert stats["runbooks_generated"] == 0
