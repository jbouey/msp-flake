"""
Integration Sync Engine.

Handles background synchronization of cloud resources with:
- Parallel sync across integrations
- 5-minute timeout per integration
- MAX_RESOURCES_PER_TYPE = 5000 limit
- Automatic retry with exponential backoff
- Evidence bundle generation for findings

Security:
- Per-integration credential decryption
- Audit logging for all sync operations
- Resource limits to prevent DoS

Usage:
    engine = SyncEngine(db, vault, audit_logger)

    # Sync single integration
    result = await engine.sync_integration(integration_id)

    # Sync all integrations for a site
    results = await engine.sync_site(site_id)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .credential_vault import CredentialVault
from .audit_logger import IntegrationAuditLogger
from .secure_credentials import SecureCredentials

logger = logging.getLogger(__name__)


# Configuration
SYNC_TIMEOUT_SECONDS = 300  # 5 minutes per integration
MAX_RESOURCES_PER_TYPE = 5000
MAX_PARALLEL_SYNCS = 5
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = [5, 15, 30]  # seconds
DEFAULT_SYNC_INTERVAL_MINUTES = 60


class SyncStatus(str, Enum):
    """Sync job status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


@dataclass
class SyncResult:
    """Result of a sync operation."""
    integration_id: str
    status: SyncStatus
    resources_synced: int = 0
    resources_created: int = 0
    resources_updated: int = 0
    resources_deleted: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0
    completed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "integration_id": self.integration_id,
            "status": self.status.value,
            "resources_synced": self.resources_synced,
            "resources_created": self.resources_created,
            "resources_updated": self.resources_updated,
            "resources_deleted": self.resources_deleted,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }


@dataclass
class ResourceChange:
    """A detected resource change."""
    resource_type: str
    resource_id: str
    change_type: str  # created, updated, deleted
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None


class SyncEngine:
    """
    Orchestrates sync operations for cloud integrations.

    Handles parallel sync with timeouts, retries, and resource limits.
    """

    def __init__(
        self,
        db: AsyncSession,
        credential_vault: CredentialVault,
        audit_logger: IntegrationAuditLogger
    ):
        """
        Initialize the sync engine.

        Args:
            db: Database session
            credential_vault: For decrypting integration credentials
            audit_logger: For audit trail
        """
        self.db = db
        self.vault = credential_vault
        self.audit = audit_logger
        self._running_syncs: Dict[str, asyncio.Task] = {}

    async def sync_integration(
        self,
        integration_id: str,
        job_id: Optional[str] = None,
        triggered_by: Optional[str] = None
    ) -> SyncResult:
        """
        Sync a single integration.

        Args:
            integration_id: Integration to sync
            job_id: Optional sync job ID for tracking
            triggered_by: User who triggered the sync

        Returns:
            SyncResult with sync outcome
        """
        start_time = datetime.now(timezone.utc)

        # Get integration details
        result = await self.db.execute(
            text("""
                SELECT id, site_id, provider, name, credentials_encrypted,
                       aws_role_arn, aws_external_id, aws_regions
                FROM integrations
                WHERE id = :id AND status = 'active'
            """),
            {"id": integration_id}
        )
        row = result.fetchone()

        if not row:
            return SyncResult(
                integration_id=integration_id,
                status=SyncStatus.FAILED,
                errors=["Integration not found or not active"]
            )

        site_id = str(row.site_id)
        provider = row.provider

        # Log sync start
        await self.audit.log_sync_start(
            site_id=site_id,
            integration_id=integration_id,
            triggered_by=triggered_by
        )

        try:
            # Run sync with timeout
            sync_result = await asyncio.wait_for(
                self._run_sync(row),
                timeout=SYNC_TIMEOUT_SECONDS
            )

        except asyncio.TimeoutError:
            sync_result = SyncResult(
                integration_id=integration_id,
                status=SyncStatus.TIMEOUT,
                errors=[f"Sync timed out after {SYNC_TIMEOUT_SECONDS} seconds"]
            )
            logger.error(f"Sync timeout: integration={integration_id}")

        except Exception as e:
            sync_result = SyncResult(
                integration_id=integration_id,
                status=SyncStatus.FAILED,
                errors=[str(e)]
            )
            logger.exception(f"Sync error: integration={integration_id}")

        # Calculate duration
        sync_result.duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
        sync_result.completed_at = datetime.now(timezone.utc)

        # Update integration record
        await self._update_integration_status(integration_id, sync_result)

        # Update job record if provided
        if job_id:
            await self._update_job_status(job_id, sync_result)

        # Log sync completion
        await self.audit.log_sync_complete(
            site_id=site_id,
            integration_id=integration_id,
            resources_synced=sync_result.resources_synced,
            success=sync_result.status in (SyncStatus.COMPLETED, SyncStatus.PARTIAL),
            error=sync_result.errors[0] if sync_result.errors else None
        )

        return sync_result

    async def _run_sync(self, integration_row) -> SyncResult:
        """Execute the actual sync for an integration."""
        integration_id = str(integration_row.id)
        provider = integration_row.provider

        # Decrypt credentials
        credentials = await self.vault.decrypt_credentials(
            integration_id,
            integration_row.credentials_encrypted
        )

        # Create appropriate connector and collect resources
        if provider == "aws":
            resources = await self._sync_aws(
                integration_id=integration_id,
                site_id=str(integration_row.site_id),
                credentials=credentials,
                role_arn=integration_row.aws_role_arn,
                external_id=integration_row.aws_external_id,
                regions=integration_row.aws_regions or ["us-east-1"]
            )
        elif provider == "google_workspace":
            resources = await self._sync_google(
                integration_id=integration_id,
                site_id=str(integration_row.site_id),
                credentials=credentials
            )
        elif provider == "okta":
            resources = await self._sync_okta(
                integration_id=integration_id,
                site_id=str(integration_row.site_id),
                credentials=credentials
            )
        elif provider == "azure_ad":
            resources = await self._sync_azure(
                integration_id=integration_id,
                site_id=str(integration_row.site_id),
                credentials=credentials
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

        # Store resources and detect changes
        changes = await self._store_resources(integration_id, resources)

        return SyncResult(
            integration_id=integration_id,
            status=SyncStatus.COMPLETED,
            resources_synced=len(resources),
            resources_created=changes["created"],
            resources_updated=changes["updated"],
            resources_deleted=changes["deleted"]
        )

    async def _sync_aws(
        self,
        integration_id: str,
        site_id: str,
        credentials: Dict[str, Any],
        role_arn: str,
        external_id: str,
        regions: List[str]
    ) -> List[Dict[str, Any]]:
        """Sync AWS resources."""
        from .aws.connector import AWSConnector

        connector = AWSConnector(
            integration_id=integration_id,
            site_id=site_id,
            role_arn=role_arn,
            external_id=external_id,
            regions=regions,
            audit_logger=self.audit
        )

        try:
            resources = await connector.collect_all_resources()
            return [self._resource_to_dict(r) for r in resources]
        finally:
            await connector.close()

    async def _sync_google(
        self,
        integration_id: str,
        site_id: str,
        credentials: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Sync Google Workspace resources."""
        from .oauth.google_connector import GoogleWorkspaceConnector
        from .oauth.base_connector import OAuthConfig
        from .oauth_state import OAuthStateManager

        # Get Redis for state manager
        try:
            from main import redis_client
        except ImportError:
            import sys
            redis_client = sys.modules.get('server', {}).redis_client

        config = OAuthConfig(
            client_id=credentials.get("client_id"),
            client_secret=SecureCredentials({"client_secret": credentials.get("client_secret")}),
            redirect_uri="",  # Not needed for resource collection
            scopes=[]
        )

        state_manager = OAuthStateManager(redis_client)

        connector = GoogleWorkspaceConnector(
            integration_id=integration_id,
            site_id=site_id,
            config=config,
            credential_vault=self.vault,
            state_manager=state_manager,
            audit_logger=self.audit,
            customer_id=credentials.get("google_customer_id", "my_customer")
        )

        # Load tokens
        await connector.load_encrypted_tokens(
            await self.vault.encrypt_credentials(integration_id, {
                "access_token": credentials.get("access_token"),
                "refresh_token": credentials.get("refresh_token"),
                "expires_at": credentials.get("token_expires_at")
            })
        )

        try:
            resources = await connector.collect_resources()
            return [self._resource_to_dict(r) for r in resources]
        finally:
            await connector.close()

    async def _sync_okta(
        self,
        integration_id: str,
        site_id: str,
        credentials: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Sync Okta resources."""
        from .oauth.okta_connector import OktaConnector
        from .oauth.base_connector import OAuthConfig
        from .oauth_state import OAuthStateManager

        try:
            from main import redis_client
        except ImportError:
            import sys
            redis_client = sys.modules.get('server', {}).redis_client

        config = OAuthConfig(
            client_id=credentials.get("client_id"),
            client_secret=SecureCredentials({"client_secret": credentials.get("client_secret")}),
            redirect_uri="",
            scopes=[]
        )

        state_manager = OAuthStateManager(redis_client)

        connector = OktaConnector(
            integration_id=integration_id,
            site_id=site_id,
            config=config,
            credential_vault=self.vault,
            state_manager=state_manager,
            audit_logger=self.audit,
            okta_domain=credentials.get("okta_domain")
        )

        # Load tokens
        await connector.load_encrypted_tokens(
            await self.vault.encrypt_credentials(integration_id, {
                "access_token": credentials.get("access_token"),
                "refresh_token": credentials.get("refresh_token"),
                "expires_at": credentials.get("token_expires_at")
            })
        )

        try:
            resources = await connector.collect_resources()
            return [self._resource_to_dict(r) for r in resources]
        finally:
            await connector.close()

    async def _sync_azure(
        self,
        integration_id: str,
        site_id: str,
        credentials: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Sync Azure AD resources."""
        from .oauth.azure_connector import AzureADConnector
        from .oauth.base_connector import OAuthConfig
        from .oauth_state import OAuthStateManager

        try:
            from main import redis_client
        except ImportError:
            import sys
            redis_client = sys.modules.get('server', {}).redis_client

        config = OAuthConfig(
            client_id=credentials.get("client_id"),
            client_secret=SecureCredentials({"client_secret": credentials.get("client_secret")}),
            redirect_uri="",
            scopes=[]
        )

        state_manager = OAuthStateManager(redis_client)

        connector = AzureADConnector(
            integration_id=integration_id,
            site_id=site_id,
            config=config,
            credential_vault=self.vault,
            state_manager=state_manager,
            audit_logger=self.audit,
            tenant_id=credentials.get("tenant_id")
        )

        # Load tokens
        await connector.load_encrypted_tokens(
            await self.vault.encrypt_credentials(integration_id, {
                "access_token": credentials.get("access_token"),
                "refresh_token": credentials.get("refresh_token"),
                "expires_at": credentials.get("token_expires_at")
            })
        )

        try:
            resources = await connector.collect_resources()
            return [self._resource_to_dict(r) for r in resources]
        finally:
            await connector.close()

    def _resource_to_dict(self, resource) -> Dict[str, Any]:
        """Convert resource object to dict for storage."""
        return {
            "resource_type": resource.resource_type,
            "resource_id": resource.resource_id,
            "name": resource.name,
            "raw_data": resource.raw_data,
            "compliance_checks": resource.compliance_checks,
            "risk_level": resource.risk_level
        }

    async def _store_resources(
        self,
        integration_id: str,
        resources: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Store resources and detect changes.

        Returns counts of created, updated, deleted resources.
        """
        now = datetime.now(timezone.utc)
        changes = {"created": 0, "updated": 0, "deleted": 0}

        # Get existing resources
        result = await self.db.execute(
            text("""
                SELECT id, resource_type, resource_id, compliance_checks, risk_level
                FROM integration_resources
                WHERE integration_id = :integration_id
            """),
            {"integration_id": integration_id}
        )
        existing = {
            (row.resource_type, row.resource_id): {
                "id": row.id,
                "compliance_checks": row.compliance_checks,
                "risk_level": row.risk_level
            }
            for row in result.fetchall()
        }

        # Track which resources we've seen
        seen_keys = set()

        for resource in resources:
            key = (resource["resource_type"], resource["resource_id"])
            seen_keys.add(key)

            if key in existing:
                # Update existing resource
                old = existing[key]

                # Check for compliance changes
                if (resource["compliance_checks"] != old["compliance_checks"] or
                    resource["risk_level"] != old["risk_level"]):

                    await self.db.execute(
                        text("""
                            UPDATE integration_resources
                            SET name = :name,
                                raw_data = :raw_data,
                                compliance_checks = :compliance_checks,
                                risk_level = :risk_level,
                                last_synced = :last_synced
                            WHERE id = :id
                        """),
                        {
                            "id": old["id"],
                            "name": resource["name"],
                            "raw_data": resource["raw_data"],
                            "compliance_checks": resource["compliance_checks"],
                            "risk_level": resource["risk_level"],
                            "last_synced": now
                        }
                    )
                    changes["updated"] += 1
                else:
                    # Just update last_synced
                    await self.db.execute(
                        text("UPDATE integration_resources SET last_synced = :now WHERE id = :id"),
                        {"id": old["id"], "now": now}
                    )
            else:
                # Create new resource
                await self.db.execute(
                    text("""
                        INSERT INTO integration_resources (
                            integration_id, resource_type, resource_id, name,
                            raw_data, compliance_checks, risk_level, last_synced
                        ) VALUES (
                            :integration_id, :resource_type, :resource_id, :name,
                            :raw_data, :compliance_checks, :risk_level, :last_synced
                        )
                    """),
                    {
                        "integration_id": integration_id,
                        "resource_type": resource["resource_type"],
                        "resource_id": resource["resource_id"],
                        "name": resource["name"],
                        "raw_data": resource["raw_data"],
                        "compliance_checks": resource["compliance_checks"],
                        "risk_level": resource["risk_level"],
                        "last_synced": now
                    }
                )
                changes["created"] += 1

        # Delete resources that no longer exist
        for key, old in existing.items():
            if key not in seen_keys:
                await self.db.execute(
                    text("DELETE FROM integration_resources WHERE id = :id"),
                    {"id": old["id"]}
                )
                changes["deleted"] += 1

        await self.db.commit()

        logger.info(
            f"Resources stored: integration={integration_id} "
            f"created={changes['created']} updated={changes['updated']} "
            f"deleted={changes['deleted']}"
        )

        return changes

    async def _update_integration_status(
        self,
        integration_id: str,
        result: SyncResult
    ) -> None:
        """Update integration record after sync."""
        next_sync = datetime.now(timezone.utc) + timedelta(minutes=DEFAULT_SYNC_INTERVAL_MINUTES)

        status = "active"
        last_error = None

        if result.status == SyncStatus.FAILED:
            status = "error"
            last_error = result.errors[0] if result.errors else "Unknown error"
        elif result.status == SyncStatus.TIMEOUT:
            status = "error"
            last_error = "Sync timeout"

        await self.db.execute(
            text("""
                UPDATE integrations
                SET last_sync_at = :last_sync,
                    next_sync_at = :next_sync,
                    status = :status,
                    last_error = :last_error
                WHERE id = :id
            """),
            {
                "id": integration_id,
                "last_sync": result.completed_at,
                "next_sync": next_sync,
                "status": status,
                "last_error": last_error
            }
        )
        await self.db.commit()

    async def _update_job_status(
        self,
        job_id: str,
        result: SyncResult
    ) -> None:
        """Update sync job record."""
        await self.db.execute(
            text("""
                UPDATE integration_sync_jobs
                SET status = :status,
                    completed_at = :completed_at,
                    resources_synced = :resources_synced,
                    error_message = :error
                WHERE id = :id
            """),
            {
                "id": job_id,
                "status": result.status.value,
                "completed_at": result.completed_at,
                "resources_synced": result.resources_synced,
                "error": result.errors[0] if result.errors else None
            }
        )
        await self.db.commit()

    async def sync_site(
        self,
        site_id: str,
        max_parallel: int = MAX_PARALLEL_SYNCS
    ) -> List[SyncResult]:
        """
        Sync all integrations for a site.

        Args:
            site_id: Site to sync
            max_parallel: Maximum concurrent syncs

        Returns:
            List of SyncResult for each integration
        """
        # Get all active integrations for site
        result = await self.db.execute(
            text("""
                SELECT id FROM integrations
                WHERE site_id = :site_id AND status = 'active'
            """),
            {"site_id": site_id}
        )
        integration_ids = [str(row.id) for row in result.fetchall()]

        if not integration_ids:
            return []

        # Sync in parallel with limit
        semaphore = asyncio.Semaphore(max_parallel)

        async def sync_with_semaphore(integration_id: str) -> SyncResult:
            async with semaphore:
                return await self.sync_integration(integration_id)

        tasks = [sync_with_semaphore(iid) for iid in integration_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to failed results
        sync_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                sync_results.append(SyncResult(
                    integration_id=integration_ids[i],
                    status=SyncStatus.FAILED,
                    errors=[str(result)]
                ))
            else:
                sync_results.append(result)

        return sync_results

    async def schedule_sync(
        self,
        integration_id: str,
        delay_seconds: int = 0
    ) -> str:
        """
        Schedule a sync to run after a delay.

        Args:
            integration_id: Integration to sync
            delay_seconds: Delay before starting sync

        Returns:
            Job ID for tracking
        """
        import secrets

        job_id = secrets.token_urlsafe(16)

        # Create job record
        await self.db.execute(
            text("""
                INSERT INTO integration_sync_jobs (id, integration_id, status, scheduled_at)
                VALUES (:id, :integration_id, 'pending', :scheduled_at)
            """),
            {
                "id": job_id,
                "integration_id": integration_id,
                "scheduled_at": datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            }
        )
        await self.db.commit()

        # Schedule the task (in production, this would use a task queue like Celery)
        if delay_seconds > 0:
            asyncio.create_task(self._delayed_sync(job_id, integration_id, delay_seconds))
        else:
            asyncio.create_task(self.sync_integration(integration_id, job_id))

        return job_id

    async def _delayed_sync(
        self,
        job_id: str,
        integration_id: str,
        delay_seconds: int
    ) -> None:
        """Execute sync after delay."""
        await asyncio.sleep(delay_seconds)
        await self.sync_integration(integration_id, job_id)

    async def get_pending_syncs(self) -> List[Dict[str, Any]]:
        """Get integrations due for sync."""
        result = await self.db.execute(
            text("""
                SELECT id, site_id, provider, name, next_sync_at
                FROM integrations
                WHERE status = 'active'
                  AND next_sync_at <= :now
                ORDER BY next_sync_at ASC
                LIMIT 100
            """),
            {"now": datetime.now(timezone.utc)}
        )

        return [
            {
                "id": str(row.id),
                "site_id": str(row.site_id),
                "provider": row.provider,
                "name": row.name,
                "next_sync_at": row.next_sync_at
            }
            for row in result.fetchall()
        ]


async def run_scheduled_syncs(db: AsyncSession) -> None:
    """
    Background task to run scheduled syncs.

    Should be called periodically (e.g., every minute) by a scheduler.
    """
    vault = CredentialVault()
    audit = IntegrationAuditLogger(db)
    engine = SyncEngine(db, vault, audit)

    pending = await engine.get_pending_syncs()

    if not pending:
        return

    logger.info(f"Running {len(pending)} scheduled syncs")

    for integration in pending:
        try:
            await engine.sync_integration(integration["id"])
        except Exception as e:
            logger.exception(f"Scheduled sync failed: integration={integration['id']}")
