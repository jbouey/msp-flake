#!/usr/bin/env python3
"""
Integration Tests for Evidence Pipeline

Tests the full evidence collection flow:
1. Bundle creation
2. Schema validation
3. Cryptographic signing
4. Signature verification

Run with: python3 test_integration.py
"""

import sys
import json
from pathlib import Path

from pipeline import EvidencePipeline, MockDataGenerator
from config import EvidenceConfig


def test_pipeline_with_successful_incident():
    """Test: Successful incident remediation generates valid bundle"""

    print("\n" + "=" * 70)
    print("TEST 1: Successful Incident Remediation")
    print("=" * 70)

    # Create mock data
    incident = MockDataGenerator.create_mock_incident(
        incident_id="INC-20251101-9001",
        event_type="backup_failure",
        severity="high"
    )
    runbook = MockDataGenerator.create_mock_runbook()
    execution = MockDataGenerator.create_mock_execution(mttr_seconds=322, sla_met=True)
    actions = MockDataGenerator.create_mock_actions()
    artifacts = MockDataGenerator.create_mock_artifacts()

    # Run pipeline
    pipeline = EvidencePipeline(client_id="test-client-integration")
    bundle_path, sig_path = pipeline.process_incident(
        incident=incident,
        runbook=runbook,
        execution=execution,
        actions=actions,
        artifacts=artifacts
    )

    # Verify bundle was created
    assert bundle_path.exists(), f"Bundle not found: {bundle_path}"
    assert sig_path.exists(), f"Signature not found: {sig_path}"

    # Verify bundle is valid JSON
    with open(bundle_path) as f:
        bundle = json.load(f)

    # Verify required fields
    assert bundle['bundle_id'].startswith('EB-'), "Invalid bundle ID format"
    assert bundle['incident']['incident_id'] == "INC-20251101-9001"
    assert bundle['incident']['severity'] == "high"
    assert bundle['execution']['sla_met'] == True
    assert len(bundle['actions_taken']) == 4

    # Verify signature
    is_valid = pipeline.verify_bundle(str(bundle_path), str(sig_path))
    assert is_valid, "Signature verification failed"

    print(f"✅ Bundle created: {bundle_path.name}")
    print(f"✅ Signature valid: {sig_path.name}")
    print(f"✅ SLA met: {bundle['execution']['sla_met']}")
    print(f"✅ MTTR: {bundle['execution']['mttr_seconds']}s")

    return True


def test_pipeline_with_failed_incident():
    """Test: Failed incident remediation generates valid bundle"""

    print("\n" + "=" * 70)
    print("TEST 2: Failed Incident Remediation")
    print("=" * 70)

    # Create mock data with failures
    incident = MockDataGenerator.create_mock_incident(
        incident_id="INC-20251101-9002",
        event_type="service_crash",
        severity="critical"
    )
    runbook = MockDataGenerator.create_mock_runbook(runbook_id="RB-SERVICE-001")
    execution = MockDataGenerator.create_mock_execution(mttr_seconds=7200, sla_met=False)

    # Create actions with one failure
    from bundler import ActionStep
    actions = [
        ActionStep(
            step=1,
            action="check_service_status",
            script_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            result="ok",
            exit_code=0,
            timestamp="2025-11-01T05:30:00Z",
            stdout_excerpt="Service not running",
            stderr_excerpt=None,
            error_message=None
        ),
        ActionStep(
            step=2,
            action="restart_service",
            script_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            result="failed",
            exit_code=1,
            timestamp="2025-11-01T05:30:15Z",
            stdout_excerpt=None,
            stderr_excerpt="Error: Failed to start service",
            error_message="Service startup timeout after 60 seconds"
        )
    ]

    artifacts = MockDataGenerator.create_mock_artifacts()

    # Run pipeline
    pipeline = EvidencePipeline(client_id="test-client-integration")
    bundle_path, sig_path = pipeline.process_incident(
        incident=incident,
        runbook=runbook,
        execution=execution,
        actions=actions,
        artifacts=artifacts
    )

    # Verify bundle was created
    assert bundle_path.exists()
    assert sig_path.exists()

    # Load and verify bundle
    with open(bundle_path) as f:
        bundle = json.load(f)

    # Verify failure was captured
    assert bundle['execution']['sla_met'] == False
    assert bundle['outputs']['resolution_status'] == "partial"  # Some steps succeeded
    assert any(action['result'] == 'failed' for action in bundle['actions_taken'])

    # Verify signature
    is_valid = pipeline.verify_bundle(str(bundle_path), str(sig_path))
    assert is_valid

    print(f"✅ Bundle created: {bundle_path.name}")
    print(f"✅ Signature valid: {sig_path.name}")
    print(f"⚠️  SLA missed: {bundle['execution']['sla_met']}")
    print(f"⚠️  Resolution: {bundle['outputs']['resolution_status']}")
    print(f"⚠️  Failed action captured: {bundle['actions_taken'][1]['error_message']}")

    return True


def test_bundle_immutability():
    """Test: Modifying bundle invalidates signature"""

    print("\n" + "=" * 70)
    print("TEST 3: Bundle Immutability (Tamper Detection)")
    print("=" * 70)

    # Create a bundle
    incident = MockDataGenerator.create_mock_incident(incident_id="INC-20251101-9003")
    runbook = MockDataGenerator.create_mock_runbook()
    execution = MockDataGenerator.create_mock_execution()
    actions = MockDataGenerator.create_mock_actions()
    artifacts = MockDataGenerator.create_mock_artifacts()

    pipeline = EvidencePipeline(client_id="test-client-integration")
    bundle_path, sig_path = pipeline.process_incident(
        incident=incident,
        runbook=runbook,
        execution=execution,
        actions=actions,
        artifacts=artifacts
    )

    # Verify signature is valid
    assert pipeline.verify_bundle(str(bundle_path), str(sig_path))
    print("✅ Original bundle signature valid")

    # Modify the bundle
    with open(bundle_path) as f:
        bundle = json.load(f)

    bundle['execution']['mttr_seconds'] = 9999  # Tamper with data

    with open(bundle_path, 'w') as f:
        json.dump(bundle, f, indent=2)

    print("⚠️  Bundle tampered (changed MTTR to 9999)")

    # Try to verify - should fail
    from subprocess import CalledProcessError
    try:
        pipeline.verify_bundle(str(bundle_path), str(sig_path))
        print("❌ TEST FAILED: Signature verification should have failed!")
        return False
    except CalledProcessError:
        print("✅ Tampering detected: Signature verification failed as expected")
        return True


def test_configuration_validation():
    """Test: Configuration validation catches missing components"""

    print("\n" + "=" * 70)
    print("TEST 4: Configuration Validation")
    print("=" * 70)

    # Validate current config
    try:
        EvidenceConfig.validate()
        print("✅ Current configuration valid")

        # Print config details
        print("\nConfiguration Details:")
        print(f"  Evidence Dir: {EvidenceConfig.EVIDENCE_DIR}")
        print(f"  Private Key: {EvidenceConfig.PRIVATE_KEY}")
        print(f"  Public Key: {EvidenceConfig.PUBLIC_KEY}")
        print(f"  Schema: {EvidenceConfig.SCHEMA_PATH}")

        return True
    except ValueError as e:
        print(f"❌ Configuration invalid: {e}")
        return False


def test_sequential_bundle_generation():
    """Test: Multiple bundles generate sequential IDs"""

    print("\n" + "=" * 70)
    print("TEST 5: Sequential Bundle ID Generation")
    print("=" * 70)

    pipeline = EvidencePipeline(client_id="test-client-integration")

    bundle_ids = []

    for i in range(3):
        incident = MockDataGenerator.create_mock_incident(
            incident_id=f"INC-20251101-900{i+4}"
        )
        runbook = MockDataGenerator.create_mock_runbook()
        execution = MockDataGenerator.create_mock_execution()
        actions = MockDataGenerator.create_mock_actions()
        artifacts = MockDataGenerator.create_mock_artifacts()

        bundle_path, _ = pipeline.process_incident(
            incident=incident,
            runbook=runbook,
            execution=execution,
            actions=actions,
            artifacts=artifacts
        )

        with open(bundle_path) as f:
            bundle = json.load(f)

        bundle_ids.append(bundle['bundle_id'])
        print(f"  Created: {bundle['bundle_id']}")

    # Verify IDs are sequential
    assert len(bundle_ids) == 3
    assert all(bid.startswith('EB-20251101-') for bid in bundle_ids)

    print(f"✅ Generated {len(bundle_ids)} sequential bundle IDs")

    return True


def run_all_tests():
    """Run all integration tests"""

    print()
    print("=" * 70)
    print("EVIDENCE PIPELINE INTEGRATION TEST SUITE")
    print("=" * 70)

    tests = [
        ("Successful Incident", test_pipeline_with_successful_incident),
        ("Failed Incident", test_pipeline_with_failed_incident),
        ("Bundle Immutability", test_bundle_immutability),
        ("Configuration Validation", test_configuration_validation),
        ("Sequential Bundle IDs", test_sequential_bundle_generation)
    ]

    results = []

    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"\n❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, result, _ in results if result)
    total = len(results)

    for name, result, error in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
        if error:
            print(f"        Error: {error}")

    print()
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
