"""
ExecutionResult Schema - Rich Telemetry for Runbook Execution

This is the data goldmine for learning. Every runbook execution must populate this schema
with comprehensive telemetry about what happened, why, and what changed.

The richer the data captured here, the better the learning engine can improve runbooks.
"""

from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


class ExecutionStatus(str, Enum):
    """Overall execution status"""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class FailureType(str, Enum):
    """Categories of runbook failures for learning"""
    WRONG_DIAGNOSIS = "wrong_diagnosis"        # Misclassified incident
    WRONG_RUNBOOK = "wrong_runbook"            # Right diagnosis, wrong solution
    RUNBOOK_INSUFFICIENT = "runbook_insufficient"  # Runbook incomplete
    ENVIRONMENT_DIFF = "environment_difference"    # Env-specific issue
    EXTERNAL_DEPENDENCY = "external_dependency"    # External service issue
    PERMISSION_DENIED = "permission_denied"        # Access/auth issue


class StepExecution(BaseModel):
    """Telemetry for a single runbook step"""
    step_number: int = Field(..., description="Step index in runbook")
    action: str = Field(..., description="Action name executed")
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    success: bool
    output: Optional[str] = Field(None, description="Step output/logs")
    error: Optional[str] = Field(None, description="Error message if failed")
    state_changes: Dict[str, Any] = Field(default_factory=dict, description="What this step changed")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ExecutionResult(BaseModel):
    """
    Rich telemetry from runbook execution
    THIS IS THE DATA GOLDMINE FOR LEARNING

    Every field here contributes to the learning engine's ability to:
    1. Understand what went wrong
    2. Generate better runbooks
    3. Improve incident classification
    4. Build confidence metrics
    """

    # ============================================================================
    # IDENTITY - Who/What/Where
    # ============================================================================
    execution_id: str = Field(..., description="Unique execution ID (exec-YYYYMMDD-NNNN)")
    runbook_id: str = Field(..., description="Which runbook ran (RB-XXX-YYY-NNN)")
    incident_id: str = Field(..., description="Which incident triggered this (inc-YYYYMMDD-NNNN)")
    incident_type: str = Field(..., description="Classified incident type (service_crash, disk_full, etc.)")

    # Context
    client_id: str = Field(..., description="Client identifier")
    hostname: str = Field(..., description="Target system hostname")
    platform: str = Field(..., description="windows/linux/darwin")

    # ============================================================================
    # TIMING - When did things happen
    # ============================================================================
    started_at: datetime = Field(..., description="Execution start timestamp")
    completed_at: datetime = Field(..., description="Execution completion timestamp")
    duration_seconds: float = Field(..., description="Total execution time")

    # ============================================================================
    # SUCCESS METRICS - Did it work?
    # ============================================================================
    status: ExecutionStatus = Field(..., description="Overall status (success/failure/partial)")
    success: bool = Field(..., description="Did runbook complete without errors?")
    verification_passed: Optional[bool] = Field(None, description="Did fix actually work?")
    verification_method: Optional[str] = Field(None, description="How we verified (metric_check, service_test, etc.)")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Confidence that fix worked (0.0-1.0)")

    # ============================================================================
    # STATE CAPTURE - CRITICAL FOR LEARNING
    # ============================================================================
    state_before: Dict[str, Any] = Field(
        default_factory=dict,
        description="System state before execution (services, metrics, files, etc.)"
    )
    state_after: Dict[str, Any] = Field(
        default_factory=dict,
        description="System state after execution"
    )
    state_diff: Dict[str, Any] = Field(
        default_factory=dict,
        description="What changed (computed from before/after)"
    )

    # ============================================================================
    # EXECUTION TRACE - What actually happened
    # ============================================================================
    executed_steps: List[StepExecution] = Field(
        default_factory=list,
        description="Detailed trace of each step execution"
    )

    # ============================================================================
    # ERROR DETAILS - If it failed, why?
    # ============================================================================
    error_message: Optional[str] = Field(None, description="Primary error message")
    error_step: Optional[int] = Field(None, description="Which step failed (step_number)")
    error_traceback: Optional[str] = Field(None, description="Full stack trace if available")
    retry_count: int = Field(0, description="How many times we retried")

    # ============================================================================
    # LEARNING SIGNALS - Human feedback and classification
    # ============================================================================
    was_correct_runbook: Optional[bool] = Field(
        None,
        description="Did we pick the right fix? (set after human review)"
    )
    was_correct_diagnosis: Optional[bool] = Field(
        None,
        description="Was incident classification correct?"
    )
    failure_type: Optional[FailureType] = Field(
        None,
        description="Categorized failure type (set by learning engine)"
    )
    manual_intervention_needed: bool = Field(
        False,
        description="Did a human have to step in?"
    )
    human_feedback: Optional[str] = Field(
        None,
        description="Free-form feedback from reviewer"
    )

    # ============================================================================
    # GENERATED ARTIFACTS - Evidence and compliance
    # ============================================================================
    evidence_bundle_id: str = Field(..., description="Evidence bundle ID (EB-YYYYMMDD-NNNN)")
    log_artifacts: List[str] = Field(
        default_factory=list,
        description="Paths to log files captured"
    )

    # ============================================================================
    # METADATA - For tracking and organization
    # ============================================================================
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    notes: Optional[str] = Field(None, description="Additional notes")

    @validator('duration_seconds', pre=True, always=True)
    def compute_duration(cls, v, values):
        """Auto-compute duration if not provided"""
        if v is None or v == 0:
            if 'started_at' in values and 'completed_at' in values:
                start = values['started_at']
                end = values['completed_at']
                if isinstance(start, datetime) and isinstance(end, datetime):
                    return (end - start).total_seconds()
        return v

    @validator('confidence')
    def validate_confidence(cls, v):
        """Ensure confidence is between 0 and 1"""
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
        json_schema_extra = {
            "example": {
                "execution_id": "exec-20251110-0001",
                "runbook_id": "RB-WIN-SERVICE-001",
                "incident_id": "inc-20251110-0042",
                "incident_type": "service_crash",
                "client_id": "clinic-001",
                "hostname": "srv-dc01",
                "platform": "windows",
                "started_at": "2025-11-10T14:32:01Z",
                "completed_at": "2025-11-10T14:35:23Z",
                "duration_seconds": 202,
                "status": "failure",
                "success": False,
                "verification_passed": False,
                "verification_method": "service_status_check",
                "confidence": 0.0,
                "state_before": {
                    "service_status": "stopped",
                    "cpu_usage": 12,
                    "memory_mb": 2048,
                    "error_logs_count": 45
                },
                "state_after": {
                    "service_status": "stopped",
                    "cpu_usage": 12,
                    "memory_mb": 2048,
                    "error_logs_count": 47
                },
                "state_diff": {
                    "service_status": "no_change",
                    "error_logs_count": "+2"
                },
                "executed_steps": [
                    {
                        "step_number": 1,
                        "action": "check_service_status",
                        "started_at": "2025-11-10T14:32:01Z",
                        "completed_at": "2025-11-10T14:32:03Z",
                        "duration_seconds": 2,
                        "success": True,
                        "output": "Service is stopped"
                    },
                    {
                        "step_number": 2,
                        "action": "start_service",
                        "started_at": "2025-11-10T14:32:03Z",
                        "completed_at": "2025-11-10T14:32:05Z",
                        "duration_seconds": 2,
                        "success": False,
                        "error": "Service failed to start: dependency missing"
                    }
                ],
                "error_message": "Service failed to start: dependency missing",
                "error_step": 2,
                "retry_count": 0,
                "failure_type": "runbook_insufficient",
                "evidence_bundle_id": "EB-20251110-0001",
                "tags": ["windows", "service", "failure"]
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return self.model_dump(mode='json')

    def get_summary(self) -> str:
        """Human-readable summary of execution"""
        status_emoji = "✅" if self.success else "❌"
        verification_status = ""
        if self.verification_passed is not None:
            verification_status = " (verified ✅)" if self.verification_passed else " (verification failed ❌)"

        return (
            f"{status_emoji} {self.runbook_id} on {self.hostname}\n"
            f"   Duration: {self.duration_seconds:.1f}s{verification_status}\n"
            f"   Incident: {self.incident_type}\n"
            f"   {self.error_message if self.error_message else 'Success'}"
        )
