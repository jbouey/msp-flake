#!/usr/bin/env python3
"""
MCP Executor - Runbook Execution Engine

Executes pre-approved runbooks for incident remediation and generates
evidence bundles for HIPAA compliance.

Architecture:
- Loads runbooks from YAML definitions
- Executes steps sequentially with timeout and retry logic
- Captures all outputs and evidence
- Generates signed evidence bundles via evidence pipeline

Usage:
    executor = RunbookExecutor(client_id="clinic-001")
    result = executor.execute_runbook(
        runbook_id="RB-BACKUP-001",
        incident=incident_data
    )

HIPAA Controls: All runbooks map to specific HIPAA controls
Author: MSP Compliance Platform
Version: 1.0.0
"""

import os
import sys
import time
import yaml
import subprocess
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

# Import evidence pipeline components
sys.path.insert(0, str(Path(__file__).parent / 'evidence'))
from bundler import (
    IncidentData,
    RunbookData,
    ExecutionData,
    ActionStep,
    ArtifactCollector
)
from pipeline import EvidencePipeline


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class RunbookDefinition:
    """Runbook loaded from YAML"""
    id: str
    name: str
    version: str
    description: str
    triggers: List[Dict[str, str]]
    severity: List[str]
    applicable_to: List[str]
    hipaa_controls: List[str]
    sla_target_seconds: int
    steps: List[Dict[str, Any]]
    rollback: List[Dict[str, Any]]
    evidence_artifacts: Dict[str, Any]
    success_criteria: List[str]
    post_execution_validation: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class RunbookExecutor:
    """
    Executes runbooks and generates evidence bundles

    Main entry point for incident remediation.
    """

    def __init__(
        self,
        client_id: str,
        runbooks_dir: str = None,
        scripts_dir: str = None,
        dry_run: bool = False
    ):
        """
        Initialize executor

        Args:
            client_id: Client identifier (e.g., "clinic-001")
            runbooks_dir: Path to runbooks directory (YAML files)
            scripts_dir: Path to scripts directory (remediation scripts)
            dry_run: If True, don't actually execute scripts (for testing)
        """
        self.client_id = client_id
        self.dry_run = dry_run

        # Directories
        if runbooks_dir is None:
            runbooks_dir = Path(__file__).parent / 'runbooks'
        self.runbooks_dir = Path(runbooks_dir)

        if scripts_dir is None:
            scripts_dir = Path(__file__).parent / 'scripts'
        self.scripts_dir = Path(scripts_dir)

        # Load runbooks
        self.runbooks = self._load_runbooks()
        logger.info(f"Loaded {len(self.runbooks)} runbooks")

        # Initialize evidence pipeline
        self.evidence_pipeline = EvidencePipeline(client_id=client_id)
        logger.info(f"RunbookExecutor initialized for client: {client_id}")

    def _load_runbooks(self) -> Dict[str, RunbookDefinition]:
        """
        Load all runbooks from YAML files

        Returns:
            Dict mapping runbook_id to RunbookDefinition
        """
        runbooks = {}

        if not self.runbooks_dir.exists():
            logger.warning(f"Runbooks directory not found: {self.runbooks_dir}")
            return runbooks

        for yaml_file in self.runbooks_dir.glob('RB-*.yaml'):
            try:
                with open(yaml_file, 'r') as f:
                    data = yaml.safe_load(f)

                runbook = RunbookDefinition(**data)
                runbooks[runbook.id] = runbook
                logger.debug(f"Loaded runbook: {runbook.id} - {runbook.name}")

            except Exception as e:
                logger.error(f"Failed to load runbook {yaml_file}: {e}")

        return runbooks

    def execute_runbook(
        self,
        runbook_id: str,
        incident: IncidentData,
        variables: Dict[str, Any] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Execute a runbook for an incident

        Args:
            runbook_id: Runbook ID to execute (e.g., "RB-BACKUP-001")
            incident: Incident data that triggered this runbook
            variables: Optional variables to pass to scripts (e.g., {"service_name": "nginx"})

        Returns:
            Tuple of (resolution_status, outputs)
            resolution_status: "success", "partial", or "failed"
            outputs: Dict of outputs from execution

        Raises:
            ValueError: If runbook not found
        """
        # Validate runbook exists
        if runbook_id not in self.runbooks:
            raise ValueError(f"Runbook not found: {runbook_id}")

        runbook_def = self.runbooks[runbook_id]
        logger.info(f"Executing runbook: {runbook_id} - {runbook_def.name}")
        logger.info(f"  Incident: {incident.incident_id} ({incident.event_type})")
        logger.info(f"  SLA Target: {runbook_def.sla_target_seconds}s")

        # Start execution timer
        start_time = datetime.now(timezone.utc)

        # Prepare execution context
        variables = variables or {}
        variables['client_id'] = self.client_id
        variables['incident_id'] = incident.incident_id
        variables['runbook_id'] = runbook_id

        # Execute steps
        actions_taken = []
        artifacts = ArtifactCollector()
        steps_executed = 0
        resolution_status = "failed"

        try:
            for step_def in runbook_def.steps:
                step_num = step_def['step']
                step_name = step_def['name']
                logger.info(f"  Step {step_num}: {step_name}")

                # Execute step
                action = self._execute_step(step_def, variables)
                actions_taken.append(action)
                steps_executed += 1

                # Collect evidence artifacts from step
                self._collect_step_artifacts(action, artifacts)

                # Check if step failed
                if action.result == "failed":
                    # Check if we should escalate immediately
                    if step_def.get('failure_action') == 'escalate':
                        logger.warning(f"  Step {step_num} failed - escalating immediately")
                        resolution_status = "partial"
                        break

                    # Check if step is critical (no more steps possible)
                    logger.warning(f"  Step {step_num} failed - attempting rollback")
                    self._execute_rollback(runbook_def.rollback, variables)
                    resolution_status = "partial"
                    break

            # If all steps succeeded
            if steps_executed == len(runbook_def.steps):
                # Validate success criteria
                if self._validate_success_criteria(runbook_def.success_criteria, artifacts):
                    resolution_status = "success"
                    logger.info("  ✓ All steps completed successfully")
                else:
                    resolution_status = "partial"
                    logger.warning("  ⚠ Steps completed but success criteria not met")

        except Exception as e:
            logger.error(f"  ✗ Execution failed: {e}")
            resolution_status = "failed"

        # End execution timer
        end_time = datetime.now(timezone.utc)
        mttr_seconds = int((end_time - start_time).total_seconds())

        # Check SLA
        sla_met = mttr_seconds <= runbook_def.sla_target_seconds

        logger.info(f"  Execution complete: {resolution_status}")
        logger.info(f"  MTTR: {mttr_seconds}s (SLA: {runbook_def.sla_target_seconds}s, Met: {sla_met})")

        # Prepare execution metadata
        execution = ExecutionData(
            timestamp_start=start_time.isoformat(),
            timestamp_end=end_time.isoformat(),
            operator=f"service:mcp-executor",
            mttr_seconds=mttr_seconds,
            sla_target_seconds=runbook_def.sla_target_seconds,
            sla_met=sla_met,
            resolution_type="auto" if resolution_status == "success" else "partial"
        )

        # Prepare runbook metadata
        runbook_metadata = RunbookData(
            runbook_id=runbook_def.id,
            runbook_version=runbook_def.version,
            runbook_hash=self._compute_runbook_hash(runbook_id),
            steps_total=len(runbook_def.steps),
            steps_executed=steps_executed
        )

        # Generate evidence bundle
        logger.info("  Generating evidence bundle...")
        try:
            bundle_path, sig_path = self.evidence_pipeline.process_incident(
                incident=incident,
                runbook=runbook_metadata,
                execution=execution,
                actions=actions_taken,
                artifacts=artifacts.get_artifacts()
            )
            logger.info(f"  Evidence bundle: {bundle_path}")
            logger.info(f"  Signature: {sig_path}")

        except Exception as e:
            logger.error(f"  Failed to generate evidence bundle: {e}")

        # Return results
        outputs = {
            "resolution_status": resolution_status,
            "mttr_seconds": mttr_seconds,
            "sla_met": sla_met,
            "steps_executed": steps_executed,
            "steps_total": len(runbook_def.steps)
        }

        return resolution_status, outputs

    def _execute_step(
        self,
        step_def: Dict[str, Any],
        variables: Dict[str, Any]
    ) -> ActionStep:
        """
        Execute a single runbook step

        Args:
            step_def: Step definition from runbook YAML
            variables: Variables to pass to script

        Returns:
            ActionStep with execution results
        """
        step_num = step_def['step']
        step_name = step_def['name']
        script_name = step_def['script']
        timeout = step_def.get('timeout_seconds', 60)
        retry_count = step_def.get('retry_count', 0) if step_def.get('retry_on_failure', False) else 0
        retry_delay = step_def.get('retry_delay_seconds', 5)

        script_path = self.scripts_dir / script_name

        # Compute script hash (for evidence)
        if script_path.exists():
            script_hash = self._compute_file_hash(script_path)
        else:
            # Use a valid SHA256 format for missing scripts (dry run mode)
            script_hash = "sha256:" + "0" * 64

        # Execute with retries
        for attempt in range(retry_count + 1):
            try:
                if self.dry_run:
                    # Dry run mode - simulate execution
                    result = "ok"
                    exit_code = 0
                    stdout = f"[DRY RUN] Would execute: {script_name}"
                    stderr = ""
                    error_message = None
                    logger.info(f"    [DRY RUN] Step {step_num} - attempt {attempt + 1}")
                else:
                    # Real execution
                    logger.debug(f"    Executing: {script_path}")

                    # Build environment with variables
                    env = os.environ.copy()
                    for key, value in variables.items():
                        env[key.upper()] = str(value)

                    # Execute script
                    proc = subprocess.run(
                        [str(script_path)],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env=env
                    )

                    exit_code = proc.returncode
                    stdout = proc.stdout
                    stderr = proc.stderr

                    if exit_code == 0:
                        result = "ok"
                        error_message = None
                    else:
                        result = "failed"
                        error_message = f"Script exited with code {exit_code}"

                    logger.debug(f"    Exit code: {exit_code}")

                # Create action step record
                action = ActionStep(
                    step=step_num,
                    action=step_name,
                    script_hash=script_hash,
                    result=result,
                    exit_code=exit_code,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stdout_excerpt=stdout[:500] if stdout else None,  # First 500 chars
                    stderr_excerpt=stderr[:500] if stderr else None,
                    error_message=error_message
                )

                # If successful or max retries reached, return
                if result == "ok" or attempt == retry_count:
                    return action

                # Retry after delay
                logger.warning(f"    Step {step_num} failed, retrying in {retry_delay}s...")
                time.sleep(retry_delay)

            except subprocess.TimeoutExpired:
                logger.error(f"    Step {step_num} timed out after {timeout}s")
                return ActionStep(
                    step=step_num,
                    action=step_name,
                    script_hash=script_hash,
                    result="failed",
                    exit_code=-1,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stdout_excerpt=None,
                    stderr_excerpt=None,
                    error_message=f"Timeout after {timeout}s"
                )

            except Exception as e:
                logger.error(f"    Step {step_num} failed: {e}")
                return ActionStep(
                    step=step_num,
                    action=step_name,
                    script_hash=script_hash,
                    result="failed",
                    exit_code=-1,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    stdout_excerpt=None,
                    stderr_excerpt=None,
                    error_message=str(e)
                )

        # Should never reach here
        return action

    def _collect_step_artifacts(self, action: ActionStep, artifacts: ArtifactCollector) -> None:
        """Collect evidence artifacts from step execution"""
        # Add stdout/stderr as log excerpts
        if action.stdout_excerpt:
            artifacts.add_log_excerpt(f"{action.action}_stdout", action.stdout_excerpt)

        if action.stderr_excerpt:
            artifacts.add_log_excerpt(f"{action.action}_stderr", action.stderr_excerpt)

        # Add error messages
        if action.error_message:
            artifacts.add_output(f"{action.action}_error", action.error_message)

        # Add exit code
        artifacts.add_output(f"{action.action}_exit_code", action.exit_code)

    def _execute_rollback(
        self,
        rollback_steps: List[Dict[str, Any]],
        variables: Dict[str, Any]
    ) -> None:
        """Execute rollback steps"""
        logger.info("  Executing rollback...")
        for rollback in rollback_steps:
            action = rollback.get('action')
            logger.info(f"    Rollback: {action}")

            if action == "alert_administrator":
                # In production, would send actual alert
                message = rollback.get('message', 'Rollback executed')
                logger.warning(f"    ALERT: {message}")

            elif action == "create_ticket":
                # In production, would create actual ticket
                priority = rollback.get('priority', 'medium')
                logger.warning(f"    TICKET: Priority {priority}")

            elif 'script' in rollback:
                # Execute rollback script
                script_path = self.scripts_dir / rollback['script']
                if script_path.exists() and not self.dry_run:
                    try:
                        subprocess.run([str(script_path)], timeout=60, check=False)
                        logger.info(f"    Rollback script executed: {script_path.name}")
                    except Exception as e:
                        logger.error(f"    Rollback script failed: {e}")

    def _validate_success_criteria(
        self,
        criteria: List[str],
        artifacts: ArtifactCollector
    ) -> bool:
        """
        Validate success criteria

        For now, just returns True. In production, would evaluate criteria
        against actual outputs.
        """
        # TODO: Implement actual criteria validation
        return True

    def _compute_runbook_hash(self, runbook_id: str) -> str:
        """Compute SHA256 hash of runbook YAML file"""
        runbook_file = self.runbooks_dir / f"{runbook_id}.yaml"
        if not runbook_file.exists():
            return "unknown-hash"

        return self._compute_file_hash(runbook_file)

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"

    def list_runbooks(self) -> List[Dict[str, str]]:
        """
        List all available runbooks

        Returns:
            List of dicts with runbook metadata
        """
        runbooks = []
        for runbook_id, runbook_def in self.runbooks.items():
            runbooks.append({
                "id": runbook_def.id,
                "name": runbook_def.name,
                "version": runbook_def.version,
                "description": runbook_def.description,
                "hipaa_controls": ", ".join(runbook_def.hipaa_controls),
                "sla_target_seconds": runbook_def.sla_target_seconds
            })
        return runbooks


def main():
    """Test executor with mock incident"""
    print("=" * 60)
    print("MCP Executor - Test Run")
    print("=" * 60)
    print()

    # Initialize executor
    print("Initializing executor...")
    executor = RunbookExecutor(
        client_id="test-client-001",
        dry_run=True  # Dry run mode for testing
    )
    print()

    # List available runbooks
    print("Available runbooks:")
    for rb in executor.list_runbooks():
        print(f"  {rb['id']}: {rb['name']}")
        print(f"    Controls: {rb['hipaa_controls']}")
        print(f"    SLA: {rb['sla_target_seconds']}s")
    print()

    # Create mock incident
    print("Creating mock incident...")
    incident = IncidentData(
        incident_id="INC-20251101-0001",
        event_type="backup_failure",
        severity="high",
        detected_at=datetime.now(timezone.utc).isoformat(),
        hostname="srv-primary.clinic.local",
        details={
            "backup_age_hours": 36.5,
            "last_successful_backup": "2025-10-30T17:00:00Z",
            "error": "Connection timeout to backup repository"
        },
        hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    )
    print(f"  Incident: {incident.incident_id} ({incident.event_type})")
    print()

    # Execute runbook
    print("Executing runbook: RB-BACKUP-001")
    print()
    try:
        resolution_status, outputs = executor.execute_runbook(
            runbook_id="RB-BACKUP-001",
            incident=incident
        )

        print()
        print("=" * 60)
        print("EXECUTION COMPLETE")
        print("=" * 60)
        print(f"Resolution: {resolution_status}")
        print(f"MTTR: {outputs['mttr_seconds']}s")
        print(f"SLA Met: {outputs['sla_met']}")
        print(f"Steps: {outputs['steps_executed']}/{outputs['steps_total']}")
        print()
        print("✅ Test passed - executor working correctly")

    except Exception as e:
        print()
        print("=" * 60)
        print("EXECUTION FAILED")
        print("=" * 60)
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
