"""Tests for Go agent deployment module (agent_deployment.py).

Tests WinRM-based agent deployment pipeline: directory creation,
binary transfer, config creation, service installation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone

from compliance_agent.agent_deployment import (
    GoAgentDeployer,
    DeploymentResult,
)


@dataclass
class MockScriptResult:
    """Mock for WindowsExecutor.run_script result."""
    success: bool
    output: dict
    error: str = ""


class TestGoAgentDeployerInit:
    """Test deployer initialization."""

    def test_credential_format_plain(self):
        """Plain username should be formatted with domain prefix."""
        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=MagicMock(),
        )
        assert deployer.domain == "northvalley.local"
        assert deployer.username == "admin"

    def test_credential_format_with_domain(self):
        """Username with domain prefix should be left as-is during deploy."""
        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="NORTHVALLEY\\admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=MagicMock(),
        )
        assert deployer.username == "NORTHVALLEY\\admin"


class TestSingleDeployment:
    """Test deploying to a single workstation."""

    @pytest.mark.asyncio
    async def test_deploy_success(self, tmp_path):
        """Full 5-step WinRM deployment succeeds."""
        # Create a fake agent binary
        binary = tmp_path / "osiris-agent.exe"
        binary.write_bytes(b"\x00" * 100)

        executor = MagicMock()
        executor.run_script.side_effect = [
            # check_agent_status: not installed
            MockScriptResult(success=True, output={"stdout": '{"installed": false}'}),
            # Step 1: mkdir
            MockScriptResult(success=True, output={"stdout": "OK"}),
            # Step 2+3: write binary (base64)
            MockScriptResult(success=True, output={"stdout": "OK"}),
            # Step 4: write config
            MockScriptResult(success=True, output={"stdout": "OK"}),
            # Step 5: install + start service
            MockScriptResult(success=True, output={"stdout": "SUCCESS"}),
        ]

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', binary):
            results = await deployer.deploy_to_workstations([
                {"hostname": "WS01", "ip_address": "192.168.88.100", "os": "Windows 10"},
            ])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].hostname == "WS01"
        assert results[0].method == "winrm"

    @pytest.mark.asyncio
    async def test_deploy_already_running_skips(self, tmp_path):
        """If agent is already running, deployment should be skipped."""
        binary = tmp_path / "osiris-agent.exe"
        binary.write_bytes(b"\x00" * 100)

        executor = MagicMock()
        executor.run_script.side_effect = [
            # check_agent_status: already running
            MockScriptResult(
                success=True,
                output={"stdout": '{"installed": true, "status": "Running", "version": "0.1.0"}'},
            ),
        ]

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', binary):
            results = await deployer.deploy_to_workstations([
                {"hostname": "WS02", "ip_address": "192.168.88.101"},
            ])

        assert results[0].success is True
        # Should only have made 1 call (status check), not the full 5-step pipeline
        assert executor.run_script.call_count == 1

    @pytest.mark.asyncio
    async def test_deploy_mkdir_failure(self, tmp_path):
        """Step 1 failure (mkdir) should fail the deployment."""
        binary = tmp_path / "osiris-agent.exe"
        binary.write_bytes(b"\x00" * 100)

        executor = MagicMock()
        executor.run_script.side_effect = [
            # check_agent_status: not installed
            MockScriptResult(success=True, output={"stdout": '{"installed": false}'}),
            # Step 1: mkdir fails
            MockScriptResult(success=False, output={}, error="Access denied"),
        ]

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', binary):
            results = await deployer.deploy_to_workstations([
                {"hostname": "WS03", "ip_address": "192.168.88.102"},
            ])

        assert results[0].success is False
        assert "directory" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_deploy_service_install_failure(self, tmp_path):
        """Step 5 failure (service install) should fail the deployment."""
        binary = tmp_path / "osiris-agent.exe"
        binary.write_bytes(b"\x00" * 100)

        executor = MagicMock()
        executor.run_script.side_effect = [
            # check_agent_status: not installed
            MockScriptResult(success=True, output={"stdout": '{"installed": false}'}),
            # Steps 1-4 succeed
            MockScriptResult(success=True, output={"stdout": "OK"}),
            MockScriptResult(success=True, output={"stdout": "OK"}),
            MockScriptResult(success=True, output={"stdout": "OK"}),
            # Step 5: service install fails
            MockScriptResult(success=False, output={"stderr": "Service already exists"}, error="Service install failed"),
        ]

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', binary):
            results = await deployer.deploy_to_workstations([
                {"hostname": "WS04", "ip_address": "192.168.88.103"},
            ])

        assert results[0].success is False


class TestBinaryAvailability:
    """Test agent binary availability checks."""

    @pytest.mark.asyncio
    async def test_binary_not_found_fails_all(self, tmp_path):
        """All deployments fail if binary is missing."""
        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=MagicMock(),
        )

        nonexistent = tmp_path / "nonexistent.exe"
        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', nonexistent):
            results = await deployer.deploy_to_workstations([
                {"hostname": "WS01"},
                {"hostname": "WS02"},
            ])

        assert len(results) == 2
        assert all(not r.success for r in results)
        assert all("binary" in r.error.lower() for r in results)


class TestConcurrentDeployment:
    """Test concurrent deployment to multiple workstations."""

    @pytest.mark.asyncio
    async def test_concurrent_deployments(self, tmp_path):
        """Deploying to multiple workstations concurrently."""
        binary = tmp_path / "osiris-agent.exe"
        binary.write_bytes(b"\x00" * 100)

        call_count = 0

        def mock_run_script(**kwargs):
            nonlocal call_count
            call_count += 1
            target = kwargs.get("target", "")
            script = kwargs.get("script", "")
            if "Get-Service" in script and "ConvertTo-Json" in script:
                return MockScriptResult(success=True, output={"stdout": '{"installed": false}'})
            if "SUCCESS" in script or "Start-Service" in script:
                return MockScriptResult(success=True, output={"stdout": "SUCCESS"})
            return MockScriptResult(success=True, output={"stdout": "OK"})

        executor = MagicMock()
        executor.run_script.side_effect = mock_run_script

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        workstations = [
            {"hostname": f"WS{i:02d}", "ip_address": f"192.168.88.{100+i}"}
            for i in range(3)
        ]

        with patch.object(GoAgentDeployer, 'AGENT_BINARY_PATH', binary):
            results = await deployer.deploy_to_workstations(workstations, max_concurrent=2)

        assert len(results) == 3


class TestAgentStatusCheck:
    """Test agent status checking."""

    @pytest.mark.asyncio
    async def test_check_running_agent(self):
        """Check status of a running agent."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=True,
            output={"stdout": '{"installed": true, "status": "Running", "start_type": "Automatic", "version": "0.1.0"}'},
        )

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        status = await deployer.check_agent_status("WS01")

        assert status["installed"] is True
        assert status["status"] == "Running"

    @pytest.mark.asyncio
    async def test_check_agent_not_installed(self):
        """Check status when agent is not installed."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=True,
            output={"stdout": '{"installed": false}'},
        )

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        status = await deployer.check_agent_status("WS01")

        assert status["installed"] is False

    @pytest.mark.asyncio
    async def test_check_agent_connection_error(self):
        """Check status when WinRM connection fails."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=False,
            output={},
            error="Connection refused",
        )

        deployer = GoAgentDeployer(
            domain="northvalley.local",
            username="admin",
            password="test",
            appliance_addr="192.168.88.241:50051",
            executor=executor,
        )

        status = await deployer.check_agent_status("WS01")

        assert status["installed"] is False
        assert "error" in status
