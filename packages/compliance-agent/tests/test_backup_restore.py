"""
Tests for Backup Restore Testing Module.

Tests the backup restore verification functionality per HIPAA §164.308(a)(7)(ii)(A).
"""

import pytest
import asyncio
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from compliance_agent.backup_restore_test import (
    BackupRestoreTester,
    RestoreTestConfig,
    RestoreTestResult,
    run_backup_restore_test
)
from compliance_agent.config import AgentConfig


class TestRestoreTestConfig:
    """Test RestoreTestConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RestoreTestConfig()

        assert config.enabled is True
        assert config.backup_type == "restic"
        assert config.backup_repo is None
        assert config.test_dir == "/tmp/restore-test"
        assert config.max_files_to_test == 10
        assert config.cleanup_after_test is True
        assert config.max_age_days == 7

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RestoreTestConfig(
            backup_type="borg",
            backup_repo="/backup/repo",
            max_files_to_test=5,
            max_age_days=14
        )

        assert config.backup_type == "borg"
        assert config.backup_repo == "/backup/repo"
        assert config.max_files_to_test == 5
        assert config.max_age_days == 14

    def test_file_patterns(self):
        """Test file patterns configuration."""
        config = RestoreTestConfig(
            file_patterns=["*.sql", "*.db", "*.conf"]
        )

        assert "*.sql" in config.file_patterns
        assert "*.db" in config.file_patterns
        assert "*.conf" in config.file_patterns

    def test_critical_paths(self):
        """Test critical paths configuration."""
        config = RestoreTestConfig(
            critical_paths=["/etc/", "/var/lib/mysql/"]
        )

        assert "/etc/" in config.critical_paths
        assert "/var/lib/mysql/" in config.critical_paths


class TestRestoreTestResult:
    """Test RestoreTestResult dataclass."""

    def test_create_result(self):
        """Test creating a restore test result."""
        result = RestoreTestResult(
            test_id="RT-20251204120000-abc123",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic",
            snapshot_id="abc123",
            files_restored=5,
            checksums_matched=5
        )

        assert result.test_id == "RT-20251204120000-abc123"
        assert result.outcome == "success"
        assert result.files_restored == 5
        assert result.checksums_matched == 5
        assert result.checksums_failed == 0

    def test_failed_result(self):
        """Test creating a failed restore test result."""
        result = RestoreTestResult(
            test_id="RT-20251204120000-def456",
            timestamp=datetime.now(timezone.utc),
            outcome="failed",
            backup_type="restic",
            error="No snapshots available"
        )

        assert result.outcome == "failed"
        assert result.error == "No snapshots available"

    def test_partial_result(self):
        """Test creating a partial restore test result."""
        result = RestoreTestResult(
            test_id="RT-20251204120000-ghi789",
            timestamp=datetime.now(timezone.utc),
            outcome="partial",
            backup_type="restic",
            files_restored=5,
            checksums_matched=3,
            checksums_failed=2
        )

        assert result.outcome == "partial"
        assert result.checksums_matched == 3
        assert result.checksums_failed == 2

    def test_hipaa_controls(self):
        """Test HIPAA control references."""
        result = RestoreTestResult(
            test_id="RT-test",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic"
        )

        assert "164.308(a)(7)(ii)(A)" in result.hipaa_controls
        assert "164.310(d)(2)(iv)" in result.hipaa_controls


class TestBackupRestoreTester:
    """Test BackupRestoreTester class."""

    @pytest.fixture
    def agent_config(self, tmp_path):
        """Create mock agent config."""
        config = MagicMock(spec=AgentConfig)
        config.data_dir = tmp_path
        config.evidence_dir = tmp_path / "evidence"
        return config

    @pytest.fixture
    def restore_config(self, tmp_path):
        """Create restore test config."""
        return RestoreTestConfig(
            backup_type="restic",
            backup_repo="/tmp/test-repo",
            test_dir=str(tmp_path / "restore-test"),
            cleanup_after_test=True
        )

    def test_init(self, agent_config, restore_config):
        """Test tester initialization."""
        tester = BackupRestoreTester(agent_config, restore_config)

        assert tester.config == agent_config
        assert tester.restore_config == restore_config
        assert tester.status_file.parent == agent_config.data_dir

    def test_should_test_file_critical_path(self, agent_config, restore_config):
        """Test file selection for critical paths."""
        tester = BackupRestoreTester(agent_config, restore_config)

        assert tester._should_test_file("/etc/nginx/nginx.conf") is True
        assert tester._should_test_file("/var/lib/postgresql/data.db") is True
        assert tester._should_test_file("/home/user/random.txt") is False

    def test_should_test_file_pattern(self, agent_config, restore_config):
        """Test file selection by pattern."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Default patterns include *.conf, *.json, *.db, *.sql
        assert tester._should_test_file("/opt/app/config.conf") is True
        assert tester._should_test_file("/opt/app/data.json") is True
        assert tester._should_test_file("/opt/app/data.db") is True
        assert tester._should_test_file("/opt/app/backup.sql") is True
        assert tester._should_test_file("/opt/app/README.md") is False

    @pytest.mark.asyncio
    async def test_get_last_test_age_no_file(self, agent_config, restore_config):
        """Test getting last test age when no status file exists."""
        tester = BackupRestoreTester(agent_config, restore_config)

        age = await tester.get_last_test_age_days()
        assert age is None

    @pytest.mark.asyncio
    async def test_get_last_test_age_with_file(self, agent_config, restore_config, tmp_path):
        """Test getting last test age from status file."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Create status file with test from 5 days ago
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
        status = {
            "last_restore_test": five_days_ago.isoformat()
        }

        tester.status_file.parent.mkdir(parents=True, exist_ok=True)
        with open(tester.status_file, "w") as f:
            json.dump(status, f)

        age = await tester.get_last_test_age_days()
        assert age == 5

    @pytest.mark.asyncio
    async def test_needs_test_never_tested(self, agent_config, restore_config):
        """Test needs_test returns True when never tested."""
        tester = BackupRestoreTester(agent_config, restore_config)

        needs = await tester.needs_test()
        assert needs is True

    @pytest.mark.asyncio
    async def test_needs_test_recent(self, agent_config, restore_config):
        """Test needs_test returns False for recent test."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Create status file with test from 2 days ago
        two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        status = {
            "last_restore_test": two_days_ago.isoformat()
        }

        tester.status_file.parent.mkdir(parents=True, exist_ok=True)
        with open(tester.status_file, "w") as f:
            json.dump(status, f)

        needs = await tester.needs_test()
        assert needs is False

    @pytest.mark.asyncio
    async def test_needs_test_old(self, agent_config, restore_config):
        """Test needs_test returns True for old test."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Create status file with test from 10 days ago
        ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
        status = {
            "last_restore_test": ten_days_ago.isoformat()
        }

        tester.status_file.parent.mkdir(parents=True, exist_ok=True)
        with open(tester.status_file, "w") as f:
            json.dump(status, f)

        needs = await tester.needs_test()
        assert needs is True  # max_age_days default is 7


class TestRestoreTestExecution:
    """Test restore test execution with mocked commands."""

    @pytest.fixture
    def agent_config(self, tmp_path):
        """Create mock agent config."""
        config = MagicMock(spec=AgentConfig)
        config.data_dir = tmp_path
        config.evidence_dir = tmp_path / "evidence"
        return config

    @pytest.fixture
    def restore_config(self, tmp_path):
        """Create restore test config."""
        return RestoreTestConfig(
            backup_type="restic",
            backup_repo="/tmp/test-repo",
            test_dir=str(tmp_path / "restore-test"),
            restic_password_file="/tmp/restic-password",
            cleanup_after_test=False  # Keep for inspection
        )

    @pytest.mark.asyncio
    async def test_run_restore_test_no_repo(self, agent_config, tmp_path):
        """Test restore test fails without repo configured."""
        config = RestoreTestConfig(
            backup_type="restic",
            backup_repo=None,  # No repo
            test_dir=str(tmp_path / "restore-test")
        )

        tester = BackupRestoreTester(agent_config, config)
        result = await tester.run_restore_test()

        assert result.outcome == "failed"
        assert "No backup repository configured" in result.error

    @pytest.mark.asyncio
    async def test_run_restore_test_unsupported_type(self, agent_config, tmp_path):
        """Test restore test fails for unsupported backup type."""
        config = RestoreTestConfig(
            backup_type="unsupported",
            backup_repo="/some/repo",
            test_dir=str(tmp_path / "restore-test")
        )

        tester = BackupRestoreTester(agent_config, config)
        result = await tester.run_restore_test()

        assert result.outcome == "failed"
        assert "Unsupported backup type" in result.error

    @pytest.mark.asyncio
    async def test_run_restore_test_creates_test_dir(self, agent_config, restore_config, tmp_path):
        """Test that restore test creates test directory."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Mock run_command to avoid actual command execution
        with patch('compliance_agent.backup_restore_test.run_command') as mock_cmd:
            # Return empty snapshots
            mock_cmd.return_value = MagicMock(stdout="[]")

            result = await tester.run_restore_test()

        # Should have tried to list snapshots
        assert mock_cmd.called

    @pytest.mark.asyncio
    async def test_verify_restored_files(self, agent_config, restore_config, tmp_path):
        """Test file verification with checksums."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Create test files
        test_dir = tmp_path / "verify-test"
        test_dir.mkdir(parents=True)

        (test_dir / "file1.txt").write_text("content1")
        (test_dir / "file2.txt").write_text("content2")
        (test_dir / "subdir").mkdir()
        (test_dir / "subdir" / "file3.txt").write_text("content3")

        matched, failed, details = await tester._verify_restored_files(test_dir, [])

        assert matched == 3
        assert failed == 0
        assert details["files_verified"] == 3
        assert len(details["sample_checksums"]) == 3


class TestRestoreTestStatusTracking:
    """Test restore test status file tracking."""

    @pytest.fixture
    def agent_config(self, tmp_path):
        """Create mock agent config."""
        config = MagicMock(spec=AgentConfig)
        config.data_dir = tmp_path
        return config

    @pytest.fixture
    def restore_config(self, tmp_path):
        """Create restore test config."""
        return RestoreTestConfig(
            backup_type="restic",
            backup_repo="/tmp/test-repo",
            test_dir=str(tmp_path / "restore-test")
        )

    @pytest.mark.asyncio
    async def test_update_status_creates_file(self, agent_config, restore_config):
        """Test status update creates file."""
        tester = BackupRestoreTester(agent_config, restore_config)

        result = RestoreTestResult(
            test_id="RT-test-001",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic",
            files_restored=5,
            checksums_matched=5
        )

        await tester._update_status(result)

        assert tester.status_file.exists()

        with open(tester.status_file) as f:
            status = json.load(f)

        assert status["last_restore_test_id"] == "RT-test-001"
        assert status["last_restore_test_outcome"] == "success"

    @pytest.mark.asyncio
    async def test_update_status_maintains_history(self, agent_config, restore_config):
        """Test status update maintains history."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Add multiple results
        for i in range(5):
            result = RestoreTestResult(
                test_id=f"RT-test-{i:03d}",
                timestamp=datetime.now(timezone.utc),
                outcome="success",
                backup_type="restic",
                files_restored=5,
                checksums_matched=5
            )
            await tester._update_status(result)

        with open(tester.status_file) as f:
            status = json.load(f)

        # Should have history
        assert "restore_test_history" in status
        assert len(status["restore_test_history"]) == 5

        # Most recent should be first
        assert status["restore_test_history"][0]["test_id"] == "RT-test-004"

    @pytest.mark.asyncio
    async def test_update_status_limits_history(self, agent_config, restore_config):
        """Test status update limits history to 10 entries."""
        tester = BackupRestoreTester(agent_config, restore_config)

        # Add more than 10 results
        for i in range(15):
            result = RestoreTestResult(
                test_id=f"RT-test-{i:03d}",
                timestamp=datetime.now(timezone.utc),
                outcome="success",
                backup_type="restic",
                files_restored=5,
                checksums_matched=5
            )
            await tester._update_status(result)

        with open(tester.status_file) as f:
            status = json.load(f)

        # Should be limited to 10
        assert len(status["restore_test_history"]) == 10


class TestConvenienceFunction:
    """Test the run_backup_restore_test convenience function."""

    @pytest.fixture
    def agent_config(self, tmp_path):
        """Create mock agent config."""
        config = MagicMock(spec=AgentConfig)
        config.data_dir = tmp_path
        return config

    @pytest.mark.asyncio
    async def test_run_backup_restore_test_no_repo(self, agent_config):
        """Test convenience function without repo."""
        result = await run_backup_restore_test(
            config=agent_config,
            backup_type="restic",
            backup_repo=None
        )

        assert result.outcome == "failed"
        assert "No backup repository configured" in result.error


class TestHIPAACompliance:
    """Test HIPAA compliance aspects of restore testing."""

    def test_result_includes_hipaa_controls(self):
        """Test that results include HIPAA control references."""
        result = RestoreTestResult(
            test_id="RT-test",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic"
        )

        # Must include Data Backup Plan control
        assert "164.308(a)(7)(ii)(A)" in result.hipaa_controls
        # Must include Data Backup and Storage control
        assert "164.310(d)(2)(iv)" in result.hipaa_controls

    def test_result_includes_timing_evidence(self):
        """Test that results include timing for evidence."""
        result = RestoreTestResult(
            test_id="RT-test",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic",
            restore_duration_seconds=120.5,
            verification_duration_seconds=30.2
        )

        assert result.restore_duration_seconds == 120.5
        assert result.verification_duration_seconds == 30.2

    def test_result_includes_action_trail(self):
        """Test that results can include action trail for audit."""
        from compliance_agent.models import ActionTaken

        actions = [
            ActionTaken(
                action="list_snapshots",
                timestamp=datetime.now(timezone.utc),
                details={"count": 5}
            ),
            ActionTaken(
                action="restore_files",
                timestamp=datetime.now(timezone.utc),
                details={"count": 3}
            )
        ]

        result = RestoreTestResult(
            test_id="RT-test",
            timestamp=datetime.now(timezone.utc),
            outcome="success",
            backup_type="restic",
            actions=actions
        )

        assert len(result.actions) == 2
        assert result.actions[0].action == "list_snapshots"
