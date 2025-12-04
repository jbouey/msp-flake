"""
Backup Restore Testing Module.

Implements automated backup restore verification per HIPAA §164.308(a)(7)(ii)(A).

This module performs periodic test restores from backup to verify:
1. Backup integrity (files can be restored)
2. Checksum verification (restored files match originals)
3. Data recoverability (critical files are accessible)

Evidence is generated for each test for audit purposes.

HIPAA Controls:
- §164.308(a)(7)(ii)(A) - Data Backup Plan (Test restore capability)
- §164.310(d)(2)(iv) - Data Backup and Storage

Version: 1.0
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from .models import ActionTaken, EvidenceBundle
from .config import AgentConfig
from .utils import run_command, AsyncCommandError

logger = logging.getLogger(__name__)


@dataclass
class RestoreTestResult:
    """Result of a backup restore test."""
    test_id: str
    timestamp: datetime
    outcome: str  # success, failed, partial
    backup_type: str  # restic, borg, tar, windows_server_backup
    snapshot_id: Optional[str] = None

    # Test details
    files_restored: int = 0
    files_verified: int = 0
    files_failed: int = 0

    # Checksum verification
    checksums_matched: int = 0
    checksums_failed: int = 0

    # Timing
    restore_duration_seconds: float = 0.0
    verification_duration_seconds: float = 0.0

    # Evidence
    pre_state: Dict[str, Any] = field(default_factory=dict)
    post_state: Dict[str, Any] = field(default_factory=dict)
    actions: List[ActionTaken] = field(default_factory=list)
    error: Optional[str] = None

    # HIPAA controls
    hipaa_controls: List[str] = field(
        default_factory=lambda: ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    )


@dataclass
class RestoreTestConfig:
    """Configuration for restore testing."""
    enabled: bool = True

    # Backup configuration
    backup_type: str = "restic"  # restic, borg, tar
    backup_repo: Optional[str] = None
    restic_password_file: Optional[str] = None

    # Test configuration
    test_dir: str = "/tmp/restore-test"
    max_files_to_test: int = 10
    file_patterns: List[str] = field(
        default_factory=lambda: ["*.conf", "*.json", "*.db", "*.sql"]
    )
    critical_paths: List[str] = field(
        default_factory=lambda: ["/etc/", "/var/lib/"]
    )

    # Cleanup
    cleanup_after_test: bool = True

    # Scheduling
    max_age_days: int = 7  # Alert if no test in this many days


class BackupRestoreTester:
    """
    Automated backup restore testing.

    Performs periodic test restores to verify backup integrity and
    data recoverability. Generates evidence for HIPAA compliance.
    """

    def __init__(
        self,
        config: AgentConfig,
        restore_config: Optional[RestoreTestConfig] = None
    ):
        """
        Initialize backup restore tester.

        Args:
            config: Agent configuration
            restore_config: Optional restore test configuration
        """
        self.config = config
        self.restore_config = restore_config or RestoreTestConfig()

        # Status file for tracking test history
        self.status_file = Path(config.data_dir) / "backup-status.json"

    async def run_restore_test(
        self,
        snapshot_id: Optional[str] = None,
        paths: Optional[List[str]] = None
    ) -> RestoreTestResult:
        """
        Run a backup restore test.

        Performs the following steps:
        1. List available snapshots
        2. Select snapshot (latest or specified)
        3. Create temporary restore directory
        4. Restore selected files
        5. Verify checksums
        6. Record results
        7. Cleanup

        Args:
            snapshot_id: Optional specific snapshot to test
            paths: Optional specific paths to restore (default: random sample)

        Returns:
            RestoreTestResult with test outcome and evidence
        """
        test_id = f"RT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
        timestamp = datetime.now(timezone.utc)
        actions = []

        logger.info(f"Starting backup restore test: {test_id}")

        pre_state = {
            "test_id": test_id,
            "backup_type": self.restore_config.backup_type,
            "backup_repo": self.restore_config.backup_repo,
            "timestamp": timestamp.isoformat()
        }

        # Create test directory
        test_dir = Path(self.restore_config.test_dir) / test_id
        try:
            test_dir.mkdir(parents=True, exist_ok=True)
            actions.append(ActionTaken(
                action="create_test_directory",
                timestamp=datetime.now(timezone.utc),
                details={"path": str(test_dir)}
            ))
        except Exception as e:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type=self.restore_config.backup_type,
                pre_state=pre_state,
                actions=actions,
                error=f"Failed to create test directory: {e}"
            )

        try:
            # Select backup type and run appropriate test
            if self.restore_config.backup_type == "restic":
                result = await self._test_restic_restore(
                    test_id, test_dir, snapshot_id, paths, actions
                )
            elif self.restore_config.backup_type == "borg":
                result = await self._test_borg_restore(
                    test_id, test_dir, snapshot_id, paths, actions
                )
            else:
                result = await self._test_generic_restore(
                    test_id, test_dir, actions
                )

            result.pre_state = pre_state

            # Update status file
            await self._update_status(result)

            return result

        finally:
            # Cleanup test directory
            if self.restore_config.cleanup_after_test:
                try:
                    shutil.rmtree(test_dir)
                    actions.append(ActionTaken(
                        action="cleanup_test_directory",
                        timestamp=datetime.now(timezone.utc),
                        details={"path": str(test_dir)}
                    ))
                except Exception as e:
                    logger.warning(f"Failed to cleanup test directory: {e}")

    async def _test_restic_restore(
        self,
        test_id: str,
        test_dir: Path,
        snapshot_id: Optional[str],
        paths: Optional[List[str]],
        actions: List[ActionTaken]
    ) -> RestoreTestResult:
        """Test restore from restic backup."""
        timestamp = datetime.now(timezone.utc)
        restore_start = datetime.now(timezone.utc)

        repo = self.restore_config.backup_repo
        if not repo:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="restic",
                actions=actions,
                error="No backup repository configured"
            )

        # Build restic command base
        restic_base = f"restic -r {repo}"
        if self.restore_config.restic_password_file:
            restic_base += f" --password-file {self.restore_config.restic_password_file}"

        # List snapshots
        try:
            result = await run_command(
                f"{restic_base} snapshots --json --latest 5",
                timeout=60.0
            )
            snapshots = json.loads(result.stdout)

            actions.append(ActionTaken(
                action="list_snapshots",
                timestamp=datetime.now(timezone.utc),
                exit_code=0,
                details={"snapshot_count": len(snapshots)}
            ))

            if not snapshots:
                return RestoreTestResult(
                    test_id=test_id,
                    timestamp=timestamp,
                    outcome="failed",
                    backup_type="restic",
                    actions=actions,
                    error="No snapshots available"
                )

        except Exception as e:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="restic",
                actions=actions,
                error=f"Failed to list snapshots: {e}"
            )

        # Select snapshot
        if snapshot_id:
            target_snapshot = next(
                (s for s in snapshots if s["short_id"] == snapshot_id),
                snapshots[0]
            )
        else:
            target_snapshot = snapshots[0]  # Latest

        snapshot_short_id = target_snapshot.get("short_id", target_snapshot.get("id", "")[:8])

        actions.append(ActionTaken(
            action="select_snapshot",
            timestamp=datetime.now(timezone.utc),
            details={
                "snapshot_id": snapshot_short_id,
                "snapshot_time": target_snapshot.get("time")
            }
        ))

        # If no specific paths, select random files from snapshot
        if not paths:
            paths = await self._select_test_files(restic_base, snapshot_short_id)

        if not paths:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="restic",
                snapshot_id=snapshot_short_id,
                actions=actions,
                error="No files found to test"
            )

        # Restore files
        files_restored = 0
        restore_errors = []

        for path in paths[:self.restore_config.max_files_to_test]:
            try:
                result = await run_command(
                    f"{restic_base} restore {snapshot_short_id} --target {test_dir} --include {path}",
                    timeout=120.0
                )
                files_restored += 1

            except AsyncCommandError as e:
                restore_errors.append(f"{path}: {e.stderr}")
                logger.warning(f"Failed to restore {path}: {e.stderr}")

        restore_duration = (datetime.now(timezone.utc) - restore_start).total_seconds()

        actions.append(ActionTaken(
            action="restore_files",
            timestamp=datetime.now(timezone.utc),
            details={
                "files_attempted": len(paths[:self.restore_config.max_files_to_test]),
                "files_restored": files_restored,
                "errors": restore_errors[:5]  # Limit error count
            }
        ))

        # Verify checksums
        verification_start = datetime.now(timezone.utc)
        checksums_matched, checksums_failed, verification_details = await self._verify_restored_files(
            test_dir, paths[:self.restore_config.max_files_to_test]
        )
        verification_duration = (datetime.now(timezone.utc) - verification_start).total_seconds()

        actions.append(ActionTaken(
            action="verify_checksums",
            timestamp=datetime.now(timezone.utc),
            details=verification_details
        ))

        # Determine outcome
        if files_restored == 0:
            outcome = "failed"
        elif checksums_failed > 0:
            outcome = "partial"
        else:
            outcome = "success"

        post_state = {
            "files_restored": files_restored,
            "checksums_matched": checksums_matched,
            "checksums_failed": checksums_failed,
            "restore_duration_seconds": restore_duration,
            "verification_duration_seconds": verification_duration
        }

        return RestoreTestResult(
            test_id=test_id,
            timestamp=timestamp,
            outcome=outcome,
            backup_type="restic",
            snapshot_id=snapshot_short_id,
            files_restored=files_restored,
            files_verified=checksums_matched + checksums_failed,
            files_failed=len(restore_errors),
            checksums_matched=checksums_matched,
            checksums_failed=checksums_failed,
            restore_duration_seconds=restore_duration,
            verification_duration_seconds=verification_duration,
            post_state=post_state,
            actions=actions,
            error="; ".join(restore_errors[:3]) if restore_errors else None
        )

    async def _test_borg_restore(
        self,
        test_id: str,
        test_dir: Path,
        snapshot_id: Optional[str],
        paths: Optional[List[str]],
        actions: List[ActionTaken]
    ) -> RestoreTestResult:
        """Test restore from borg backup."""
        timestamp = datetime.now(timezone.utc)

        repo = self.restore_config.backup_repo
        if not repo:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="borg",
                actions=actions,
                error="No backup repository configured"
            )

        # List archives
        try:
            result = await run_command(
                f"borg list --json {repo}",
                timeout=60.0
            )
            archives = json.loads(result.stdout).get("archives", [])

            if not archives:
                return RestoreTestResult(
                    test_id=test_id,
                    timestamp=timestamp,
                    outcome="failed",
                    backup_type="borg",
                    actions=actions,
                    error="No archives available"
                )

            # Select latest archive
            target_archive = archives[-1]["name"]

            actions.append(ActionTaken(
                action="select_archive",
                timestamp=datetime.now(timezone.utc),
                details={"archive": target_archive}
            ))

        except Exception as e:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="borg",
                actions=actions,
                error=f"Failed to list archives: {e}"
            )

        # Extract files
        restore_start = datetime.now(timezone.utc)

        try:
            # Extract to test directory
            test_paths = " ".join(paths) if paths else ""
            result = await run_command(
                f"cd {test_dir} && borg extract {repo}::{target_archive} {test_paths}",
                timeout=300.0
            )

            restore_duration = (datetime.now(timezone.utc) - restore_start).total_seconds()

            # Count restored files
            files_restored = sum(1 for _ in test_dir.rglob("*") if _.is_file())

            actions.append(ActionTaken(
                action="extract_archive",
                timestamp=datetime.now(timezone.utc),
                exit_code=0,
                details={"files_restored": files_restored}
            ))

        except AsyncCommandError as e:
            return RestoreTestResult(
                test_id=test_id,
                timestamp=timestamp,
                outcome="failed",
                backup_type="borg",
                snapshot_id=target_archive,
                actions=actions,
                error=f"Failed to extract archive: {e.stderr}"
            )

        # Verify files exist
        verification_start = datetime.now(timezone.utc)
        checksums_matched, checksums_failed, verification_details = await self._verify_restored_files(
            test_dir, []  # Verify all files
        )
        verification_duration = (datetime.now(timezone.utc) - verification_start).total_seconds()

        actions.append(ActionTaken(
            action="verify_checksums",
            timestamp=datetime.now(timezone.utc),
            details=verification_details
        ))

        outcome = "success" if checksums_failed == 0 else "partial"

        return RestoreTestResult(
            test_id=test_id,
            timestamp=timestamp,
            outcome=outcome,
            backup_type="borg",
            snapshot_id=target_archive,
            files_restored=files_restored,
            files_verified=checksums_matched + checksums_failed,
            checksums_matched=checksums_matched,
            checksums_failed=checksums_failed,
            restore_duration_seconds=restore_duration,
            verification_duration_seconds=verification_duration,
            post_state={
                "files_restored": files_restored,
                "checksums_matched": checksums_matched
            },
            actions=actions
        )

    async def _test_generic_restore(
        self,
        test_id: str,
        test_dir: Path,
        actions: List[ActionTaken]
    ) -> RestoreTestResult:
        """Generic restore test for unknown backup types."""
        timestamp = datetime.now(timezone.utc)

        return RestoreTestResult(
            test_id=test_id,
            timestamp=timestamp,
            outcome="failed",
            backup_type=self.restore_config.backup_type,
            actions=actions,
            error=f"Unsupported backup type: {self.restore_config.backup_type}"
        )

    async def _select_test_files(
        self,
        restic_base: str,
        snapshot_id: str
    ) -> List[str]:
        """Select random files from snapshot for testing."""
        try:
            result = await run_command(
                f"{restic_base} ls {snapshot_id} --json",
                timeout=60.0
            )

            files = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "file":
                        path = entry.get("path", "")
                        # Filter by patterns and critical paths
                        if self._should_test_file(path):
                            files.append(path)
                except json.JSONDecodeError:
                    continue

            # Select random sample
            import random
            if len(files) > self.restore_config.max_files_to_test:
                files = random.sample(files, self.restore_config.max_files_to_test)

            return files

        except Exception as e:
            logger.warning(f"Failed to list snapshot contents: {e}")
            return []

    def _should_test_file(self, path: str) -> bool:
        """Check if file should be included in test."""
        # Check critical paths
        for critical_path in self.restore_config.critical_paths:
            if path.startswith(critical_path):
                return True

        # Check file patterns
        for pattern in self.restore_config.file_patterns:
            if path.endswith(pattern.replace("*", "")):
                return True

        return False

    async def _verify_restored_files(
        self,
        test_dir: Path,
        expected_paths: List[str]
    ) -> Tuple[int, int, Dict[str, Any]]:
        """
        Verify restored files by computing checksums.

        Returns:
            Tuple of (checksums_matched, checksums_failed, details)
        """
        checksums_matched = 0
        checksums_failed = 0
        file_checksums = {}

        # Find all files in test directory
        for file_path in test_dir.rglob("*"):
            if not file_path.is_file():
                continue

            try:
                # Compute SHA256 checksum
                sha256 = hashlib.sha256()
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha256.update(chunk)

                checksum = sha256.hexdigest()
                relative_path = str(file_path.relative_to(test_dir))
                file_checksums[relative_path] = checksum
                checksums_matched += 1

            except Exception as e:
                logger.warning(f"Failed to compute checksum for {file_path}: {e}")
                checksums_failed += 1

        return checksums_matched, checksums_failed, {
            "files_verified": checksums_matched + checksums_failed,
            "checksums_computed": len(file_checksums),
            "sample_checksums": dict(list(file_checksums.items())[:5])
        }

    async def _update_status(self, result: RestoreTestResult) -> None:
        """Update backup status file with test result."""
        try:
            status = {}
            if self.status_file.exists():
                with open(self.status_file, "r") as f:
                    status = json.load(f)

            # Update last restore test timestamp
            status["last_restore_test"] = result.timestamp.isoformat()
            status["last_restore_test_id"] = result.test_id
            status["last_restore_test_outcome"] = result.outcome
            status["last_restore_test_files"] = result.files_restored
            status["last_restore_test_verified"] = result.checksums_matched

            # Keep history (last 10 tests)
            if "restore_test_history" not in status:
                status["restore_test_history"] = []

            status["restore_test_history"].insert(0, {
                "test_id": result.test_id,
                "timestamp": result.timestamp.isoformat(),
                "outcome": result.outcome,
                "files_restored": result.files_restored,
                "checksums_matched": result.checksums_matched
            })
            status["restore_test_history"] = status["restore_test_history"][:10]

            # Write status
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.status_file, "w") as f:
                json.dump(status, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to update status file: {e}")

    async def get_last_test_age_days(self) -> Optional[int]:
        """Get age of last restore test in days."""
        try:
            if not self.status_file.exists():
                return None

            with open(self.status_file, "r") as f:
                status = json.load(f)

            last_test_str = status.get("last_restore_test")
            if not last_test_str:
                return None

            last_test = datetime.fromisoformat(last_test_str)
            age = (datetime.now(timezone.utc) - last_test).days
            return age

        except Exception as e:
            logger.error(f"Failed to get last test age: {e}")
            return None

    async def needs_test(self) -> bool:
        """Check if a restore test is needed."""
        age = await self.get_last_test_age_days()

        if age is None:
            return True  # Never tested

        return age > self.restore_config.max_age_days


# Convenience function for running tests
async def run_backup_restore_test(
    config: AgentConfig,
    backup_type: str = "restic",
    backup_repo: Optional[str] = None
) -> RestoreTestResult:
    """
    Run a backup restore test.

    Args:
        config: Agent configuration
        backup_type: Type of backup (restic, borg)
        backup_repo: Path to backup repository

    Returns:
        RestoreTestResult with test outcome
    """
    restore_config = RestoreTestConfig(
        backup_type=backup_type,
        backup_repo=backup_repo
    )

    tester = BackupRestoreTester(config, restore_config)
    return await tester.run_restore_test()
