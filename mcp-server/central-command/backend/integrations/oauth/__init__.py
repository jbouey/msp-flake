"""
OAuth Integration Module.

Provides secure OAuth 2.0 + PKCE connectors for:
- Google Workspace
- Okta
- Azure AD (Microsoft Entra ID)

Security:
- PKCE (Proof Key for Code Exchange) for all flows
- Single-use state tokens with site binding
- Automatic token refresh before expiry
- SecureCredentials wrapper for token storage
"""

from .base_connector import BaseOAuthConnector
from .google_connector import GoogleWorkspaceConnector
from .okta_connector import OktaConnector
from .azure_connector import AzureADConnector

__all__ = [
    "BaseOAuthConnector",
    "GoogleWorkspaceConnector",
    "OktaConnector",
    "AzureADConnector",
]
