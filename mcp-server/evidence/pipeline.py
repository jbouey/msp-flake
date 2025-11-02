#!/usr/bin/env python3
"""
Evidence Pipeline - Integration Module

Coordinates bundler, signer, and (future) uploader.
Called by MCP executor after each runbook execution to create
tamper-evident evidence bundles.

Usage:
    pipeline = EvidencePipeline(client_id="clinic-001")

    bundle_path, sig_path = await pipeline.process_incident(
        incident=incident_data,
        runbook=runbook_metadata,
        execution=execution_metadata,
        actions=action_steps,
        artifacts=collected_artifacts
    )
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple, Any

from bundler import (
    EvidenceBundler,
    IncidentData,
    RunbookData,
    ExecutionData,
    ActionStep
)
from signer import EvidenceSigner
from config import EvidenceConfig

# Optional: Import uploader if WORM storage is configured
try:
    from uploader import EvidenceUploader
    UPLOADER_AVAILABLE = True
except ImportError:
    UPLOADER_AVAILABLE = False
    logger.warning("Evidence uploader not available - WORM storage disabled")


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class EvidencePipeline:
    """
    Orchestrates evidence collection, signing, and storage

    This is the main entry point for evidence generation.
    MCP executor calls this after each runbook execution.
    """

    def __init__(self, client_id: str, config: EvidenceConfig = None, enable_worm: bool = None):
        """
        Initialize evidence pipeline

        Args:
            client_id: Client identifier (e.g., "clinic-001")
            config: Configuration object (defaults to EvidenceConfig)
            enable_worm: Enable WORM storage upload (defaults to checking env var MSP_WORM_BUCKET)
        """
        self.client_id = client_id
        self.config = config or EvidenceConfig

        # Validate configuration on init
        try:
            self.config.validate()
        except ValueError as e:
            logger.error(f"Configuration validation failed: {e}")
            raise

        # Initialize components
        self.bundler = EvidenceBundler(
            client_id=client_id,
            output_dir=str(self.config.EVIDENCE_DIR),
            schema_path=str(self.config.SCHEMA_PATH)
        )

        self.signer = EvidenceSigner(
            private_key_path=str(self.config.PRIVATE_KEY),
            public_key_path=str(self.config.PUBLIC_KEY)
        )

        # Initialize uploader if WORM storage is enabled
        self.uploader = None
        worm_bucket = os.getenv('MSP_WORM_BUCKET')

        if enable_worm is None:
            enable_worm = bool(worm_bucket)

        if enable_worm:
            if not UPLOADER_AVAILABLE:
                logger.warning("WORM storage requested but uploader not available")
            elif not worm_bucket:
                logger.warning("WORM storage requested but MSP_WORM_BUCKET not set")
            else:
                try:
                    self.uploader = EvidenceUploader(
                        bucket_name=worm_bucket,
                        aws_region=os.getenv('AWS_REGION', 'us-east-1'),
                        retention_days=90
                    )
                    logger.info(f"WORM storage enabled: {worm_bucket}")
                except Exception as e:
                    logger.warning(f"Failed to initialize WORM storage: {e}")

        logger.info(f"Evidence pipeline initialized for client: {client_id}")

    def process_incident(
        self,
        incident: IncidentData,
        runbook: RunbookData,
        execution: ExecutionData,
        actions: List[ActionStep],
        artifacts: Dict[str, Any]
    ) -> Tuple[Path, Path]:
        """
        Process incident and generate signed evidence bundle

        This is the main method called by MCP executor.

        Args:
            incident: Incident details that triggered remediation
            runbook: Runbook metadata
            execution: Execution timing and metadata
            actions: List of remediation steps executed
            artifacts: Additional evidence artifacts

        Returns:
            Tuple of (bundle_path, signature_path)

        Raises:
            Exception if bundle creation, validation, or signing fails
        """
        try:
            # Step 1: Create evidence bundle
            logger.info(f"Creating evidence bundle for incident: {incident.incident_id}")
            bundle = self.bundler.create_bundle(
                incident=incident,
                runbook=runbook,
                execution=execution,
                actions=actions,
                artifacts=artifacts
            )

            bundle_id = bundle['bundle_id']
            logger.info(f"Evidence bundle created: {bundle_id}")

            # Step 2: Validate bundle against schema
            logger.info(f"Validating bundle: {bundle_id}")
            self.bundler.validate_bundle(bundle)
            logger.info(f"Bundle validation passed: {bundle_id}")

            # Step 3: Write bundle to disk
            logger.info(f"Writing bundle to disk: {bundle_id}")
            bundle_path = self.bundler.write_bundle(bundle)
            logger.info(f"Bundle written: {bundle_path}")

            # Step 4: Sign bundle
            logger.info(f"Signing bundle: {bundle_id}")

            # Set COSIGN_PASSWORD environment variable for signer
            os.environ['COSIGN_PASSWORD'] = self.config.COSIGN_PASSWORD

            sig_path = self.signer.sign_bundle(str(bundle_path))
            logger.info(f"Bundle signed: {sig_path}")

            # Step 5: Verify signature immediately
            logger.info(f"Verifying signature: {bundle_id}")
            self.signer.verify_signature(str(bundle_path), str(sig_path))
            logger.info(f"Signature verified: {bundle_id}")

            # Step 6: Upload to WORM storage (if enabled)
            if self.uploader:
                logger.info(f"Uploading to WORM storage: {bundle_id}")
                try:
                    bundle_uri, sig_uri = self.uploader.upload_bundle(
                        bundle_path=bundle_path,
                        signature_path=Path(sig_path),
                        client_id=self.client_id
                    )
                    logger.info(f"WORM storage upload complete: {bundle_uri}")

                    # Update bundle with storage locations
                    # (In production, would re-write bundle with storage_locations)
                    # For now, just log the URIs
                    logger.info(f"   Bundle URI: {bundle_uri}")
                    logger.info(f"   Signature URI: {sig_uri}")

                except Exception as e:
                    # Don't fail the whole pipeline if WORM upload fails
                    # Evidence is still stored locally and signed
                    logger.error(f"WORM storage upload failed (non-fatal): {e}")

            # Success
            logger.info(f"✅ Evidence pipeline complete for {bundle_id}")
            logger.info(f"   Bundle: {bundle_path}")
            logger.info(f"   Signature: {sig_path}")
            logger.info(f"   Bundle hash: {bundle['evidence_bundle_hash']}")

            return bundle_path, Path(sig_path)

        except Exception as e:
            logger.error(f"❌ Evidence pipeline failed: {e}")
            raise

    def verify_bundle(self, bundle_path: str, sig_path: str = None) -> bool:
        """
        Verify an existing evidence bundle

        Args:
            bundle_path: Path to evidence bundle JSON
            sig_path: Path to signature (defaults to bundle_path + .bundle)

        Returns:
            True if verification succeeds
        """
        return self.signer.verify_signature(bundle_path, sig_path)


class MockDataGenerator:
    """
    Helper class to generate mock incident data for testing

    In production, this data comes from the actual incident detection
    and runbook execution systems.
    """

    @staticmethod
    def create_mock_incident(
        incident_id: str = "INC-20251101-0001",
        event_type: str = "backup_failure",
        severity: str = "high",
        hostname: str = "srv-primary.clinic.local"
    ) -> IncidentData:
        """Generate mock incident data"""
        return IncidentData(
            incident_id=incident_id,
            event_type=event_type,
            severity=severity,
            detected_at="2025-11-01T05:30:00Z",
            hostname=hostname,
            details={
                "backup_age_hours": 36.5,
                "last_successful_backup": "2025-10-30T17:00:00Z",
                "error": "Connection timeout to backup repository"
            },
            hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
        )

    @staticmethod
    def create_mock_runbook(
        runbook_id: str = "RB-BACKUP-001"
    ) -> RunbookData:
        """Generate mock runbook metadata"""
        return RunbookData(
            runbook_id=runbook_id,
            runbook_version="1.0",
            runbook_hash="sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            steps_total=4,
            steps_executed=4
        )

    @staticmethod
    def create_mock_execution(
        mttr_seconds: int = 322,
        sla_met: bool = True
    ) -> ExecutionData:
        """Generate mock execution metadata"""
        return ExecutionData(
            timestamp_start="2025-11-01T05:30:00Z",
            timestamp_end="2025-11-01T05:35:22Z",
            operator="service:mcp-executor",
            mttr_seconds=mttr_seconds,
            sla_target_seconds=14400,  # 4 hours
            sla_met=sla_met,
            resolution_type="auto"
        )

    @staticmethod
    def create_mock_actions() -> List[ActionStep]:
        """Generate mock action steps"""
        return [
            ActionStep(
                step=1,
                action="check_backup_logs",
                script_hash="sha256:1111111111111111111111111111111111111111111111111111111111111111",
                result="ok",
                exit_code=0,
                timestamp="2025-11-01T05:30:15Z",
                stdout_excerpt="Found error: Connection timeout to repository",
                stderr_excerpt=None,
                error_message=None
            ),
            ActionStep(
                step=2,
                action="verify_disk_space",
                script_hash="sha256:2222222222222222222222222222222222222222222222222222222222222222",
                result="ok",
                exit_code=0,
                timestamp="2025-11-01T05:30:45Z",
                stdout_excerpt="Available: 142.3 GB",
                stderr_excerpt=None,
                error_message=None
            ),
            ActionStep(
                step=3,
                action="restart_backup_service",
                script_hash="sha256:3333333333333333333333333333333333333333333333333333333333333333",
                result="ok",
                exit_code=0,
                timestamp="2025-11-01T05:32:12Z",
                stdout_excerpt=None,
                stderr_excerpt=None,
                error_message=None
            ),
            ActionStep(
                step=4,
                action="trigger_manual_backup",
                script_hash="sha256:4444444444444444444444444444444444444444444444444444444444444444",
                result="ok",
                exit_code=0,
                timestamp="2025-11-01T05:35:22Z",
                stdout_excerpt="Backup completed successfully: 12.4 GB transferred",
                stderr_excerpt=None,
                error_message=None
            )
        ]

    @staticmethod
    def create_mock_artifacts() -> Dict[str, Any]:
        """Generate mock artifacts"""
        from bundler import ArtifactCollector

        collector = ArtifactCollector()
        collector.add_log_excerpt(
            "backup_log",
            "[2025-11-01 05:30:00] Starting backup job...\n"
            "[2025-11-01 05:30:05] ERROR: Connection timeout to backup.example.com\n"
            "[2025-11-01 05:32:15] Backup service restarted\n"
            "[2025-11-01 05:32:30] Starting backup job...\n"
            "[2025-11-01 05:35:22] Backup completed: 12.4 GB"
        )
        collector.add_checksum(
            "backup_file",
            "sha256:5555555555555555555555555555555555555555555555555555555555555555"
        )
        collector.add_output("disk_usage_before", "87%")
        collector.add_output("disk_usage_after", "89%")
        collector.add_output("backup_size_gb", 12.4)
        collector.add_output("backup_duration_seconds", 192)

        return collector.get_artifacts()


if __name__ == "__main__":
    """
    Test the evidence pipeline with mock data
    """
    print("=" * 60)
    print("Evidence Pipeline Integration Test")
    print("=" * 60)
    print()

    # Generate mock data
    print("Generating mock incident data...")
    incident = MockDataGenerator.create_mock_incident()
    runbook = MockDataGenerator.create_mock_runbook()
    execution = MockDataGenerator.create_mock_execution()
    actions = MockDataGenerator.create_mock_actions()
    artifacts = MockDataGenerator.create_mock_artifacts()

    print(f"  Incident: {incident.incident_id} ({incident.event_type})")
    print(f"  Runbook: {runbook.runbook_id}")
    print(f"  Execution: {execution.mttr_seconds}s MTTR")
    print(f"  Actions: {len(actions)} steps")
    print()

    # Create pipeline
    print("Initializing evidence pipeline...")
    pipeline = EvidencePipeline(client_id="test-client-001")
    print()

    # Process incident
    print("Processing incident through evidence pipeline...")
    print()

    try:
        bundle_path, sig_path = pipeline.process_incident(
            incident=incident,
            runbook=runbook,
            execution=execution,
            actions=actions,
            artifacts=artifacts
        )

        print()
        print("=" * 60)
        print("✅ INTEGRATION TEST PASSED")
        print("=" * 60)
        print()
        print(f"Evidence Bundle: {bundle_path}")
        print(f"Signature: {sig_path}")
        print()
        print(f"Bundle size: {bundle_path.stat().st_size:,} bytes")
        print(f"Signature size: {sig_path.stat().st_size:,} bytes")
        print()
        print("You can verify the signature with:")
        print(f"  cosign verify-blob \\")
        print(f"    --key ~/msp-production/signing-keys/private-key.pub \\")
        print(f"    --bundle {sig_path} \\")
        print(f"    {bundle_path}")

    except Exception as e:
        print()
        print("=" * 60)
        print("❌ INTEGRATION TEST FAILED")
        print("=" * 60)
        print()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
