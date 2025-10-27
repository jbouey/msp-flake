"""
MCP Executor - Runs pre-approved runbook steps
Executes structured remediation actions with evidence collection
"""
import os
import json
import yaml
import hashlib
import subprocess
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path


RUNBOOK_DIR = Path(os.getenv("RUNBOOK_DIR", "../runbooks"))
EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR", "../evidence"))


class RunbookExecutor:
    """Execute runbook steps with evidence collection"""

    def __init__(self, runbook_dir: Path = RUNBOOK_DIR, evidence_dir: Path = EVIDENCE_DIR):
        self.runbook_dir = Path(runbook_dir)
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def execute_runbook(self, runbook_id: str, params: Dict = None) -> Dict:
        """
        Execute a runbook and collect evidence

        Args:
            runbook_id: e.g., "RB-BACKUP-001"
            params: Runtime parameters from planner

        Returns:
            Execution result with evidence bundle
        """
        start_time = datetime.utcnow()
        execution_id = f"EXE-{start_time.strftime('%Y%m%d-%H%M%S')}-{runbook_id}"

        # Load runbook
        runbook = self._load_runbook(runbook_id)
        if not runbook:
            return {
                "execution_id": execution_id,
                "status": "failed",
                "error": f"Runbook {runbook_id} not found"
            }

        # Merge params
        params = params or {}

        # Execute steps
        steps_executed = []
        evidence_collected = {}
        success = True

        print(f"[executor] Starting execution: {execution_id}")
        print(f"[executor] Runbook: {runbook.get('name')}")

        for step in runbook.get("steps", []):
            step_result = self._execute_step(step, params, runbook_id)
            steps_executed.append(step_result)

            # Collect evidence from step
            if step_result.get("evidence"):
                evidence_collected.update(step_result["evidence"])

            # Check for failure
            if not step_result.get("success", False):
                success = False
                print(f"[executor] Step {step['id']} failed: {step_result.get('error')}")

                # Run rollback if configured
                if runbook.get("rollback"):
                    print(f"[executor] Executing rollback...")
                    self._execute_rollback(runbook, params)

                break

        end_time = datetime.utcnow()
        duration_seconds = (end_time - start_time).total_seconds()

        # Build evidence bundle
        evidence_bundle = self._build_evidence_bundle(
            execution_id=execution_id,
            runbook_id=runbook_id,
            runbook=runbook,
            steps_executed=steps_executed,
            evidence_collected=evidence_collected,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            success=success
        )

        # Write evidence to disk
        self._write_evidence_bundle(evidence_bundle)

        return {
            "execution_id": execution_id,
            "runbook_id": runbook_id,
            "status": "success" if success else "failed",
            "duration_seconds": duration_seconds,
            "steps_executed": len(steps_executed),
            "evidence_bundle_id": evidence_bundle["bundle_id"],
            "evidence_bundle_hash": evidence_bundle["evidence_bundle_hash"]
        }

    def _load_runbook(self, runbook_id: str) -> Optional[Dict]:
        """Load runbook YAML file"""
        runbook_path = self.runbook_dir / f"{runbook_id}.yaml"

        if not runbook_path.exists():
            # Try alternate naming
            runbook_path = self.runbook_dir / f"{runbook_id}-*.yaml"
            matches = list(self.runbook_dir.glob(f"{runbook_id}-*.yaml"))
            if not matches:
                print(f"[executor] Runbook not found: {runbook_id}")
                return None
            runbook_path = matches[0]

        try:
            with open(runbook_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"[executor] Failed to load runbook: {e}")
            return None

    def _execute_step(self, step: Dict, params: Dict, runbook_id: str) -> Dict:
        """Execute a single runbook step"""
        step_id = step.get("id", "unknown")
        action = step.get("action")
        description = step.get("description", "")
        timeout = step.get("timeout_seconds", 60)

        print(f"[executor] Executing step {step_id}: {description}")

        # In production, this would dispatch to actual tool implementations
        # For now, we'll simulate execution
        try:
            # Simulate step execution based on action type
            result = self._simulate_action(action, step.get("params", {}), params)

            return {
                "step_id": step_id,
                "action": action,
                "success": True,
                "output": result.get("output", ""),
                "evidence": result.get("evidence", {}),
                "duration_seconds": result.get("duration", 1.0)
            }

        except Exception as e:
            return {
                "step_id": step_id,
                "action": action,
                "success": False,
                "error": str(e),
                "duration_seconds": 0
            }

    def _simulate_action(self, action: str, step_params: Dict, runtime_params: Dict) -> Dict:
        """
        Simulate action execution
        In production, this would call actual remediation tools
        """

        # Merge params (runtime overrides step defaults)
        params = {**step_params, **runtime_params}

        # Simulate different action types
        if action == "check_backup_logs":
            return {
                "output": "Retrieved 100 lines from backup log",
                "evidence": {
                    "backup_log_excerpt": "Last backup: failed at 02:00",
                    "log_hash": "sha256:abc123..."
                },
                "duration": 0.5
            }

        elif action == "verify_disk_space":
            return {
                "output": "Disk usage: 45% of 500GB",
                "evidence": {
                    "disk_usage_before": "45%",
                    "filesystem": "/dev/sda1",
                    "available_gb": 275
                },
                "duration": 0.2
            }

        elif action == "restart_service":
            service = params.get("service_name", "unknown")
            return {
                "output": f"Service {service} restarted successfully",
                "evidence": {
                    "service_status_before": "failed",
                    "service_status_after": "active",
                    "restart_timestamp": datetime.utcnow().isoformat()
                },
                "duration": 2.0
            }

        elif action == "check_certificate_status":
            return {
                "output": "Certificate expires in 25 days",
                "evidence": {
                    "cert_expiry_date": "2025-11-18",
                    "days_remaining": 25,
                    "cert_fingerprint": "SHA256:xyz789..."
                },
                "duration": 0.3
            }

        else:
            # Generic action
            return {
                "output": f"Action {action} completed",
                "evidence": {
                    "action": action,
                    "params": params
                },
                "duration": 1.0
            }

    def _execute_rollback(self, runbook: Dict, params: Dict):
        """Execute rollback steps if main execution fails"""
        rollback_steps = runbook.get("rollback", [])

        for step in rollback_steps:
            action = step.get("action")
            print(f"[executor] Rollback: {action}")

            # Execute rollback action
            try:
                self._simulate_action(action, step.get("params", {}), params)
            except Exception as e:
                print(f"[executor] Rollback step failed: {e}")

    def _build_evidence_bundle(
        self,
        execution_id: str,
        runbook_id: str,
        runbook: Dict,
        steps_executed: List[Dict],
        evidence_collected: Dict,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: float,
        success: bool
    ) -> Dict:
        """Build comprehensive evidence bundle"""

        # Collect all evidence into structured bundle
        bundle = {
            "bundle_id": f"EB-{start_time.strftime('%Y%m%d-%H%M%S')}-{runbook_id}",
            "execution_id": execution_id,
            "runbook_id": runbook_id,
            "runbook_name": runbook.get("name"),
            "runbook_version": runbook.get("version"),
            "timestamp_start": start_time.isoformat(),
            "timestamp_end": end_time.isoformat(),
            "duration_seconds": duration_seconds,
            "operator": "service:mcp-executor",
            "hipaa_controls": runbook.get("hipaa_controls", []),
            "severity": runbook.get("severity", "unknown"),
            "status": "success" if success else "failed",

            # Execution details
            "steps_executed": steps_executed,
            "steps_total": len(runbook.get("steps", [])),
            "steps_successful": sum(1 for s in steps_executed if s.get("success")),

            # Evidence collected
            "evidence": evidence_collected,

            # SLA compliance
            "sla_max_duration_minutes": runbook.get("sla", {}).get("max_duration_minutes"),
            "sla_met": duration_seconds < (runbook.get("sla", {}).get("max_duration_minutes", 999) * 60),

            # Metadata
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "platform": "MSP Automation Platform",
                "version": "1.0.0"
            }
        }

        # Compute hash of entire bundle
        bundle_json = json.dumps(bundle, sort_keys=True)
        bundle["evidence_bundle_hash"] = hashlib.sha256(bundle_json.encode()).hexdigest()

        return bundle

    def _write_evidence_bundle(self, bundle: Dict):
        """Write evidence bundle to disk"""
        bundle_id = bundle["bundle_id"]
        bundle_path = self.evidence_dir / f"{bundle_id}.json"

        try:
            with open(bundle_path, 'w') as f:
                json.dump(bundle, f, indent=2)

            print(f"[executor] Evidence bundle written: {bundle_path}")

            # Also append to hash-chained log (for next task)
            self._append_to_evidence_chain(bundle)

        except Exception as e:
            print(f"[executor] Failed to write evidence bundle: {e}")

    def _append_to_evidence_chain(self, bundle: Dict):
        """Append evidence to hash-chained audit log"""
        chain_file = self.evidence_dir / "evidence_chain.jsonl"

        # Simple append for now - will enhance with hash chaining in next task
        try:
            with open(chain_file, 'a') as f:
                f.write(json.dumps({
                    "bundle_id": bundle["bundle_id"],
                    "timestamp": bundle["timestamp_start"],
                    "runbook_id": bundle["runbook_id"],
                    "status": bundle["status"],
                    "hash": bundle["evidence_bundle_hash"]
                }) + "\n")
        except Exception as e:
            print(f"[executor] Failed to append to evidence chain: {e}")


# Convenience function
def execute_runbook(runbook_id: str, params: Dict = None) -> Dict:
    """Execute a runbook"""
    executor = RunbookExecutor()
    return executor.execute_runbook(runbook_id, params)


if __name__ == "__main__":
    # Test execution
    print("Testing Runbook Executor\n")

    executor = RunbookExecutor()

    # Test with backup failure runbook
    result = executor.execute_runbook(
        runbook_id="RB-BACKUP-001",
        params={"hostname": "test-server"}
    )

    print(f"\n{'='*60}")
    print(f"Execution Result:")
    print(f"  ID: {result['execution_id']}")
    print(f"  Status: {result['status']}")
    print(f"  Duration: {result['duration_seconds']}s")
    print(f"  Steps: {result['steps_executed']}")
    print(f"  Evidence: {result['evidence_bundle_id']}")
    print(f"  Hash: {result['evidence_bundle_hash'][:16]}...")
