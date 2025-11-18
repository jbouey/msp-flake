"""
Example Executor Integration with Learning System

This shows how to integrate the learning system into your existing
runbook executor. The key is capturing rich telemetry at every step.

Key integration points:
1. Capture state before/after execution
2. Track each step execution
3. Verify the fix actually worked
4. Trigger learning engine analysis
"""

from typing import Dict, Any, List
from datetime import datetime
import asyncio

from ..schemas.execution_result import ExecutionResult, ExecutionStatus, StepExecution
from ..learning.learning_engine import LearningEngine
from ..review.review_queue import ReviewQueue


class StateCapture:
    """
    Captures system state for before/after comparison

    Extensible - add more checks as needed for your environment.
    """

    async def capture(
        self,
        hostname: str,
        platform: str,
        checks: List[str] = None
    ) -> Dict[str, Any]:
        """
        Capture current system state

        Args:
            hostname: Target system
            platform: windows/linux/darwin
            checks: List of state checks to perform

        Returns:
            Dictionary of state information
        """
        if checks is None:
            checks = ["services", "disk", "cpu", "memory", "processes"]

        state = {
            "hostname": hostname,
            "platform": platform,
            "captured_at": datetime.utcnow().isoformat()
        }

        for check in checks:
            if check == "services":
                state["services"] = await self._check_services(hostname, platform)
            elif check == "disk":
                state["disk"] = await self._check_disk(hostname)
            elif check == "cpu":
                state["cpu"] = await self._check_cpu(hostname)
            elif check == "memory":
                state["memory"] = await self._check_memory(hostname)
            elif check == "processes":
                state["processes"] = await self._check_processes(hostname)

        return state

    async def _check_services(self, hostname: str, platform: str) -> Dict[str, str]:
        """Check status of key services"""
        # TODO: Implement actual service checks via SSH/WinRM
        # Example:
        return {
            "nginx": "running",
            "postgresql": "running",
            "redis": "stopped"
        }

    async def _check_disk(self, hostname: str) -> Dict[str, Any]:
        """Check disk usage"""
        # TODO: Implement actual disk checks
        return {
            "usage_percent": 75,
            "free_gb": 128.5,
            "total_gb": 512.0
        }

    async def _check_cpu(self, hostname: str) -> Dict[str, Any]:
        """Check CPU usage"""
        # TODO: Implement actual CPU checks
        return {
            "usage_percent": 45,
            "load_average": [1.2, 1.5, 1.3]
        }

    async def _check_memory(self, hostname: str) -> Dict[str, Any]:
        """Check memory usage"""
        # TODO: Implement actual memory checks
        return {
            "usage_percent": 62,
            "free_mb": 2048,
            "total_mb": 8192
        }

    async def _check_processes(self, hostname: str) -> List[str]:
        """Check running processes"""
        # TODO: Implement actual process checks
        return ["systemd", "nginx", "postgresql"]

    def compute_diff(self, before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute what changed between before and after

        Returns structured diff showing changes.
        """
        diff = {}

        # Services changed
        if "services" in before and "services" in after:
            services_diff = {}
            all_services = set(before["services"].keys()) | set(after["services"].keys())

            for service in all_services:
                before_status = before["services"].get(service, "unknown")
                after_status = after["services"].get(service, "unknown")

                if before_status != after_status:
                    services_diff[service] = f"{before_status} -> {after_status}"

            if services_diff:
                diff["services"] = services_diff

        # Disk usage changed
        if "disk" in before and "disk" in after:
            before_usage = before["disk"].get("usage_percent", 0)
            after_usage = after["disk"].get("usage_percent", 0)

            if abs(before_usage - after_usage) > 1:  # Changed by more than 1%
                diff["disk_usage"] = f"{before_usage}% -> {after_usage}%"

        # CPU changed
        if "cpu" in before and "cpu" in after:
            before_cpu = before["cpu"].get("usage_percent", 0)
            after_cpu = after["cpu"].get("usage_percent", 0)

            if abs(before_cpu - after_cpu) > 5:  # Changed by more than 5%
                diff["cpu_usage"] = f"{before_cpu}% -> {after_cpu}%"

        return diff


class FixVerifier:
    """
    Verifies that a fix actually worked

    Different incident types need different verification methods.
    """

    async def verify(
        self,
        incident_type: str,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """
        Verify the fix worked

        Args:
            incident_type: Type of incident
            state_before: State before remediation
            state_after: State after remediation

        Returns:
            (passed, method, confidence)
        """
        if incident_type == "service_crash":
            return await self._verify_service_fix(state_before, state_after)

        elif incident_type == "disk_full":
            return await self._verify_disk_fix(state_before, state_after)

        elif incident_type == "high_cpu":
            return await self._verify_cpu_fix(state_before, state_after)

        elif incident_type == "memory_leak":
            return await self._verify_memory_fix(state_before, state_after)

        elif incident_type == "cert_expiry":
            return await self._verify_cert_fix(state_before, state_after)

        else:
            # Unknown incident type - can't verify
            return (None, "no_verification_method", 0.0)

    async def _verify_service_fix(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """Verify service is now running"""
        services_before = state_before.get("services", {})
        services_after = state_after.get("services", {})

        # Check if any stopped service is now running
        for service, status_before in services_before.items():
            status_after = services_after.get(service, "unknown")

            if status_before in ["stopped", "failed"] and status_after == "running":
                return (True, "service_status_check", 0.95)

        return (False, "service_status_check", 0.5)

    async def _verify_disk_fix(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """Verify disk usage decreased"""
        usage_before = state_before.get("disk", {}).get("usage_percent", 100)
        usage_after = state_after.get("disk", {}).get("usage_percent", 100)

        # Check if disk usage decreased by at least 5%
        if usage_before - usage_after >= 5:
            confidence = min(0.9, 0.5 + (usage_before - usage_after) / 100)
            return (True, "disk_usage_check", confidence)

        return (False, "disk_usage_check", 0.3)

    async def _verify_cpu_fix(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """Verify CPU usage decreased"""
        cpu_before = state_before.get("cpu", {}).get("usage_percent", 100)
        cpu_after = state_after.get("cpu", {}).get("usage_percent", 100)

        # Check if CPU usage decreased significantly
        if cpu_before - cpu_after >= 10:
            confidence = min(0.85, 0.5 + (cpu_before - cpu_after) / 200)
            return (True, "cpu_usage_check", confidence)

        return (False, "cpu_usage_check", 0.4)

    async def _verify_memory_fix(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """Verify memory usage decreased"""
        mem_before = state_before.get("memory", {}).get("usage_percent", 100)
        mem_after = state_after.get("memory", {}).get("usage_percent", 100)

        if mem_before - mem_after >= 10:
            confidence = min(0.85, 0.5 + (mem_before - mem_after) / 200)
            return (True, "memory_usage_check", confidence)

        return (False, "memory_usage_check", 0.4)

    async def _verify_cert_fix(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> tuple[bool, str, float]:
        """Verify certificate was renewed"""
        # TODO: Implement cert expiry checks
        # For now, assume success if no error
        return (True, "cert_expiry_check", 0.8)


class RunbookExecutor:
    """
    Example runbook executor with learning integration

    This shows the pattern for capturing rich telemetry.
    """

    def __init__(
        self,
        db: Any,
        llm_client: Any,
        runbook_repo: Any
    ):
        self.db = db
        self.state_capture = StateCapture()
        self.verifier = FixVerifier()

        # Initialize learning system
        review_queue = ReviewQueue(db)
        self.learning_engine = LearningEngine(
            llm_client=llm_client,
            runbook_repo=runbook_repo,
            review_queue=review_queue,
            db=db
        )

    async def execute_runbook(
        self,
        runbook: Dict[str, Any],
        incident: Dict[str, Any],
        params: Dict[str, Any]
    ) -> ExecutionResult:
        """
        Execute a runbook and capture rich telemetry

        This is the TEMPLATE for integration.

        Args:
            runbook: Runbook definition
            incident: Incident that triggered execution
            params: Execution parameters (client_id, hostname, etc.)

        Returns:
            ExecutionResult with complete telemetry
        """
        # Generate IDs
        execution_id = f"exec-{datetime.utcnow().strftime('%Y%m%d')}-{self._generate_sequence()}"
        evidence_bundle_id = f"EB-{datetime.utcnow().strftime('%Y%m%d')}-{self._generate_sequence()}"

        started_at = datetime.utcnow()

        # STEP 1: Capture state BEFORE execution
        state_before = await self.state_capture.capture(
            hostname=params["hostname"],
            platform=runbook["platform"],
            checks=["services", "disk", "cpu", "memory"]
        )

        # STEP 2: Execute the runbook
        executed_steps = []
        success = True
        error_message = None
        error_step = None

        try:
            for i, step in enumerate(runbook["steps"]):
                step_result = await self._execute_step(step, params, i + 1)
                executed_steps.append(step_result)

                if not step_result.success:
                    success = False
                    error_message = step_result.error
                    error_step = step_result.step_number
                    break

        except Exception as e:
            success = False
            error_message = str(e)
            error_step = len(executed_steps)

        # STEP 3: Capture state AFTER execution
        state_after = await self.state_capture.capture(
            hostname=params["hostname"],
            platform=runbook["platform"],
            checks=["services", "disk", "cpu", "memory"]
        )

        # STEP 4: Compute what changed
        state_diff = self.state_capture.compute_diff(state_before, state_after)

        # STEP 5: Verify the fix actually worked
        verification_passed, verification_method, confidence = await self.verifier.verify(
            incident_type=incident["type"],
            state_before=state_before,
            state_after=state_after
        )

        completed_at = datetime.utcnow()

        # STEP 6: Build ExecutionResult
        execution_result = ExecutionResult(
            execution_id=execution_id,
            runbook_id=runbook["id"],
            incident_id=incident["id"],
            incident_type=incident["type"],
            client_id=params["client_id"],
            hostname=params["hostname"],
            platform=runbook["platform"],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
            status=ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILURE,
            success=success,
            verification_passed=verification_passed,
            verification_method=verification_method,
            confidence=confidence,
            state_before=state_before,
            state_after=state_after,
            state_diff=state_diff,
            executed_steps=executed_steps,
            error_message=error_message,
            error_step=error_step,
            evidence_bundle_id=evidence_bundle_id,
            tags=[incident["type"], runbook["platform"]]
        )

        # STEP 7: Store in database
        await self.db.execution_results.insert_one(execution_result.to_dict())

        # STEP 8: TRIGGER LEARNING ENGINE
        # This is where the magic happens - system learns from this execution
        try:
            await self.learning_engine.analyze_execution(execution_result)
        except Exception as e:
            # Don't fail execution if learning fails
            print(f"Learning engine error: {e}")

        return execution_result

    async def _execute_step(
        self,
        step: Dict[str, Any],
        params: Dict[str, Any],
        step_number: int
    ) -> StepExecution:
        """
        Execute a single runbook step

        Captures timing, output, errors for learning.
        """
        started_at = datetime.utcnow()

        try:
            # TODO: Implement actual step execution
            # This would call your existing remediation tools
            # For now, simulate execution
            await asyncio.sleep(0.1)

            output = f"Step {step_number} completed successfully"
            success = True
            error = None

        except Exception as e:
            output = None
            success = False
            error = str(e)

        completed_at = datetime.utcnow()

        return StepExecution(
            step_number=step_number,
            action=step["action"],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=(completed_at - started_at).total_seconds(),
            success=success,
            output=output,
            error=error,
            state_changes={}  # TODO: Track specific state changes per step
        )

    def _generate_sequence(self) -> str:
        """Generate sequence number for IDs"""
        # TODO: Implement proper sequence generation
        import random
        return f"{random.randint(1, 9999):04d}"
