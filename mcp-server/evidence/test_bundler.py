#!/usr/bin/env python3
"""
Test Evidence Bundler

Quick validation that bundler creates valid bundles
Run with: python test_bundler.py
"""

import sys
import json
from pathlib import Path
from bundler import (
    EvidenceBundler,
    IncidentData,
    RunbookData,
    ExecutionData,
    ActionStep,
    ArtifactCollector
)


def test_bundle_creation():
    """Test creating a complete evidence bundle"""
    print("Testing evidence bundle creation...\n")

    # Create bundler (use /tmp for testing)
    bundler = EvidenceBundler(
        client_id="test-client-001",
        output_dir="/tmp/msp-evidence-test",
        schema_path=str(Path(__file__).parent.parent.parent / "opt/msp/evidence/schema/evidence-bundle-v1.schema.json")
    )

    # Create test incident
    incident = IncidentData(
        incident_id="INC-20251031-0001",
        event_type="backup_failure",
        severity="high",
        detected_at="2025-10-31T14:32:01Z",
        hostname="test-server.example.com",
        details={
            "backup_age_hours": 36.5,
            "error": "Connection timeout"
        },
        hipaa_controls=["164.308(a)(7)(ii)(A)"]
    )

    # Create runbook metadata
    runbook = RunbookData(
        runbook_id="RB-BACKUP-001",
        runbook_version="1.0",
        runbook_hash="sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        steps_total=4,
        steps_executed=4
    )

    # Create execution data
    execution = ExecutionData(
        timestamp_start="2025-10-31T14:32:01Z",
        timestamp_end="2025-10-31T14:37:23Z",
        operator="service:mcp-executor",
        mttr_seconds=322,
        sla_target_seconds=14400,
        sla_met=True,
        resolution_type="auto"
    )

    # Create action steps
    actions = [
        ActionStep(
            step=1,
            action="check_backup_logs",
            script_hash="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:32:15Z",
            stdout_excerpt="Error found: Connection timeout"
        ),
        ActionStep(
            step=2,
            action="verify_disk_space",
            script_hash="sha256:2222222222222222222222222222222222222222222222222222222222222222",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:32:30Z",
            stdout_excerpt="Available: 45.2 GB"
        ),
        ActionStep(
            step=3,
            action="restart_backup_service",
            script_hash="sha256:3333333333333333333333333333333333333333333333333333333333333333",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:33:45Z"
        ),
        ActionStep(
            step=4,
            action="trigger_manual_backup",
            script_hash="sha256:4444444444444444444444444444444444444444444444444444444444444444",
            result="ok",
            exit_code=0,
            timestamp="2025-10-31T14:37:23Z",
            stdout_excerpt="Backup completed successfully"
        )
    ]

    # Collect artifacts
    collector = ArtifactCollector()
    collector.add_log_excerpt("backup_log", "Sample backup log content...")
    collector.add_checksum("backup_file", "sha256:5555555555555555555555555555555555555555555555555555555555555555")
    collector.add_output("disk_usage_before", "87%")
    collector.add_output("disk_usage_after", "62%")

    artifacts = collector.get_artifacts()

    # Create bundle
    print("Creating bundle...")
    bundle = bundler.create_bundle(
        incident=incident,
        runbook=runbook,
        execution=execution,
        actions=actions,
        artifacts=artifacts
    )

    print(f"✅ Bundle created: {bundle['bundle_id']}")
    print(f"   Hash: {bundle['evidence_bundle_hash']}")
    print(f"   Resolution: {bundle['outputs']['resolution_status']}")

    # Validate bundle
    print("\nValidating bundle against schema...")
    try:
        bundler.validate_bundle(bundle)
        print("✅ Bundle passed schema validation")
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        return False

    # Write bundle
    print("\nWriting bundle to disk...")
    bundle_path = bundler.write_bundle(bundle)
    print(f"✅ Bundle written: {bundle_path}")

    # Verify file exists and is valid JSON
    with open(bundle_path) as f:
        loaded_bundle = json.load(f)

    print(f"✅ Bundle file is valid JSON ({bundle_path.stat().st_size} bytes)")

    # Display bundle structure
    print("\nBundle structure:")
    print(f"  - Bundle ID: {loaded_bundle['bundle_id']}")
    print(f"  - Client: {loaded_bundle['client_id']}")
    print(f"  - Incident: {loaded_bundle['incident']['incident_id']}")
    print(f"  - Runbook: {loaded_bundle['runbook']['runbook_id']}")
    print(f"  - Actions: {len(loaded_bundle['actions_taken'])} steps")
    print(f"  - HIPAA Controls: {', '.join(loaded_bundle['incident']['hipaa_controls'])}")
    print(f"  - MTTR: {loaded_bundle['execution']['mttr_seconds']}s")
    print(f"  - SLA Met: {loaded_bundle['execution']['sla_met']}")

    print("\n✅ All tests passed!")
    print(f"\nTest bundle location: {bundle_path}")
    print("You can now test signing with: python signer.py <bundle_path>")

    return True


if __name__ == "__main__":
    success = test_bundle_creation()
    sys.exit(0 if success else 1)
