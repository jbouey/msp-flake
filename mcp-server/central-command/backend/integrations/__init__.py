"""
Cloud Integration System for OsirisCare.

Secure connectors for AWS, Google Workspace, Okta, and Azure AD
to collect compliance evidence for healthcare SMBs.

Security Features:
- Per-integration HKDF key derivation (no shared encryption keys)
- Single-use OAuth state tokens with 10-minute TTL
- Tenant isolation with ownership verification
- SecureCredentials wrapper prevents log exposure
- Resource limits (5000 per type, 5-minute sync timeout)

HIPAA Controls:
- 164.312(a)(1) - Access Control (tenant isolation)
- 164.312(b) - Audit Controls (comprehensive logging)
- 164.312(c)(1) - Integrity (signed evidence bundles)
- 164.312(d) - Person Authentication (OAuth/STS)
"""

from .secure_credentials import SecureCredentials, OAuthTokens, AWSCredentials
from .credential_vault import CredentialVault
from .oauth_state import OAuthStateManager, StateInvalidError, StateSiteMismatchError
from .tenant_isolation import (
    require_site_access,
    require_integration_access,
    require_resource_access,
    TenantIsolation,
    TenantContext,
)
from .audit_logger import IntegrationAuditLogger, AuditEventType, AuditCategory
from .sync_engine import SyncEngine, SyncResult, SyncStatus
from .api import router

__all__ = [
    # Security components
    "SecureCredentials",
    "OAuthTokens",
    "AWSCredentials",
    "CredentialVault",
    "OAuthStateManager",
    "StateInvalidError",
    "StateSiteMismatchError",
    # Tenant isolation
    "require_site_access",
    "require_integration_access",
    "require_resource_access",
    "TenantIsolation",
    "TenantContext",
    # Audit logging
    "IntegrationAuditLogger",
    "AuditEventType",
    "AuditCategory",
    # Sync engine
    "SyncEngine",
    "SyncResult",
    "SyncStatus",
    # API router
    "router",
]
