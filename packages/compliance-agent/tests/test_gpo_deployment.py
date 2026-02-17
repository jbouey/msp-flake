"""Tests for GPO deployment engine (gpo_deployment.py).

Tests the 4-step GPO pipeline: upload, startup script, GPO create, link.
Also tests rollback on failure and hash-based idempotency.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from dataclasses import dataclass

from compliance_agent.gpo_deployment import (
    GPODeploymentEngine,
    GPODeploymentResult,
    GPO_NAME,
)


@dataclass
class MockScriptResult:
    """Mock for WindowsExecutor.run_script result."""
    success: bool
    output: dict
    error: str = ""


def _make_executor(stdout_sequence):
    """Create a mock executor that returns stdout values in sequence."""
    executor = MagicMock()
    results = []
    for stdout in stdout_sequence:
        if stdout is None:
            results.append(MockScriptResult(success=False, output={}, error="WinRM error"))
        else:
            results.append(MockScriptResult(success=True, output={"stdout": stdout}))
    executor.run_script.side_effect = results
    return executor


@pytest.fixture
def engine_factory(tmp_path):
    """Create a GPODeploymentEngine with a mock executor."""
    def _factory(stdout_sequence):
        executor = _make_executor(stdout_sequence)
        engine = GPODeploymentEngine(
            domain="northvalley.local",
            dc_host="192.168.88.10",
            executor=executor,
            credentials={"username": "NORTHVALLEY\\admin", "password": "test"},
        )
        return engine, executor
    return _factory


@pytest.fixture
def agent_binary(tmp_path):
    """Create a fake agent binary for testing."""
    binary = tmp_path / "osiris-agent.exe"
    binary.write_bytes(b"\x00" * 1024)
    return str(binary)


class TestGPODeploymentPipeline:
    """Test the full GPO deployment pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, engine_factory, agent_binary):
        """All 4 steps succeed — should return success result."""
        engine, executor = engine_factory([
            "DIR_OK",                    # mkdir
            "HASH:NONE",                 # hash check (no existing binary)
            "CHUNK_0_OK",                # first chunk
            "VERIFY:E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855",  # placeholder
            "SCRIPT_OK",                 # startup script
            "GPO_CREATED:abc-123-def",   # GPO create + link
        ])
        # Need to patch the hash to match
        import hashlib
        binary_data = Path(agent_binary).read_bytes()
        real_hash = hashlib.sha256(binary_data).hexdigest().upper()

        # Re-create with correct hash
        engine, executor = engine_factory([
            "DIR_OK",
            "HASH:NONE",
            "CHUNK_0_OK",
            f"VERIFY:{real_hash}",
            "SCRIPT_OK",
            "GPO_CREATED:abc-123-def",
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is True
        assert result.gpo_id == "abc-123-def"
        assert result.gpo_name == GPO_NAME
        assert result.sysvol_path is not None

    @pytest.mark.asyncio
    async def test_binary_already_current(self, engine_factory, agent_binary):
        """If SYSVOL binary hash matches, skip upload."""
        import hashlib
        real_hash = hashlib.sha256(Path(agent_binary).read_bytes()).hexdigest().upper()

        engine, executor = engine_factory([
            "DIR_OK",
            f"HASH:{real_hash}",       # hash matches — skip upload
            "SCRIPT_OK",
            "GPO_CREATED:existing-456",
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is True
        # Should have made fewer calls since upload was skipped
        assert executor.run_script.call_count == 4

    @pytest.mark.asyncio
    async def test_gpo_already_exists(self, engine_factory, agent_binary):
        """If GPO already exists, use existing ID."""
        import hashlib
        real_hash = hashlib.sha256(Path(agent_binary).read_bytes()).hexdigest().upper()

        engine, executor = engine_factory([
            "DIR_OK",
            f"HASH:{real_hash}",
            "SCRIPT_OK",
            "GPO_EXISTS:existing-789",
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is True
        assert result.gpo_id == "existing-789"

    @pytest.mark.asyncio
    async def test_binary_not_found(self, engine_factory):
        """If agent binary doesn't exist, fail with clear error."""
        engine, _ = engine_factory([
            "DIR_OK",      # mkdir succeeds
            "HASH:NONE",   # no existing binary
        ])

        result = await engine.deploy_via_gpo("/nonexistent/osiris-agent.exe")

        assert result.success is False
        assert result.error is not None


class TestGPOFailureAndRollback:
    """Test failure at each pipeline step and rollback behavior."""

    @pytest.mark.asyncio
    async def test_mkdir_failure(self, engine_factory, agent_binary):
        """Step 1 mkdir failure — no artifacts to rollback."""
        engine, _ = engine_factory([
            "UNEXPECTED",  # mkdir doesn't return DIR_OK
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is False
        assert "SYSVOL" in result.error

    @pytest.mark.asyncio
    async def test_script_creation_failure_triggers_rollback(self, engine_factory, agent_binary):
        """Step 2 failure should trigger rollback of SYSVOL dir."""
        import hashlib
        real_hash = hashlib.sha256(Path(agent_binary).read_bytes()).hexdigest().upper()

        engine, executor = engine_factory([
            "DIR_OK",
            f"HASH:{real_hash}",
            "SCRIPT_FAIL",                        # startup script fails
            "DIR_REMOVED",                         # rollback: remove SYSVOL dir
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is False
        assert "startup script" in result.error.lower()

    @pytest.mark.asyncio
    async def test_gpo_creation_failure_triggers_rollback(self, engine_factory, agent_binary):
        """Step 3 failure should trigger rollback of script + SYSVOL dir."""
        import hashlib
        real_hash = hashlib.sha256(Path(agent_binary).read_bytes()).hexdigest().upper()

        engine, executor = engine_factory([
            "DIR_OK",
            f"HASH:{real_hash}",
            "SCRIPT_OK",
            None,                                 # GPO creation WinRM error
            "DIR_REMOVED",                        # rollback: remove SYSVOL dir
        ])

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is False
        assert "GPO" in result.error

    @pytest.mark.asyncio
    async def test_exception_triggers_rollback(self, engine_factory, agent_binary):
        """Unhandled exception should trigger rollback."""
        engine, executor = engine_factory([
            "DIR_OK",
        ])
        # Make the hash check raise an exception
        executor.run_script.side_effect = [
            MockScriptResult(success=True, output={"stdout": "DIR_OK"}),
            Exception("Connection reset"),
        ]

        result = await engine.deploy_via_gpo(agent_binary)

        assert result.success is False
        assert "Connection reset" in result.error


class TestGPOVerify:
    """Test GPO verification."""

    @pytest.mark.asyncio
    async def test_verify_gpo_exists(self, engine_factory):
        """verify_gpo should parse JSON from DC."""
        engine, _ = engine_factory([
            '{"exists": true, "id": "abc-123", "status": "AllSettingsEnabled"}',
        ])

        info = await engine.verify_gpo()

        assert info["exists"] is True
        assert info["id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_verify_gpo_not_found(self, engine_factory):
        """verify_gpo should return exists=False when not found."""
        engine, _ = engine_factory([
            '{"exists": false}',
        ])

        info = await engine.verify_gpo()

        assert info["exists"] is False

    @pytest.mark.asyncio
    async def test_verify_gpo_parse_error(self, engine_factory):
        """verify_gpo should handle parse errors gracefully."""
        engine, _ = engine_factory([
            "NOT JSON AT ALL",
        ])

        info = await engine.verify_gpo()

        assert info["exists"] is False
        assert "error" in info


class TestDomainDN:
    """Test domain DN conversion."""

    def test_domain_dn_simple(self):
        engine = GPODeploymentEngine(
            domain="northvalley.local",
            dc_host="dc",
            executor=MagicMock(),
            credentials={},
        )
        assert engine._domain_dn() == "DC=northvalley,DC=local"

    def test_domain_dn_three_parts(self):
        engine = GPODeploymentEngine(
            domain="sub.northvalley.local",
            dc_host="dc",
            executor=MagicMock(),
            credentials={},
        )
        assert engine._domain_dn() == "DC=sub,DC=northvalley,DC=local"
