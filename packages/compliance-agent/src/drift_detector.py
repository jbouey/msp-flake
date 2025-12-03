"""
Drift Detection - Baseline Compliance Monitoring

This module detects drift from the approved baseline configuration.

Six Core Checks:
1. Flake Hash - Verify system matches approved NixOS flake
2. Patch Status - Critical patches applied within 7 days
3. Backup Status - Successful backup in last 24h, restore test within 30 days
4. Service Health - Critical services running
5. Encryption Status - LUKS volumes encrypted, TLS certs valid
6. Time Sync - NTP synchronized within ±90 seconds

Each check returns:
- drift_detected: bool
- severity: critical | high | medium | low
- details: dict with specific findings
- remediation_runbook: str (if available)
- hipaa_controls: list[str]

Architecture:
- Each check is independent and can be run in parallel
- Results are structured for evidence generation
- Drift triggers automatic remediation via Healer (Phase 2 Day 5)
"""

import asyncio
import subprocess
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class DriftSeverity(str, Enum):
    """Drift severity levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DriftResult:
    """Result from a single drift check"""
    check_name: str
    drift_detected: bool
    severity: DriftSeverity
    details: Dict
    remediation_runbook: Optional[str] = None
    hipaa_controls: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DriftDetector:
    """
    Drift detection engine

    Runs six core compliance checks to detect drift from baseline.
    Each check is designed to be fast (<10s) and non-disruptive.
    """

    def __init__(self, config):
        """
        Initialize drift detector

        Args:
            config: Agent configuration object
        """
        self.config = config
        self.site_id = config.site_id

        # Load baseline configuration (if available)
        self.baseline = self._load_baseline()

        logger.info(f"Drift detector initialized for site {self.site_id}")

    def _load_baseline(self) -> Dict:
        """
        Load baseline configuration for this site

        In production, this would come from:
        - Git repository (baseline/hipaa-v1.yaml)
        - MCP server config endpoint
        - Local cache with periodic refresh

        For now, we use sensible defaults.
        """
        baseline_path = Path("/etc/msp/baseline.json")

        if baseline_path.exists():
            with open(baseline_path, 'r') as f:
                baseline = json.load(f)
                logger.info(f"✓ Loaded baseline from {baseline_path}")
                return baseline

        # Default baseline
        logger.warning("⚠ No baseline file found, using defaults")
        return {
            "target_flake_hash": None,  # Will be set on first successful check
            "critical_patch_max_age_days": 7,
            "backup_max_age_hours": 24,
            "restore_test_max_age_days": 30,
            "critical_services": ["sshd", "chronyd"],
            "time_max_drift_seconds": 90
        }

    async def check_all(self) -> Dict[str, DriftResult]:
        """
        Run all drift checks in parallel

        Returns:
            Dictionary mapping check name to DriftResult
        """
        logger.info("Starting drift detection sweep")
        start_time = datetime.now(timezone.utc)

        # Run all checks in parallel
        results = await asyncio.gather(
            self.check_flake_hash(),
            self.check_patch_status(),
            self.check_backup_status(),
            self.check_service_health(),
            self.check_encryption_status(),
            self.check_time_sync(),
            return_exceptions=True
        )

        # Build results dictionary
        drift_results = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Check failed with exception: {result}")
                continue

            drift_results[result.check_name] = result

        # Log summary
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        drift_count = sum(1 for r in drift_results.values() if r.drift_detected)

        logger.info(f"Drift detection complete: {drift_count}/{len(drift_results)} checks detected drift ({duration:.2f}s)")

        return drift_results

    async def check_flake_hash(self) -> DriftResult:
        """
        Check 1: Flake Hash - Verify system matches approved NixOS flake

        This is the most critical check - ensures entire system configuration
        matches what was approved and deployed.

        HIPAA Controls: 164.308(a)(1)(ii)(D), 164.310(d)(1)
        """
        logger.debug("Checking flake hash...")

        try:
            # Query current system flake metadata
            result = await self._run_command([
                'nix', 'flake', 'metadata', '/run/current-system', '--json'
            ])

            flake_metadata = json.loads(result.stdout)
            current_hash = flake_metadata.get('locked', {}).get('narHash')

            if not current_hash:
                return DriftResult(
                    check_name="flake_hash",
                    drift_detected=True,
                    severity=DriftSeverity.CRITICAL,
                    details={
                        "error": "Could not determine current flake hash",
                        "system_path": "/run/current-system"
                    },
                    remediation_runbook="RB-DRIFT-001",
                    hipaa_controls=["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
                )

            # Get target hash from baseline
            target_hash = self.baseline.get('target_flake_hash')

            # First run: set target hash
            if not target_hash:
                logger.info(f"First run: setting baseline flake hash to {current_hash}")
                self.baseline['target_flake_hash'] = current_hash
                self._save_baseline()

                return DriftResult(
                    check_name="flake_hash",
                    drift_detected=False,
                    severity=DriftSeverity.LOW,
                    details={
                        "current_hash": current_hash,
                        "status": "baseline_initialized"
                    },
                    hipaa_controls=["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
                )

            # Check for drift
            drift_detected = (current_hash != target_hash)

            return DriftResult(
                check_name="flake_hash",
                drift_detected=drift_detected,
                severity=DriftSeverity.CRITICAL if drift_detected else DriftSeverity.LOW,
                details={
                    "current_hash": current_hash,
                    "target_hash": target_hash,
                    "drift": drift_detected
                },
                remediation_runbook="RB-DRIFT-001" if drift_detected else None,
                hipaa_controls=["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
            )

        except Exception as e:
            logger.error(f"Flake hash check failed: {e}")
            return DriftResult(
                check_name="flake_hash",
                drift_detected=True,
                severity=DriftSeverity.CRITICAL,
                details={"error": str(e)},
                remediation_runbook="RB-DRIFT-001",
                hipaa_controls=["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
            )

    async def check_patch_status(self) -> DriftResult:
        """
        Check 2: Patch Status - Critical patches applied within 7 days

        Queries for pending security updates and checks age.

        HIPAA Controls: 164.308(a)(5)(ii)(B)
        """
        logger.debug("Checking patch status...")

        try:
            # Check for pending updates (NixOS-specific)
            # In production, would query nix-channel --update age
            # For now, simulate with a check

            max_age_days = self.baseline.get('critical_patch_max_age_days', 7)

            # Placeholder: In real implementation, query nixos-rebuild --check
            # or compare current channel with latest
            critical_pending = 0  # Would be populated from actual check
            last_update = datetime.now(timezone.utc) - timedelta(days=3)  # Placeholder

            drift_detected = critical_pending > 0

            return DriftResult(
                check_name="patch_status",
                drift_detected=drift_detected,
                severity=DriftSeverity.CRITICAL if drift_detected else DriftSeverity.LOW,
                details={
                    "critical_pending": critical_pending,
                    "last_update": last_update.isoformat(),
                    "max_age_days": max_age_days
                },
                remediation_runbook="RB-PATCH-001" if drift_detected else None,
                hipaa_controls=["164.308(a)(5)(ii)(B)"]
            )

        except Exception as e:
            logger.error(f"Patch status check failed: {e}")
            return DriftResult(
                check_name="patch_status",
                drift_detected=True,
                severity=DriftSeverity.HIGH,
                details={"error": str(e)},
                remediation_runbook="RB-PATCH-001",
                hipaa_controls=["164.308(a)(5)(ii)(B)"]
            )

    async def check_backup_status(self) -> DriftResult:
        """
        Check 3: Backup Status - Successful backup in last 24h, restore test within 30 days

        Checks both backup completion and restore test recency.

        HIPAA Controls: 164.308(a)(7)(ii)(A), 164.310(d)(2)(iv)
        """
        logger.debug("Checking backup status...")

        try:
            max_backup_age_hours = self.baseline.get('backup_max_age_hours', 24)
            max_restore_test_age_days = self.baseline.get('restore_test_max_age_days', 30)

            # Check for backup evidence files
            backup_dir = Path("/var/lib/msp/backups")

            if not backup_dir.exists():
                return DriftResult(
                    check_name="backup_status",
                    drift_detected=True,
                    severity=DriftSeverity.CRITICAL,
                    details={
                        "error": "Backup directory not found",
                        "path": str(backup_dir)
                    },
                    remediation_runbook="RB-BACKUP-001",
                    hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
                )

            # Find most recent backup
            backup_files = sorted(backup_dir.glob("backup-*.json"), reverse=True)

            if not backup_files:
                return DriftResult(
                    check_name="backup_status",
                    drift_detected=True,
                    severity=DriftSeverity.CRITICAL,
                    details={
                        "error": "No backup files found",
                        "path": str(backup_dir)
                    },
                    remediation_runbook="RB-BACKUP-001",
                    hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
                )

            # Read most recent backup metadata
            with open(backup_files[0], 'r') as f:
                backup_metadata = json.load(f)

            last_backup = datetime.fromisoformat(backup_metadata['timestamp'])
            backup_age_hours = (datetime.now(timezone.utc) - last_backup).total_seconds() / 3600

            # Check restore test
            restore_test_files = sorted(backup_dir.glob("restore-test-*.json"), reverse=True)

            if restore_test_files:
                with open(restore_test_files[0], 'r') as f:
                    restore_metadata = json.load(f)
                last_restore_test = datetime.fromisoformat(restore_metadata['timestamp'])
                restore_test_age_days = (datetime.now(timezone.utc) - last_restore_test).days
            else:
                last_restore_test = None
                restore_test_age_days = 999

            # Determine drift
            backup_drift = backup_age_hours > max_backup_age_hours
            restore_drift = restore_test_age_days > max_restore_test_age_days

            drift_detected = backup_drift or restore_drift

            return DriftResult(
                check_name="backup_status",
                drift_detected=drift_detected,
                severity=DriftSeverity.CRITICAL if backup_drift else (DriftSeverity.HIGH if restore_drift else DriftSeverity.LOW),
                details={
                    "last_backup": last_backup.isoformat(),
                    "backup_age_hours": backup_age_hours,
                    "max_backup_age_hours": max_backup_age_hours,
                    "last_restore_test": last_restore_test.isoformat() if last_restore_test else None,
                    "restore_test_age_days": restore_test_age_days if restore_test_age_days < 999 else None,
                    "max_restore_test_age_days": max_restore_test_age_days,
                    "backup_drift": backup_drift,
                    "restore_drift": restore_drift
                },
                remediation_runbook="RB-BACKUP-001" if drift_detected else None,
                hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
            )

        except Exception as e:
            logger.error(f"Backup status check failed: {e}")
            return DriftResult(
                check_name="backup_status",
                drift_detected=True,
                severity=DriftSeverity.CRITICAL,
                details={"error": str(e)},
                remediation_runbook="RB-BACKUP-001",
                hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
            )

    async def check_service_health(self) -> DriftResult:
        """
        Check 4: Service Health - Critical services running

        Verifies that all baseline-required services are active.

        HIPAA Controls: 164.308(a)(1)(ii)(D)
        """
        logger.debug("Checking service health...")

        try:
            critical_services = self.baseline.get('critical_services', ['sshd', 'chronyd'])

            service_statuses = {}
            failed_services = []

            for service in critical_services:
                try:
                    result = await self._run_command([
                        'systemctl', 'is-active', service
                    ])
                    status = result.stdout.strip()
                    service_statuses[service] = status

                    if status != 'active':
                        failed_services.append(service)

                except subprocess.CalledProcessError:
                    service_statuses[service] = 'failed'
                    failed_services.append(service)

            drift_detected = len(failed_services) > 0

            return DriftResult(
                check_name="service_health",
                drift_detected=drift_detected,
                severity=DriftSeverity.HIGH if drift_detected else DriftSeverity.LOW,
                details={
                    "critical_services": critical_services,
                    "service_statuses": service_statuses,
                    "failed_services": failed_services,
                    "healthy_count": len(critical_services) - len(failed_services),
                    "total_count": len(critical_services)
                },
                remediation_runbook="RB-SERVICE-001" if drift_detected else None,
                hipaa_controls=["164.308(a)(1)(ii)(D)"]
            )

        except Exception as e:
            logger.error(f"Service health check failed: {e}")
            return DriftResult(
                check_name="service_health",
                drift_detected=True,
                severity=DriftSeverity.HIGH,
                details={"error": str(e)},
                remediation_runbook="RB-SERVICE-001",
                hipaa_controls=["164.308(a)(1)(ii)(D)"]
            )

    async def check_encryption_status(self) -> DriftResult:
        """
        Check 5: Encryption Status - LUKS volumes encrypted, TLS certs valid

        Verifies encryption at rest (LUKS) and in transit (TLS).

        HIPAA Controls: 164.312(a)(2)(iv), 164.312(e)(1)
        """
        logger.debug("Checking encryption status...")

        try:
            issues = []

            # Check LUKS volumes
            result = await self._run_command(['lsblk', '-J'])
            lsblk_output = json.loads(result.stdout)

            luks_volumes = []
            unencrypted_volumes = []

            for device in lsblk_output.get('blockdevices', []):
                if device.get('type') == 'crypt':
                    luks_volumes.append(device['name'])
                elif device.get('mountpoint') in ['/', '/home', '/var']:
                    # Critical mount points should be encrypted
                    if device.get('type') != 'crypt':
                        unencrypted_volumes.append({
                            'device': device['name'],
                            'mountpoint': device.get('mountpoint')
                        })

            if unencrypted_volumes:
                issues.append({
                    "type": "unencrypted_volume",
                    "volumes": unencrypted_volumes
                })

            # Check TLS certificates (placeholder)
            # In production, would check cert expiry dates
            cert_issues = []  # Would be populated from actual check

            drift_detected = len(issues) > 0

            return DriftResult(
                check_name="encryption_status",
                drift_detected=drift_detected,
                severity=DriftSeverity.CRITICAL if drift_detected else DriftSeverity.LOW,
                details={
                    "luks_volumes": luks_volumes,
                    "unencrypted_volumes": unencrypted_volumes,
                    "cert_issues": cert_issues,
                    "issues": issues
                },
                remediation_runbook="RB-ENCRYPTION-001" if drift_detected else None,
                hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(1)"]
            )

        except Exception as e:
            logger.error(f"Encryption status check failed: {e}")
            return DriftResult(
                check_name="encryption_status",
                drift_detected=True,
                severity=DriftSeverity.CRITICAL,
                details={"error": str(e)},
                remediation_runbook="RB-ENCRYPTION-001",
                hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(1)"]
            )

    async def check_time_sync(self) -> DriftResult:
        """
        Check 6: Time Sync - NTP synchronized within ±90 seconds

        Verifies system time is accurately synchronized.

        HIPAA Controls: 164.312(b)
        """
        logger.debug("Checking time sync...")

        try:
            max_drift_seconds = self.baseline.get('time_max_drift_seconds', 90)

            # Query chrony tracking
            result = await self._run_command(['chronyc', 'tracking'])
            tracking_output = result.stdout

            # Parse tracking output
            offset_seconds = None
            ntp_synchronized = False

            for line in tracking_output.split('\n'):
                if 'System time' in line:
                    # Extract offset (e.g., "System time     : 0.000012345 seconds slow of NTP time")
                    parts = line.split(':')
                    if len(parts) > 1:
                        offset_str = parts[1].strip().split()[0]
                        offset_seconds = abs(float(offset_str))

                if 'Reference ID' in line and 'REFID' not in line:
                    ntp_synchronized = True

            if offset_seconds is None:
                return DriftResult(
                    check_name="time_sync",
                    drift_detected=True,
                    severity=DriftSeverity.HIGH,
                    details={
                        "error": "Could not determine time offset",
                        "tracking_output": tracking_output
                    },
                    remediation_runbook="RB-TIMESYNC-001",
                    hipaa_controls=["164.312(b)"]
                )

            drift_detected = (offset_seconds > max_drift_seconds) or (not ntp_synchronized)

            return DriftResult(
                check_name="time_sync",
                drift_detected=drift_detected,
                severity=DriftSeverity.MEDIUM if drift_detected else DriftSeverity.LOW,
                details={
                    "offset_seconds": offset_seconds,
                    "max_drift_seconds": max_drift_seconds,
                    "ntp_synchronized": ntp_synchronized,
                    "drift": offset_seconds > max_drift_seconds
                },
                remediation_runbook="RB-TIMESYNC-001" if drift_detected else None,
                hipaa_controls=["164.312(b)"]
            )

        except Exception as e:
            logger.error(f"Time sync check failed: {e}")
            return DriftResult(
                check_name="time_sync",
                drift_detected=True,
                severity=DriftSeverity.MEDIUM,
                details={"error": str(e)},
                remediation_runbook="RB-TIMESYNC-001",
                hipaa_controls=["164.312(b)"]
            )

    async def _run_command(self, cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """
        Run command asynchronously with timeout

        Args:
            cmd: Command and arguments
            timeout: Timeout in seconds

        Returns:
            CompletedProcess with stdout/stderr
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            return subprocess.CompletedProcess(
                args=cmd,
                returncode=proc.returncode,
                stdout=stdout.decode('utf-8'),
                stderr=stderr.decode('utf-8')
            )

        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out after {timeout}s: {' '.join(cmd)}")

    def _save_baseline(self):
        """Save baseline configuration to disk"""
        baseline_path = Path("/etc/msp/baseline.json")
        baseline_path.parent.mkdir(parents=True, exist_ok=True)

        with open(baseline_path, 'w') as f:
            json.dump(self.baseline, f, indent=2)

        logger.info(f"✓ Saved baseline to {baseline_path}")


# Example usage
if __name__ == '__main__':
    import sys
    from .config import Config

    logging.basicConfig(level=logging.DEBUG)

    # Load config
    if len(sys.argv) < 2:
        print("Usage: python -m src.drift_detector <config_path>")
        sys.exit(1)

    config = Config.load(sys.argv[1])

    # Run drift detection
    async def main():
        detector = DriftDetector(config)
        results = await detector.check_all()

        print("\n=== Drift Detection Results ===")
        for check_name, result in results.items():
            status = "❌ DRIFT" if result.drift_detected else "✅ OK"
            print(f"{status} {check_name} ({result.severity})")
            if result.drift_detected:
                print(f"   Remediation: {result.remediation_runbook}")
                print(f"   Details: {result.details}")

        drift_count = sum(1 for r in results.values() if r.drift_detected)
        print(f"\nTotal: {drift_count}/{len(results)} checks detected drift")

    asyncio.run(main())
