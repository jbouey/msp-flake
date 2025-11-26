"""
Windows Compliance Collector.

Connects to Windows servers via WinRM, runs compliance checks,
and stores results for the web UI dashboard.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from .runbooks.windows.executor import WindowsExecutor, WindowsTarget, ExecutionResult
from .runbooks.windows.runbooks import RUNBOOKS
from .incident_db import IncidentDB, Incident
from .evidence import EvidenceStore

logger = logging.getLogger(__name__)


@dataclass
class WindowsCheckResult:
    """Result of a Windows compliance check."""
    check_id: str
    check_name: str
    target: str
    status: str  # pass, fail, warn, error
    drifted: bool
    severity: str
    hipaa_controls: List[str]
    details: Dict[str, Any]
    timestamp: str
    duration_seconds: float
    error: Optional[str] = None


class WindowsCollector:
    """
    Collect compliance data from Windows servers.

    Runs all Windows runbooks in detect-only mode and stores
    results for dashboard display.
    """

    def __init__(
        self,
        targets: List[WindowsTarget],
        incident_db_path: str = "/var/lib/msp-compliance-agent/incidents.db",
        evidence_dir: str = "/var/lib/msp-compliance-agent/evidence",
        site_id: str = "unknown"
    ):
        self.targets = {t.hostname: t for t in targets}
        self.executor = WindowsExecutor(targets)
        self.incident_db = IncidentDB(incident_db_path)
        self.evidence_dir = Path(evidence_dir)
        self.site_id = site_id

        # Ensure evidence directory exists
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def add_target(self, target: WindowsTarget):
        """Add a Windows target."""
        self.targets[target.hostname] = target
        self.executor.add_target(target)

    async def collect_all(self) -> Dict[str, List[WindowsCheckResult]]:
        """
        Run all compliance checks on all targets.

        Returns:
            Dict mapping hostname to list of check results
        """
        all_results = {}

        for hostname, target in self.targets.items():
            logger.info(f"Collecting compliance data from {hostname}")

            try:
                results = await self._collect_from_target(target)
                all_results[hostname] = results

                # Store results
                await self._store_results(hostname, results)

            except Exception as e:
                logger.exception(f"Failed to collect from {hostname}")
                all_results[hostname] = [WindowsCheckResult(
                    check_id="connection",
                    check_name="Connection Test",
                    target=hostname,
                    status="error",
                    drifted=True,
                    severity="critical",
                    hipaa_controls=[],
                    details={},
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=0,
                    error=str(e)
                )]

        return all_results

    async def _collect_from_target(self, target: WindowsTarget) -> List[WindowsCheckResult]:
        """Run all checks on a single target."""
        results = []

        # First check connectivity
        health = await self.executor.check_target_health(target)

        if not health.get("Healthy", False):
            return [WindowsCheckResult(
                check_id="health",
                check_name="Target Health",
                target=target.hostname,
                status="error",
                drifted=True,
                severity="critical",
                hipaa_controls=[],
                details=health,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=0,
                error=health.get("Error", "Connection failed")
            )]

        # Run all runbooks in detect-only mode
        for runbook_id, runbook in RUNBOOKS.items():
            logger.info(f"Running {runbook_id} on {target.hostname}")

            try:
                exec_results = await self.executor.run_runbook(
                    target,
                    runbook_id,
                    phases=["detect"]
                )

                if exec_results:
                    exec_result = exec_results[0]
                    parsed = exec_result.output.get("parsed", {})

                    # Determine status from detection result
                    drifted = parsed.get("Drifted", True) if parsed else True

                    if exec_result.error:
                        status = "error"
                    elif drifted:
                        status = "fail"
                    else:
                        status = "pass"

                    results.append(WindowsCheckResult(
                        check_id=runbook_id,
                        check_name=runbook.name,
                        target=target.hostname,
                        status=status,
                        drifted=drifted,
                        severity=runbook.severity,
                        hipaa_controls=runbook.hipaa_controls,
                        details=parsed or {},
                        timestamp=exec_result.timestamp,
                        duration_seconds=exec_result.duration_seconds,
                        error=exec_result.error
                    ))

            except Exception as e:
                logger.exception(f"Check {runbook_id} failed on {target.hostname}")
                results.append(WindowsCheckResult(
                    check_id=runbook_id,
                    check_name=runbook.name,
                    target=target.hostname,
                    status="error",
                    drifted=True,
                    severity=runbook.severity,
                    hipaa_controls=runbook.hipaa_controls,
                    details={},
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=0,
                    error=str(e)
                ))

        return results

    async def _store_results(self, hostname: str, results: List[WindowsCheckResult]):
        """Store check results in incident DB and evidence store."""

        for result in results:
            # Create incident for failed checks
            if result.status in ["fail", "error"]:
                incident = Incident(
                    incident_id=f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{result.check_id}",
                    check_type=result.check_id,
                    severity=result.severity,
                    status="open",
                    source=f"windows:{hostname}",
                    details=result.details,
                    hipaa_controls=result.hipaa_controls,
                    created_at=result.timestamp
                )
                self.incident_db.create_incident(incident)

            # Create evidence bundle
            evidence = {
                "bundle_id": f"EB-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{result.check_id}-{hostname}",
                "site_id": self.site_id,
                "source": f"windows:{hostname}",
                "check": result.check_id,
                "check_name": result.check_name,
                "outcome": "success" if result.status == "pass" else "failed",
                "hipaa_controls": result.hipaa_controls,
                "timestamp": result.timestamp,
                "duration_seconds": result.duration_seconds,
                "details": result.details,
                "error": result.error
            }

            # Write evidence bundle
            bundle_dir = self.evidence_dir / evidence["bundle_id"]
            bundle_dir.mkdir(parents=True, exist_ok=True)

            with open(bundle_dir / "bundle.json", "w") as f:
                json.dump(evidence, f, indent=2)

    async def get_compliance_summary(self) -> Dict[str, Any]:
        """
        Get summary of compliance status across all targets.

        Returns:
            Summary dict for dashboard display
        """
        all_results = await self.collect_all()

        total_checks = 0
        passed = 0
        failed = 0
        errors = 0

        by_control = {}
        by_target = {}

        for hostname, results in all_results.items():
            target_summary = {"passed": 0, "failed": 0, "errors": 0}

            for result in results:
                total_checks += 1

                if result.status == "pass":
                    passed += 1
                    target_summary["passed"] += 1
                elif result.status == "fail":
                    failed += 1
                    target_summary["failed"] += 1
                else:
                    errors += 1
                    target_summary["errors"] += 1

                # Track by HIPAA control
                for control in result.hipaa_controls:
                    if control not in by_control:
                        by_control[control] = {"passed": 0, "failed": 0}

                    if result.status == "pass":
                        by_control[control]["passed"] += 1
                    else:
                        by_control[control]["failed"] += 1

            by_target[hostname] = target_summary

        score = (passed / total_checks * 100) if total_checks > 0 else 0

        return {
            "score": round(score, 1),
            "status": "healthy" if score >= 80 else "warning" if score >= 50 else "critical",
            "total_checks": total_checks,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "by_control": by_control,
            "by_target": by_target,
            "last_check": datetime.now(timezone.utc).isoformat()
        }


async def run_collection_loop(
    collector: WindowsCollector,
    interval_seconds: int = 300
):
    """
    Run continuous collection loop.

    Args:
        collector: WindowsCollector instance
        interval_seconds: Time between collection runs
    """
    while True:
        try:
            logger.info("Starting Windows compliance collection")
            summary = await collector.get_compliance_summary()
            logger.info(f"Collection complete: score={summary['score']}%, "
                       f"passed={summary['passed']}, failed={summary['failed']}")
        except Exception as e:
            logger.exception("Collection loop error")

        await asyncio.sleep(interval_seconds)


# CLI entry point
def main():
    """CLI entry point for Windows collector."""
    import argparse

    parser = argparse.ArgumentParser(description="Windows Compliance Collector")
    parser.add_argument("--host", required=True, help="Windows host to connect to")
    parser.add_argument("--port", type=int, default=5985, help="WinRM port")
    parser.add_argument("--username", required=True, help="Windows username")
    parser.add_argument("--password", required=True, help="Windows password")
    parser.add_argument("--ssl", action="store_true", help="Use HTTPS")
    parser.add_argument("--interval", type=int, default=300, help="Collection interval")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--evidence-dir", default="/tmp/evidence", help="Evidence directory")
    parser.add_argument("--db-path", default="/tmp/incidents.db", help="Incident DB path")
    parser.add_argument("--site-id", default="test", help="Site ID")

    args = parser.parse_args()

    target = WindowsTarget(
        hostname=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        use_ssl=args.ssl
    )

    collector = WindowsCollector(
        targets=[target],
        incident_db_path=args.db_path,
        evidence_dir=args.evidence_dir,
        site_id=args.site_id
    )

    if args.once:
        # Single collection run
        summary = asyncio.run(collector.get_compliance_summary())
        print(json.dumps(summary, indent=2))
    else:
        # Continuous loop
        asyncio.run(run_collection_loop(collector, args.interval))


if __name__ == "__main__":
    main()
