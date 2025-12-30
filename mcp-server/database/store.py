"""
Incident Store - Central repository for all MCP data.

This is the bridge between agents and the learning loop.
All incidents, executions, patterns, and rules flow through here.
"""

import os
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy import create_engine, func, desc, and_, or_
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base,
    ClientRecord,
    ApplianceRecord,
    IncidentRecord,
    ExecutionRecord,
    PatternRecord,
    RuleRecord,
    AuditLog,
)


# Database URL - defaults to SQLite, can be overridden for PostgreSQL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:////var/lib/mcp-server/mcp.db"
)

# Global engine and session factory
_engine = None
_SessionFactory = None
_store_instance = None


def init_database(url: str = None) -> "IncidentStore":
    """Initialize database connection and create tables."""
    global _engine, _SessionFactory, _store_instance

    db_url = url or DATABASE_URL

    # Use SQLite with check_same_thread=False for async compatibility
    if db_url.startswith("sqlite:"):
        _engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            echo=os.getenv("DB_ECHO", "false").lower() == "true"
        )
    else:
        _engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            echo=os.getenv("DB_ECHO", "false").lower() == "true"
        )

    # Create tables
    Base.metadata.create_all(_engine)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    _store_instance = IncidentStore(_SessionFactory)

    print(f"Database initialized: {db_url}")
    return _store_instance


def get_store() -> "IncidentStore":
    """Get the singleton store instance."""
    global _store_instance
    if _store_instance is None:
        init_database()
    return _store_instance


class IncidentStore:
    """
    Central data store for MCP Server.

    Provides CRUD operations for all entities and
    aggregation queries for dashboard and learning.
    """

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    @contextmanager
    def session(self) -> Session:
        """Context manager for database sessions."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # =========================================================================
    # CLIENT OPERATIONS
    # =========================================================================

    def get_or_create_client(self, site_id: str, name: str = None, **kwargs) -> ClientRecord:
        """Get existing client or create new one."""
        with self.session() as session:
            client = session.query(ClientRecord).filter_by(site_id=site_id).first()
            if not client:
                client = ClientRecord(
                    site_id=site_id,
                    name=name or site_id,
                    **kwargs
                )
                session.add(client)
                session.commit()
                self._audit(session, "client_created", "system", "client", site_id)
            return client

    def get_all_clients(self, active_only: bool = True) -> List[ClientRecord]:
        """Get all clients."""
        with self.session() as session:
            query = session.query(ClientRecord)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.order_by(ClientRecord.name).all()

    def get_client(self, site_id: str) -> Optional[ClientRecord]:
        """Get client by site_id."""
        with self.session() as session:
            return session.query(ClientRecord).filter_by(site_id=site_id).first()

    def update_client_health(self, site_id: str, overall: float, connectivity: float, compliance: float):
        """Update client health scores."""
        with self.session() as session:
            client = session.query(ClientRecord).filter_by(site_id=site_id).first()
            if client:
                client.overall_health = overall
                client.connectivity_score = connectivity
                client.compliance_score = compliance
                client.health_status = self._get_health_status(overall)
                client.last_seen = datetime.now(timezone.utc)

    def _get_health_status(self, score: float) -> str:
        if score < 50:
            return "critical"
        elif score < 80:
            return "warning"
        return "healthy"

    # =========================================================================
    # APPLIANCE OPERATIONS
    # =========================================================================

    def register_appliance(
        self,
        appliance_id: str,
        site_id: str,
        hostname: str,
        **kwargs
    ) -> ApplianceRecord:
        """Register or update an appliance."""
        with self.session() as session:
            # Get or create client
            client = session.query(ClientRecord).filter_by(site_id=site_id).first()
            if not client:
                client = ClientRecord(site_id=site_id, name=site_id)
                session.add(client)
                session.flush()

            # Get or create appliance
            appliance = session.query(ApplianceRecord).filter_by(appliance_id=appliance_id).first()
            if not appliance:
                appliance = ApplianceRecord(
                    appliance_id=appliance_id,
                    client_id=client.id,
                    hostname=hostname,
                    **kwargs
                )
                session.add(appliance)
                self._audit(session, "appliance_registered", "system", "appliance", appliance_id)
            else:
                # Update existing
                appliance.hostname = hostname
                appliance.last_checkin = datetime.now(timezone.utc)
                appliance.is_online = True
                for key, value in kwargs.items():
                    if hasattr(appliance, key):
                        setattr(appliance, key, value)

            return appliance

    def appliance_checkin(self, appliance_id: str, health_metrics: Dict[str, Any]):
        """Record appliance check-in with health data."""
        with self.session() as session:
            appliance = session.query(ApplianceRecord).filter_by(appliance_id=appliance_id).first()
            if appliance:
                appliance.last_checkin = datetime.now(timezone.utc)
                appliance.is_online = True
                appliance.overall_health = health_metrics.get("overall_health", appliance.overall_health)
                appliance.checkin_rate = health_metrics.get("checkin_rate", appliance.checkin_rate)
                appliance.healing_rate = health_metrics.get("healing_rate", appliance.healing_rate)
                appliance.compliance_checks = health_metrics.get("compliance_checks", appliance.compliance_checks)

    def get_client_appliances(self, site_id: str) -> List[ApplianceRecord]:
        """Get all appliances for a client."""
        with self.session() as session:
            client = session.query(ClientRecord).filter_by(site_id=site_id).first()
            if not client:
                return []
            return session.query(ApplianceRecord).filter_by(client_id=client.id).all()

    # =========================================================================
    # INCIDENT OPERATIONS
    # =========================================================================

    def create_incident(
        self,
        site_id: str,
        hostname: str,
        incident_type: str,
        severity: str,
        **kwargs
    ) -> IncidentRecord:
        """Create a new incident."""
        with self.session() as session:
            # Generate incident ID
            now = datetime.now(timezone.utc)
            today_count = session.query(IncidentRecord).filter(
                func.date(IncidentRecord.created_at) == now.date()
            ).count()
            incident_id = f"inc-{now.strftime('%Y%m%d')}-{today_count + 1:04d}"

            # Get client ID if exists
            client = session.query(ClientRecord).filter_by(site_id=site_id).first()
            client_db_id = client.id if client else None

            incident = IncidentRecord(
                incident_id=incident_id,
                client_id=client_db_id,
                site_id=site_id,
                hostname=hostname,
                incident_type=incident_type,
                severity=severity,
                **kwargs
            )
            session.add(incident)
            session.commit()

            self._audit(session, "incident_created", "system", "incident", incident_id, {
                "site_id": site_id,
                "incident_type": incident_type,
                "severity": severity
            })

            return incident

    def get_incidents(
        self,
        site_id: str = None,
        resolved: bool = None,
        level: str = None,
        limit: int = 50,
        since: datetime = None
    ) -> List[IncidentRecord]:
        """Get incidents with optional filters."""
        with self.session() as session:
            query = session.query(IncidentRecord)

            if site_id:
                query = query.filter_by(site_id=site_id)
            if resolved is not None:
                query = query.filter_by(resolved=resolved)
            if level:
                query = query.filter_by(resolution_level=level)
            if since:
                query = query.filter(IncidentRecord.created_at >= since)

            return query.order_by(desc(IncidentRecord.created_at)).limit(limit).all()

    def resolve_incident(
        self,
        incident_id: str,
        resolution_level: str,
        runbook_id: str = None,
        execution_log: str = None,
        evidence_bundle_id: str = None
    ):
        """Mark incident as resolved."""
        with self.session() as session:
            incident = session.query(IncidentRecord).filter_by(incident_id=incident_id).first()
            if incident:
                incident.resolved = True
                incident.resolved_at = datetime.now(timezone.utc)
                incident.resolution_level = resolution_level
                incident.runbook_executed = runbook_id
                incident.execution_log = execution_log
                incident.evidence_bundle_id = evidence_bundle_id

                self._audit(session, "incident_resolved", "system", "incident", incident_id, {
                    "resolution_level": resolution_level,
                    "runbook_id": runbook_id
                })

    def get_incident(self, incident_id: str) -> Optional[IncidentRecord]:
        """Get single incident by ID."""
        with self.session() as session:
            return session.query(IncidentRecord).filter_by(incident_id=incident_id).first()

    # =========================================================================
    # EXECUTION OPERATIONS
    # =========================================================================

    def record_execution(
        self,
        runbook_id: str,
        site_id: str,
        hostname: str,
        success: bool,
        incident_id: str = None,
        **kwargs
    ) -> ExecutionRecord:
        """Record a runbook execution result."""
        with self.session() as session:
            # Generate execution ID
            now = datetime.now(timezone.utc)
            today_count = session.query(ExecutionRecord).filter(
                func.date(ExecutionRecord.created_at) == now.date()
            ).count()
            execution_id = f"exec-{now.strftime('%Y%m%d')}-{today_count + 1:04d}"

            execution = ExecutionRecord(
                execution_id=execution_id,
                runbook_id=runbook_id,
                site_id=site_id,
                hostname=hostname,
                success=success,
                incident_id=incident_id,
                **kwargs
            )
            session.add(execution)
            session.commit()

            # Update pattern statistics if this was an L2 decision
            if kwargs.get("incident_type"):
                self._update_pattern(
                    session,
                    incident_type=kwargs["incident_type"],
                    runbook_id=runbook_id,
                    success=success,
                    resolution_time_ms=kwargs.get("duration_seconds", 0) * 1000,
                    incident_id=incident_id
                )

            return execution

    def get_executions(
        self,
        runbook_id: str = None,
        site_id: str = None,
        success: bool = None,
        limit: int = 20
    ) -> List[ExecutionRecord]:
        """Get execution records with optional filters."""
        with self.session() as session:
            query = session.query(ExecutionRecord)

            if runbook_id:
                query = query.filter_by(runbook_id=runbook_id)
            if site_id:
                query = query.filter_by(site_id=site_id)
            if success is not None:
                query = query.filter_by(success=success)

            return query.order_by(desc(ExecutionRecord.created_at)).limit(limit).all()

    # =========================================================================
    # PATTERN & LEARNING OPERATIONS
    # =========================================================================

    def _update_pattern(
        self,
        session: Session,
        incident_type: str,
        runbook_id: str,
        success: bool,
        resolution_time_ms: float,
        incident_id: str = None
    ):
        """Update or create pattern from execution."""
        # Generate pattern signature
        signature = hashlib.md5(f"{incident_type}:{runbook_id}".encode()).hexdigest()[:16]

        pattern = session.query(PatternRecord).filter_by(pattern_signature=signature).first()

        if not pattern:
            pattern_id = f"pattern-{signature}"
            pattern = PatternRecord(
                pattern_id=pattern_id,
                pattern_signature=signature,
                incident_type=incident_type,
                runbook_id=runbook_id,
                description=f"Pattern for {incident_type} resolved by {runbook_id}",
                proposed_rule=f"When {incident_type} detected, execute {runbook_id}",
                example_incidents=[incident_id] if incident_id else []
            )
            session.add(pattern)

        pattern.update_stats(success, resolution_time_ms)

        # Add to example incidents (max 10)
        if incident_id and pattern.example_incidents:
            if incident_id not in pattern.example_incidents:
                examples = pattern.example_incidents[:9]
                examples.append(incident_id)
                pattern.example_incidents = examples

    def get_promotion_candidates(
        self,
        min_occurrences: int = 5,
        min_success_rate: float = 90.0
    ) -> List[PatternRecord]:
        """Get patterns eligible for L1 promotion."""
        with self.session() as session:
            return session.query(PatternRecord).filter(
                PatternRecord.status == "pending",
                PatternRecord.occurrences >= min_occurrences,
                PatternRecord.success_rate >= min_success_rate
            ).order_by(desc(PatternRecord.success_rate), desc(PatternRecord.occurrences)).all()

    def get_promotion_history(self, limit: int = 20) -> List[PatternRecord]:
        """Get recently promoted patterns."""
        with self.session() as session:
            return session.query(PatternRecord).filter(
                PatternRecord.status == "promoted"
            ).order_by(desc(PatternRecord.promoted_at)).limit(limit).all()

    def promote_pattern(self, pattern_id: str, promoted_by: str = "admin") -> Optional[RuleRecord]:
        """Promote a pattern to L1 rule."""
        with self.session() as session:
            pattern = session.query(PatternRecord).filter_by(pattern_id=pattern_id).first()
            if not pattern:
                return None

            # Generate rule ID
            rule_id = f"RB-AUTO-{pattern.pattern_signature.upper()[:8]}"

            # Create L1 rule
            rule = RuleRecord(
                rule_id=rule_id,
                name=f"Auto: {pattern.incident_type}",
                description=pattern.proposed_rule,
                incident_type=pattern.incident_type,
                runbook_id=pattern.runbook_id,
                source_pattern_id=pattern_id,
                match_conditions={
                    "incident_type": pattern.incident_type
                }
            )
            session.add(rule)

            # Update pattern
            pattern.status = "promoted"
            pattern.promoted_at = datetime.now(timezone.utc)
            pattern.promoted_to_rule_id = rule_id

            self._audit(session, "pattern_promoted", promoted_by, "pattern", pattern_id, {
                "new_rule_id": rule_id
            })

            return rule

    # =========================================================================
    # RULE OPERATIONS
    # =========================================================================

    def get_active_rules(self) -> List[RuleRecord]:
        """Get all active L1 rules."""
        with self.session() as session:
            return session.query(RuleRecord).filter_by(is_active=True).all()

    def get_rules_for_incident_type(self, incident_type: str) -> List[RuleRecord]:
        """Get L1 rules that match an incident type."""
        with self.session() as session:
            return session.query(RuleRecord).filter(
                RuleRecord.is_active == True,
                RuleRecord.incident_type == incident_type
            ).all()

    def update_rule_stats(self, rule_id: str, success: bool, execution_time_ms: float):
        """Update rule execution statistics."""
        with self.session() as session:
            rule = session.query(RuleRecord).filter_by(rule_id=rule_id).first()
            if rule:
                rule.record_execution(success, execution_time_ms)

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_global_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for dashboard."""
        with self.session() as session:
            now = datetime.now(timezone.utc)

            total_clients = session.query(ClientRecord).filter_by(is_active=True).count()
            total_appliances = session.query(ApplianceRecord).count()
            online_appliances = session.query(ApplianceRecord).filter_by(is_online=True).count()

            # Incident counts
            incidents_24h = session.query(IncidentRecord).filter(
                IncidentRecord.created_at >= now - timedelta(hours=24)
            ).count()
            incidents_7d = session.query(IncidentRecord).filter(
                IncidentRecord.created_at >= now - timedelta(days=7)
            ).count()
            incidents_30d = session.query(IncidentRecord).filter(
                IncidentRecord.created_at >= now - timedelta(days=30)
            ).count()

            # Resolution rates (30 day window)
            resolved_30d = session.query(IncidentRecord).filter(
                IncidentRecord.created_at >= now - timedelta(days=30),
                IncidentRecord.resolved == True
            )
            l1_count = resolved_30d.filter(IncidentRecord.resolution_level == "L1").count()
            l2_count = resolved_30d.filter(IncidentRecord.resolution_level == "L2").count()
            l3_count = resolved_30d.filter(IncidentRecord.resolution_level == "L3").count()
            total_resolved = l1_count + l2_count + l3_count

            l1_rate = (l1_count / total_resolved * 100) if total_resolved > 0 else 0.0
            l2_rate = (l2_count / total_resolved * 100) if total_resolved > 0 else 0.0
            l3_rate = (l3_count / total_resolved * 100) if total_resolved > 0 else 0.0

            # Average health scores
            avg_compliance = session.query(func.avg(ClientRecord.compliance_score)).scalar() or 0.0
            avg_connectivity = session.query(func.avg(ClientRecord.connectivity_score)).scalar() or 0.0

            return {
                "total_clients": total_clients,
                "total_appliances": total_appliances,
                "online_appliances": online_appliances,
                "avg_compliance_score": round(avg_compliance, 1),
                "avg_connectivity_score": round(avg_connectivity, 1),
                "incidents_24h": incidents_24h,
                "incidents_7d": incidents_7d,
                "incidents_30d": incidents_30d,
                "l1_resolution_rate": round(l1_rate, 1),
                "l2_resolution_rate": round(l2_rate, 1),
                "l3_escalation_rate": round(l3_rate, 1),
            }

    def get_learning_status(self) -> Dict[str, Any]:
        """Get learning loop statistics."""
        with self.session() as session:
            now = datetime.now(timezone.utc)

            # L1 rules count
            l1_rules = session.query(RuleRecord).filter_by(is_active=True).count()

            # L2 decisions in last 30 days (executions without matching L1 rule)
            l2_decisions = session.query(ExecutionRecord).filter(
                ExecutionRecord.created_at >= now - timedelta(days=30)
            ).count()

            # Patterns awaiting promotion
            awaiting = session.query(PatternRecord).filter_by(status="pending").count()

            # Recently promoted (last 30 days)
            recently_promoted = session.query(PatternRecord).filter(
                PatternRecord.status == "promoted",
                PatternRecord.promoted_at >= now - timedelta(days=30)
            ).count()

            # Promotion success rate
            promoted_rules = session.query(RuleRecord).filter(
                RuleRecord.source_pattern_id.isnot(None)
            ).all()

            if promoted_rules:
                total_execs = sum(r.execution_count for r in promoted_rules)
                total_success = sum(r.success_count for r in promoted_rules)
                promo_success_rate = (total_success / total_execs * 100) if total_execs > 0 else 0.0
            else:
                promo_success_rate = 0.0

            return {
                "total_l1_rules": l1_rules,
                "total_l2_decisions_30d": l2_decisions,
                "patterns_awaiting_promotion": awaiting,
                "recently_promoted_count": recently_promoted,
                "promotion_success_rate": round(promo_success_rate, 1),
            }

    # =========================================================================
    # AUDIT LOGGING
    # =========================================================================

    def _audit(
        self,
        session: Session,
        action: str,
        actor: str,
        target_type: str,
        target_id: str,
        details: Dict[str, Any] = None
    ):
        """Create audit log entry."""
        log = AuditLog(
            action=action,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            details=details or {}
        )
        session.add(log)

    def get_audit_logs(
        self,
        action: str = None,
        actor: str = None,
        limit: int = 100
    ) -> List[AuditLog]:
        """Get audit logs with optional filters."""
        with self.session() as session:
            query = session.query(AuditLog)

            if action:
                query = query.filter_by(action=action)
            if actor:
                query = query.filter_by(actor=actor)

            return query.order_by(desc(AuditLog.timestamp)).limit(limit).all()
