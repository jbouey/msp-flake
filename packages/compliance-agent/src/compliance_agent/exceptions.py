"""
Compliance Exception Management.

Allows partners and clients to create documented exceptions for specific
compliance checks, runbooks, or controls. All exceptions are audited and
time-limited to maintain HIPAA compliance.

Exception Tiers:
- client_admin: Single device, single check, 30 days max
- partner: Site-wide, any runbook, 90 days max
- l3_escalation: Any scope, 1 year max
- central_command: Fleet-wide, indefinite (annual review)
"""

import json
import sqlite3
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
from uuid import uuid4


class ExceptionScope(str, Enum):
    """What type of item is being excepted."""
    RUNBOOK = "runbook"
    CHECK = "check"
    CONTROL = "control"


class ExceptionAction(str, Enum):
    """What action to take for the exception."""
    SUPPRESS_ALERT = "suppress_alert"
    SKIP_REMEDIATION = "skip_remediation"
    BOTH = "both"


class ApprovalTier(str, Enum):
    """Who approved the exception."""
    CLIENT_ADMIN = "client_admin"
    PARTNER = "partner"
    L3_ESCALATION = "l3_escalation"
    CENTRAL_COMMAND = "central_command"


# Maximum durations by tier (in days)
MAX_DURATION_DAYS = {
    ApprovalTier.CLIENT_ADMIN: 30,
    ApprovalTier.PARTNER: 90,
    ApprovalTier.L3_ESCALATION: 365,
    ApprovalTier.CENTRAL_COMMAND: 3650,  # 10 years (effectively indefinite)
}


@dataclass
class ExceptionScope_:
    """Defines what is being excepted."""
    scope_type: ExceptionScope
    item_id: str  # runbook_id, check_id, or control_id
    device_filter: Optional[str] = None  # e.g., "hostname:legacy-*" or "ip:192.168.1.*"

    def matches_device(self, hostname: str, ip: Optional[str] = None) -> bool:
        """Check if a device matches this exception's filter."""
        if not self.device_filter:
            return True  # No filter = applies to all devices

        filter_parts = self.device_filter.split(":", 1)
        if len(filter_parts) != 2:
            return False

        filter_type, pattern = filter_parts
        pattern = pattern.replace("*", ".*")

        import re
        if filter_type == "hostname" and hostname:
            return bool(re.match(f"^{pattern}$", hostname, re.IGNORECASE))
        elif filter_type == "ip" and ip:
            return bool(re.match(f"^{pattern}$", ip))

        return False


@dataclass
class ExceptionApproval:
    """Who approved the exception."""
    requested_by: str  # email
    approved_by: str  # email
    approval_date: str  # ISO format
    approval_tier: ApprovalTier
    approval_notes: Optional[str] = None


@dataclass
class ExceptionValidity:
    """When the exception is valid."""
    start_date: str  # ISO format
    expiration_date: str  # ISO format
    requires_renewal: bool = True
    renewal_reminder_days: int = 14  # Days before expiration to remind


@dataclass
class ExceptionJustification:
    """Documentation for the exception (required for HIPAA)."""
    reason: str
    compensating_control: Optional[str] = None
    risk_accepted_by: str  # Name and role
    risk_assessment: Optional[str] = None  # Optional risk score/notes


@dataclass
class ComplianceException:
    """A documented compliance exception."""
    id: str
    site_id: str
    scope: ExceptionScope_
    approval: ExceptionApproval
    validity: ExceptionValidity
    justification: ExceptionJustification
    action: ExceptionAction = ExceptionAction.BOTH

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True

    # Audit
    audit_hash: Optional[str] = None

    def is_valid(self) -> bool:
        """Check if exception is currently valid."""
        if not self.is_active:
            return False

        now = datetime.now(timezone.utc)
        start = datetime.fromisoformat(self.validity.start_date.replace('Z', '+00:00'))
        end = datetime.fromisoformat(self.validity.expiration_date.replace('Z', '+00:00'))

        return start <= now <= end

    def days_until_expiration(self) -> int:
        """Get days until this exception expires."""
        now = datetime.now(timezone.utc)
        end = datetime.fromisoformat(self.validity.expiration_date.replace('Z', '+00:00'))
        return max(0, (end - now).days)

    def needs_renewal_reminder(self) -> bool:
        """Check if renewal reminder should be sent."""
        return self.days_until_expiration() <= self.validity.renewal_reminder_days

    def compute_audit_hash(self) -> str:
        """Compute hash of exception for audit trail."""
        data = {
            "id": self.id,
            "site_id": self.site_id,
            "scope": asdict(self.scope),
            "approval": asdict(self.approval),
            "validity": asdict(self.validity),
            "justification": asdict(self.justification),
            "action": self.action.value,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "site_id": self.site_id,
            "scope": {
                "type": self.scope.scope_type.value,
                "item_id": self.scope.item_id,
                "device_filter": self.scope.device_filter,
            },
            "approval": {
                "requested_by": self.approval.requested_by,
                "approved_by": self.approval.approved_by,
                "approval_date": self.approval.approval_date,
                "approval_tier": self.approval.approval_tier.value,
                "approval_notes": self.approval.approval_notes,
            },
            "validity": {
                "start_date": self.validity.start_date,
                "expiration_date": self.validity.expiration_date,
                "requires_renewal": self.validity.requires_renewal,
                "renewal_reminder_days": self.validity.renewal_reminder_days,
            },
            "justification": {
                "reason": self.justification.reason,
                "compensating_control": self.justification.compensating_control,
                "risk_accepted_by": self.justification.risk_accepted_by,
                "risk_assessment": self.justification.risk_assessment,
            },
            "action": self.action.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_active": self.is_active,
            "audit_hash": self.audit_hash,
            "is_valid": self.is_valid(),
            "days_until_expiration": self.days_until_expiration(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComplianceException":
        """Create from dictionary."""
        scope = ExceptionScope_(
            scope_type=ExceptionScope(data["scope"]["type"]),
            item_id=data["scope"]["item_id"],
            device_filter=data["scope"].get("device_filter"),
        )

        approval = ExceptionApproval(
            requested_by=data["approval"]["requested_by"],
            approved_by=data["approval"]["approved_by"],
            approval_date=data["approval"]["approval_date"],
            approval_tier=ApprovalTier(data["approval"]["approval_tier"]),
            approval_notes=data["approval"].get("approval_notes"),
        )

        validity = ExceptionValidity(
            start_date=data["validity"]["start_date"],
            expiration_date=data["validity"]["expiration_date"],
            requires_renewal=data["validity"].get("requires_renewal", True),
            renewal_reminder_days=data["validity"].get("renewal_reminder_days", 14),
        )

        justification = ExceptionJustification(
            reason=data["justification"]["reason"],
            compensating_control=data["justification"].get("compensating_control"),
            risk_accepted_by=data["justification"]["risk_accepted_by"],
            risk_assessment=data["justification"].get("risk_assessment"),
        )

        return cls(
            id=data["id"],
            site_id=data["site_id"],
            scope=scope,
            approval=approval,
            validity=validity,
            justification=justification,
            action=ExceptionAction(data.get("action", "both")),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            is_active=data.get("is_active", True),
            audit_hash=data.get("audit_hash"),
        )


class ExceptionManager:
    """Manages compliance exceptions with SQLite persistence."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the exceptions database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exceptions (
                    id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    device_filter TEXT,
                    requested_by TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approval_date TEXT NOT NULL,
                    approval_tier TEXT NOT NULL,
                    approval_notes TEXT,
                    start_date TEXT NOT NULL,
                    expiration_date TEXT NOT NULL,
                    requires_renewal INTEGER DEFAULT 1,
                    renewal_reminder_days INTEGER DEFAULT 14,
                    reason TEXT NOT NULL,
                    compensating_control TEXT,
                    risk_accepted_by TEXT NOT NULL,
                    risk_assessment TEXT,
                    action TEXT DEFAULT 'both',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    audit_hash TEXT,

                    UNIQUE(site_id, scope_type, item_id, device_filter)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exceptions_site
                ON exceptions(site_id, is_active)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exceptions_item
                ON exceptions(scope_type, item_id, is_active)
            """)

            # Audit log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exception_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exception_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    performed_by TEXT NOT NULL,
                    performed_at TEXT NOT NULL,
                    old_state TEXT,
                    new_state TEXT,
                    notes TEXT
                )
            """)

            conn.commit()

    def create_exception(
        self,
        site_id: str,
        scope_type: ExceptionScope,
        item_id: str,
        requested_by: str,
        approved_by: str,
        approval_tier: ApprovalTier,
        reason: str,
        risk_accepted_by: str,
        duration_days: Optional[int] = None,
        device_filter: Optional[str] = None,
        compensating_control: Optional[str] = None,
        action: ExceptionAction = ExceptionAction.BOTH,
        approval_notes: Optional[str] = None,
    ) -> ComplianceException:
        """Create a new compliance exception."""

        # Enforce max duration by tier
        max_days = MAX_DURATION_DAYS[approval_tier]
        if duration_days is None or duration_days > max_days:
            duration_days = max_days

        now = datetime.now(timezone.utc)

        exception = ComplianceException(
            id=f"EXC-{now.strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
            site_id=site_id,
            scope=ExceptionScope_(
                scope_type=scope_type,
                item_id=item_id,
                device_filter=device_filter,
            ),
            approval=ExceptionApproval(
                requested_by=requested_by,
                approved_by=approved_by,
                approval_date=now.isoformat(),
                approval_tier=approval_tier,
                approval_notes=approval_notes,
            ),
            validity=ExceptionValidity(
                start_date=now.isoformat(),
                expiration_date=(now + timedelta(days=duration_days)).isoformat(),
                requires_renewal=True,
                renewal_reminder_days=min(14, duration_days // 2),
            ),
            justification=ExceptionJustification(
                reason=reason,
                compensating_control=compensating_control,
                risk_accepted_by=risk_accepted_by,
            ),
            action=action,
        )

        exception.audit_hash = exception.compute_audit_hash()

        self._save_exception(exception)
        self._log_audit(exception.id, "created", approved_by, new_state=exception.to_dict())

        return exception

    def _save_exception(self, exc: ComplianceException):
        """Save exception to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO exceptions (
                    id, site_id, scope_type, item_id, device_filter,
                    requested_by, approved_by, approval_date, approval_tier, approval_notes,
                    start_date, expiration_date, requires_renewal, renewal_reminder_days,
                    reason, compensating_control, risk_accepted_by, risk_assessment,
                    action, created_at, updated_at, is_active, audit_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                exc.id, exc.site_id, exc.scope.scope_type.value, exc.scope.item_id, exc.scope.device_filter,
                exc.approval.requested_by, exc.approval.approved_by, exc.approval.approval_date,
                exc.approval.approval_tier.value, exc.approval.approval_notes,
                exc.validity.start_date, exc.validity.expiration_date,
                1 if exc.validity.requires_renewal else 0, exc.validity.renewal_reminder_days,
                exc.justification.reason, exc.justification.compensating_control,
                exc.justification.risk_accepted_by, exc.justification.risk_assessment,
                exc.action.value, exc.created_at, exc.updated_at, 1 if exc.is_active else 0, exc.audit_hash,
            ))
            conn.commit()

    def _log_audit(
        self,
        exception_id: str,
        action: str,
        performed_by: str,
        old_state: Optional[Dict] = None,
        new_state: Optional[Dict] = None,
        notes: Optional[str] = None,
    ):
        """Log an audit entry."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO exception_audit_log
                (exception_id, action, performed_by, performed_at, old_state, new_state, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                exception_id,
                action,
                performed_by,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(old_state) if old_state else None,
                json.dumps(new_state) if new_state else None,
                notes,
            ))
            conn.commit()

    def get_exception(self, exception_id: str) -> Optional[ComplianceException]:
        """Get an exception by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM exceptions WHERE id = ?",
                (exception_id,)
            ).fetchone()

            if row:
                return self._row_to_exception(row)
            return None

    def get_site_exceptions(self, site_id: str, active_only: bool = True) -> List[ComplianceException]:
        """Get all exceptions for a site."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            query = "SELECT * FROM exceptions WHERE site_id = ?"
            params = [site_id]

            if active_only:
                query += " AND is_active = 1"

            query += " ORDER BY created_at DESC"

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_exception(row) for row in rows]

    def check_exception(
        self,
        site_id: str,
        scope_type: ExceptionScope,
        item_id: str,
        hostname: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> Optional[ComplianceException]:
        """
        Check if an active exception exists for this check/runbook.

        Returns the exception if found and valid, None otherwise.
        """
        exceptions = self.get_site_exceptions(site_id, active_only=True)

        for exc in exceptions:
            # Match scope type and item ID
            if exc.scope.scope_type != scope_type:
                continue
            if exc.scope.item_id != item_id:
                continue

            # Check validity
            if not exc.is_valid():
                continue

            # Check device filter
            if exc.scope.device_filter:
                if not exc.scope.matches_device(hostname or "", ip):
                    continue

            return exc

        return None

    def should_suppress_alert(
        self,
        site_id: str,
        scope_type: ExceptionScope,
        item_id: str,
        hostname: Optional[str] = None,
    ) -> bool:
        """Check if alerts should be suppressed for this item."""
        exc = self.check_exception(site_id, scope_type, item_id, hostname)
        if exc and exc.action in (ExceptionAction.SUPPRESS_ALERT, ExceptionAction.BOTH):
            return True
        return False

    def should_skip_remediation(
        self,
        site_id: str,
        scope_type: ExceptionScope,
        item_id: str,
        hostname: Optional[str] = None,
    ) -> bool:
        """Check if remediation should be skipped for this item."""
        exc = self.check_exception(site_id, scope_type, item_id, hostname)
        if exc and exc.action in (ExceptionAction.SKIP_REMEDIATION, ExceptionAction.BOTH):
            return True
        return False

    def revoke_exception(self, exception_id: str, revoked_by: str, reason: str) -> bool:
        """Revoke (deactivate) an exception."""
        exc = self.get_exception(exception_id)
        if not exc:
            return False

        old_state = exc.to_dict()
        exc.is_active = False
        exc.updated_at = datetime.now(timezone.utc).isoformat()

        self._save_exception(exc)
        self._log_audit(
            exception_id,
            "revoked",
            revoked_by,
            old_state=old_state,
            new_state=exc.to_dict(),
            notes=reason,
        )

        return True

    def renew_exception(
        self,
        exception_id: str,
        renewed_by: str,
        duration_days: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> Optional[ComplianceException]:
        """Renew an exception for another period."""
        exc = self.get_exception(exception_id)
        if not exc:
            return None

        old_state = exc.to_dict()

        # Enforce max duration
        max_days = MAX_DURATION_DAYS[exc.approval.approval_tier]
        if duration_days is None or duration_days > max_days:
            duration_days = max_days

        now = datetime.now(timezone.utc)
        exc.validity.start_date = now.isoformat()
        exc.validity.expiration_date = (now + timedelta(days=duration_days)).isoformat()
        exc.updated_at = now.isoformat()
        exc.audit_hash = exc.compute_audit_hash()

        self._save_exception(exc)
        self._log_audit(
            exception_id,
            "renewed",
            renewed_by,
            old_state=old_state,
            new_state=exc.to_dict(),
            notes=reason or f"Renewed for {duration_days} days",
        )

        return exc

    def get_expiring_soon(self, days: int = 14) -> List[ComplianceException]:
        """Get exceptions expiring within the specified days."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

            rows = conn.execute("""
                SELECT * FROM exceptions
                WHERE is_active = 1 AND expiration_date <= ?
                ORDER BY expiration_date ASC
            """, (cutoff,)).fetchall()

            return [self._row_to_exception(row) for row in rows]

    def get_audit_log(self, exception_id: str) -> List[Dict[str, Any]]:
        """Get audit log for an exception."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute("""
                SELECT * FROM exception_audit_log
                WHERE exception_id = ?
                ORDER BY performed_at DESC
            """, (exception_id,)).fetchall()

            return [
                {
                    "action": row["action"],
                    "performed_by": row["performed_by"],
                    "performed_at": row["performed_at"],
                    "notes": row["notes"],
                }
                for row in rows
            ]

    def get_exception_summary(self, site_id: str) -> Dict[str, Any]:
        """Get summary of exceptions for a site."""
        exceptions = self.get_site_exceptions(site_id, active_only=False)

        active = [e for e in exceptions if e.is_active and e.is_valid()]
        expired = [e for e in exceptions if e.is_active and not e.is_valid()]
        revoked = [e for e in exceptions if not e.is_active]
        expiring_soon = [e for e in active if e.needs_renewal_reminder()]

        return {
            "total": len(exceptions),
            "active": len(active),
            "expired": len(expired),
            "revoked": len(revoked),
            "expiring_soon": len(expiring_soon),
            "by_scope": {
                "runbook": len([e for e in active if e.scope.scope_type == ExceptionScope.RUNBOOK]),
                "check": len([e for e in active if e.scope.scope_type == ExceptionScope.CHECK]),
                "control": len([e for e in active if e.scope.scope_type == ExceptionScope.CONTROL]),
            },
            "by_tier": {
                "client_admin": len([e for e in active if e.approval.approval_tier == ApprovalTier.CLIENT_ADMIN]),
                "partner": len([e for e in active if e.approval.approval_tier == ApprovalTier.PARTNER]),
                "l3_escalation": len([e for e in active if e.approval.approval_tier == ApprovalTier.L3_ESCALATION]),
            },
        }

    def _row_to_exception(self, row: sqlite3.Row) -> ComplianceException:
        """Convert database row to ComplianceException."""
        return ComplianceException(
            id=row["id"],
            site_id=row["site_id"],
            scope=ExceptionScope_(
                scope_type=ExceptionScope(row["scope_type"]),
                item_id=row["item_id"],
                device_filter=row["device_filter"],
            ),
            approval=ExceptionApproval(
                requested_by=row["requested_by"],
                approved_by=row["approved_by"],
                approval_date=row["approval_date"],
                approval_tier=ApprovalTier(row["approval_tier"]),
                approval_notes=row["approval_notes"],
            ),
            validity=ExceptionValidity(
                start_date=row["start_date"],
                expiration_date=row["expiration_date"],
                requires_renewal=bool(row["requires_renewal"]),
                renewal_reminder_days=row["renewal_reminder_days"],
            ),
            justification=ExceptionJustification(
                reason=row["reason"],
                compensating_control=row["compensating_control"],
                risk_accepted_by=row["risk_accepted_by"],
                risk_assessment=row["risk_assessment"],
            ),
            action=ExceptionAction(row["action"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_active=bool(row["is_active"]),
            audit_hash=row["audit_hash"],
        )
