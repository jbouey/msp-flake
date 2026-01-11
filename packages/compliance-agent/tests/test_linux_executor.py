"""
Tests for Linux Executor and Runbooks.

Uses mocking to simulate SSH connections without requiring real Linux hosts.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from compliance_agent.runbooks.linux.executor import (
    LinuxTarget,
    LinuxExecutionResult,
    LinuxExecutor,
    execute_on_linux,
)
from compliance_agent.runbooks.linux.runbooks import (
    LinuxRunbook,
    RUNBOOKS,
    get_runbook,
    get_l1_runbooks,
    get_l2_runbooks,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def linux_target():
    """Create a test Linux target."""
    return LinuxTarget(
        hostname="192.168.1.100",
        port=22,
        username="testuser",
        password="testpass",
    )


@pytest.fixture
def linux_target_with_key():
    """Create a Linux target with SSH key."""
    return LinuxTarget(
        hostname="192.168.1.101",
        port=22,
        username="admin",
        private_key="-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----",
    )


@pytest.fixture
def mock_ssh_connection():
    """Create a mock SSH connection."""
    conn = AsyncMock()
    conn.is_closed.return_value = False

    # Mock run method
    result = MagicMock()
    result.stdout = "COMPLIANT\n"
    result.stderr = ""
    result.exit_status = 0
    conn.run = AsyncMock(return_value=result)

    return conn


@pytest.fixture
def executor():
    """Create an executor with no targets."""
    return LinuxExecutor()


# =============================================================================
# LINUX TARGET TESTS
# =============================================================================

class TestLinuxTarget:
    def test_create_basic_target(self):
        target = LinuxTarget(hostname="192.168.1.100")
        assert target.hostname == "192.168.1.100"
        assert target.port == 22
        assert target.username == "root"
        assert target.password is None
        assert target.private_key is None

    def test_create_target_with_password(self, linux_target):
        assert linux_target.hostname == "192.168.1.100"
        assert linux_target.username == "testuser"
        assert linux_target.password == "testpass"

    def test_create_target_with_key(self, linux_target_with_key):
        assert linux_target_with_key.username == "admin"
        assert "OPENSSH" in linux_target_with_key.private_key

    def test_target_to_dict(self, linux_target):
        d = linux_target.to_dict()
        assert d["hostname"] == "192.168.1.100"
        assert d["port"] == 22
        assert d["has_password"] is True
        assert d["has_private_key"] is False
        # Should not include actual password
        assert "password" not in d or d.get("password") is None


# =============================================================================
# EXECUTION RESULT TESTS
# =============================================================================

class TestLinuxExecutionResult:
    def test_create_result(self):
        result = LinuxExecutionResult(
            success=True,
            runbook_id="LIN-SSH-001",
            target="192.168.1.100",
            phase="detect",
            output={"stdout": "COMPLIANT"},
            duration_seconds=1.5,
        )
        assert result.success is True
        assert result.runbook_id == "LIN-SSH-001"
        assert result.exit_code == 0
        assert result.timestamp  # Auto-generated

    def test_result_generates_hash(self):
        result = LinuxExecutionResult(
            success=True,
            runbook_id="LIN-SSH-001",
            target="192.168.1.100",
            phase="detect",
            output={"stdout": "COMPLIANT"},
            duration_seconds=1.0,
        )
        assert result.output_hash
        assert len(result.output_hash) == 16

    def test_result_to_evidence(self):
        result = LinuxExecutionResult(
            success=True,
            runbook_id="LIN-SSH-001",
            target="192.168.1.100",
            phase="detect",
            output={"stdout": "COMPLIANT"},
            duration_seconds=1.0,
            hipaa_controls=["164.312(a)(1)"],
        )
        evidence = result.to_evidence()
        assert "execution_id" in evidence
        assert evidence["runbook_id"] == "LIN-SSH-001"
        assert evidence["hipaa_controls"] == ["164.312(a)(1)"]


# =============================================================================
# LINUX EXECUTOR TESTS
# =============================================================================

class TestLinuxExecutor:
    def test_create_executor(self, executor):
        assert len(executor.targets) == 0
        assert executor._default_retries == 2

    def test_add_target(self, executor, linux_target):
        executor.add_target(linux_target)
        assert linux_target.hostname in executor.targets

    def test_remove_target(self, executor, linux_target):
        executor.add_target(linux_target)
        executor.remove_target(linux_target.hostname)
        assert linux_target.hostname not in executor.targets

    @pytest.mark.asyncio
    async def test_execute_script_success(self, executor, linux_target, mock_ssh_connection):
        """Test successful script execution with mocked SSH."""
        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            result = await executor.execute_script(
                linux_target,
                "echo 'hello'",
                timeout=10,
                retries=1
            )

        assert result.success is True
        assert "COMPLIANT" in result.output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_execute_script_failure(self, executor, linux_target):
        """Test script execution failure."""
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.stdout = "ERROR"
        mock_result.stderr = "Command not found"
        mock_result.exit_status = 1
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.is_closed.return_value = False

        with patch.object(executor, '_get_connection', return_value=mock_conn):
            result = await executor.execute_script(
                linux_target,
                "nonexistent_command",
                timeout=10,
                retries=0
            )

        assert result.success is False
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_execute_script_timeout(self, executor, linux_target):
        """Test script execution timeout."""
        mock_conn = AsyncMock()
        mock_conn.is_closed.return_value = False

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(10)

        mock_conn.run = slow_run

        with patch.object(executor, '_get_connection', return_value=mock_conn):
            result = await executor.execute_script(
                linux_target,
                "sleep 100",
                timeout=0.1,
                retries=0
            )

        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_detect_distro_ubuntu(self, executor, linux_target, mock_ssh_connection):
        """Test distro detection for Ubuntu."""
        mock_result = MagicMock()
        mock_result.stdout = "ubuntu\n"
        mock_result.stderr = ""
        mock_result.exit_status = 0
        mock_ssh_connection.run = AsyncMock(return_value=mock_result)

        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            distro = await executor.detect_distro(linux_target)

        assert distro == "ubuntu"
        assert executor._distro_cache[linux_target.hostname] == "ubuntu"

    @pytest.mark.asyncio
    async def test_detect_distro_rhel(self, executor, linux_target, mock_ssh_connection):
        """Test distro detection for RHEL/CentOS."""
        mock_result = MagicMock()
        mock_result.stdout = "centos\n"
        mock_result.stderr = ""
        mock_result.exit_status = 0
        mock_ssh_connection.run = AsyncMock(return_value=mock_result)

        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            distro = await executor.detect_distro(linux_target)

        # CentOS should normalize to rhel
        assert distro == "rhel"

    @pytest.mark.asyncio
    async def test_run_runbook_success(self, executor, linux_target, mock_ssh_connection):
        """Test running a full runbook."""
        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            with patch.object(executor, 'detect_distro', return_value="ubuntu"):
                results = await executor.run_runbook(
                    linux_target,
                    "LIN-SSH-001",
                    phases=["detect"]
                )

        assert len(results) >= 1
        assert results[0].runbook_id == "LIN-SSH-001"

    @pytest.mark.asyncio
    async def test_check_target_health(self, executor, linux_target, mock_ssh_connection):
        """Test health check."""
        mock_result = MagicMock()
        mock_result.stdout = '{"hostname": "testhost", "healthy": true}'
        mock_result.stderr = ""
        mock_result.exit_status = 0
        mock_ssh_connection.run = AsyncMock(return_value=mock_result)

        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            health = await executor.check_target_health(linux_target)

        assert health.get("healthy") is True or "hostname" in health


# =============================================================================
# LINUX RUNBOOK TESTS
# =============================================================================

class TestLinuxRunbooks:
    def test_runbooks_exist(self):
        """Verify runbooks are registered."""
        assert len(RUNBOOKS) >= 15
        assert "LIN-SSH-001" in RUNBOOKS
        assert "LIN-FW-001" in RUNBOOKS

    def test_get_runbook(self):
        """Test retrieving a runbook by ID."""
        rb = get_runbook("LIN-SSH-001")
        assert rb is not None
        assert rb.name == "SSH Root Login Disabled"
        assert rb.severity == "high"

    def test_get_nonexistent_runbook(self):
        """Test retrieving non-existent runbook."""
        rb = get_runbook("FAKE-001")
        assert rb is None

    def test_l1_runbooks(self):
        """Test getting L1 auto-heal runbooks."""
        l1 = get_l1_runbooks()
        assert len(l1) > 0
        for rb in l1:
            assert rb.l1_auto_heal is True

    def test_l2_runbooks(self):
        """Test getting L2 LLM-eligible runbooks."""
        l2 = get_l2_runbooks()
        assert len(l2) > 0
        for rb in l2:
            assert rb.l2_llm_eligible is True
            assert rb.l1_auto_heal is False

    def test_runbook_has_detect_script(self):
        """All runbooks should have detection scripts."""
        for rb_id, rb in RUNBOOKS.items():
            assert rb.detect_script, f"{rb_id} missing detect_script"
            assert len(rb.detect_script) > 10

    def test_runbook_has_hipaa_controls(self):
        """All runbooks should have HIPAA controls."""
        for rb_id, rb in RUNBOOKS.items():
            assert rb.hipaa_controls, f"{rb_id} missing hipaa_controls"
            assert len(rb.hipaa_controls) >= 1

    def test_ssh_runbook_details(self):
        """Test SSH runbook structure."""
        rb = get_runbook("LIN-SSH-001")
        assert "PermitRootLogin" in rb.detect_script
        assert rb.remediate_script is not None
        assert "sed" in rb.remediate_script
        assert rb.l1_auto_heal is True

    def test_firewall_runbook_distro_specific(self):
        """Test firewall runbook has distro-specific scripts."""
        rb = get_runbook("LIN-FW-001")
        assert rb.remediate_ubuntu is not None
        assert rb.remediate_rhel is not None
        assert "ufw" in rb.remediate_ubuntu
        assert "firewalld" in rb.remediate_rhel

    def test_runbook_to_dict(self):
        """Test runbook serialization."""
        rb = get_runbook("LIN-SSH-001")
        d = rb.to_dict()
        assert d["id"] == "LIN-SSH-001"
        assert d["severity"] == "high"
        assert "164.312" in str(d["hipaa_controls"])


# =============================================================================
# INTEGRATION TESTS (with mocking)
# =============================================================================

class TestLinuxIntegration:
    @pytest.mark.asyncio
    async def test_full_detection_flow(self, executor, linux_target, mock_ssh_connection):
        """Test full detection flow with multiple runbooks."""
        executor.add_target(linux_target)

        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            with patch.object(executor, 'detect_distro', return_value="ubuntu"):
                results = await executor.run_all_checks(linux_target)

        assert len(results) > 0
        assert "LIN-SSH-001" in results

    @pytest.mark.asyncio
    async def test_execute_on_linux_helper(self, mock_ssh_connection):
        """Test convenience function exists and has correct signature."""
        # Just verify the function is importable and has the expected parameters
        import inspect
        sig = inspect.signature(execute_on_linux)
        params = list(sig.parameters.keys())
        assert "hostname" in params
        assert "username" in params
        assert "runbook_id" in params


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_connection_retry(self, executor, linux_target):
        """Test retry logic on connection failure."""
        call_count = 0

        async def failing_connection(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection refused")
            # Third attempt succeeds
            mock_conn = AsyncMock()
            mock_result = MagicMock()
            mock_result.stdout = "OK"
            mock_result.stderr = ""
            mock_result.exit_status = 0
            mock_conn.run = AsyncMock(return_value=mock_result)
            mock_conn.is_closed.return_value = False
            return mock_conn

        with patch.object(executor, '_get_connection', side_effect=failing_connection):
            result = await executor.execute_script(
                linux_target,
                "echo test",
                timeout=10,
                retries=3,
                retry_delay=0.01
            )

        # Should have retried
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_invalidate_connection(self, executor, linux_target, mock_ssh_connection):
        """Test connection invalidation."""
        with patch.object(executor, '_get_connection', return_value=mock_ssh_connection):
            await executor.execute_script(linux_target, "echo test", retries=0)

        # Manually add to cache for test
        executor._connection_cache[linux_target.hostname] = mock_ssh_connection

        executor.invalidate_connection(linux_target.hostname)
        assert linux_target.hostname not in executor._connection_cache
