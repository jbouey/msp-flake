"""
SQLAlchemy models for centralized MCP Server storage.

This is the backbone of the learning loop - all incidents, executions,
patterns, and rules flow through these models.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, JSON, ForeignKey, Enum as SQLEnum,
    create_engine, Index
)
from sqlalchemy.orm import relationship, declarative_base
import enum


Base = declarative_base()


class HealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class ResolutionLevel(str, enum.Enum):
    L1 = "L1"  # Deterministic rules
    L2 = "L2"  # LLM-assisted
    L3 = "L3"  # Human escalation


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PatternStatus(str, enum.Enum):
    PENDING = "pending"      # Awaiting promotion
    APPROVED = "approved"    # Ready to promote
    PROMOTED = "promoted"    # Converted to L1 rule
    REJECTED = "rejected"    # Not suitable for L1


# =============================================================================
# CLIENT & APPLIANCE TRACKING
# =============================================================================

class ClientRecord(Base):
    """Registered clients/sites"""
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    contact_name = Column(String(255))
    contact_email = Column(String(255))
    is_active = Column(Boolean, default=True)

    # Computed health scores (updated periodically)
    overall_health = Column(Float, default=100.0)
    connectivity_score = Column(Float, default=100.0)
    compliance_score = Column(Float, default=100.0)
    health_status = Column(String(20), default="healthy")

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    appliances = relationship("ApplianceRecord", back_populates="client")
    incidents = relationship("IncidentRecord", back_populates="client")

    __table_args__ = (
        Index('idx_client_active', 'is_active'),
        Index('idx_client_health', 'health_status'),
    )


class ApplianceRecord(Base):
    """Individual appliances deployed at client sites"""
    __tablename__ = "appliances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    appliance_id = Column(String(255), unique=True, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45))  # IPv6 compatible
    version = Column(String(50))
    is_online = Column(Boolean, default=False)

    # Health metrics
    overall_health = Column(Float, default=100.0)
    checkin_rate = Column(Float, default=100.0)  # % of expected check-ins
    healing_rate = Column(Float, default=100.0)  # % of issues self-healed
    order_execution_rate = Column(Float, default=100.0)

    # Compliance checks (stored as JSON for flexibility)
    compliance_checks = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_checkin = Column(DateTime)

    # Relationships
    client = relationship("ClientRecord", back_populates="appliances")

    __table_args__ = (
        Index('idx_appliance_online', 'is_online'),
        Index('idx_appliance_client', 'client_id'),
    )


# =============================================================================
# INCIDENT & EXECUTION TRACKING
# =============================================================================

class IncidentRecord(Base):
    """Centralized incident storage - all incidents from all agents"""
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(255), unique=True, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    site_id = Column(String(255), nullable=False, index=True)  # For lookups before client exists
    hostname = Column(String(255), nullable=False)
    appliance_id = Column(String(255))

    # Incident classification
    incident_type = Column(String(100), nullable=False, index=True)
    check_type = Column(String(50))  # backup, patching, antivirus, etc.
    severity = Column(String(20), nullable=False, index=True)
    hipaa_controls = Column(JSON, default=list)

    # Resolution tracking
    resolution_level = Column(String(5))  # L1, L2, L3
    resolved = Column(Boolean, default=False, index=True)
    resolved_at = Column(DateTime)
    resolution_notes = Column(Text)

    # Evidence
    evidence_bundle_id = Column(String(255))
    evidence_hash = Column(String(128))
    runbook_executed = Column(String(255))
    execution_log = Column(Text)

    # Additional context
    drift_data = Column(JSON, default=dict)
    details = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    client = relationship("ClientRecord", back_populates="incidents")
    execution = relationship("ExecutionRecord", back_populates="incident", uselist=False)

    __table_args__ = (
        Index('idx_incident_site_date', 'site_id', 'created_at'),
        Index('idx_incident_type_level', 'incident_type', 'resolution_level'),
        Index('idx_incident_unresolved', 'resolved', 'severity'),
    )


class ExecutionRecord(Base):
    """Runbook execution results - feeds the learning engine"""
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(255), unique=True, nullable=False, index=True)
    incident_id = Column(String(255), ForeignKey("incidents.incident_id"))
    runbook_id = Column(String(255), nullable=False, index=True)

    # Context
    site_id = Column(String(255), nullable=False)
    hostname = Column(String(255), nullable=False)
    platform = Column(String(50))
    incident_type = Column(String(100))

    # Results
    success = Column(Boolean, nullable=False, index=True)
    status = Column(String(20))  # success, failure, partial
    verification_passed = Column(Boolean)
    verification_method = Column(String(100))
    confidence = Column(Float, default=0.0)

    # Timing
    duration_seconds = Column(Float)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # State capture (crucial for learning)
    state_before = Column(JSON, default=dict)
    state_after = Column(JSON, default=dict)
    state_diff = Column(JSON, default=dict)

    # Step-by-step trace
    executed_steps = Column(JSON, default=list)

    # Error details
    error_message = Column(Text)
    error_step = Column(Integer)
    retry_count = Column(Integer, default=0)
    failure_type = Column(String(50))  # From FailureType enum

    # Learning signals
    was_correct_runbook = Column(Boolean)
    was_correct_diagnosis = Column(Boolean)
    human_feedback = Column(Text)

    # Evidence
    evidence_bundle_id = Column(String(255))

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    incident = relationship("IncidentRecord", back_populates="execution")

    __table_args__ = (
        Index('idx_execution_runbook_success', 'runbook_id', 'success'),
        Index('idx_execution_site_date', 'site_id', 'created_at'),
        Index('idx_execution_failure_type', 'failure_type'),
    )


# =============================================================================
# LEARNING LOOP - PATTERNS & RULES
# =============================================================================

class PatternRecord(Base):
    """
    Detected patterns from L2 decisions.

    When the same L2 decision is made repeatedly with high success,
    it becomes a pattern candidate for L1 promotion.
    """
    __tablename__ = "patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern_id = Column(String(255), unique=True, nullable=False, index=True)
    pattern_signature = Column(String(255), nullable=False, index=True)  # Unique pattern fingerprint

    # Pattern details
    description = Column(Text)
    incident_type = Column(String(100), nullable=False)
    runbook_id = Column(String(255))
    proposed_rule = Column(Text)  # The L1 rule that would be created

    # Statistics
    occurrences = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_resolution_time_ms = Column(Float)
    total_resolution_time_ms = Column(Float, default=0.0)

    # Promotion status
    status = Column(String(20), default="pending", index=True)  # pending, approved, promoted, rejected
    promoted_at = Column(DateTime)
    promoted_to_rule_id = Column(String(255))  # The L1 rule ID after promotion

    # Context
    example_incidents = Column(JSON, default=list)  # Sample incident IDs showing this pattern

    # Timestamps
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('idx_pattern_status_rate', 'status', 'success_rate'),
        Index('idx_pattern_signature', 'pattern_signature'),
    )

    def update_stats(self, success: bool, resolution_time_ms: float):
        """Update pattern statistics after an execution"""
        self.occurrences += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1

        self.success_rate = (self.success_count / self.occurrences) * 100 if self.occurrences > 0 else 0.0
        self.total_resolution_time_ms += resolution_time_ms
        self.avg_resolution_time_ms = self.total_resolution_time_ms / self.occurrences
        self.last_seen = datetime.now(timezone.utc)


class RuleRecord(Base):
    """
    L1 deterministic rules - the result of learning.

    These are the rules that run at $0 cost and <100ms latency.
    They are created from promoted L2 patterns.
    """
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(255), unique=True, nullable=False, index=True)

    # Rule definition
    name = Column(String(255), nullable=False)
    description = Column(Text)
    incident_type = Column(String(100), nullable=False, index=True)
    runbook_id = Column(String(255), nullable=False)

    # Match conditions (stored as JSON for flexibility)
    match_conditions = Column(JSON, default=dict)

    # Execution parameters
    parameters = Column(JSON, default=dict)
    timeout_seconds = Column(Integer, default=300)
    max_retries = Column(Integer, default=1)

    # HIPAA mapping
    hipaa_controls = Column(JSON, default=list)

    # Status
    is_active = Column(Boolean, default=True, index=True)
    version = Column(Integer, default=1)

    # Lineage (from pattern)
    source_pattern_id = Column(String(255))

    # Statistics
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_execution_time_ms = Column(Float)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_executed = Column(DateTime)

    __table_args__ = (
        Index('idx_rule_active_type', 'is_active', 'incident_type'),
    )

    def record_execution(self, success: bool, execution_time_ms: float):
        """Record an execution of this rule"""
        self.execution_count += 1
        if success:
            self.success_count += 1

        self.success_rate = (self.success_count / self.execution_count) * 100 if self.execution_count > 0 else 0.0

        # Rolling average
        if self.avg_execution_time_ms is None:
            self.avg_execution_time_ms = execution_time_ms
        else:
            self.avg_execution_time_ms = (self.avg_execution_time_ms * 0.9) + (execution_time_ms * 0.1)

        self.last_executed = datetime.now(timezone.utc)


# =============================================================================
# AUDIT & HISTORY
# =============================================================================

class AuditLog(Base):
    """Audit trail for compliance"""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    action = Column(String(100), nullable=False)
    actor = Column(String(255))  # User or "system"
    target_type = Column(String(50))  # incident, rule, pattern, etc.
    target_id = Column(String(255))
    details = Column(JSON, default=dict)
    ip_address = Column(String(45))

    __table_args__ = (
        Index('idx_audit_action_date', 'action', 'timestamp'),
        Index('idx_audit_target', 'target_type', 'target_id'),
    )
