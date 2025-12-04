"""
Approval policy for disruptive remediation actions.

Implements governance over auto-remediation:
- Defines which actions require human approval
- Manages approval queue (pending, approved, rejected)
- Provides audit trail for all approval decisions

HIPAA Controls:
- §164.308(a)(4)(ii)(B) - Access authorization
- §164.312(b) - Audit controls
"""

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


class ActionCategory(str, Enum):
    """Categories of remediation actions."""

    # Disruptive - requires approval or maintenance window
    DISRUPTIVE = "disruptive"

    # Service restart - moderate impact
    SERVICE_RESTART = "service_restart"

    # Configuration change - low impact
    CONFIG_CHANGE = "config_change"

    # Informational - no action, just alerting
    ALERT_ONLY = "alert_only"


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUTO_APPROVED = "auto_approved"  # Within maintenance window


# Define which actions require approval
ACTION_POLICIES: Dict[str, Dict[str, Any]] = {
    # Patching - switch NixOS generation
    "update_to_baseline_generation": {
        "category": ActionCategory.DISRUPTIVE,
        "requires_approval": True,
        "auto_approve_in_maintenance": True,
        "description": "Switch to baseline NixOS generation (may reboot)",
        "risk_level": "high",
        "hipaa_impact": ["§164.310(a)(2)(iv) - Maintenance records"]
    },

    # AV/EDR restart
    "restart_av_service": {
        "category": ActionCategory.SERVICE_RESTART,
        "requires_approval": False,
        "auto_approve_in_maintenance": True,
        "description": "Restart antivirus/EDR service",
        "risk_level": "low",
        "hipaa_impact": []
    },

    # Backup job
    "run_backup_job": {
        "category": ActionCategory.CONFIG_CHANGE,
        "requires_approval": False,
        "auto_approve_in_maintenance": True,
        "description": "Trigger manual backup job",
        "risk_level": "low",
        "hipaa_impact": ["§164.308(a)(7)(ii)(A) - Data backup plan"]
    },

    # Logging restart
    "restart_logging_services": {
        "category": ActionCategory.SERVICE_RESTART,
        "requires_approval": False,
        "auto_approve_in_maintenance": True,
        "description": "Restart logging services (journald, fluent-bit)",
        "risk_level": "low",
        "hipaa_impact": ["§164.312(b) - Audit controls"]
    },

    # Firewall restore
    "restore_firewall_baseline": {
        "category": ActionCategory.DISRUPTIVE,
        "requires_approval": True,
        "auto_approve_in_maintenance": True,
        "description": "Restore firewall ruleset from baseline",
        "risk_level": "high",
        "hipaa_impact": ["§164.312(e)(1) - Transmission security"]
    },

    # Encryption - always requires manual intervention
    "enable_volume_encryption": {
        "category": ActionCategory.ALERT_ONLY,
        "requires_approval": True,
        "auto_approve_in_maintenance": False,  # Always needs human
        "description": "Enable LUKS encryption (requires manual intervention)",
        "risk_level": "critical",
        "hipaa_impact": ["§164.312(a)(2)(iv) - Encryption"]
    },

    # Windows BitLocker
    "enable_bitlocker": {
        "category": ActionCategory.DISRUPTIVE,
        "requires_approval": True,
        "auto_approve_in_maintenance": False,  # Always needs human
        "description": "Enable BitLocker encryption on Windows",
        "risk_level": "critical",
        "hipaa_impact": ["§164.312(a)(2)(iv) - Encryption"]
    },

    # Windows patching
    "apply_windows_updates": {
        "category": ActionCategory.DISRUPTIVE,
        "requires_approval": True,
        "auto_approve_in_maintenance": True,
        "description": "Apply Windows security updates",
        "risk_level": "high",
        "hipaa_impact": ["§164.308(a)(5)(ii)(B) - Protection from malware"]
    }
}


@dataclass
class ApprovalRequest:
    """An approval request for a disruptive action."""

    request_id: str
    action_name: str
    drift_check: str
    site_id: str
    host_id: str
    category: str
    description: str
    risk_level: str
    pre_state: Dict[str, Any]
    created_at: str
    expires_at: str
    status: str = "pending"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    auto_approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ApprovalManager:
    """
    Manages approval requests for disruptive actions.

    Uses SQLite for persistence with audit trail.
    """

    def __init__(self, db_path: Path):
        """
        Initialize approval manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS approval_requests (
                request_id TEXT PRIMARY KEY,
                action_name TEXT NOT NULL,
                drift_check TEXT NOT NULL,
                site_id TEXT NOT NULL,
                host_id TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                risk_level TEXT,
                pre_state TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                approved_by TEXT,
                approved_at TEXT,
                rejection_reason TEXT,
                auto_approved INTEGER DEFAULT 0
            )
        ''')

        # Audit log for all approval decisions
        conn.execute('''
            CREATE TABLE IF NOT EXISTS approval_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT,
                timestamp TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (request_id) REFERENCES approval_requests(request_id)
            )
        ''')

        # Create indexes
        conn.execute('CREATE INDEX IF NOT EXISTS idx_status ON approval_requests(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_site ON approval_requests(site_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_created ON approval_requests(created_at)')

        conn.commit()
        conn.close()

    def requires_approval(
        self,
        action_name: str,
        in_maintenance_window: bool = False
    ) -> bool:
        """
        Check if an action requires approval.

        Args:
            action_name: Name of the remediation action
            in_maintenance_window: Whether currently in maintenance window

        Returns:
            True if approval is required
        """
        policy = ACTION_POLICIES.get(action_name)

        if not policy:
            # Unknown action - require approval by default
            logger.warning(f"Unknown action {action_name}, requiring approval")
            return True

        if not policy["requires_approval"]:
            return False

        # Check if auto-approve in maintenance window
        if in_maintenance_window and policy.get("auto_approve_in_maintenance", False):
            return False

        return True

    def create_request(
        self,
        action_name: str,
        drift_check: str,
        site_id: str,
        host_id: str,
        pre_state: Dict[str, Any],
        expires_hours: int = 24
    ) -> ApprovalRequest:
        """
        Create a new approval request.

        Args:
            action_name: Name of the remediation action
            drift_check: Type of drift that triggered this
            site_id: Site identifier
            host_id: Host identifier
            pre_state: System state before remediation
            expires_hours: Hours until request expires

        Returns:
            Created ApprovalRequest
        """
        import uuid
        from datetime import timedelta

        policy = ACTION_POLICIES.get(action_name, {})

        now = datetime.now(timezone.utc)
        request_id = f"APR-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

        request = ApprovalRequest(
            request_id=request_id,
            action_name=action_name,
            drift_check=drift_check,
            site_id=site_id,
            host_id=host_id,
            category=policy.get("category", ActionCategory.DISRUPTIVE).value if isinstance(policy.get("category"), ActionCategory) else policy.get("category", "disruptive"),
            description=policy.get("description", action_name),
            risk_level=policy.get("risk_level", "medium"),
            pre_state=pre_state,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=expires_hours)).isoformat()
        )

        # Save to database
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            INSERT INTO approval_requests
            (request_id, action_name, drift_check, site_id, host_id, category,
             description, risk_level, pre_state, created_at, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.request_id, request.action_name, request.drift_check,
            request.site_id, request.host_id, request.category,
            request.description, request.risk_level, json.dumps(request.pre_state),
            request.created_at, request.expires_at, request.status
        ))

        # Audit log
        conn.execute('''
            INSERT INTO approval_audit_log (request_id, action, actor, timestamp, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            request.request_id, "created", "system",
            now.isoformat(), json.dumps({"action_name": action_name})
        ))

        conn.commit()
        conn.close()

        logger.info(f"Created approval request {request_id} for {action_name}")
        return request

    def approve(
        self,
        request_id: str,
        approved_by: str,
        auto_approved: bool = False
    ) -> Optional[ApprovalRequest]:
        """
        Approve an action request.

        Args:
            request_id: Request ID to approve
            approved_by: Username/ID of approver
            auto_approved: Whether this was auto-approved

        Returns:
            Updated ApprovalRequest or None if not found
        """
        now = datetime.now(timezone.utc)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Check request exists and is pending
        cursor = conn.execute(
            'SELECT * FROM approval_requests WHERE request_id = ?',
            (request_id,)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None

        if row['status'] != 'pending':
            conn.close()
            logger.warning(f"Request {request_id} is not pending: {row['status']}")
            return None

        # Check expiration
        expires_at = datetime.fromisoformat(row['expires_at'])
        if now > expires_at:
            conn.execute(
                'UPDATE approval_requests SET status = ? WHERE request_id = ?',
                ('expired', request_id)
            )
            conn.commit()
            conn.close()
            logger.warning(f"Request {request_id} has expired")
            return None

        # Approve
        conn.execute('''
            UPDATE approval_requests
            SET status = ?, approved_by = ?, approved_at = ?, auto_approved = ?
            WHERE request_id = ?
        ''', ('approved', approved_by, now.isoformat(), 1 if auto_approved else 0, request_id))

        # Audit log
        conn.execute('''
            INSERT INTO approval_audit_log (request_id, action, actor, timestamp, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            request_id, "approved", approved_by, now.isoformat(),
            json.dumps({"auto_approved": auto_approved})
        ))

        conn.commit()

        # Fetch updated request
        cursor = conn.execute(
            'SELECT * FROM approval_requests WHERE request_id = ?',
            (request_id,)
        )
        row = cursor.fetchone()
        conn.close()

        logger.info(f"Approved request {request_id} by {approved_by}")
        return self._row_to_request(row)

    def reject(
        self,
        request_id: str,
        rejected_by: str,
        reason: str
    ) -> Optional[ApprovalRequest]:
        """
        Reject an action request.

        Args:
            request_id: Request ID to reject
            rejected_by: Username/ID of rejector
            reason: Reason for rejection

        Returns:
            Updated ApprovalRequest or None if not found
        """
        now = datetime.now(timezone.utc)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Check request exists and is pending
        cursor = conn.execute(
            'SELECT * FROM approval_requests WHERE request_id = ?',
            (request_id,)
        )
        row = cursor.fetchone()

        if not row or row['status'] != 'pending':
            conn.close()
            return None

        # Reject
        conn.execute('''
            UPDATE approval_requests
            SET status = ?, approved_by = ?, approved_at = ?, rejection_reason = ?
            WHERE request_id = ?
        ''', ('rejected', rejected_by, now.isoformat(), reason, request_id))

        # Audit log
        conn.execute('''
            INSERT INTO approval_audit_log (request_id, action, actor, timestamp, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            request_id, "rejected", rejected_by, now.isoformat(),
            json.dumps({"reason": reason})
        ))

        conn.commit()

        # Fetch updated request
        cursor = conn.execute(
            'SELECT * FROM approval_requests WHERE request_id = ?',
            (request_id,)
        )
        row = cursor.fetchone()
        conn.close()

        logger.info(f"Rejected request {request_id} by {rejected_by}: {reason}")
        return self._row_to_request(row)

    def get_pending(
        self,
        site_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ApprovalRequest]:
        """
        Get pending approval requests.

        Args:
            site_id: Filter by site (optional)
            limit: Maximum number of results

        Returns:
            List of pending requests
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        if site_id:
            cursor = conn.execute('''
                SELECT * FROM approval_requests
                WHERE status = 'pending' AND site_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (site_id, limit))
        else:
            cursor = conn.execute('''
                SELECT * FROM approval_requests
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_request(row) for row in rows]

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Get a specific approval request."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            'SELECT * FROM approval_requests WHERE request_id = ?',
            (request_id,)
        )
        row = cursor.fetchone()
        conn.close()

        return self._row_to_request(row) if row else None

    def is_approved(self, request_id: str) -> bool:
        """Check if a request has been approved."""
        request = self.get_request(request_id)
        return request is not None and request.status == "approved"

    def get_audit_log(
        self,
        request_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get audit log entries.

        Args:
            request_id: Filter by request (optional)
            limit: Maximum number of results

        Returns:
            List of audit log entries
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        if request_id:
            cursor = conn.execute('''
                SELECT * FROM approval_audit_log
                WHERE request_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (request_id, limit))
        else:
            cursor = conn.execute('''
                SELECT * FROM approval_audit_log
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def expire_old_requests(self) -> int:
        """
        Mark expired pending requests.

        Returns:
            Number of requests expired
        """
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            UPDATE approval_requests
            SET status = 'expired'
            WHERE status = 'pending' AND expires_at < ?
        ''', (now,))

        expired_count = cursor.rowcount
        conn.commit()
        conn.close()

        if expired_count > 0:
            logger.info(f"Expired {expired_count} pending approval requests")

        return expired_count

    def get_stats(self) -> Dict[str, Any]:
        """Get approval statistics."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        stats = {}

        # Count by status
        cursor = conn.execute('''
            SELECT status, COUNT(*) as count
            FROM approval_requests
            GROUP BY status
        ''')
        stats["by_status"] = {row['status']: row['count'] for row in cursor.fetchall()}

        # Count by action
        cursor = conn.execute('''
            SELECT action_name, COUNT(*) as count
            FROM approval_requests
            GROUP BY action_name
        ''')
        stats["by_action"] = {row['action_name']: row['count'] for row in cursor.fetchall()}

        # Recent activity
        cursor = conn.execute('''
            SELECT COUNT(*) as count
            FROM approval_requests
            WHERE created_at > datetime('now', '-24 hours')
        ''')
        stats["requests_24h"] = cursor.fetchone()['count']

        conn.close()
        return stats

    def _row_to_request(self, row: sqlite3.Row) -> ApprovalRequest:
        """Convert database row to ApprovalRequest."""
        return ApprovalRequest(
            request_id=row['request_id'],
            action_name=row['action_name'],
            drift_check=row['drift_check'],
            site_id=row['site_id'],
            host_id=row['host_id'],
            category=row['category'],
            description=row['description'],
            risk_level=row['risk_level'],
            pre_state=json.loads(row['pre_state']) if row['pre_state'] else {},
            created_at=row['created_at'],
            expires_at=row['expires_at'],
            status=row['status'],
            approved_by=row['approved_by'],
            approved_at=row['approved_at'],
            rejection_reason=row['rejection_reason'],
            auto_approved=bool(row['auto_approved'])
        )


def get_action_policy(action_name: str) -> Dict[str, Any]:
    """
    Get the policy for a specific action.

    Args:
        action_name: Name of the action

    Returns:
        Policy dictionary or default policy
    """
    return ACTION_POLICIES.get(action_name, {
        "category": ActionCategory.DISRUPTIVE,
        "requires_approval": True,
        "auto_approve_in_maintenance": False,
        "description": f"Unknown action: {action_name}",
        "risk_level": "high",
        "hipaa_impact": []
    })
