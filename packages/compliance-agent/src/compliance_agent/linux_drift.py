"""
Linux Drift Detector.

Detects configuration drift on Linux servers (Ubuntu/RHEL) by running
detection runbooks via SSH and comparing results to baseline.

Integrates with the three-tier auto-healing system:
- L1: Deterministic fixes for known drift patterns
- L2: LLM-guided remediation for complex cases
- L3: Human escalation for critical changes

Version: 1.0
"""

import asyncio
import logging
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import yaml

from .runbooks.linux.executor import LinuxTarget, LinuxExecutor, LinuxExecutionResult
from .runbooks.linux.runbooks import RUNBOOKS, get_runbook, get_l1_runbooks, get_l2_runbooks

logger = logging.getLogger(__name__)


@dataclass
class DriftResult:
    """Result of drift detection for a single check."""
    target: str
    runbook_id: str
    check_type: str
    severity: str
    compliant: bool
    drift_description: str
    raw_output: str
    hipaa_controls: List[str]
    timestamp: str = ""
    distro: str = ""
    l1_eligible: bool = False
    l2_eligible: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "runbook_id": self.runbook_id,
            "check_type": self.check_type,
            "severity": self.severity,
            "compliant": self.compliant,
            "drift_description": self.drift_description,
            "hipaa_controls": self.hipaa_controls,
            "timestamp": self.timestamp,
            "distro": self.distro,
            "l1_eligible": self.l1_eligible,
            "l2_eligible": self.l2_eligible,
        }


@dataclass
class RemediationResult:
    """Result of drift remediation attempt."""
    target: str
    runbook_id: str
    success: bool
    phases_completed: List[str]
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class LinuxDriftDetector:
    """
    Detect and remediate configuration drift on Linux servers.

    Usage:
        detector = LinuxDriftDetector(targets=[
            LinuxTarget(hostname="192.168.1.100", username="admin", password="...")
        ])
        drifts = await detector.detect_all()
        for drift in drifts:
            if not drift.compliant and drift.l1_eligible:
                result = await detector.remediate(drift)
    """

    def __init__(
        self,
        targets: Optional[List[LinuxTarget]] = None,
        baseline_path: Optional[str] = None,
        executor: Optional[LinuxExecutor] = None
    ):
        """
        Initialize detector.

        Args:
            targets: List of Linux targets to monitor
            baseline_path: Path to linux_baseline.yaml
            executor: Optional pre-configured executor
        """
        self.targets = targets or []
        self.executor = executor or LinuxExecutor(targets)
        self.baseline = self._load_baseline(baseline_path)

        # Track detection history for evidence
        self._detection_history: List[DriftResult] = []

    def _load_baseline(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Load baseline configuration from YAML."""
        if path is None:
            # Default path relative to this file
            base_dir = Path(__file__).parent / "baselines"
            path = base_dir / "linux_baseline.yaml"

        try:
            with open(path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Baseline not found at {path}, using defaults")
            return self._default_baseline()

    def _default_baseline(self) -> Dict[str, Any]:
        """Return minimal default baseline."""
        return {
            "version": "1.0",
            "ssh_config": {"enabled": True},
            "firewall": {"enabled": True, "required": True},
            "services": {"enabled": True},
            "audit": {"enabled": True},
            "permissions": {"enabled": True},
        }

    def add_target(self, target: LinuxTarget):
        """Add a Linux target."""
        self.targets.append(target)
        self.executor.add_target(target)

    def remove_target(self, hostname: str):
        """Remove a Linux target."""
        self.targets = [t for t in self.targets if t.hostname != hostname]
        self.executor.remove_target(hostname)

    async def detect_all(self) -> List[DriftResult]:
        """
        Run all detection checks on all targets.

        Returns:
            List of DriftResult for each check on each target
        """
        all_results = []

        for target in self.targets:
            try:
                results = await self.detect_host(target)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Detection failed for {target.hostname}: {e}")
                # Add a failure result
                all_results.append(DriftResult(
                    target=target.hostname,
                    runbook_id="DETECT-ERROR",
                    check_type="connectivity",
                    severity="critical",
                    compliant=False,
                    drift_description=f"Detection failed: {e}",
                    raw_output="",
                    hipaa_controls=[],
                ))

        # Store for evidence
        self._detection_history.extend(all_results)

        return all_results

    async def detect_host(self, target: LinuxTarget) -> List[DriftResult]:
        """
        Run all detection checks on a single host.

        Args:
            target: Linux target to check

        Returns:
            List of DriftResult for each check
        """
        results = []

        # Detect distro first
        try:
            distro = await self.executor.detect_distro(target)
            logger.info(f"Detected distro for {target.hostname}: {distro}")
        except Exception as e:
            logger.error(f"Failed to detect distro for {target.hostname}: {e}")
            # Return error result
            return [DriftResult(
                target=target.hostname,
                runbook_id="DETECT-ERROR",
                check_type="connectivity",
                severity="critical",
                compliant=False,
                drift_description=f"Failed to connect: {e}",
                raw_output="",
                hipaa_controls=[],
            )]

        # Get runbooks filtered by baseline
        enabled_checks = self._get_enabled_checks()

        for runbook_id, runbook in RUNBOOKS.items():
            # Skip if check type is disabled in baseline
            if runbook.check_type not in enabled_checks:
                continue

            try:
                exec_results = await self.executor.run_runbook(
                    target,
                    runbook_id,
                    phases=["detect"]
                )

                if not exec_results:
                    continue

                exec_result = exec_results[0]

                # Parse result
                stdout = exec_result.output.get("stdout", "")
                compliant = "COMPLIANT" in stdout or exec_result.success

                drift_desc = ""
                if not compliant:
                    # Extract drift description from output
                    if "DRIFT:" in stdout:
                        drift_desc = stdout.split("DRIFT:")[1].strip().split("\n")[0]
                    else:
                        drift_desc = stdout.strip()[:200] or "Non-compliant state detected"

                results.append(DriftResult(
                    target=target.hostname,
                    runbook_id=runbook_id,
                    check_type=runbook.check_type,
                    severity=runbook.severity,
                    compliant=compliant,
                    drift_description=drift_desc,
                    raw_output=stdout,
                    hipaa_controls=runbook.hipaa_controls,
                    distro=distro,
                    l1_eligible=runbook.l1_auto_heal,
                    l2_eligible=runbook.l2_llm_eligible and not runbook.l1_auto_heal,
                ))

            except Exception as e:
                logger.error(f"Check {runbook_id} failed on {target.hostname}: {e}")
                results.append(DriftResult(
                    target=target.hostname,
                    runbook_id=runbook_id,
                    check_type=runbook.check_type,
                    severity=runbook.severity,
                    compliant=False,
                    drift_description=f"Check failed: {e}",
                    raw_output="",
                    hipaa_controls=runbook.hipaa_controls,
                    distro=distro,
                ))

        return results

    def _get_enabled_checks(self) -> set:
        """Get set of enabled check types from baseline."""
        enabled = set()
        # All check types used by runbooks in the registry
        all_check_types = [
            "ssh_config", "firewall", "services", "audit",
            "patching", "encryption", "accounts", "permissions", "mac",
            "kernel", "cron", "logging", "network", "boot",
            "banner", "crypto", "time_sync", "integrity", "incident_response",
        ]
        for check_type in all_check_types:
            config = self.baseline.get(check_type, {})
            if config.get("enabled", True):
                enabled.add(check_type)
        return enabled

    async def detect_category(self, target: LinuxTarget, category: str) -> List[DriftResult]:
        """
        Run detection for a specific category only.

        Args:
            target: Linux target
            category: Check type (ssh_config, firewall, etc.)

        Returns:
            List of DriftResult for that category
        """
        results = []
        distro = await self.executor.detect_distro(target)

        for runbook_id, runbook in RUNBOOKS.items():
            if runbook.check_type != category:
                continue

            try:
                exec_results = await self.executor.run_runbook(
                    target,
                    runbook_id,
                    phases=["detect"]
                )

                if exec_results:
                    exec_result = exec_results[0]
                    stdout = exec_result.output.get("stdout", "")
                    compliant = "COMPLIANT" in stdout or exec_result.success

                    results.append(DriftResult(
                        target=target.hostname,
                        runbook_id=runbook_id,
                        check_type=runbook.check_type,
                        severity=runbook.severity,
                        compliant=compliant,
                        drift_description="" if compliant else stdout.strip()[:200],
                        raw_output=stdout,
                        hipaa_controls=runbook.hipaa_controls,
                        distro=distro,
                        l1_eligible=runbook.l1_auto_heal,
                        l2_eligible=runbook.l2_llm_eligible,
                    ))
            except Exception as e:
                logger.error(f"Check {runbook_id} failed: {e}")

        return results

    async def remediate(self, drift: DriftResult) -> RemediationResult:
        """
        Execute remediation for a specific drift.

        Only runs if the drift is L1-eligible or explicitly requested.

        Args:
            drift: DriftResult from detection

        Returns:
            RemediationResult with outcome
        """
        start_time = datetime.now(timezone.utc)

        # Find target
        target = None
        for t in self.targets:
            if t.hostname == drift.target:
                target = t
                break

        if not target:
            return RemediationResult(
                target=drift.target,
                runbook_id=drift.runbook_id,
                success=False,
                phases_completed=[],
                error=f"Target not found: {drift.target}"
            )

        try:
            # Run full runbook (detect, remediate, verify)
            exec_results = await self.executor.run_runbook(
                target,
                drift.runbook_id,
                phases=["detect", "remediate", "verify"],
                collect_evidence=True
            )

            phases_completed = [r.phase for r in exec_results if r.success]

            # Check if verification passed
            verify_results = [r for r in exec_results if r.phase == "verify"]
            success = bool(verify_results and verify_results[0].success)

            # If no verify phase, check remediate phase
            if not verify_results:
                remediate_results = [r for r in exec_results if r.phase == "remediate"]
                success = bool(remediate_results and remediate_results[0].success)

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()

            return RemediationResult(
                target=drift.target,
                runbook_id=drift.runbook_id,
                success=success,
                phases_completed=phases_completed,
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Remediation failed for {drift.runbook_id} on {drift.target}: {e}")
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            return RemediationResult(
                target=drift.target,
                runbook_id=drift.runbook_id,
                success=False,
                phases_completed=[],
                error=str(e),
                duration_seconds=duration
            )

    async def remediate_all_l1(self) -> List[RemediationResult]:
        """
        Auto-remediate all L1-eligible drifts found in last detection.

        Returns:
            List of RemediationResult for each remediation attempt
        """
        results = []

        # Get drifts from history that are L1-eligible and not compliant
        l1_drifts = [
            d for d in self._detection_history
            if not d.compliant and d.l1_eligible
        ]

        for drift in l1_drifts:
            result = await self.remediate(drift)
            results.append(result)

        return results

    async def generate_evidence(
        self,
        results: Optional[List[DriftResult]] = None,
        include_raw: bool = False
    ) -> Dict[str, Any]:
        """
        Generate evidence bundle from drift detection results.

        Args:
            results: List of DriftResult (uses history if not provided)
            include_raw: Include raw command output

        Returns:
            Evidence bundle dictionary
        """
        if results is None:
            results = self._detection_history

        timestamp = datetime.now(timezone.utc).isoformat()

        # Group by target
        by_target: Dict[str, List[DriftResult]] = {}
        for r in results:
            if r.target not in by_target:
                by_target[r.target] = []
            by_target[r.target].append(r)

        evidence = {
            "type": "linux_drift_detection",
            "version": "1.0",
            "timestamp": timestamp,
            "baseline_version": self.baseline.get("version", "unknown"),
            "targets_scanned": len(by_target),
            "total_checks": len(results),
            "compliant_count": sum(1 for r in results if r.compliant),
            "drift_count": sum(1 for r in results if not r.compliant),
            "targets": {}
        }

        for target, target_results in by_target.items():
            target_evidence = {
                "hostname": target,
                "distro": target_results[0].distro if target_results else "unknown",
                "checks": [],
                "summary": {
                    "total": len(target_results),
                    "compliant": sum(1 for r in target_results if r.compliant),
                    "drifted": sum(1 for r in target_results if not r.compliant),
                    "critical": sum(1 for r in target_results if not r.compliant and r.severity == "critical"),
                    "high": sum(1 for r in target_results if not r.compliant and r.severity == "high"),
                }
            }

            for result in target_results:
                check = {
                    "runbook_id": result.runbook_id,
                    "check_type": result.check_type,
                    "severity": result.severity,
                    "compliant": result.compliant,
                    "hipaa_controls": result.hipaa_controls,
                    "timestamp": result.timestamp,
                }
                if not result.compliant:
                    check["drift_description"] = result.drift_description
                if include_raw:
                    check["raw_output"] = result.raw_output

                target_evidence["checks"].append(check)

            evidence["targets"][target] = target_evidence

        # Add evidence hash
        evidence_str = json.dumps(evidence, sort_keys=True)
        evidence["hash"] = hashlib.sha256(evidence_str.encode()).hexdigest()

        return evidence

    def get_drift_summary(self, results: Optional[List[DriftResult]] = None) -> Dict[str, Any]:
        """
        Get summary of drift detection results.

        Args:
            results: List of DriftResult (uses history if not provided)

        Returns:
            Summary dictionary
        """
        if results is None:
            results = self._detection_history

        return {
            "total_checks": len(results),
            "compliant": sum(1 for r in results if r.compliant),
            "drifted": sum(1 for r in results if not r.compliant),
            "l1_actionable": sum(1 for r in results if not r.compliant and r.l1_eligible),
            "l2_actionable": sum(1 for r in results if not r.compliant and r.l2_eligible),
            "l3_escalation": sum(1 for r in results if not r.compliant and not r.l1_eligible and not r.l2_eligible),
            "by_severity": {
                "critical": sum(1 for r in results if not r.compliant and r.severity == "critical"),
                "high": sum(1 for r in results if not r.compliant and r.severity == "high"),
                "medium": sum(1 for r in results if not r.compliant and r.severity == "medium"),
                "low": sum(1 for r in results if not r.compliant and r.severity == "low"),
            },
            "by_category": self._group_by_category(results),
        }

    def _group_by_category(self, results: List[DriftResult]) -> Dict[str, Dict[str, int]]:
        """Group results by check category."""
        categories: Dict[str, Dict[str, int]] = {}
        for r in results:
            if r.check_type not in categories:
                categories[r.check_type] = {"compliant": 0, "drifted": 0}
            if r.compliant:
                categories[r.check_type]["compliant"] += 1
            else:
                categories[r.check_type]["drifted"] += 1
        return categories

    async def close(self):
        """Close all connections."""
        await self.executor.close_all()
