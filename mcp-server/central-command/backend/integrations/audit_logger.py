"""
Comprehensive audit logging for integration access.

HIPAA 164.312(b) requires audit controls that record and examine
activity in systems that contain or use PHI.

This logger records:
- OAuth flows (authorize, callback, token refresh)
- Role assumptions (AWS STS)
- Sync operations (start, complete, resources accessed)
- Credential operations (create, rotate, delete)
- Configuration changes
- Access denials and security events

All logs are stored in the append-only integration_audit_log table.

Usage:
    audit = IntegrationAuditLogger(db)

    await audit.log_oauth_started(
        site_id="site-123",
        provider="google_workspace",
        user_id="user-456",
        ip_address="192.168.1.1"
    )

    await audit.log_sync_completed(
        integration_id="int-789",
        resources_collected=150,
        duration_seconds=45
    )
"""

import logging
import json
from datetime import datetime
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, asdict
from enum import Enum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Audit event types for categorization."""

    # OAuth events
    OAUTH_STARTED = "oauth_started"
    OAUTH_CALLBACK = "oauth_callback"
    OAUTH_SUCCESS = "oauth_success"
    OAUTH_FAILED = "oauth_failed"
    TOKEN_REFRESHED = "token_refreshed"
    TOKEN_REFRESH_FAILED = "token_refresh_failed"

    # AWS events
    AWS_ROLE_ASSUMED = "aws_role_assumed"
    AWS_ROLE_FAILED = "aws_role_failed"
    AWS_SESSION_CREATED = "aws_session_created"
    AWS_SESSION_EXPIRED = "aws_session_expired"

    # Integration lifecycle
    INTEGRATION_CREATED = "integration_created"
    INTEGRATION_CONNECTED = "integration_connected"
    INTEGRATION_DISCONNECTED = "integration_disconnected"
    INTEGRATION_DELETED = "integration_deleted"
    INTEGRATION_DISABLED = "integration_disabled"
    INTEGRATION_ENABLED = "integration_enabled"

    # Sync events
    SYNC_STARTED = "sync_started"
    SYNC_COMPLETED = "sync_completed"
    SYNC_FAILED = "sync_failed"
    SYNC_TIMEOUT = "sync_timeout"

    # Resource events
    RESOURCES_COLLECTED = "resources_collected"
    RESOURCE_COMPLIANCE_CHANGED = "resource_compliance_changed"
    RESOURCES_EXPORTED = "resources_exported"

    # Credential events
    CREDENTIALS_CREATED = "credentials_created"
    CREDENTIALS_UPDATED = "credentials_updated"
    CREDENTIALS_ROTATED = "credentials_rotated"
    CREDENTIALS_DELETED = "credentials_deleted"
    CREDENTIALS_ACCESSED = "credentials_accessed"

    # Configuration events
    CONFIG_UPDATED = "config_updated"
    RESOURCE_TYPES_CHANGED = "resource_types_changed"
    SYNC_SCHEDULE_CHANGED = "sync_schedule_changed"

    # Security events
    ACCESS_DENIED = "access_denied"
    INVALID_STATE = "invalid_state"
    STATE_MISMATCH = "state_mismatch"
    RATE_LIMITED = "rate_limited"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


class AuditCategory(str, Enum):
    """Audit event categories."""
    AUTH = "auth"
    SYNC = "sync"
    CREDENTIAL = "credential"
    CONFIG = "config"
    SECURITY = "security"
    RESOURCE = "resource"
    LIFECYCLE = "lifecycle"


# Map event types to categories
EVENT_CATEGORIES: Dict[AuditEventType, AuditCategory] = {
    # Auth
    AuditEventType.OAUTH_STARTED: AuditCategory.AUTH,
    AuditEventType.OAUTH_CALLBACK: AuditCategory.AUTH,
    AuditEventType.OAUTH_SUCCESS: AuditCategory.AUTH,
    AuditEventType.OAUTH_FAILED: AuditCategory.AUTH,
    AuditEventType.TOKEN_REFRESHED: AuditCategory.AUTH,
    AuditEventType.TOKEN_REFRESH_FAILED: AuditCategory.AUTH,
    AuditEventType.AWS_ROLE_ASSUMED: AuditCategory.AUTH,
    AuditEventType.AWS_ROLE_FAILED: AuditCategory.AUTH,
    AuditEventType.AWS_SESSION_CREATED: AuditCategory.AUTH,
    AuditEventType.AWS_SESSION_EXPIRED: AuditCategory.AUTH,

    # Lifecycle
    AuditEventType.INTEGRATION_CREATED: AuditCategory.LIFECYCLE,
    AuditEventType.INTEGRATION_CONNECTED: AuditCategory.LIFECYCLE,
    AuditEventType.INTEGRATION_DISCONNECTED: AuditCategory.LIFECYCLE,
    AuditEventType.INTEGRATION_DELETED: AuditCategory.LIFECYCLE,
    AuditEventType.INTEGRATION_DISABLED: AuditCategory.LIFECYCLE,
    AuditEventType.INTEGRATION_ENABLED: AuditCategory.LIFECYCLE,

    # Sync
    AuditEventType.SYNC_STARTED: AuditCategory.SYNC,
    AuditEventType.SYNC_COMPLETED: AuditCategory.SYNC,
    AuditEventType.SYNC_FAILED: AuditCategory.SYNC,
    AuditEventType.SYNC_TIMEOUT: AuditCategory.SYNC,

    # Resource
    AuditEventType.RESOURCES_COLLECTED: AuditCategory.RESOURCE,
    AuditEventType.RESOURCE_COMPLIANCE_CHANGED: AuditCategory.RESOURCE,
    AuditEventType.RESOURCES_EXPORTED: AuditCategory.RESOURCE,

    # Credential
    AuditEventType.CREDENTIALS_CREATED: AuditCategory.CREDENTIAL,
    AuditEventType.CREDENTIALS_UPDATED: AuditCategory.CREDENTIAL,
    AuditEventType.CREDENTIALS_ROTATED: AuditCategory.CREDENTIAL,
    AuditEventType.CREDENTIALS_DELETED: AuditCategory.CREDENTIAL,
    AuditEventType.CREDENTIALS_ACCESSED: AuditCategory.CREDENTIAL,

    # Config
    AuditEventType.CONFIG_UPDATED: AuditCategory.CONFIG,
    AuditEventType.RESOURCE_TYPES_CHANGED: AuditCategory.CONFIG,
    AuditEventType.SYNC_SCHEDULE_CHANGED: AuditCategory.CONFIG,

    # Security
    AuditEventType.ACCESS_DENIED: AuditCategory.SECURITY,
    AuditEventType.INVALID_STATE: AuditCategory.SECURITY,
    AuditEventType.STATE_MISMATCH: AuditCategory.SECURITY,
    AuditEventType.RATE_LIMITED: AuditCategory.SECURITY,
    AuditEventType.SUSPICIOUS_ACTIVITY: AuditCategory.SECURITY,
}


@dataclass
class AuditEntry:
    """Audit log entry data."""
    site_id: str
    event_type: AuditEventType
    event_category: AuditCategory
    integration_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    actor_username: Optional[str] = None
    actor_ip: Optional[str] = None
    actor_user_agent: Optional[str] = None
    request_id: Optional[str] = None
    request_path: Optional[str] = None
    event_data: Optional[Dict[str, Any]] = None
    resources_affected: Optional[List[Dict[str, str]]] = None
    resource_count: int = 0


class IntegrationAuditLogger:
    """
    Audit logger for integration operations.

    All methods write to the append-only integration_audit_log table.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the audit logger.

        Args:
            db: Async database session
        """
        self.db = db

    async def _get_site_uuid(self, site_id: str) -> Optional[str]:
        """
        Convert human-readable site_id to UUID.

        Args:
            site_id: Human-readable site ID (e.g., "physical-appliance-pilot-1aea78")

        Returns:
            UUID string or None if not found
        """
        # Check if it's already a UUID format
        try:
            import uuid as uuid_module
            uuid_module.UUID(site_id)
            return site_id  # Already a UUID
        except ValueError:
            pass

        # Look up the UUID from the sites table
        result = await self.db.execute(
            text("SELECT id FROM sites WHERE site_id = :site_id"),
            {"site_id": site_id}
        )
        row = result.fetchone()
        return str(row[0]) if row else None

    async def _log(self, entry: AuditEntry) -> int:
        """
        Write an audit entry to the database.

        Args:
            entry: Audit entry data

        Returns:
            ID of the created audit log entry
        """
        # Convert human-readable site_id to UUID
        site_uuid = await self._get_site_uuid(entry.site_id)
        if not site_uuid:
            logger.warning(f"Site not found for audit log: {entry.site_id}")
            return 0

        result = await self.db.execute(
            text("""
                INSERT INTO integration_audit_log (
                    integration_id,
                    site_id,
                    event_type,
                    event_category,
                    event_data,
                    actor_user_id,
                    actor_username,
                    actor_ip,
                    actor_user_agent,
                    request_id,
                    request_path,
                    resources_affected,
                    resource_count
                ) VALUES (
                    :integration_id,
                    CAST(:site_id AS uuid),
                    :event_type,
                    :event_category,
                    :event_data,
                    :actor_user_id,
                    :actor_username,
                    :actor_ip,
                    :actor_user_agent,
                    :request_id,
                    :request_path,
                    :resources_affected,
                    :resource_count
                )
                RETURNING id
            """),
            {
                "integration_id": entry.integration_id,
                "site_id": site_uuid,
                "event_type": entry.event_type.value,
                "event_category": entry.event_category.value,
                "event_data": json.dumps(entry.event_data) if entry.event_data else None,
                "actor_user_id": entry.actor_user_id,
                "actor_username": entry.actor_username,
                "actor_ip": entry.actor_ip,
                "actor_user_agent": entry.actor_user_agent,
                "request_id": entry.request_id,
                "request_path": entry.request_path,
                "resources_affected": json.dumps(entry.resources_affected) if entry.resources_affected else None,
                "resource_count": entry.resource_count,
            }
        )
        await self.db.commit()

        row = result.fetchone()
        audit_id = row[0] if row else 0

        logger.debug(
            f"Audit logged: {entry.event_type.value} site={entry.site_id} "
            f"integration={entry.integration_id} audit_id={audit_id}"
        )

        return audit_id

    # ==========================================================================
    # OAuth Events
    # ==========================================================================

    async def log_oauth_started(
        self,
        site_id: str,
        provider: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> int:
        """Log OAuth flow initiation."""
        return await self._log(AuditEntry(
            site_id=site_id,
            event_type=AuditEventType.OAUTH_STARTED,
            event_category=AuditCategory.AUTH,
            actor_user_id=user_id,
            actor_ip=ip_address,
            actor_user_agent=user_agent,
            event_data={"provider": provider}
        ))

    async def log_oauth_success(
        self,
        site_id: str,
        integration_id: str,
        provider: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> int:
        """Log successful OAuth completion."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.OAUTH_SUCCESS,
            event_category=AuditCategory.AUTH,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={"provider": provider}
        ))

    async def log_oauth_failed(
        self,
        site_id: str,
        provider: str,
        error: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> int:
        """Log OAuth failure."""
        return await self._log(AuditEntry(
            site_id=site_id,
            event_type=AuditEventType.OAUTH_FAILED,
            event_category=AuditCategory.AUTH,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={"provider": provider, "error": error}
        ))

    async def log_token_refreshed(
        self,
        site_id: str,
        integration_id: str
    ) -> int:
        """Log successful token refresh."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.TOKEN_REFRESHED,
            event_category=AuditCategory.AUTH,
        ))

    # ==========================================================================
    # AWS Events
    # ==========================================================================

    async def log_aws_role_assumed(
        self,
        site_id: str,
        integration_id: str,
        role_arn: str,
        session_duration: int,
        user_id: Optional[str] = None
    ) -> int:
        """Log AWS role assumption."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.AWS_ROLE_ASSUMED,
            event_category=AuditCategory.AUTH,
            actor_user_id=user_id,
            event_data={
                "role_arn": role_arn,
                "session_duration_seconds": session_duration
            }
        ))

    async def log_aws_role_failed(
        self,
        site_id: str,
        integration_id: str,
        role_arn: str,
        error: str,
        user_id: Optional[str] = None
    ) -> int:
        """Log AWS role assumption failure."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.AWS_ROLE_FAILED,
            event_category=AuditCategory.AUTH,
            actor_user_id=user_id,
            event_data={"role_arn": role_arn, "error": error}
        ))

    # ==========================================================================
    # Integration Lifecycle Events
    # ==========================================================================

    async def log_integration_created(
        self,
        site_id: str,
        integration_id: str,
        provider: str,
        name: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> int:
        """Log integration creation."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.INTEGRATION_CREATED,
            event_category=AuditCategory.LIFECYCLE,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={"provider": provider, "name": name}
        ))

    async def log_integration_deleted(
        self,
        site_id: str,
        integration_id: str,
        provider: str,
        name: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> int:
        """Log integration deletion."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.INTEGRATION_DELETED,
            event_category=AuditCategory.LIFECYCLE,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={"provider": provider, "name": name}
        ))

    # ==========================================================================
    # Sync Events
    # ==========================================================================

    async def log_sync_started(
        self,
        site_id: str,
        integration_id: str,
        resource_types: List[str],
        triggered_by: str = "manual"
    ) -> int:
        """Log sync operation start."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.SYNC_STARTED,
            event_category=AuditCategory.SYNC,
            event_data={
                "resource_types": resource_types,
                "triggered_by": triggered_by
            }
        ))

    async def log_sync_completed(
        self,
        site_id: str,
        integration_id: str,
        resources_collected: int,
        duration_seconds: int,
        resource_types: Optional[Dict[str, int]] = None
    ) -> int:
        """Log sync operation completion."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.SYNC_COMPLETED,
            event_category=AuditCategory.SYNC,
            resource_count=resources_collected,
            event_data={
                "resources_collected": resources_collected,
                "duration_seconds": duration_seconds,
                "resource_types": resource_types
            }
        ))

    async def log_sync_failed(
        self,
        site_id: str,
        integration_id: str,
        error: str,
        duration_seconds: Optional[int] = None
    ) -> int:
        """Log sync operation failure."""
        return await self._log(AuditEntry(
            site_id=site_id,
            integration_id=integration_id,
            event_type=AuditEventType.SYNC_FAILED,
            event_category=AuditCategory.SYNC,
            event_data={
                "error": error,
                "duration_seconds": duration_seconds
            }
        ))

    # ==========================================================================
    # Security Events
    # ==========================================================================

    async def log_access_denied(
        self,
        site_id: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        reason: Optional[str] = None
    ) -> int:
        """Log access denial (for security monitoring)."""
        return await self._log(AuditEntry(
            site_id=site_id,
            event_type=AuditEventType.ACCESS_DENIED,
            event_category=AuditCategory.SECURITY,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "reason": reason
            }
        ))

    async def log_suspicious_activity(
        self,
        site_id: str,
        activity_type: str,
        details: Dict[str, Any],
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> int:
        """Log suspicious activity for security monitoring."""
        return await self._log(AuditEntry(
            site_id=site_id,
            event_type=AuditEventType.SUSPICIOUS_ACTIVITY,
            event_category=AuditCategory.SECURITY,
            actor_user_id=user_id,
            actor_ip=ip_address,
            event_data={
                "activity_type": activity_type,
                "details": details
            }
        ))

    # ==========================================================================
    # Query Methods
    # ==========================================================================

    async def get_recent_events(
        self,
        site_id: str,
        integration_id: Optional[str] = None,
        category: Optional[AuditCategory] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent audit events for a site/integration.

        Args:
            site_id: Site ID
            integration_id: Optional integration ID filter
            category: Optional category filter
            limit: Max results

        Returns:
            List of audit events
        """
        query = """
            SELECT
                id,
                integration_id,
                event_type,
                event_category,
                event_data,
                actor_user_id,
                actor_username,
                actor_ip,
                resource_count,
                created_at
            FROM integration_audit_log
            WHERE site_id = :site_id
        """
        params: Dict[str, Any] = {"site_id": site_id, "limit": limit}

        if integration_id:
            query += " AND integration_id = :integration_id"
            params["integration_id"] = integration_id

        if category:
            query += " AND event_category = :category"
            params["category"] = category.value

        query += " ORDER BY created_at DESC LIMIT :limit"

        result = await self.db.execute(text(query), params)
        rows = result.fetchall()

        return [
            {
                "id": row[0],
                "integration_id": row[1],
                "event_type": row[2],
                "event_category": row[3],
                "event_data": json.loads(row[4]) if row[4] else None,
                "actor_user_id": row[5],
                "actor_username": row[6],
                "actor_ip": str(row[7]) if row[7] else None,
                "resource_count": row[8],
                "created_at": row[9].isoformat() if row[9] else None,
            }
            for row in rows
        ]

    async def get_security_events(
        self,
        site_id: str,
        since_hours: int = 24,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent security events for monitoring.

        Args:
            site_id: Site ID
            since_hours: Hours to look back
            limit: Max results

        Returns:
            List of security events
        """
        result = await self.db.execute(
            text("""
                SELECT
                    id,
                    integration_id,
                    event_type,
                    event_data,
                    actor_user_id,
                    actor_ip,
                    created_at
                FROM integration_audit_log
                WHERE site_id = :site_id
                  AND event_category = 'security'
                  AND created_at > NOW() - INTERVAL ':hours hours'
                ORDER BY created_at DESC
                LIMIT :limit
            """.replace(":hours", str(since_hours))),
            {"site_id": site_id, "limit": limit}
        )
        rows = result.fetchall()

        return [
            {
                "id": row[0],
                "integration_id": row[1],
                "event_type": row[2],
                "event_data": json.loads(row[3]) if row[3] else None,
                "actor_user_id": row[4],
                "actor_ip": str(row[5]) if row[5] else None,
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]
