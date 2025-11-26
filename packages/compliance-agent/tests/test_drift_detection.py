#!/usr/bin/env python3
"""
Test Drift Detection with Synthetic Violations

DEPRECATED: This test references old module structure.
Skip for now - drift detection is tested in test_drift.py

Test Cases:
1. Flake hash drift (critical)
2. Patch status drift (critical)
3. Backup drift (critical)
4. Service health drift (high)
5. Encryption drift (critical)
6. Time sync drift (medium)
"""
import pytest
pytestmark = pytest.mark.skip(reason="Deprecated test - uses old module structure")

import asyncio
import json
import tempfile
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DriftTestSetup:
    """Setup synthetic environment for drift testing"""

    def __init__(self, test_dir: Path):
        self.test_dir = test_dir
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def create_baseline(self) -> Path:
        """Create test baseline configuration"""
        baseline = {
            "target_flake_hash": "sha256:abc123original",
            "critical_patch_max_age_days": 7,
            "backup_max_age_hours": 24,
            "restore_test_max_age_days": 30,
            "critical_services": ["sshd", "chronyd", "test-service"],
            "time_max_drift_seconds": 90
        }

        baseline_path = self.test_dir / "baseline.json"
        with open(baseline_path, 'w') as f:
            json.dump(baseline, f, indent=2)

        logger.info(f"✓ Created test baseline at {baseline_path}")
        return baseline_path

    def create_backup_dir(self, scenario: str) -> Path:
        """
        Create backup directory with test data

        Scenarios:
        - fresh: Recent backup (< 24h)
        - stale: Old backup (> 24h)
        - missing: No backup files
        """
        backup_dir = self.test_dir / "backups"
        backup_dir.mkdir(exist_ok=True)

        if scenario == "fresh":
            # Recent backup
            backup_metadata = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "size_gb": 127.4,
                "checksum": "sha256:test123"
            }

            backup_file = backup_dir / "backup-latest.json"
            with open(backup_file, 'w') as f:
                json.dump(backup_metadata, f)

            # Recent restore test
            restore_metadata = {
                "timestamp": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                "files_verified": 3,
                "checksum_valid": True
            }

            restore_file = backup_dir / "restore-test-latest.json"
            with open(restore_file, 'w') as f:
                json.dump(restore_metadata, f)

            logger.info("✓ Created fresh backup scenario")

        elif scenario == "stale":
            # Old backup (2 days ago)
            backup_metadata = {
                "timestamp": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                "size_gb": 125.1,
                "checksum": "sha256:test456"
            }

            backup_file = backup_dir / "backup-old.json"
            with open(backup_file, 'w') as f:
                json.dump(backup_metadata, f)

            logger.info("✓ Created stale backup scenario")

        elif scenario == "missing":
            # No backup files
            logger.info("✓ Created missing backup scenario")

        return backup_dir

    def create_test_config(self) -> Path:
        """Create test configuration file"""
        # Create dummy cert files
        cert_dir = self.test_dir / "certs"
        cert_dir.mkdir(exist_ok=True)

        for cert_file in ["client-cert.pem", "client-key.pem", "ca-cert.pem"]:
            (cert_dir / cert_file).write_text("dummy cert")

        config = {
            "site_id": "test-site-001",
            "mcp_base_url": "https://mcp.test.example.com",
            "mcp_public_key": "0" * 64,  # 32 bytes in hex
            "client_cert": str(cert_dir / "client-cert.pem"),
            "client_key": str(cert_dir / "client-key.pem"),
            "ca_cert": str(cert_dir / "ca-cert.pem"),
            "queue_path": str(self.test_dir / "queue.db"),
            "poll_interval": 60,
            "deployment_mode": "direct"
        }

        config_path = self.test_dir / "config.yaml"
        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        logger.info(f"✓ Created test config at {config_path}")
        return config_path


async def test_drift_detection():
    """Run drift detection tests"""

    print("\n" + "="*60)
    print("DRIFT DETECTION TEST SUITE")
    print("="*60 + "\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir)

        # Setup test environment
        logger.info("Setting up test environment...")
        setup = DriftTestSetup(test_dir)

        baseline_path = setup.create_baseline()
        config_path = setup.create_test_config()

        # Override /etc/msp/baseline.json for testing
        import os
        os.environ['MSP_BASELINE_PATH'] = str(baseline_path)

        # Import after environment setup
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

        from config import Config
        from drift_detector import DriftDetector

        # Load configuration
        config = Config.load(str(config_path))
        logger.info(f"✓ Loaded config for site: {config.site_id}")

        # Test Case 1: All checks passing (baseline)
        print("\n--- Test Case 1: Baseline (All Passing) ---")
        setup.create_backup_dir("fresh")

        detector = DriftDetector(config)
        results = await detector.check_all()

        print_results(results)

        # Test Case 2: Backup drift
        print("\n--- Test Case 2: Backup Drift (Stale Backup) ---")
        setup.create_backup_dir("stale")

        detector = DriftDetector(config)
        results = await detector.check_all()

        print_results(results)

        # Test Case 3: Missing backups
        print("\n--- Test Case 3: Missing Backups ---")
        setup.create_backup_dir("missing")

        detector = DriftDetector(config)
        results = await detector.check_all()

        print_results(results)

        # Test Case 4: Flake hash drift (simulate)
        print("\n--- Test Case 4: Flake Hash Drift ---")
        print("(Note: This would require mock nix command - simulated)")

        # Test Case 5: Service health drift (simulate)
        print("\n--- Test Case 5: Service Health Drift ---")
        print("(Note: This would require mock systemctl - simulated)")

        print("\n" + "="*60)
        print("TEST SUITE COMPLETE")
        print("="*60 + "\n")


def print_results(results: dict):
    """Print drift detection results"""

    for check_name, result in results.items():
        if result.drift_detected:
            status = f"❌ DRIFT ({result.severity.value.upper()})"
            color_code = "\033[91m"  # Red
        else:
            status = "✅ OK"
            color_code = "\033[92m"  # Green

        reset_code = "\033[0m"

        print(f"{color_code}{status}{reset_code} {check_name}")

        if result.drift_detected:
            print(f"   Remediation: {result.remediation_runbook}")
            print(f"   HIPAA Controls: {', '.join(result.hipaa_controls)}")

            # Print key details
            details = result.details
            if 'error' in details:
                print(f"   Error: {details['error']}")
            elif check_name == "backup_status":
                if details.get('backup_drift'):
                    print(f"   Backup age: {details['backup_age_hours']:.1f}h (max: {details['max_backup_age_hours']}h)")
                if details.get('restore_drift'):
                    print(f"   Restore test age: {details['restore_test_age_days']}d (max: {details['max_restore_test_age_days']}d)")

    drift_count = sum(1 for r in results.values() if r.drift_detected)
    total_checks = len(results)

    print(f"\nSummary: {drift_count}/{total_checks} checks detected drift")


if __name__ == '__main__':
    try:
        asyncio.run(test_drift_detection())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        raise
