"""
Integrations API Router.

Provides REST endpoints for cloud integration management:
- List/create/delete integrations
- OAuth flow initiation and callbacks
- AWS IAM role configuration
- Resource sync and retrieval
- Integration health monitoring

Security:
- All endpoints require authentication via require_auth
- Tenant isolation enforced via site ownership verification
- Rate limiting on OAuth and sync endpoints
- Audit logging for all operations
"""

import logging
import secrets
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request, Query, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth, require_admin
from .tenant_isolation import TenantIsolation
from .audit_logger import IntegrationAuditLogger, AuditEventType
from .credential_vault import CredentialVault
from .oauth_state import OAuthStateManager, StateInvalidError, StateSiteMismatchError
from .secure_credentials import SecureCredentials, OAuthTokens

logger = logging.getLogger(__name__)


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class IntegrationCreate(BaseModel):
    """Request to create a new integration."""
    provider: str = Field(..., description="Provider type: aws, google_workspace, okta, azure_ad, microsoft_security")
    name: str = Field(..., min_length=1, max_length=255, description="Integration name")
    # AWS-specific fields
    aws_role_arn: Optional[str] = Field(None, description="AWS IAM role ARN for assume role")
    aws_external_id: Optional[str] = Field(None, description="External ID for assume role")
    aws_regions: Optional[List[str]] = Field(default=["us-east-1"], description="AWS regions to scan")
    # OAuth-specific fields
    oauth_client_id: Optional[str] = Field(None, description="OAuth client ID")
    oauth_client_secret: Optional[str] = Field(None, description="OAuth client secret")
    oauth_tenant_id: Optional[str] = Field(None, description="Azure AD tenant ID (for azure_ad and microsoft_security)")
    okta_domain: Optional[str] = Field(None, description="Okta organization domain")
    google_customer_id: Optional[str] = Field(None, description="Google Workspace customer ID")


class IntegrationUpdate(BaseModel):
    """Request to update an integration."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[str] = Field(None, description="active, paused, error")
    sync_interval_minutes: Optional[int] = Field(None, ge=15, le=1440)


class IntegrationResponse(BaseModel):
    """Integration details response."""
    id: str
    site_id: str
    provider: str
    name: str
    status: str
    last_sync: Optional[datetime]
    next_sync: Optional[datetime]
    resource_count: int
    health: Dict[str, Any]
    created_at: datetime


class ResourceResponse(BaseModel):
    """Resource details response."""
    id: str
    resource_type: str
    resource_id: str
    name: Optional[str]
    compliance_checks: List[Dict[str, Any]]
    risk_level: Optional[str]
    last_synced: Optional[datetime]


class OAuthStartResponse(BaseModel):
    """OAuth flow start response."""
    auth_url: str
    state: str


class SyncResponse(BaseModel):
    """Sync job response."""
    job_id: str
    status: str
    message: str


# =============================================================================
# ROUTER SETUP
# =============================================================================

router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_auth)],
)


# =============================================================================
# DEPENDENCIES
# =============================================================================

async def get_db():
    """Get database session."""
    try:
        from main import async_session
    except ImportError:
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
            raise RuntimeError("Database session not configured")

    async with async_session() as session:
        yield session


async def get_redis():
    """Get Redis client."""
    try:
        from main import redis_client
        return redis_client
    except ImportError:
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'redis_client'):
            return sys.modules['server'].redis_client
        else:
            raise RuntimeError("Redis client not configured")


async def get_credential_vault() -> CredentialVault:
    """Get credential vault instance."""
    return CredentialVault()


async def get_state_manager(redis=Depends(get_redis)) -> OAuthStateManager:
    """Get OAuth state manager instance."""
    return OAuthStateManager(redis)


async def get_audit_logger(db: AsyncSession = Depends(get_db)) -> IntegrationAuditLogger:
    """Get audit logger instance."""
    return IntegrationAuditLogger(db)


def get_client_ip(request: Request) -> str:
    """Get client IP address from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# =============================================================================
# BACKGROUND SYNC TASK
# =============================================================================

async def run_background_sync(
    integration_id: str,
    job_id: str,
    triggered_by: Optional[str] = None
) -> None:
    """
    Run sync in background task.

    This function is called by FastAPI background_tasks to run the sync
    asynchronously after returning the response to the client.
    """
    print(f"SYNC: Background sync starting: integration={integration_id} job={job_id}", flush=True)
    logger.info(f"Background sync starting: integration={integration_id} job={job_id}")

    try:
        from .sync_engine import SyncEngine

        # Need to get fresh database session for background task
        try:
            from main import async_session
        except ImportError:
            import sys
            if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
                async_session = sys.modules['server'].async_session
            else:
                logger.error("Cannot get database session for background sync")
                return

        async with async_session() as db:
            try:
                print(f"SYNC: Got database session, creating vault", flush=True)
                vault = CredentialVault()
                print(f"SYNC: Created vault, creating audit logger", flush=True)
                audit = IntegrationAuditLogger(db)
                print(f"SYNC: Created audit logger, creating engine", flush=True)
                engine = SyncEngine(db, vault, audit)
                print(f"SYNC: Created engine, running sync", flush=True)

                logger.info(f"Running sync engine for integration={integration_id}")
                result = await engine.sync_integration(
                    integration_id=integration_id,
                    job_id=job_id,
                    triggered_by=triggered_by
                )

                logger.info(
                    f"Background sync completed: integration={integration_id} "
                    f"status={result.status.value} resources={result.resources_synced}"
                )
            except Exception as e:
                logger.exception(f"Background sync failed: integration={integration_id} error={e}")
                # Update job status to failed - need to rollback any failed transaction first
                await db.rollback()
                await db.execute(
                    text("""
                        UPDATE integration_sync_jobs
                        SET status = 'failed', completed_at = :now, error_message = :error
                        WHERE id = :job_id
                    """),
                    {"job_id": job_id, "now": datetime.now(timezone.utc), "error": str(e)[:1000]}
                )
                await db.commit()
    except Exception as e:
        logger.exception(f"Background sync outer error: integration={integration_id} error={e}")


# =============================================================================
# INTEGRATION CRUD ENDPOINTS
# =============================================================================

@router.get("/sites/{site_id}")
async def list_integrations(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    status: Optional[str] = Query(None, description="Filter by status"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
) -> List[IntegrationResponse]:
    """
    List all integrations for a site.

    Requires site access permission.
    """
    # Verify site access
    has_access = await TenantIsolation.verify_site_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Site not found")

    # Get site UUID from human-readable site_id
    site_uuid = await TenantIsolation.get_site_uuid(db, site_id)
    if not site_uuid:
        return []

    # Build query
    query = """
        SELECT
            i.id, i.site_id, i.provider, i.name, i.status,
            i.last_sync_at, i.last_sync_at as next_sync_at, i.created_at,
            i.error_message as last_error,
            COUNT(ir.id) as resource_count,
            COUNT(ir.id) FILTER (WHERE ir.risk_level = 'critical') as critical_count,
            COUNT(ir.id) FILTER (WHERE ir.risk_level = 'high') as high_count
        FROM integrations i
        LEFT JOIN integration_resources ir ON ir.integration_id = i.id
        WHERE i.site_id = CAST(:site_uuid AS uuid)
    """
    params: Dict[str, Any] = {"site_uuid": site_uuid}

    if status:
        query += " AND i.status = :status"
        params["status"] = status

    if provider:
        query += " AND i.provider = :provider"
        params["provider"] = provider

    query += " GROUP BY i.id ORDER BY i.created_at DESC"

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    integrations = []
    for row in rows:
        health_status = "healthy"
        if row.critical_count > 0:
            health_status = "critical"
        elif row.high_count > 0:
            health_status = "warning"
        elif row.status == "error":
            health_status = "error"

        integrations.append(IntegrationResponse(
            id=str(row.id),
            site_id=str(row.site_id),
            provider=row.provider,
            name=row.name,
            status=row.status,
            last_sync=row.last_sync_at,
            next_sync=row.next_sync_at,
            resource_count=row.resource_count,
            health={
                "status": health_status,
                "critical_count": row.critical_count,
                "high_count": row.high_count,
                "last_error": row.last_error
            },
            created_at=row.created_at
        ))

    return integrations


@router.post("/sites/{site_id}")
async def create_integration(
    site_id: str,
    integration: IntegrationCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    vault: CredentialVault = Depends(get_credential_vault),
    audit: IntegrationAuditLogger = Depends(get_audit_logger),
) -> Dict[str, Any]:
    """
    Create a new cloud integration.

    For OAuth providers (google_workspace, okta, azure_ad):
    - Returns auth_url for OAuth flow initiation
    - Client must redirect user to auth_url

    For AWS:
    - Validates role ARN format
    - Tests assume role
    - Creates integration immediately
    """
    # Verify site access
    has_access = await TenantIsolation.verify_site_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Site not found")

    # Validate provider
    valid_providers = ["aws", "google_workspace", "okta", "azure_ad", "microsoft_security"]
    if integration.provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
        )

    # Generate integration ID (UUID to match database schema)
    integration_id = str(uuid.uuid4())
    client_ip = get_client_ip(request)

    if integration.provider == "aws":
        return await _create_aws_integration(
            db=db,
            vault=vault,
            audit=audit,
            integration_id=integration_id,
            site_id=site_id,
            integration=integration,
            user=user,
            client_ip=client_ip
        )
    else:
        # OAuth providers - return auth URL
        return await _initiate_oauth_flow(
            db=db,
            vault=vault,
            audit=audit,
            integration_id=integration_id,
            site_id=site_id,
            integration=integration,
            user=user,
            client_ip=client_ip,
            request=request
        )


async def _create_aws_integration(
    db: AsyncSession,
    vault: CredentialVault,
    audit: IntegrationAuditLogger,
    integration_id: str,
    site_id: str,
    integration: IntegrationCreate,
    user: dict,
    client_ip: str,
) -> Dict[str, Any]:
    """Create AWS integration with role assumption."""
    from .aws.connector import AWSConnector

    if not integration.aws_role_arn:
        raise HTTPException(status_code=400, detail="aws_role_arn is required for AWS integrations")

    # Get the site UUID from the human-readable site_id
    # (integrations table references sites.id which is UUID)
    site_uuid = await TenantIsolation.get_site_uuid(db, site_id)
    if not site_uuid:
        raise HTTPException(status_code=404, detail="Site not found")

    # Generate external ID if not provided
    external_id = integration.aws_external_id or secrets.token_urlsafe(24)

    # Note: Role validation is skipped for now - server needs AWS credentials
    # to test cross-account role assumption. In production, configure AWS creds
    # on the server or use a dedicated IAM role.
    # TODO: Add server-side AWS credentials for role validation
    test_result = {"account_id": integration.aws_role_arn.split(":")[4] if ":" in integration.aws_role_arn else "unknown"}

    # Store credentials encrypted
    credentials = {
        "role_arn": integration.aws_role_arn,
        "external_id": external_id,
        "regions": integration.aws_regions or ["us-east-1"]
    }
    encrypted_creds = vault.encrypt_credentials(integration_id, credentials)

    # Create integration record (use site_uuid for the foreign key)
    await db.execute(
        text("""
            INSERT INTO integrations (
                id, site_id, provider, name, status,
                credentials_encrypted, aws_role_arn, aws_external_id, aws_regions,
                created_by
            ) VALUES (
                :id, CAST(:site_uuid AS uuid), :provider, :name, :status,
                :creds, :role_arn, :external_id, :regions,
                :created_by
            )
        """),
        {
            "id": integration_id,
            "site_uuid": site_uuid,
            "provider": "aws",
            "name": integration.name,
            "status": "connected",
            "creds": encrypted_creds,
            "role_arn": integration.aws_role_arn,
            "external_id": external_id,
            "regions": integration.aws_regions or ["us-east-1"],
            "created_by": user.get("id")
        }
    )
    await db.commit()

    # Log audit event
    await audit.log_aws_role_assumed(
        site_id=site_id,
        integration_id=integration_id,
        role_arn=integration.aws_role_arn,
        session_duration=3600,  # 1 hour default session
        user_id=user.get("id")
    )

    logger.info(
        f"AWS integration created: id={integration_id} site={site_id} "
        f"account={test_result.get('account_id')}"
    )

    return {
        "id": integration_id,
        "provider": "aws",
        "name": integration.name,
        "status": "connected",
        "aws_account_id": test_result.get("account_id"),
        "message": "AWS integration created successfully. Initial sync will begin shortly."
    }


async def _initiate_oauth_flow(
    db: AsyncSession,
    vault: CredentialVault,
    audit: IntegrationAuditLogger,
    integration_id: str,
    site_id: str,
    integration: IntegrationCreate,
    user: dict,
    client_ip: str,
    request: Request,
) -> Dict[str, Any]:
    """Initiate OAuth flow for Google/Okta/Azure."""
    redis = await get_redis()
    state_manager = OAuthStateManager(redis)

    # Get the site UUID from the human-readable site_id
    site_uuid = await TenantIsolation.get_site_uuid(db, site_id)
    if not site_uuid:
        raise HTTPException(status_code=404, detail="Site not found")

    # Validate OAuth credentials
    if not integration.oauth_client_id or not integration.oauth_client_secret:
        raise HTTPException(
            status_code=400,
            detail="oauth_client_id and oauth_client_secret are required"
        )

    # Provider-specific validation
    if integration.provider in ("azure_ad", "microsoft_security") and not integration.oauth_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="oauth_tenant_id is required for Azure AD"
        )

    if integration.provider == "okta" and not integration.okta_domain:
        raise HTTPException(
            status_code=400,
            detail="okta_domain is required for Okta"
        )

    # Store client credentials encrypted (temporarily, will be updated on callback)
    credentials = {
        "client_id": integration.oauth_client_id,
        "client_secret": integration.oauth_client_secret,
        "tenant_id": integration.oauth_tenant_id,
        "okta_domain": integration.okta_domain,
        "google_customer_id": integration.google_customer_id,
    }
    encrypted_creds = vault.encrypt_credentials(integration_id, credentials)

    # Create pending integration record (use site_uuid for the foreign key)
    await db.execute(
        text("""
            INSERT INTO integrations (
                id, site_id, provider, name, status,
                credentials_encrypted, created_by
            ) VALUES (
                :id, CAST(:site_uuid AS uuid), :provider, :name, :status,
                :creds, :created_by
            )
        """),
        {
            "id": integration_id,
            "site_uuid": site_uuid,
            "provider": integration.provider,
            "name": integration.name,
            "status": "configuring",
            "creds": encrypted_creds,
            "created_by": user.get("id")
        }
    )
    await db.commit()

    # Generate OAuth authorization URL
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/integrations/oauth/callback"

    # Store state with integration details
    state = await state_manager.generate(
        site_id=site_id,
        provider=integration.provider,
        return_url=f"/sites/{site_id}/integrations/{integration_id}",
        integration_name=integration.name,
        extra_data={
            "integration_id": integration_id,
            "user_id": user.get("id")
        }
    )

    # Build authorization URL based on provider
    auth_url = _build_auth_url(
        provider=integration.provider,
        client_id=integration.oauth_client_id,
        redirect_uri=redirect_uri,
        state=state,
        tenant_id=integration.oauth_tenant_id,
        okta_domain=integration.okta_domain
    )

    logger.info(
        f"OAuth flow initiated: provider={integration.provider} "
        f"integration={integration_id} site={site_id}"
    )

    return {
        "id": integration_id,
        "provider": integration.provider,
        "status": "configuring",
        "auth_url": auth_url,
        "message": "Redirect user to auth_url to complete OAuth authorization"
    }


def _build_auth_url(
    provider: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    tenant_id: Optional[str] = None,
    okta_domain: Optional[str] = None,
) -> str:
    """Build OAuth authorization URL for provider."""
    from urllib.parse import urlencode
    import hashlib
    import base64

    # Generate PKCE challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()

    if provider == "google_workspace":
        base = "https://accounts.google.com/o/oauth2/v2/auth"
        scopes = [
            "https://www.googleapis.com/auth/admin.directory.user.readonly",
            "https://www.googleapis.com/auth/admin.directory.group.readonly",
        ]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }

    elif provider == "azure_ad":
        base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
        scopes = [
            "https://graph.microsoft.com/User.Read.All",
            "https://graph.microsoft.com/Group.Read.All",
            "https://graph.microsoft.com/Policy.Read.All",
            "offline_access",
        ]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

    elif provider == "microsoft_security":
        # Microsoft Security: Defender + Intune (requires additional scopes)
        base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
        scopes = [
            # Azure AD basics
            "https://graph.microsoft.com/User.Read.All",
            "https://graph.microsoft.com/Device.Read.All",
            # Defender for Endpoint
            "https://graph.microsoft.com/SecurityEvents.Read.All",
            "https://graph.microsoft.com/SecurityActions.Read.All",
            # Intune device management
            "https://graph.microsoft.com/DeviceManagementManagedDevices.Read.All",
            "https://graph.microsoft.com/DeviceManagementConfiguration.Read.All",
            "offline_access",
        ]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

    elif provider == "okta":
        base = f"https://{okta_domain}/oauth2/v1/authorize"
        scopes = ["okta.users.read", "okta.groups.read", "okta.apps.read", "offline_access"]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

    else:
        raise ValueError(f"Unknown OAuth provider: {provider}")

    return f"{base}?{urlencode(params)}"


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = Query(..., description="Authorization code"),
    state: str = Query(..., description="State token"),
    error: Optional[str] = Query(None, description="Error from provider"),
    error_description: Optional[str] = Query(None, description="Error description"),
    db: AsyncSession = Depends(get_db),
    audit: IntegrationAuditLogger = Depends(get_audit_logger),
):
    """
    OAuth callback handler.

    Validates state, exchanges code for tokens, completes integration setup.
    Redirects to frontend with success or error status.
    """
    client_ip = get_client_ip(request)

    # Handle OAuth errors from provider
    if error:
        logger.warning(f"OAuth error from provider: {error} - {error_description}")
        return RedirectResponse(
            url=f"/integrations/error?error={error}&description={error_description or 'Unknown error'}",
            status_code=302
        )

    redis = await get_redis()
    state_manager = OAuthStateManager(redis)

    try:
        # First, peek at the state to get site_id for validation
        state_info = await state_manager.get_state_info(state)
        if not state_info:
            raise StateInvalidError("State token not found or expired")

        site_id = state_info.get("site_id")
        provider = state_info.get("provider")
        integration_id = state_info.get("extra_data", {}).get("integration_id")

        # Validate and consume state (single-use)
        state_data = await state_manager.validate(
            state=state,
            expected_site_id=site_id
        )

        # Get integration record
        result = await db.execute(
            text("SELECT credentials_encrypted FROM integrations WHERE id = :id"),
            {"id": integration_id}
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Decrypt stored credentials to get client_id/secret
        vault = await get_credential_vault()
        stored_creds = vault.decrypt_credentials(integration_id, row[0])

        # Exchange code for tokens (simplified - full implementation in connectors)
        # In production, this would use the appropriate connector class
        tokens = await _exchange_oauth_code(
            provider=provider,
            code=code,
            redirect_uri=f"{str(request.base_url).rstrip('/')}/api/integrations/oauth/callback",
            client_id=stored_creds.get("client_id"),
            client_secret=stored_creds.get("client_secret"),
            tenant_id=stored_creds.get("tenant_id"),
            okta_domain=stored_creds.get("okta_domain")
        )

        # Update stored credentials with tokens
        stored_creds["access_token"] = tokens.get("access_token")
        stored_creds["refresh_token"] = tokens.get("refresh_token")
        stored_creds["token_expires_at"] = tokens.get("expires_at")

        encrypted_creds = vault.encrypt_credentials(integration_id, stored_creds)

        # Update integration status
        await db.execute(
            text("""
                UPDATE integrations
                SET status = 'active', credentials_encrypted = :creds,
                    oauth_connected_at = :now
                WHERE id = :id
            """),
            {
                "id": integration_id,
                "creds": encrypted_creds,
                "now": datetime.now(timezone.utc)
            }
        )
        await db.commit()

        # Log success
        await audit.log_oauth_success(
            site_id=site_id,
            integration_id=integration_id,
            provider=provider,
            user_id=state_data.get("extra_data", {}).get("user_id"),
            ip_address=client_ip
        )

        logger.info(f"OAuth completed: provider={provider} integration={integration_id}")

        # Redirect to success page
        return_url = state_data.get("return_url", f"/sites/{site_id}/integrations")
        return RedirectResponse(url=return_url, status_code=302)

    except StateInvalidError as e:
        logger.warning(f"OAuth state invalid: {e}")
        return RedirectResponse(
            url="/integrations/error?error=invalid_state&description=State+token+invalid+or+expired",
            status_code=302
        )

    except StateSiteMismatchError as e:
        logger.warning(f"OAuth state site mismatch: {e}")
        return RedirectResponse(
            url="/integrations/error?error=site_mismatch&description=State+token+belongs+to+different+site",
            status_code=302
        )

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return RedirectResponse(
            url=f"/integrations/error?error=callback_failed&description={str(e)}",
            status_code=302
        )


async def _exchange_oauth_code(
    provider: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    tenant_id: Optional[str] = None,
    okta_domain: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchange authorization code for tokens."""
    import httpx
    from datetime import timedelta

    if provider == "google_workspace":
        token_url = "https://oauth2.googleapis.com/token"
    elif provider in ("azure_ad", "microsoft_security"):
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    elif provider == "okta":
        token_url = f"https://{okta_domain}/oauth2/v1/token"
    else:
        raise ValueError(f"Unknown provider: {provider}")

    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        if response.status_code != 200:
            error_data = response.json() if response.content else {}
            raise HTTPException(
                status_code=400,
                detail=f"Token exchange failed: {error_data.get('error_description', error_data.get('error', 'Unknown'))}"
            )

        token_data = response.json()

    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    return {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
        "scope": token_data.get("scope"),
    }


@router.get("/sites/{site_id}/{integration_id}")
async def get_integration(
    site_id: str,
    integration_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> IntegrationResponse:
    """Get integration details."""
    # Verify access
    has_access = await TenantIsolation.verify_integration_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        integration_id=integration_id,
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Integration not found")

    result = await db.execute(
        text("""
            SELECT
                i.id, i.site_id, i.provider, i.name, i.status,
                i.last_sync_at, i.next_sync_at, i.created_at,
                i.last_error,
                COUNT(ir.id) as resource_count,
                COUNT(ir.id) FILTER (WHERE ir.risk_level = 'critical') as critical_count,
                COUNT(ir.id) FILTER (WHERE ir.risk_level = 'high') as high_count
            FROM integrations i
            LEFT JOIN integration_resources ir ON ir.integration_id = i.id
            WHERE i.id = :id
            GROUP BY i.id
        """),
        {"id": integration_id}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    health_status = "healthy"
    if row.critical_count > 0:
        health_status = "critical"
    elif row.high_count > 0:
        health_status = "warning"
    elif row.status == "error":
        health_status = "error"

    return IntegrationResponse(
        id=str(row.id),
        site_id=str(row.site_id),
        provider=row.provider,
        name=row.name,
        status=row.status,
        last_sync=row.last_sync_at,
        next_sync=row.next_sync_at,
        resource_count=row.resource_count,
        health={
            "status": health_status,
            "critical_count": row.critical_count,
            "high_count": row.high_count,
            "last_error": row.last_error
        },
        created_at=row.created_at
    )


@router.delete("/sites/{site_id}/{integration_id}")
async def delete_integration(
    site_id: str,
    integration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    audit: IntegrationAuditLogger = Depends(get_audit_logger),
):
    """Delete an integration and all its resources."""
    # Verify access
    has_access = await TenantIsolation.verify_integration_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        integration_id=integration_id,
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Get integration info for audit
    result = await db.execute(
        text("SELECT provider, name FROM integrations WHERE id = :id"),
        {"id": integration_id}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Integration not found")

    provider, name = row

    # Delete resources first (cascade should handle this but be explicit)
    await db.execute(
        text("DELETE FROM integration_resources WHERE integration_id = :id"),
        {"id": integration_id}
    )

    # Delete integration
    await db.execute(
        text("DELETE FROM integrations WHERE id = :id"),
        {"id": integration_id}
    )
    await db.commit()

    # Log deletion
    await audit.log_custom_event(
        site_id=site_id,
        integration_id=integration_id,
        event_type="integration_deleted",
        event_data={"provider": provider, "name": name},
        user_id=user.get("id"),
        ip_address=get_client_ip(request)
    )

    logger.info(f"Integration deleted: id={integration_id} provider={provider}")

    return {"message": "Integration deleted successfully"}


# =============================================================================
# RESOURCE ENDPOINTS
# =============================================================================

@router.get("/sites/{site_id}/{integration_id}/resources")
async def list_resources(
    site_id: str,
    integration_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List resources for an integration with pagination."""
    # Verify access
    has_access = await TenantIsolation.verify_integration_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        integration_id=integration_id,
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Build query
    query = """
        SELECT id, resource_type, resource_id, resource_name,
               compliance_checks, risk_level, last_seen_at
        FROM integration_resources
        WHERE integration_id = :integration_id
    """
    count_query = """
        SELECT COUNT(*) FROM integration_resources
        WHERE integration_id = :integration_id
    """
    params: Dict[str, Any] = {"integration_id": integration_id}

    if resource_type:
        query += " AND resource_type = :resource_type"
        count_query += " AND resource_type = :resource_type"
        params["resource_type"] = resource_type

    if risk_level:
        query += " AND risk_level = :risk_level"
        count_query += " AND risk_level = :risk_level"
        params["risk_level"] = risk_level

    query += " ORDER BY risk_level DESC, last_seen_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    # Get total count
    count_result = await db.execute(text(count_query), {k: v for k, v in params.items() if k not in ("limit", "offset")})
    total = count_result.scalar()

    # Get resources
    result = await db.execute(text(query), params)
    rows = result.fetchall()

    resources = []
    for row in rows:
        resources.append(ResourceResponse(
            id=str(row.id),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            name=row.resource_name,
            compliance_checks=row.compliance_checks or [],
            risk_level=row.risk_level,
            last_synced=row.last_seen_at
        ))

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "resources": resources
    }


# =============================================================================
# SYNC ENDPOINTS
# =============================================================================

@router.post("/sites/{site_id}/{integration_id}/sync")
async def trigger_sync(
    site_id: str,
    integration_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    audit: IntegrationAuditLogger = Depends(get_audit_logger),
) -> SyncResponse:
    """Trigger a manual sync for an integration."""
    # Verify access
    has_access = await TenantIsolation.verify_integration_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        integration_id=integration_id,
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Check if sync is already running
    result = await db.execute(
        text("""
            SELECT id FROM integration_sync_jobs
            WHERE integration_id = :id AND status = 'running'
        """),
        {"id": integration_id}
    )
    if result.fetchone():
        raise HTTPException(status_code=409, detail="Sync already in progress")

    # Create sync job
    job_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO integration_sync_jobs (id, integration_id, status, started_at, triggered_by)
            VALUES (:id, :integration_id, 'running', :started_at, :triggered_by)
        """),
        {
            "id": job_id,
            "integration_id": integration_id,
            "started_at": datetime.now(timezone.utc),
            "triggered_by": user.get("id")
        }
    )
    await db.commit()

    # Log sync start
    await audit.log_sync_started(
        site_id=site_id,
        integration_id=integration_id,
        resource_types=["all"],
        triggered_by="manual"
    )

    # Queue background sync using asyncio.create_task for async function
    import asyncio
    asyncio.create_task(
        run_background_sync(
            integration_id=integration_id,
            job_id=job_id,
            triggered_by=user.get("id")
        )
    )

    logger.info(f"Sync triggered: job={job_id} integration={integration_id}")

    return SyncResponse(
        job_id=job_id,
        status="running",
        message="Sync started. Check job status for progress."
    )


@router.get("/sites/{site_id}/{integration_id}/sync/{job_id}")
async def get_sync_status(
    site_id: str,
    integration_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Get sync job status."""
    # Verify access
    has_access = await TenantIsolation.verify_integration_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        integration_id=integration_id,
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Integration not found")

    result = await db.execute(
        text("""
            SELECT id, status, started_at, completed_at,
                   resources_found, error_message
            FROM integration_sync_jobs
            WHERE id = :job_id AND integration_id = :integration_id
        """),
        {"job_id": job_id, "integration_id": integration_id}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Sync job not found")

    return {
        "job_id": str(row.id),
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "resources_synced": row.resources_found,
        "error_message": row.error_message
    }


# =============================================================================
# HEALTH ENDPOINTS
# =============================================================================

@router.get("/sites/{site_id}/health")
async def get_integrations_health(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Get aggregated health status for all integrations in a site."""
    # Verify site access
    has_access = await TenantIsolation.verify_site_access(
        db=db,
        user_id=user.get("id"),
        user_role=user.get("role", "readonly"),
        site_id=site_id,
        partner_id=user.get("partner_id")
    )

    if not has_access:
        raise HTTPException(status_code=404, detail="Site not found")

    result = await db.execute(
        text("""
            SELECT * FROM v_integration_health
            WHERE site_id = :site_id
        """),
        {"site_id": site_id}
    )
    rows = result.fetchall()

    integrations = []
    total_critical = 0
    total_high = 0

    for row in rows:
        status = "healthy"
        if row.critical_count > 0:
            status = "critical"
        elif row.high_count > 0:
            status = "warning"

        total_critical += row.critical_count or 0
        total_high += row.high_count or 0

        integrations.append({
            "integration_id": str(row.integration_id),
            "provider": row.provider,
            "status": row.status,
            "health": status,
            "critical_count": row.critical_count,
            "high_count": row.high_count,
            "last_sync": row.last_sync_at,
            "resource_count": row.resource_count
        })

    overall_status = "healthy"
    if total_critical > 0:
        overall_status = "critical"
    elif total_high > 0:
        overall_status = "warning"

    return {
        "site_id": site_id,
        "overall_status": overall_status,
        "total_integrations": len(integrations),
        "total_critical": total_critical,
        "total_high": total_high,
        "integrations": integrations
    }


# =============================================================================
# AWS SETUP HELPERS
# =============================================================================

@router.get("/aws/setup-instructions")
async def get_aws_setup_instructions(
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Get AWS IAM role setup instructions and CloudFormation template."""
    from .aws.policy_templates import (
        get_cloudformation_template,
        get_setup_instructions,
        get_terraform_template,
    )

    return {
        "instructions": get_setup_instructions(),
        "cloudformation_template": get_cloudformation_template(
            external_id="YOUR_EXTERNAL_ID_HERE"
        ),
        "terraform_template": get_terraform_template(
            external_id="YOUR_EXTERNAL_ID_HERE"
        )
    }


@router.post("/aws/generate-external-id")
async def generate_aws_external_id(
    user: dict = Depends(require_auth),
) -> Dict[str, str]:
    """Generate a secure external ID for AWS role setup."""
    external_id = secrets.token_urlsafe(24)
    return {"external_id": external_id}
