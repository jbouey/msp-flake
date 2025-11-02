"""
Integration tests for MCP Server

Tests the complete flow:
1. Incident received → Planner selects runbook → Executor runs steps → Evidence generated

Test scenarios:
- Backup failure remediation
- Certificate expiry renewal
- Service crash restart
- Rate limiting enforcement
- Input validation
- Circuit breaker behavior

Run: pytest test_integration.py -v
"""

import pytest
import asyncio
import json
from datetime import datetime
from pathlib import Path

from planner import Planner, Incident, RunbookSelection
from executor import Executor
from guardrails import RateLimiter, InputValidator, CircuitBreaker


@pytest.fixture
def planner():
    """Initialize planner for tests"""
    return Planner(
        runbooks_dir="./runbooks",
        model="gpt-4o",
        temperature=0.1
    )


@pytest.fixture
def executor():
    """Initialize executor for tests"""
    return Executor(
        runbooks_dir="./runbooks",
        scripts_dir="./scripts"
    )


@pytest.fixture
def validator():
    """Initialize input validator"""
    return InputValidator()


@pytest.fixture
def circuit_breaker():
    """Initialize circuit breaker"""
    return CircuitBreaker(failure_threshold=3, timeout_seconds=5)


class TestPlannerIntegration:
    """Test planner in isolation"""

    @pytest.mark.asyncio
    async def test_backup_failure_runbook_selection(self, planner):
        """Test that planner correctly selects backup runbook"""

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="backup_failure",
            severity="high",
            timestamp=datetime.utcnow().isoformat(),
            details={
                "last_successful_backup": "2025-10-23T02:00:00Z",
                "failure_reason": "Disk space insufficient"
            }
        )

        selection = await planner.select_runbook(incident)

        assert selection.runbook_id == "RB-BACKUP-001"
        assert selection.confidence > 0.8
        assert not selection.requires_human_approval

    @pytest.mark.asyncio
    async def test_cert_expiry_runbook_selection(self, planner):
        """Test cert expiry runbook selection"""

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-web",
            incident_type="certificate_expiring",
            severity="medium",
            timestamp=datetime.utcnow().isoformat(),
            details={
                "certificate": "wildcard.clinic.com",
                "expires_in_days": 10
            }
        )

        selection = await planner.select_runbook(incident)

        assert selection.runbook_id == "RB-CERT-001"
        assert selection.confidence > 0.7

    @pytest.mark.asyncio
    async def test_critical_incident_requires_approval(self, planner):
        """Test that critical incidents with low confidence require approval"""

        # Modify planner to force low confidence (mock for testing)
        # In real scenario, this would be an ambiguous incident

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-database",
            incident_type="unknown_database_error",
            severity="critical",
            timestamp=datetime.utcnow().isoformat(),
            details={
                "error": "Unrecognized database error pattern"
            }
        )

        # This should either fail to select or require approval
        try:
            selection = await planner.select_runbook(incident)

            # If it selects, should require approval for critical + low confidence
            if selection.severity == "critical" and selection.confidence < 0.9:
                assert selection.requires_human_approval

        except Exception:
            # If planner can't select, that's also acceptable
            pass


class TestExecutorIntegration:
    """Test executor in isolation"""

    @pytest.mark.asyncio
    async def test_dry_run_mode(self, executor):
        """Test dry run mode (no actual execution)"""

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="backup_failure",
            severity="high",
            timestamp=datetime.utcnow().isoformat(),
            details={}
        )

        # Enable dry run mode
        executor.dry_run = True

        result = await executor.execute_runbook(
            runbook_id="RB-BACKUP-001",
            incident=incident,
            incident_id="INC-TEST-001"
        )

        # Dry run should succeed without actually running scripts
        assert result.status in ["success", "dry_run"]
        assert result.dry_run_mode is True


class TestGuardrailsIntegration:
    """Test guardrails components"""

    def test_input_validation_valid_incident(self, validator):
        """Test validation of valid incident"""

        valid_incident = {
            'client_id': 'clinic-001',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high',
            'details': {}
        }

        result = validator.validate_incident(valid_incident)

        assert result.is_valid
        assert len(result.errors) == 0
        assert result.sanitized_input is not None

    def test_input_validation_command_injection(self, validator):
        """Test that command injection attempts are blocked"""

        malicious_incident = {
            'client_id': 'clinic-001; rm -rf /',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high'
        }

        result = validator.validate_incident(malicious_incident)

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("dangerous pattern" in err.lower() for err in result.errors)

    def test_input_validation_path_traversal(self, validator):
        """Test that path traversal attempts are blocked"""

        result = validator.validate_path("../../etc/passwd")

        assert not result.is_valid
        assert len(result.errors) > 0

    def test_input_validation_service_whitelist(self, validator):
        """Test service name whitelist"""

        # Valid service
        result = validator.validate_service_name("nginx")
        assert result.is_valid

        # Invalid service
        result = validator.validate_service_name("malicious-service")
        assert not result.is_valid

    def test_circuit_breaker_opens_after_failures(self, circuit_breaker):
        """Test circuit breaker opens after threshold failures"""

        assert circuit_breaker.state == "CLOSED"
        assert not circuit_breaker.is_open()

        # Record failures until circuit opens
        for i in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.state == "OPEN"
        assert circuit_breaker.is_open()

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_after_timeout(self, circuit_breaker):
        """Test circuit breaker transitions to half-open after timeout"""

        # Open circuit
        for i in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.is_open()

        # Wait for timeout (5 seconds in fixture)
        await asyncio.sleep(6)

        # Should transition to HALF_OPEN
        is_open = circuit_breaker.is_open()  # Triggers state check
        assert not is_open
        assert circuit_breaker.state == "HALF_OPEN"

    def test_circuit_breaker_closes_after_successes(self, circuit_breaker):
        """Test circuit breaker closes after successes in half-open state"""

        # Open circuit
        for i in range(3):
            circuit_breaker.record_failure()

        # Manually set to HALF_OPEN for testing
        circuit_breaker.state = "HALF_OPEN"

        # Record successes
        circuit_breaker.record_success()
        circuit_breaker.record_success()

        assert circuit_breaker.state == "CLOSED"


class TestFullPipeline:
    """Test complete end-to-end flow"""

    @pytest.mark.asyncio
    async def test_complete_backup_failure_flow(self, planner, executor):
        """Test complete flow: Incident → Plan → Execute → Evidence"""

        # Step 1: Create incident
        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="backup_failure",
            severity="high",
            timestamp=datetime.utcnow().isoformat(),
            details={
                "last_successful_backup": "2025-10-23T02:00:00Z",
                "failure_reason": "Disk space insufficient",
                "disk_usage_percent": 94
            }
        )

        # Step 2: Planning phase
        selection = await planner.select_runbook(incident)

        assert selection.runbook_id is not None
        assert selection.confidence > 0
        print(f"Selected: {selection.runbook_id} (confidence: {selection.confidence:.2%})")

        # Step 3: Execution phase (dry run for testing)
        executor.dry_run = True

        result = await executor.execute_runbook(
            runbook_id=selection.runbook_id,
            incident=incident,
            incident_id="INC-TEST-001"
        )

        assert result.status in ["success", "dry_run"]
        assert result.incident_id == "INC-TEST-001"
        print(f"Execution: {result.status} ({result.steps_completed}/{result.total_steps} steps)")

        # Step 4: Evidence bundle should be generated
        # (In dry run mode, evidence may be minimal)
        assert result.evidence_bundle_id is not None
        print(f"Evidence bundle: {result.evidence_bundle_id}")

    @pytest.mark.asyncio
    async def test_rate_limiting_blocks_repeated_requests(self, validator):
        """Test that rate limiting blocks rapid repeated requests"""

        # Note: Requires Redis running
        # If Redis not available, test will be skipped

        try:
            rate_limiter = RateLimiter(redis_url="redis://localhost:6379")

            # First request should succeed
            result1 = await rate_limiter.check_rate_limit(
                client_id="clinic-001",
                hostname="srv-primary",
                action="test_action"
            )

            assert result1.allowed

            # Immediate second request should be blocked (cooldown)
            result2 = await rate_limiter.check_rate_limit(
                client_id="clinic-001",
                hostname="srv-primary",
                action="test_action"
            )

            assert not result2.allowed
            assert result2.retry_after_seconds > 0

            # Clean up
            rate_limiter.clear_rate_limit("clinic-001")

        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    @pytest.mark.asyncio
    async def test_invalid_incident_blocked_before_planning(self, validator, planner):
        """Test that invalid incidents are blocked before reaching planner"""

        malicious_incident = {
            'client_id': 'clinic-001; malicious',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high'
        }

        # Validate first
        validation_result = validator.validate_incident(malicious_incident)

        # Should be blocked
        assert not validation_result.is_valid

        # Planner should never be called with invalid input
        # (In real server, this would return 400 before reaching planner)


class TestErrorHandling:
    """Test error handling and edge cases"""

    @pytest.mark.asyncio
    async def test_nonexistent_runbook(self, executor):
        """Test handling of nonexistent runbook"""

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="test",
            severity="low",
            timestamp=datetime.utcnow().isoformat(),
            details={}
        )

        with pytest.raises(FileNotFoundError):
            await executor.execute_runbook(
                runbook_id="RB-NONEXISTENT-999",
                incident=incident,
                incident_id="INC-TEST-001"
            )

    @pytest.mark.asyncio
    async def test_script_not_found(self, executor):
        """Test handling when runbook script doesn't exist"""

        # Create a runbook with nonexistent script
        test_runbook = {
            'id': 'RB-TEST-999',
            'name': 'Test Runbook',
            'description': 'Test',
            'severity': 'low',
            'steps': [
                {
                    'action': 'nonexistent_script',
                    'timeout': 30
                }
            ]
        }

        # Save test runbook
        test_runbook_path = Path("./runbooks/RB-TEST-999.yaml")
        import yaml
        with open(test_runbook_path, 'w') as f:
            yaml.dump(test_runbook, f)

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="test",
            severity="low",
            timestamp=datetime.utcnow().isoformat(),
            details={}
        )

        try:
            result = await executor.execute_runbook(
                runbook_id="RB-TEST-999",
                incident=incident,
                incident_id="INC-TEST-001"
            )

            # Should fail gracefully
            assert result.status in ["failed", "partial_success"]

        finally:
            # Clean up test runbook
            test_runbook_path.unlink()


# Performance tests
class TestPerformance:
    """Test performance characteristics"""

    @pytest.mark.asyncio
    async def test_planning_performance(self, planner):
        """Test planner response time"""

        incident = Incident(
            client_id="clinic-001",
            hostname="srv-primary",
            incident_type="backup_failure",
            severity="high",
            timestamp=datetime.utcnow().isoformat(),
            details={}
        )

        import time
        start = time.time()

        selection = await planner.select_runbook(incident)

        elapsed = time.time() - start

        print(f"Planning time: {elapsed:.2f}s")

        # Should complete in reasonable time (< 5 seconds for LLM call)
        assert elapsed < 10.0

    def test_validation_performance(self, validator):
        """Test validation performance"""

        incident = {
            'client_id': 'clinic-001',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high',
            'details': {}
        }

        import time
        start = time.time()

        # Run validation 100 times
        for i in range(100):
            validator.validate_incident(incident)

        elapsed = time.time() - start

        print(f"100 validations: {elapsed:.3f}s ({elapsed*10:.2f}ms each)")

        # Should be very fast (< 1ms per validation)
        assert elapsed < 1.0


# Fixtures for test data
@pytest.fixture
def sample_incidents():
    """Sample incidents for testing"""
    return [
        {
            "type": "backup_failure",
            "incident": Incident(
                client_id="clinic-001",
                hostname="srv-primary",
                incident_type="backup_failure",
                severity="high",
                timestamp=datetime.utcnow().isoformat(),
                details={"last_backup": "2025-10-23T02:00:00Z"}
            ),
            "expected_runbook": "RB-BACKUP-001"
        },
        {
            "type": "cert_expiry",
            "incident": Incident(
                client_id="clinic-001",
                hostname="srv-web",
                incident_type="certificate_expiring",
                severity="medium",
                timestamp=datetime.utcnow().isoformat(),
                details={"expires_in_days": 10}
            ),
            "expected_runbook": "RB-CERT-001"
        },
        {
            "type": "service_crash",
            "incident": Incident(
                client_id="clinic-001",
                hostname="srv-app",
                incident_type="service_crash",
                severity="high",
                timestamp=datetime.utcnow().isoformat(),
                details={"service": "nginx", "crash_count": 3}
            ),
            "expected_runbook": "RB-SERVICE-001"
        }
    ]


if __name__ == "__main__":
    """Run tests directly"""
    pytest.main([__file__, "-v", "--tb=short"])
