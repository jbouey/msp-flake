#!/usr/bin/env python3
"""
Evidence Bundler - Collects and packages incident evidence for HIPAA compliance

This service runs as part of the MCP executor and generates cryptographically-signed
evidence bundles proving that compliance automation actually happened.

Key features:
- Collects incident metadata, runbook execution details, and artifacts
- Validates against JSON schema before signing
- Generates unique bundle IDs (EB-YYYYMMDD-NNNN)
- Prepares bundles for cryptographic signing
- Stores bundles locally before upload to WORM storage

HIPAA Controls: §164.312(b), §164.316(b)(1)(i)
"""

import json
import hashlib
import datetime
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
import jsonschema
from dataclasses import dataclass, asdict


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S UTC'
)
logger = logging.getLogger(__name__)


@dataclass
class IncidentData:
    """Incident details that triggered remediation"""
    incident_id: str
    event_type: str
    severity: str
    detected_at: str
    hostname: str
    details: Dict[str, Any]
    hipaa_controls: List[str]


@dataclass
class RunbookData:
    """Runbook execution information"""
    runbook_id: str
    runbook_version: str
    runbook_hash: str  # SHA256 of runbook YAML file
    steps_total: int
    steps_executed: int


@dataclass
class ExecutionData:
    """Runtime execution metadata"""
    timestamp_start: str
    timestamp_end: str
    operator: str  # service:mcp-executor or user:username
    mttr_seconds: int
    sla_target_seconds: int
    sla_met: bool
    resolution_type: str  # auto | manual


@dataclass
class ActionStep:
    """Individual remediation step"""
    step: int
    action: str
    script_hash: str  # SHA256 of script that ran
    result: str  # ok | failed | skipped
    exit_code: int
    timestamp: str
    error_message: Optional[str] = None
    stdout_excerpt: Optional[str] = None
    stderr_excerpt: Optional[str] = None


class EvidenceBundler:
    """
    Generates evidence bundles from incident remediation

    Usage:
        bundler = EvidenceBundler(client_id="clinic-001")
        bundle = bundler.create_bundle(incident, runbook, execution, actions, artifacts)
        bundler.write_bundle(bundle)
    """

    def __init__(
        self,
        client_id: str,
        output_dir: str = "/var/lib/msp/evidence",
        schema_path: str = "/opt/msp/evidence/schema/evidence-bundle-v1.schema.json"
    ):
        self.client_id = client_id
        self.output_dir = Path(output_dir)
        self.schema_path = Path(schema_path)
        self.bundle_version = "1.0"

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load JSON schema
        try:
            with open(self.schema_path) as f:
                self.schema = json.load(f)
            logger.info(f"Loaded evidence schema from {self.schema_path}")
        except FileNotFoundError:
            logger.warning(f"Schema not found at {self.schema_path}, validation disabled")
            self.schema = None

    def create_bundle(
        self,
        incident: IncidentData,
        runbook: RunbookData,
        execution: ExecutionData,
        actions: List[ActionStep],
        artifacts: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create evidence bundle from incident remediation

        Args:
            incident: Incident details
            runbook: Runbook metadata
            execution: Execution timing and metadata
            actions: List of steps executed
            artifacts: Additional evidence artifacts (logs, checksums, etc.)

        Returns:
            Evidence bundle dictionary ready for signing
        """

        # Generate unique bundle ID
        bundle_id = self._generate_bundle_id()

        # Build bundle structure
        bundle = {
            "bundle_id": bundle_id,
            "bundle_version": self.bundle_version,
            "client_id": self.client_id,
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",

            "incident": asdict(incident),
            "runbook": asdict(runbook),
            "execution": asdict(execution),

            "actions_taken": [asdict(action) for action in actions],

            "artifacts": artifacts,

            "outputs": {
                "resolution_status": self._determine_resolution_status(actions),
                **artifacts.get("outputs", {})
            },

            # Will be populated by this method
            "evidence_bundle_hash": None,

            # Will be populated by signer
            "signatures": {},

            # Will be populated by uploader
            "storage_locations": []
        }

        # Compute bundle hash (excludes signature and storage fields)
        bundle["evidence_bundle_hash"] = self._compute_bundle_hash(bundle)

        logger.info(f"Created evidence bundle: {bundle_id}")
        return bundle

    def validate_bundle(self, bundle: Dict[str, Any]) -> bool:
        """
        Validate bundle against JSON schema

        Args:
            bundle: Evidence bundle to validate

        Returns:
            True if valid, False otherwise

        Raises:
            jsonschema.ValidationError if validation fails
        """
        if self.schema is None:
            logger.warning("Schema validation skipped (schema not loaded)")
            return True

        try:
            jsonschema.validate(instance=bundle, schema=self.schema)
            logger.info(f"Bundle {bundle['bundle_id']} passed schema validation")
            return True
        except jsonschema.ValidationError as e:
            logger.error(f"Schema validation failed: {e.message}")
            logger.error(f"Failed at path: {'.'.join(str(p) for p in e.path)}")
            raise

    def write_bundle(self, bundle: Dict[str, Any]) -> Path:
        """
        Write bundle to local storage

        Args:
            bundle: Evidence bundle to write

        Returns:
            Path to written bundle file
        """
        bundle_id = bundle["bundle_id"]
        bundle_path = self.output_dir / f"{bundle_id}.json"

        # Validate before writing
        self.validate_bundle(bundle)

        # Write with pretty formatting for readability
        with open(bundle_path, 'w') as f:
            json.dump(bundle, f, indent=2, sort_keys=True)

        logger.info(f"Wrote evidence bundle to {bundle_path}")
        return bundle_path

    def _generate_bundle_id(self) -> str:
        """
        Generate unique bundle ID in format: EB-YYYYMMDD-NNNN

        NNNN is a sequential counter for bundles generated on same day
        """
        today = datetime.datetime.utcnow().strftime("%Y%m%d")

        # Find existing bundles from today
        existing = list(self.output_dir.glob(f"EB-{today}-*.json"))

        # Determine next sequence number
        if not existing:
            seq = 1
        else:
            # Extract sequence numbers and get max
            sequences = [
                int(p.stem.split('-')[2])
                for p in existing
                if len(p.stem.split('-')) == 3
            ]
            seq = max(sequences) + 1 if sequences else 1

        bundle_id = f"EB-{today}-{seq:04d}"
        return bundle_id

    def _compute_bundle_hash(self, bundle: Dict[str, Any]) -> str:
        """
        Compute SHA256 hash of bundle for integrity verification

        Excludes signature and storage_locations fields since those are
        added after bundle creation.
        """
        # Create copy without fields that will be added later
        bundle_for_hash = {
            k: v for k, v in bundle.items()
            if k not in ["evidence_bundle_hash", "signatures", "storage_locations"]
        }

        # Serialize deterministically (sorted keys)
        bundle_json = json.dumps(bundle_for_hash, sort_keys=True)

        # Hash
        hash_obj = hashlib.sha256(bundle_json.encode('utf-8'))
        return f"sha256:{hash_obj.hexdigest()}"

    def _determine_resolution_status(self, actions: List[ActionStep]) -> str:
        """
        Determine overall resolution status from action steps

        Returns: success | partial | failed
        """
        if not actions:
            return "failed"

        results = [action.result for action in actions]

        if all(r == "ok" for r in results):
            return "success"
        elif any(r == "ok" for r in results):
            return "partial"
        else:
            return "failed"


class ArtifactCollector:
    """
    Helper class to collect evidence artifacts during runbook execution

    Usage:
        collector = ArtifactCollector()
        collector.add_log_excerpt("backup_log", log_content)
        collector.add_checksum("backup_file", "sha256:abc123...")
        artifacts = collector.get_artifacts()
    """

    def __init__(self):
        self.artifacts = {
            "log_excerpts": {},
            "checksums": {},
            "configurations": {},
            "outputs": {}
        }

    def add_log_excerpt(self, name: str, content: str, max_lines: int = 50):
        """Add log excerpt (limited to max_lines)"""
        lines = content.split('\n')
        if len(lines) > max_lines:
            excerpt = '\n'.join(lines[-max_lines:])
            self.artifacts["log_excerpts"][name] = f"[Last {max_lines} lines]\n{excerpt}"
        else:
            self.artifacts["log_excerpts"][name] = content

    def add_checksum(self, name: str, checksum: str):
        """Add file checksum"""
        self.artifacts["checksums"][name] = checksum

    def add_config(self, name: str, config: Dict[str, Any]):
        """Add configuration snapshot"""
        self.artifacts["configurations"][name] = config

    def add_output(self, name: str, value: Any):
        """Add remediation output value"""
        self.artifacts["outputs"][name] = value

    def get_artifacts(self) -> Dict[str, Any]:
        """Get all collected artifacts"""
        return self.artifacts


# Example usage
if __name__ == "__main__":
    # This demonstrates how the bundler is used during incident response

    # 1. Create bundler instance
    bundler = EvidenceBundler(client_id="clinic-001")

    # 2. Create incident data
    incident = IncidentData(
        incident_id="INC-20251031-0042",
        event_type="backup_failure",
        severity="high",
        detected_at="2025-10-31T14:32:01Z",
        hostname="srv-primary.clinic-001.internal",
        details={
            "backup_age_hours": 36.5,
            "last_successful_backup": "2025-10-30T02:00:00Z",
            "error_message": "Failed to connect to backup repository"
        },
        hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    )

    # 3. Create runbook metadata
    runbook = RunbookData(
        runbook_id="RB-BACKUP-001",
        runbook_version="1.0",
        runbook_hash="sha256:abc123def456...",
        steps_total=4,
        steps_executed=4
    )

    # 4. Create execution data
    execution = ExecutionData(
        timestamp_start="2025-10-31T14:32:01Z",
        timestamp_end="2025-10-31T14:37:23Z",
        operator="service:mcp-executor",
        mttr_seconds=322,
        sla_target_seconds=14400,  # 4 hours
        sla_met=True,
        resolution_type="auto"
    )

    # 5. Create action steps
    actions = [
        ActionStep(
            step=1,
            action="check_backup_logs",
            script_hash="sha256:script1hash...",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:32:15Z",
            stdout_excerpt="Found error: Connection timeout"
        ),
        ActionStep(
            step=2,
            action="verify_disk_space",
            script_hash="sha256:script2hash...",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:32:30Z",
            stdout_excerpt="Available: 45.2 GB"
        ),
        ActionStep(
            step=3,
            action="restart_backup_service",
            script_hash="sha256:script3hash...",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:33:45Z"
        ),
        ActionStep(
            step=4,
            action="trigger_manual_backup",
            script_hash="sha256:script4hash...",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:37:23Z",
            stdout_excerpt="Backup completed successfully"
        )
    ]

    # 6. Collect artifacts
    collector = ArtifactCollector()
    collector.add_log_excerpt("backup_log", "Sample log content here...")
    collector.add_checksum("backup_file", "sha256:backuphash...")
    collector.add_output("backup_completion_hash", "sha256:backuphash...")
    collector.add_output("disk_usage_before", "87%")
    collector.add_output("disk_usage_after", "62%")

    artifacts = collector.get_artifacts()

    # 7. Create and write bundle
    bundle = bundler.create_bundle(
        incident=incident,
        runbook=runbook,
        execution=execution,
        actions=actions,
        artifacts=artifacts
    )

    bundle_path = bundler.write_bundle(bundle)

    print(f"✅ Evidence bundle created: {bundle_path}")
    print(f"   Bundle ID: {bundle['bundle_id']}")
    print(f"   Hash: {bundle['evidence_bundle_hash']}")
    print(f"   Resolution: {bundle['outputs']['resolution_status']}")
