"""
Portal control checks for compliance monitoring.

Maps drift detection results to the 8 portal controls:
1. endpoint_drift - Configuration drift from baseline
2. patch_freshness - Critical patch timeliness
3. backup_success - Backup job completion
4. mfa_coverage - MFA enabled for all users
5. privileged_access - Admin accounts properly controlled
6. git_protections - Branch protections in place
7. secrets_hygiene - No plaintext secrets
8. storage_posture - Storage encryption/access

Each control returns a ControlResult that can be sent to the portal API.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import AgentConfig
from .drift import DriftDetector
from .models import DriftResult
from .utils import run_command

logger = logging.getLogger(__name__)


@dataclass
class ControlResult:
    """Result of a single control check."""
    rule_id: str
    status: str  # pass, warn, fail
    checked_at: datetime
    scope_summary: str = ""
    auto_fix_triggered: bool = False
    fix_duration_sec: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
            "scope_summary": self.scope_summary,
            "auto_fix_triggered": self.auto_fix_triggered,
            "fix_duration_sec": self.fix_duration_sec
        }


class PortalControlChecker:
    """
    Runs the 8 core portal controls and returns results.

    Maps existing drift checks and adds new checks for:
    - MFA coverage
    - Privileged access review
    - Git branch protections
    - Secrets hygiene
    """

    def __init__(self, config: AgentConfig, drift_detector: Optional[DriftDetector] = None):
        """
        Initialize portal control checker.

        Args:
            config: Agent configuration
            drift_detector: Optional existing drift detector to reuse
        """
        self.config = config
        self.drift_detector = drift_detector or DriftDetector(config)

    async def check_all(self) -> List[ControlResult]:
        """
        Run all 8 portal control checks.

        Returns:
            List of ControlResult objects
        """
        logger.info("Running portal control checks")

        # Run all checks concurrently
        results = await asyncio.gather(
            self.check_endpoint_drift(),
            self.check_patch_freshness(),
            self.check_backup_success(),
            self.check_mfa_coverage(),
            self.check_privileged_access(),
            self.check_git_protections(),
            self.check_secrets_hygiene(),
            self.check_storage_posture(),
            return_exceptions=True
        )

        # Filter out exceptions and log them
        control_results = []
        control_names = [
            "endpoint_drift", "patch_freshness", "backup_success",
            "mfa_coverage", "privileged_access", "git_protections",
            "secrets_hygiene", "storage_posture"
        ]

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Control check {control_names[i]} failed: {result}")
                # Return a failed result for this control
                control_results.append(ControlResult(
                    rule_id=control_names[i],
                    status="fail",
                    checked_at=datetime.now(timezone.utc),
                    scope_summary=f"Check failed: {str(result)[:50]}"
                ))
            elif isinstance(result, ControlResult):
                control_results.append(result)

        passing = sum(1 for r in control_results if r.status == "pass")
        logger.info(f"Portal control checks complete: {passing}/8 passing")

        return control_results

    async def check_endpoint_drift(self) -> ControlResult:
        """
        Check for endpoint configuration drift.

        Maps from: firewall_baseline, encryption checks
        """
        now = datetime.now(timezone.utc)

        # Run firewall and encryption drift checks
        firewall_result = await self.drift_detector.check_firewall_baseline()
        encryption_result = await self.drift_detector.check_encryption()

        # Aggregate results
        drifted_items = []
        if firewall_result.drifted:
            drifted_items.append("firewall")
        if encryption_result.drifted:
            drifted_items.append("encryption")

        if drifted_items:
            return ControlResult(
                rule_id="endpoint_drift",
                status="fail" if len(drifted_items) > 1 else "warn",
                checked_at=now,
                scope_summary=f"Drift detected: {', '.join(drifted_items)}"
            )

        return ControlResult(
            rule_id="endpoint_drift",
            status="pass",
            checked_at=now,
            scope_summary="No configuration drift"
        )

    async def check_patch_freshness(self) -> ControlResult:
        """
        Check critical patch timeliness.

        Maps from: patching drift check
        """
        now = datetime.now(timezone.utc)

        patching_result = await self.drift_detector.check_patching()

        if patching_result.drifted:
            severity = patching_result.severity
            age_days = patching_result.pre_state.get("generation_age_days", "unknown")
            return ControlResult(
                rule_id="patch_freshness",
                status="fail" if severity in ["critical", "high"] else "warn",
                checked_at=now,
                scope_summary=f"Generation age: {age_days} days"
            )

        return ControlResult(
            rule_id="patch_freshness",
            status="pass",
            checked_at=now,
            scope_summary="Patches up to date"
        )

    async def check_backup_success(self) -> ControlResult:
        """
        Check backup success and restore testing.

        Maps from: backup_verification drift check
        """
        now = datetime.now(timezone.utc)

        backup_result = await self.drift_detector.check_backup_verification()

        if backup_result.drifted:
            age_hours = backup_result.pre_state.get("backup_age_hours", "unknown")
            restore_days = backup_result.pre_state.get("restore_test_age_days", "unknown")
            return ControlResult(
                rule_id="backup_success",
                status="fail" if backup_result.severity in ["critical", "high"] else "warn",
                checked_at=now,
                scope_summary=f"Backup: {age_hours}h ago, restore test: {restore_days}d ago"
            )

        return ControlResult(
            rule_id="backup_success",
            status="pass",
            checked_at=now,
            scope_summary="Backup and restore verified"
        )

    async def check_mfa_coverage(self) -> ControlResult:
        """
        Check MFA coverage for human accounts.

        For NixOS appliance, checks:
        - SSH key-only authentication (password auth disabled)
        - No password-based local accounts
        """
        now = datetime.now(timezone.utc)

        issues = []

        # Check SSH config for password auth
        try:
            result = await run_command(
                "grep -E '^PasswordAuthentication' /etc/ssh/sshd_config 2>/dev/null || echo 'PasswordAuthentication yes'",
                timeout=5
            )
            if "yes" in result.stdout.lower():
                issues.append("SSH password auth enabled")
        except Exception as e:
            logger.debug(f"SSH config check failed: {e}")

        # Check for password-enabled accounts (non-locked)
        try:
            result = await run_command(
                "cat /etc/shadow | grep -v ':\\*:' | grep -v ':!:' | grep -v ':!!:' | wc -l",
                timeout=5
            )
            pwd_accounts = int(result.stdout.strip())
            if pwd_accounts > 1:  # root may have a password
                issues.append(f"{pwd_accounts} password accounts")
        except Exception as e:
            logger.debug(f"Shadow check failed: {e}")

        if issues:
            return ControlResult(
                rule_id="mfa_coverage",
                status="warn",
                checked_at=now,
                scope_summary="; ".join(issues)
            )

        return ControlResult(
            rule_id="mfa_coverage",
            status="pass",
            checked_at=now,
            scope_summary="Key-only SSH, no password accounts"
        )

    async def check_privileged_access(self) -> ControlResult:
        """
        Check privileged access controls.

        Verifies:
        - Sudo requires authentication
        - Root login restricted
        - Limited sudo users
        """
        now = datetime.now(timezone.utc)

        issues = []
        sudo_users = 0

        # Check for NOPASSWD in sudoers
        try:
            result = await run_command(
                "grep -r 'NOPASSWD' /etc/sudoers /etc/sudoers.d/ 2>/dev/null | wc -l",
                timeout=5
            )
            nopasswd_count = int(result.stdout.strip())
            if nopasswd_count > 0:
                issues.append(f"{nopasswd_count} NOPASSWD rules")
        except Exception as e:
            logger.debug(f"Sudoers check failed: {e}")

        # Check root SSH login
        try:
            result = await run_command(
                "grep -E '^PermitRootLogin' /etc/ssh/sshd_config 2>/dev/null || echo 'PermitRootLogin yes'",
                timeout=5
            )
            if "yes" in result.stdout.lower():
                issues.append("Root SSH login enabled")
        except Exception as e:
            logger.debug(f"Root login check failed: {e}")

        # Count wheel/sudo group members
        try:
            result = await run_command(
                "getent group wheel sudo 2>/dev/null | cut -d: -f4 | tr ',' '\\n' | sort -u | wc -l",
                timeout=5
            )
            sudo_users = int(result.stdout.strip())
        except Exception as e:
            logger.debug(f"Sudo users check failed: {e}")

        if issues:
            return ControlResult(
                rule_id="privileged_access",
                status="warn" if len(issues) == 1 else "fail",
                checked_at=now,
                scope_summary=f"{sudo_users} sudo users; {'; '.join(issues)}"
            )

        return ControlResult(
            rule_id="privileged_access",
            status="pass",
            checked_at=now,
            scope_summary=f"{sudo_users} sudo users, controls enforced"
        )

    async def check_git_protections(self) -> ControlResult:
        """
        Check Git branch protection.

        For appliance, verifies:
        - /etc/nixos is a git repo
        - Has remote configured
        - No uncommitted changes
        """
        now = datetime.now(timezone.utc)

        nixos_dir = Path("/etc/nixos")

        if not nixos_dir.exists():
            return ControlResult(
                rule_id="git_protections",
                status="pass",
                checked_at=now,
                scope_summary="N/A - not a NixOS system"
            )

        issues = []

        # Check if git repo
        try:
            result = await run_command(
                "cd /etc/nixos && git rev-parse --git-dir 2>/dev/null",
                timeout=5
            )
            if result.return_code != 0:
                issues.append("Not a git repo")
        except Exception:
            issues.append("Not a git repo")

        if not issues:
            # Check for remote
            try:
                result = await run_command(
                    "cd /etc/nixos && git remote -v | wc -l",
                    timeout=5
                )
                if int(result.stdout.strip()) == 0:
                    issues.append("No git remote")
            except Exception:
                pass

            # Check for uncommitted changes
            try:
                result = await run_command(
                    "cd /etc/nixos && git status --porcelain | wc -l",
                    timeout=5
                )
                changes = int(result.stdout.strip())
                if changes > 0:
                    issues.append(f"{changes} uncommitted changes")
            except Exception:
                pass

        if issues:
            return ControlResult(
                rule_id="git_protections",
                status="warn",
                checked_at=now,
                scope_summary="; ".join(issues)
            )

        return ControlResult(
            rule_id="git_protections",
            status="pass",
            checked_at=now,
            scope_summary="Config tracked in git with remote"
        )

    async def check_secrets_hygiene(self) -> ControlResult:
        """
        Check for exposed secrets.

        Scans for:
        - Plaintext passwords in config files
        - API keys in environment
        - SSH keys with weak permissions
        """
        now = datetime.now(timezone.utc)

        issues = []

        # Check for common secret patterns in /etc
        secret_patterns = [
            r'password\s*=\s*["\'][^"\']+["\']',
            r'api_key\s*=\s*["\'][^"\']+["\']',
            r'secret\s*=\s*["\'][^"\']+["\']',
        ]

        try:
            # Only check common config locations
            result = await run_command(
                "grep -rliE 'password|api_key|secret' /etc/nixos/ 2>/dev/null | head -5",
                timeout=10
            )
            if result.stdout.strip():
                files = result.stdout.strip().split('\n')
                issues.append(f"{len(files)} files with potential secrets")
        except Exception as e:
            logger.debug(f"Secret scan failed: {e}")

        # Check SSH key permissions
        try:
            result = await run_command(
                "find /root/.ssh /home/*/.ssh -name 'id_*' ! -name '*.pub' -perm /077 2>/dev/null | wc -l",
                timeout=5
            )
            weak_keys = int(result.stdout.strip())
            if weak_keys > 0:
                issues.append(f"{weak_keys} SSH keys with weak permissions")
        except Exception:
            pass

        if issues:
            return ControlResult(
                rule_id="secrets_hygiene",
                status="warn",
                checked_at=now,
                scope_summary="; ".join(issues)
            )

        return ControlResult(
            rule_id="secrets_hygiene",
            status="pass",
            checked_at=now,
            scope_summary="No exposed secrets detected"
        )

    async def check_storage_posture(self) -> ControlResult:
        """
        Check storage encryption and access.

        Maps from: encryption drift check + additional checks
        """
        now = datetime.now(timezone.utc)

        # Reuse encryption check
        encryption_result = await self.drift_detector.check_encryption()

        issues = []

        if encryption_result.drifted:
            issues.append("Encryption not enabled")

        # Check for world-readable sensitive directories
        try:
            result = await run_command(
                "find /var/lib -maxdepth 2 -type d -perm /007 2>/dev/null | wc -l",
                timeout=5
            )
            world_readable = int(result.stdout.strip())
            if world_readable > 0:
                issues.append(f"{world_readable} world-readable data dirs")
        except Exception:
            pass

        if issues:
            return ControlResult(
                rule_id="storage_posture",
                status="fail" if "Encryption" in str(issues) else "warn",
                checked_at=now,
                scope_summary="; ".join(issues)
            )

        return ControlResult(
            rule_id="storage_posture",
            status="pass",
            checked_at=now,
            scope_summary="Storage encrypted, permissions correct"
        )
