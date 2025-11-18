"""
Data models for compliance agent.

Defines evidence bundle schema and related types per CLAUDE.md requirements.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, validator
import uuid


# ============================================================================
# Evidence Bundle Models (CLAUDE.md Section 7)
# ============================================================================


class ActionTaken(BaseModel):
    """Single remediation action within evidence bundle."""

    step: int = Field(
        ...,
        ge=1,
        description="Step number in remediation sequence"
    )
    action: str = Field(
        ...,
        description="Action type (e.g., nixos-rebuild, systemctl restart)"
    )
    command: Optional[str] = Field(
        default=None,
        description="Actual command executed"
    )
    exit_code: Optional[int] = Field(
        default=None,
        description="Command exit code"
    )
    duration_sec: Optional[float] = Field(
        default=None,
        ge=0,
        description="Action duration in seconds"
    )
    result: Optional[str] = Field(
        default=None,
        description="Action result summary"
    )


class EvidenceBundle(BaseModel):
    """
    Complete evidence bundle for compliance audit trail.

    Per CLAUDE.md requirements:
    - All metadata fields
    - Pre/post state capture
    - Actions taken with details
    - Outcome classification
    - HIPAA control citations
    - Rollback capability
    """

    # ========================================================================
    # Metadata
    # ========================================================================

    version: str = Field(
        default="1.0",
        description="Evidence bundle format version"
    )

    bundle_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique bundle identifier (UUID v4)"
    )

    site_id: str = Field(
        ...,
        description="Site identifier"
    )

    host_id: str = Field(
        ...,
        description="Host identifier"
    )

    deployment_mode: Literal["reseller", "direct"] = Field(
        ...,
        description="Deployment mode"
    )

    reseller_id: Optional[str] = Field(
        default=None,
        description="MSP reseller ID (if mode=reseller)"
    )

    # ========================================================================
    # Timestamps
    # ========================================================================

    timestamp_start: datetime = Field(
        ...,
        description="Action start time (UTC)"
    )

    timestamp_end: datetime = Field(
        ...,
        description="Action end time (UTC)"
    )

    # ========================================================================
    # Policy & Configuration
    # ========================================================================

    policy_version: str = Field(
        ...,
        description="Policy version identifier"
    )

    ruleset_hash: Optional[str] = Field(
        default=None,
        description="SHA256 hash of compliance ruleset"
    )

    nixos_revision: Optional[str] = Field(
        default=None,
        description="NixOS flake revision (commit hash or lastModified)"
    )

    derivation_digest: Optional[str] = Field(
        default=None,
        description="SHA256 hash of NixOS derivation"
    )

    ntp_offset_ms: Optional[int] = Field(
        default=None,
        description="NTP offset in milliseconds at check time"
    )

    # ========================================================================
    # Check Information
    # ========================================================================

    check: str = Field(
        ...,
        description="Check type (patching, av_health, backup, logging, firewall, encryption)"
    )

    hipaa_controls: Optional[List[str]] = Field(
        default=None,
        description="HIPAA Security Rule control citations"
    )

    # ========================================================================
    # State Capture
    # ========================================================================

    pre_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="System state before remediation"
    )

    post_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="System state after remediation"
    )

    # ========================================================================
    # Actions
    # ========================================================================

    action_taken: List[ActionTaken] = Field(
        default_factory=list,
        description="Remediation actions performed"
    )

    # ========================================================================
    # Rollback
    # ========================================================================

    rollback_available: bool = Field(
        default=False,
        description="Whether rollback is possible"
    )

    rollback_generation: Optional[int] = Field(
        default=None,
        description="NixOS generation to rollback to"
    )

    # ========================================================================
    # Outcome
    # ========================================================================

    outcome: Literal["success", "failed", "reverted", "deferred", "alert", "rejected", "expired"] = Field(
        ...,
        description="Remediation outcome"
    )

    error: Optional[str] = Field(
        default=None,
        description="Error message if outcome != success"
    )

    # ========================================================================
    # Order Information (if triggered by MCP order)
    # ========================================================================

    order_id: Optional[str] = Field(
        default=None,
        description="MCP order ID that triggered this action"
    )

    runbook_id: Optional[str] = Field(
        default=None,
        description="Runbook ID executed"
    )

    # ========================================================================
    # Validators
    # ========================================================================

    @validator('timestamp_end')
    def validate_end_after_start(cls, v, values):
        """Ensure end timestamp is after start timestamp."""
        if 'timestamp_start' in values and v < values['timestamp_start']:
            raise ValueError('timestamp_end must be after timestamp_start')
        return v

    @validator('reseller_id')
    def validate_reseller_id_if_needed(cls, v, values):
        """Require reseller_id if deployment_mode=reseller."""
        if values.get('deployment_mode') == 'reseller' and not v:
            raise ValueError('reseller_id required when deployment_mode=reseller')
        return v

    @validator('check')
    def validate_check_type(cls, v):
        """Validate check type."""
        valid_checks = [
            'patching',
            'av_health',
            'backup',
            'logging',
            'firewall',
            'encryption',
            'time_sync',
            'general'
        ]
        if v not in valid_checks:
            raise ValueError(f'check must be one of {valid_checks}')
        return v

    @property
    def duration_sec(self) -> float:
        """Calculate total duration."""
        return (self.timestamp_end - self.timestamp_start).total_seconds()

    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ============================================================================
# MCP Order Models
# ============================================================================


class MCPOrder(BaseModel):
    """
    MCP order from central server.

    Orders are signed by MCP and verified by agent before execution.
    """

    order_id: str = Field(
        ...,
        description="Unique order identifier"
    )

    runbook_id: str = Field(
        ...,
        description="Runbook to execute"
    )

    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Runbook parameters"
    )

    nonce: str = Field(
        ...,
        description="Unique nonce to prevent replay"
    )

    ttl: int = Field(
        ...,
        ge=60,
        description="Order TTL in seconds"
    )

    issued_at: datetime = Field(
        ...,
        description="Order issue timestamp (UTC)"
    )

    signature: Optional[str] = Field(
        default=None,
        description="Ed25519 signature (hex-encoded)"
    )

    @property
    def is_expired(self) -> bool:
        """Check if order has expired based on TTL."""
        age_seconds = (datetime.utcnow() - self.issued_at).total_seconds()
        return age_seconds > self.ttl

    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# ============================================================================
# Drift Detection Models
# ============================================================================


class DriftResult(BaseModel):
    """Result of a drift detection check."""

    check: str = Field(
        ...,
        description="Check type"
    )

    drifted: bool = Field(
        ...,
        description="Whether drift was detected"
    )

    pre_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="Current state"
    )

    recommended_action: Optional[str] = Field(
        default=None,
        description="Recommended remediation action"
    )

    severity: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Drift severity"
    )

    hipaa_controls: Optional[List[str]] = Field(
        default=None,
        description="Relevant HIPAA controls"
    )


# ============================================================================
# Remediation Models
# ============================================================================


class RemediationResult(BaseModel):
    """Result of a remediation attempt."""

    check: str = Field(
        ...,
        description="Check type that was remediated"
    )

    outcome: Literal["success", "failed", "reverted", "deferred", "alert"] = Field(
        ...,
        description="Remediation outcome"
    )

    pre_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="State before remediation"
    )

    post_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="State after remediation"
    )

    actions: List[ActionTaken] = Field(
        default_factory=list,
        description="Actions performed"
    )

    error: Optional[str] = Field(
        default=None,
        description="Error message if failed"
    )

    rollback_available: bool = Field(
        default=False,
        description="Whether rollback is possible"
    )

    rollback_generation: Optional[int] = Field(
        default=None,
        description="NixOS generation to rollback to"
    )


# ============================================================================
# Queue Models
# ============================================================================


class QueuedEvidence(BaseModel):
    """Evidence bundle queued for upload to MCP."""

    id: int = Field(
        ...,
        description="Queue entry ID (SQLite auto-increment)"
    )

    bundle_id: str = Field(
        ...,
        description="Evidence bundle ID"
    )

    bundle_path: str = Field(
        ...,
        description="Path to bundle.json"
    )

    signature_path: str = Field(
        ...,
        description="Path to bundle.sig"
    )

    created_at: datetime = Field(
        ...,
        description="Queue entry creation time"
    )

    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of upload attempts"
    )

    last_error: Optional[str] = Field(
        default=None,
        description="Last upload error message"
    )

    class Config:
        """Pydantic config."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
