"""
Drift detection module for compliance monitoring.

Checks for deviations from baseline configuration across 6 categories:
1. Patching - NixOS generation comparison
2. AV/EDR Health - Service active + binary hash
3. Backup Verification - Timestamp + checksum
4. Logging Continuity - Services up, canary reaches spool
5. Firewall Baseline - Ruleset hash comparison
6. Encryption - LUKS status

Each check returns a DriftResult with pre_state, drifted boolean,
severity level, and recommended remediation action.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

from .models import DriftResult
from .config import AgentConfig
from .utils import run_command

logger = logging.getLogger(__name__)


class DriftDetector:
    """
    Drift detection engine for compliance monitoring.

    Performs 6 categories of checks to detect deviations from baseline:
    - Patching status (NixOS generation comparison)
    - AV/EDR health (service + binary integrity)
    - Backup verification (timestamp + checksum)
    - Logging continuity (service health + canary)
    - Firewall baseline (ruleset hash)
    - Encryption status (LUKS volume checks)

    Each check returns a DriftResult with severity and remediation guidance.
    """

    def __init__(self, config: AgentConfig):
        """
        Initialize drift detector.

        Args:
            config: Agent configuration
        """
        self.config = config
        self.baseline_path = config.baseline_path
        self._baseline_cache: Optional[Dict[str, Any]] = None

    async def _load_baseline(self) -> Dict[str, Any]:
        """
        Load baseline configuration from YAML file.

        Returns:
            Baseline configuration dict
        """
        if self._baseline_cache is not None:
            return self._baseline_cache

        baseline_file = Path(self.baseline_path)

        if not baseline_file.exists():
            logger.warning(f"Baseline file not found: {self.baseline_path}")
            return {}

        try:
            import yaml
            with open(baseline_file, 'r') as f:
                self._baseline_cache = yaml.safe_load(f) or {}
            return self._baseline_cache
        except Exception as e:
            logger.error(f"Failed to load baseline: {e}")
            return {}

    async def check_all(self) -> List[DriftResult]:
        """
        Run all drift detection checks.

        Returns:
            List of DriftResult objects, one per check
        """
        logger.info("Starting drift detection checks")

        # Run all checks concurrently
        results = await asyncio.gather(
            self.check_patching(),
            self.check_av_edr_health(),
            self.check_backup_verification(),
            self.check_logging_continuity(),
            self.check_firewall_baseline(),
            self.check_encryption(),
            return_exceptions=True
        )

        # Filter out exceptions and log them
        drift_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Check {i} failed with exception: {result}")
            elif isinstance(result, DriftResult):
                drift_results.append(result)

        logger.info(f"Drift detection complete: {len(drift_results)} checks, "
                   f"{sum(1 for r in drift_results if r.drifted)} drifted")

        return drift_results

    async def check_patching(self) -> DriftResult:
        """
        Check patching status by comparing NixOS generations.

        Detects drift if:
        - Current generation differs from baseline
        - Generation is older than max_age (from baseline)

        Returns:
            DriftResult with generation comparison
        """
        logger.debug("Checking patching status")

        baseline = await self._load_baseline()
        patching_config = baseline.get('patching', {})

        # Get current NixOS generation
        try:
            result = await run_command(
                'readlink /run/current-system',
                timeout=5
            )
            current_system = result.stdout.strip()

            # Get generation list
            result = await run_command(
                'nixos-rebuild list-generations | tail -1',
                timeout=5
            )
            gen_output = result.stdout.strip()

            # Parse: "123   2025-01-15 10:23:45"
            current_gen = None
            if gen_output:
                current_gen = int(gen_output.split()[0])

        except Exception as e:
            logger.error(f"Failed to get NixOS generation: {e}")
            return DriftResult(
                check="patching",
                drifted=False,
                pre_state={"error": str(e)},
                severity="critical",
                recommended_action="investigate_nixos_generation_failure",
                hipaa_controls=["164.308(a)(5)(ii)(B)"]
            )

        # Get baseline generation
        baseline_gen = patching_config.get('expected_generation')
        max_age_days = patching_config.get('max_generation_age_days', 30)

        pre_state = {
            "current_generation": current_gen,
            "current_system": current_system,
            "baseline_generation": baseline_gen,
            "max_age_days": max_age_days
        }

        # Check for drift
        drifted = False
        severity = "low"
        recommended_action = None

        if baseline_gen and current_gen != baseline_gen:
            drifted = True
            severity = "medium"
            recommended_action = "update_to_baseline_generation"
            logger.warning(f"Patching drift: current={current_gen}, baseline={baseline_gen}")

        # Check generation age
        try:
            result = await run_command(
                f'nixos-rebuild list-generations | grep "^{current_gen} "',
                timeout=5
            )
            if result.stdout:
                parts = result.stdout.strip().split()
                if len(parts) >= 3:
                    gen_date_str = f"{parts[1]} {parts[2]}"
                    gen_date = datetime.strptime(gen_date_str, "%Y-%m-%d %H:%M:%S")
                    age_days = (datetime.now() - gen_date).days

                    pre_state["generation_age_days"] = age_days

                    if age_days > max_age_days:
                        drifted = True
                        severity = "high" if age_days > max_age_days * 2 else "medium"
                        recommended_action = "apply_system_updates"
                        logger.warning(f"Generation age drift: {age_days} days (max {max_age_days})")

        except Exception as e:
            logger.debug(f"Could not determine generation age: {e}")

        return DriftResult(
            check="patching",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.308(a)(5)(ii)(B)"]
        )

    async def check_av_edr_health(self) -> DriftResult:
        """
        Check AV/EDR health status.

        Detects drift if:
        - Service is not active
        - Binary hash doesn't match baseline

        Returns:
            DriftResult with service and binary status
        """
        logger.debug("Checking AV/EDR health")

        baseline = await self._load_baseline()
        av_config = baseline.get('av_edr', {})

        service_name = av_config.get('service_name', 'clamav')
        binary_path = av_config.get('binary_path', '/usr/bin/clamscan')
        expected_hash = av_config.get('binary_hash')

        pre_state = {
            "service_name": service_name,
            "binary_path": binary_path
        }

        # Check service status
        service_active = False
        try:
            result = await run_command(
                f'systemctl is-active {service_name}',
                timeout=5
            )
            service_active = result.stdout.strip() == 'active'
            pre_state["service_active"] = service_active
        except Exception as e:
            logger.error(f"Failed to check service status: {e}")
            pre_state["service_active"] = False
            pre_state["service_error"] = str(e)

        # Check binary hash
        binary_hash = None
        hash_matches = True

        if Path(binary_path).exists():
            try:
                with open(binary_path, 'rb') as f:
                    binary_hash = hashlib.sha256(f.read()).hexdigest()
                pre_state["binary_hash"] = binary_hash

                if expected_hash:
                    hash_matches = (binary_hash == expected_hash)
                    pre_state["hash_matches"] = hash_matches
            except Exception as e:
                logger.error(f"Failed to hash binary: {e}")
                pre_state["hash_error"] = str(e)
        else:
            pre_state["binary_exists"] = False

        # Determine drift
        drifted = False
        severity = "low"
        recommended_action = None

        if not service_active:
            drifted = True
            severity = "critical"
            recommended_action = "restart_av_service"
            logger.warning(f"AV/EDR drift: service {service_name} not active")

        if expected_hash and not hash_matches:
            drifted = True
            severity = "high"
            recommended_action = "verify_av_binary_integrity"
            logger.warning(f"AV/EDR drift: binary hash mismatch")

        return DriftResult(
            check="av_edr_health",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(b)"]
        )

    async def check_backup_verification(self) -> DriftResult:
        """
        Check backup status and verification.

        Detects drift if:
        - Last backup older than max_age
        - Backup checksum doesn't match
        - No recent test restore

        Returns:
            DriftResult with backup status
        """
        logger.debug("Checking backup verification")

        baseline = await self._load_baseline()
        backup_config = baseline.get('backup', {})

        max_age_hours = backup_config.get('max_age_hours', 24)
        backup_status_file = backup_config.get('status_file',
                                               '/var/lib/compliance-agent/backup-status.json')

        pre_state = {
            "max_age_hours": max_age_hours,
            "status_file": backup_status_file
        }

        # Read backup status
        backup_status = {}
        status_path = Path(backup_status_file)

        if status_path.exists():
            try:
                with open(status_path, 'r') as f:
                    backup_status = json.load(f)

                pre_state["last_backup"] = backup_status.get('last_backup')
                pre_state["last_restore_test"] = backup_status.get('last_restore_test')
                pre_state["checksum"] = backup_status.get('checksum')
            except Exception as e:
                logger.error(f"Failed to read backup status: {e}")
                pre_state["read_error"] = str(e)
        else:
            pre_state["status_file_exists"] = False

        # Check backup age
        backup_age_hours = None
        last_backup_str = backup_status.get('last_backup')

        if last_backup_str:
            try:
                last_backup = datetime.fromisoformat(last_backup_str)
                backup_age_hours = (datetime.utcnow() - last_backup).total_seconds() / 3600
                pre_state["backup_age_hours"] = round(backup_age_hours, 2)
            except Exception as e:
                logger.error(f"Failed to parse backup timestamp: {e}")

        # Check restore test age
        restore_test_age_days = None
        last_restore_str = backup_status.get('last_restore_test')

        if last_restore_str:
            try:
                last_restore = datetime.fromisoformat(last_restore_str)
                restore_test_age_days = (datetime.utcnow() - last_restore).days
                pre_state["restore_test_age_days"] = restore_test_age_days
            except Exception as e:
                logger.error(f"Failed to parse restore test timestamp: {e}")

        # Determine drift
        drifted = False
        severity = "low"
        recommended_action = None

        if backup_age_hours is None or backup_age_hours > max_age_hours:
            drifted = True
            severity = "critical" if backup_age_hours and backup_age_hours > max_age_hours * 2 else "high"
            recommended_action = "run_backup_job"
            logger.warning(f"Backup drift: age {backup_age_hours}h exceeds max {max_age_hours}h")

        if restore_test_age_days is None or restore_test_age_days > 30:
            drifted = True
            if severity == "low":
                severity = "medium"
            recommended_action = "run_restore_test"
            logger.warning(f"Backup drift: no restore test in {restore_test_age_days} days")

        return DriftResult(
            check="backup_verification",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
        )

    async def check_logging_continuity(self) -> DriftResult:
        """
        Check logging continuity.

        Detects drift if:
        - Logging services not active
        - Canary log message not reaching spool

        Returns:
            DriftResult with logging status
        """
        logger.debug("Checking logging continuity")

        baseline = await self._load_baseline()
        logging_config = baseline.get('logging', {})

        services = logging_config.get('services', ['rsyslog', 'systemd-journald'])

        pre_state = {
            "services": services
        }

        # Check logging services
        services_status = {}
        all_active = True

        for service in services:
            try:
                result = await run_command(
                    f'systemctl is-active {service}',
                    timeout=5
                )
                is_active = result.stdout.strip() == 'active'
                services_status[service] = is_active
                if not is_active:
                    all_active = False
            except Exception as e:
                logger.error(f"Failed to check service {service}: {e}")
                services_status[service] = False
                all_active = False

        pre_state["services_status"] = services_status

        # Check canary log
        canary_found = False

        try:
            result = await run_command(
                'journalctl -u compliance-agent --since "2 hours ago" | '
                'grep "CANARY:" | tail -1',
                timeout=10
            )

            if result.stdout:
                canary_found = True
                pre_state["canary_found"] = True
            else:
                pre_state["canary_found"] = False

        except Exception as e:
            logger.error(f"Failed to check canary log: {e}")
            pre_state["canary_error"] = str(e)

        # Determine drift
        drifted = False
        severity = "low"
        recommended_action = None

        if not all_active:
            drifted = True
            severity = "high"
            recommended_action = "restart_logging_services"
            inactive = [s for s, active in services_status.items() if not active]
            logger.warning(f"Logging drift: services inactive: {inactive}")

        if not canary_found:
            drifted = True
            if severity == "low":
                severity = "medium"
            recommended_action = "investigate_log_delivery"
            logger.warning("Logging drift: canary not found in recent logs")

        return DriftResult(
            check="logging_continuity",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.312(b)", "164.308(a)(1)(ii)(D)"]
        )

    async def check_firewall_baseline(self) -> DriftResult:
        """
        Check firewall ruleset against baseline.

        Detects drift if:
        - Ruleset hash doesn't match baseline
        - Firewall service not active

        Returns:
            DriftResult with firewall status
        """
        logger.debug("Checking firewall baseline")

        baseline = await self._load_baseline()
        firewall_config = baseline.get('firewall', {})

        expected_hash = firewall_config.get('ruleset_hash')
        service_name = firewall_config.get('service', 'nftables')

        pre_state = {
            "service": service_name,
            "expected_hash": expected_hash
        }

        # Check firewall service
        service_active = False
        try:
            result = await run_command(
                f'systemctl is-active {service_name}',
                timeout=5
            )
            service_active = result.stdout.strip() == 'active'
            pre_state["service_active"] = service_active
        except Exception as e:
            logger.error(f"Failed to check firewall service: {e}")
            pre_state["service_error"] = str(e)

        # Get current ruleset and hash
        current_hash = None
        try:
            result = await run_command(
                'nft list ruleset',
                timeout=10
            )
            ruleset = result.stdout
            current_hash = hashlib.sha256(ruleset.encode()).hexdigest()
            pre_state["current_hash"] = current_hash
            pre_state["ruleset_lines"] = len(ruleset.split('\n'))
        except Exception as e:
            logger.error(f"Failed to get firewall ruleset: {e}")
            pre_state["ruleset_error"] = str(e)

        # Determine drift
        drifted = False
        severity = "low"
        recommended_action = None

        if not service_active:
            drifted = True
            severity = "critical"
            recommended_action = "start_firewall_service"
            logger.warning(f"Firewall drift: service {service_name} not active")

        if expected_hash and current_hash and current_hash != expected_hash:
            drifted = True
            severity = "high"
            recommended_action = "restore_firewall_baseline"
            logger.warning(f"Firewall drift: ruleset hash mismatch")

        return DriftResult(
            check="firewall_baseline",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.312(a)(1)", "164.312(e)(1)"]
        )

    async def check_encryption(self) -> DriftResult:
        """
        Check encryption status.

        Detects drift if:
        - LUKS volumes not encrypted

        Returns:
            DriftResult with encryption status
        """
        logger.debug("Checking encryption status")

        baseline = await self._load_baseline()
        encryption_config = baseline.get('encryption', {})

        required_volumes = encryption_config.get('luks_volumes', [])

        pre_state = {
            "required_volumes": required_volumes
        }

        # Check LUKS volumes
        luks_status = {}
        all_encrypted = True

        for volume in required_volumes:
            try:
                result = await run_command(
                    f'cryptsetup status {volume}',
                    timeout=5
                )

                is_luks = 'LUKS' in result.stdout
                luks_status[volume] = is_luks

                if not is_luks:
                    all_encrypted = False

            except Exception as e:
                logger.error(f"Failed to check LUKS volume {volume}: {e}")
                luks_status[volume] = False
                all_encrypted = False

        pre_state["luks_status"] = luks_status

        # Determine drift
        drifted = False
        severity = "low"
        recommended_action = None

        if not all_encrypted:
            drifted = True
            severity = "critical"
            recommended_action = "enable_volume_encryption"
            unencrypted = [v for v, status in luks_status.items() if not status]
            logger.warning(f"Encryption drift: volumes not encrypted: {unencrypted}")

        return DriftResult(
            check="encryption",
            drifted=drifted,
            pre_state=pre_state,
            severity=severity,
            recommended_action=recommended_action,
            hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"]
        )
