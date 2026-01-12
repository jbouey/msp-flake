"""
Google Workspace Connector.

Collects compliance-relevant data from Google Workspace Admin API:
- Users (MFA status, last login, admin status)
- Groups (membership, settings)
- Organizational units
- Security settings

Required OAuth scopes (minimal read-only):
- admin.directory.user.readonly
- admin.directory.group.readonly
- admin.directory.orgunit.readonly
- admin.directory.domain.readonly

HIPAA Relevance:
- User MFA status (164.312(d) - Person or Entity Authentication)
- Admin accounts (164.308(a)(3) - Workforce Security)
- Login activity (164.312(b) - Audit Controls)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from .base_connector import (
    BaseOAuthConnector,
    OAuthConfig,
    IntegrationResource,
    ProviderAPIError,
)

logger = logging.getLogger(__name__)


# Google Workspace API configuration
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
ADMIN_API_BASE = "https://admin.googleapis.com/admin/directory/v1"

# Required scopes - minimal read-only access
GOOGLE_WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
]

# Resource limits
MAX_USERS = 5000
MAX_GROUPS = 5000

# MFA grace period (users without MFA for > 30 days are flagged)
MFA_GRACE_PERIOD_DAYS = 30


class GoogleWorkspaceConnector(BaseOAuthConnector):
    """
    Google Workspace Admin Directory connector.

    Collects users, groups, and org units with compliance checks.
    """

    PROVIDER = "google_workspace"
    AUTH_URL = GOOGLE_AUTH_URL
    TOKEN_URL = GOOGLE_TOKEN_URL
    USER_INFO_URL = GOOGLE_USER_INFO_URL
    SCOPES = GOOGLE_WORKSPACE_SCOPES

    def __init__(self, *args, customer_id: str = "my_customer", **kwargs):
        """
        Initialize Google Workspace connector.

        Args:
            customer_id: Google Workspace customer ID (default: "my_customer" for current domain)
            *args, **kwargs: Passed to BaseOAuthConnector
        """
        super().__init__(*args, **kwargs)
        self.customer_id = customer_id

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connection by fetching domain info.

        Returns:
            Dict with connection status and domain details
        """
        try:
            # Get domain list
            response = await self.api_request(
                "GET",
                f"{ADMIN_API_BASE}/customer/{self.customer_id}/domains"
            )

            domains = response.get("domains", [])
            primary_domain = next(
                (d for d in domains if d.get("isPrimary")),
                domains[0] if domains else {}
            )

            return {
                "status": "connected",
                "provider": self.PROVIDER,
                "domain": primary_domain.get("domainName"),
                "verified": primary_domain.get("verified", False),
                "domain_count": len(domains)
            }

        except ProviderAPIError as e:
            return {
                "status": "error",
                "provider": self.PROVIDER,
                "error": str(e),
                "error_code": e.status_code
            }

    async def collect_resources(self) -> List[IntegrationResource]:
        """
        Collect all resources from Google Workspace.

        Returns:
            List of IntegrationResource for users, groups, etc.
        """
        resources = []

        # Collect users
        users = await self._collect_users()
        resources.extend(users)

        # Collect groups
        groups = await self._collect_groups()
        resources.extend(groups)

        # Collect org units
        org_units = await self._collect_org_units()
        resources.extend(org_units)

        logger.info(
            f"Google Workspace collection complete: integration={self.integration_id} "
            f"users={len(users)} groups={len(groups)} org_units={len(org_units)}"
        )

        return resources

    async def _collect_users(self) -> List[IntegrationResource]:
        """Collect users with MFA and admin status."""
        users_data = await self.api_paginate(
            "GET",
            f"{ADMIN_API_BASE}/users",
            items_key="users",
            params={
                "customer": self.customer_id,
                "projection": "full",
                "maxResults": 500
            },
            max_items=MAX_USERS
        )

        resources = []
        now = datetime.utcnow()

        for user in users_data:
            # Extract MFA status from user data
            mfa_enabled = self._check_mfa_status(user)
            is_admin = user.get("isAdmin", False)
            is_delegated_admin = user.get("isDelegatedAdmin", False)
            is_suspended = user.get("suspended", False)
            last_login = user.get("lastLoginTime")

            # Calculate days since last login
            days_since_login = None
            if last_login:
                try:
                    last_login_dt = datetime.fromisoformat(last_login.replace("Z", "+00:00"))
                    days_since_login = (now - last_login_dt.replace(tzinfo=None)).days
                except (ValueError, TypeError):
                    pass

            # Compliance checks
            compliance_checks = {
                "mfa_enabled": {
                    "check": "MFA Enabled",
                    "status": "pass" if mfa_enabled else "fail",
                    "control": "164.312(d)",
                    "description": "Person or Entity Authentication"
                },
                "not_suspended": {
                    "check": "Account Active",
                    "status": "pass" if not is_suspended else "info",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": "Termination Procedures"
                }
            }

            # Admin accounts get additional scrutiny
            if is_admin or is_delegated_admin:
                compliance_checks["admin_mfa"] = {
                    "check": "Admin MFA Required",
                    "status": "pass" if mfa_enabled else "critical",
                    "control": "164.308(a)(4)(ii)(B)",
                    "description": "Access Establishment and Modification - Admin accounts must have MFA"
                }

            # Check for stale accounts (no login in 90 days)
            if days_since_login and days_since_login > 90 and not is_suspended:
                compliance_checks["stale_account"] = {
                    "check": "Account Activity",
                    "status": "warning",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": f"No login in {days_since_login} days"
                }

            # Determine risk level
            risk_level = self._calculate_user_risk(user, compliance_checks)

            resources.append(IntegrationResource(
                resource_type="user",
                resource_id=user.get("id"),
                name=user.get("primaryEmail"),
                raw_data={
                    "email": user.get("primaryEmail"),
                    "name": user.get("name", {}).get("fullName"),
                    "is_admin": is_admin,
                    "is_delegated_admin": is_delegated_admin,
                    "is_suspended": is_suspended,
                    "mfa_enabled": mfa_enabled,
                    "last_login": last_login,
                    "days_since_login": days_since_login,
                    "creation_time": user.get("creationTime"),
                    "org_unit_path": user.get("orgUnitPath")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    async def _collect_groups(self) -> List[IntegrationResource]:
        """Collect groups with membership counts."""
        groups_data = await self.api_paginate(
            "GET",
            f"{ADMIN_API_BASE}/groups",
            items_key="groups",
            params={
                "customer": self.customer_id,
                "maxResults": 200
            },
            max_items=MAX_GROUPS
        )

        resources = []

        for group in groups_data:
            group_email = group.get("email", "")

            # Compliance checks for groups
            compliance_checks = {
                "group_exists": {
                    "check": "Group Documented",
                    "status": "pass",
                    "control": "164.308(a)(3)",
                    "description": "Workforce Security - Group membership tracked"
                }
            }

            # Check for external members allowed
            if group.get("allowExternalMembers", False):
                compliance_checks["external_members"] = {
                    "check": "External Members",
                    "status": "warning",
                    "control": "164.308(a)(4)",
                    "description": "Group allows external members"
                }

            resources.append(IntegrationResource(
                resource_type="group",
                resource_id=group.get("id"),
                name=group_email,
                raw_data={
                    "email": group_email,
                    "name": group.get("name"),
                    "description": group.get("description"),
                    "member_count": group.get("directMembersCount", 0),
                    "admin_created": group.get("adminCreated", False),
                    "allow_external_members": group.get("allowExternalMembers", False)
                },
                compliance_checks=compliance_checks,
                risk_level="low"
            ))

        return resources

    async def _collect_org_units(self) -> List[IntegrationResource]:
        """Collect organizational units."""
        try:
            response = await self.api_request(
                "GET",
                f"{ADMIN_API_BASE}/customer/{self.customer_id}/orgunits",
                params={"type": "all"}
            )

            org_units = response.get("organizationUnits", [])

        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch org units: {e}")
            return []

        resources = []

        for ou in org_units:
            resources.append(IntegrationResource(
                resource_type="org_unit",
                resource_id=ou.get("orgUnitId"),
                name=ou.get("name"),
                raw_data={
                    "name": ou.get("name"),
                    "path": ou.get("orgUnitPath"),
                    "parent_path": ou.get("parentOrgUnitPath"),
                    "description": ou.get("description")
                },
                compliance_checks={
                    "ou_documented": {
                        "check": "OU Structure",
                        "status": "pass",
                        "control": "164.308(a)(3)",
                        "description": "Organizational structure documented"
                    }
                },
                risk_level="low"
            ))

        return resources

    def _check_mfa_status(self, user: Dict[str, Any]) -> bool:
        """
        Check if user has MFA enabled.

        Args:
            user: User data from Admin API

        Returns:
            True if MFA is enabled
        """
        # Check isEnrolledIn2Sv field (2-Step Verification)
        if user.get("isEnrolledIn2Sv", False):
            return True

        # Check for enforced 2SV
        if user.get("isEnforcedIn2Sv", False):
            return True

        return False

    def _calculate_user_risk(
        self,
        user: Dict[str, Any],
        compliance_checks: Dict[str, Any]
    ) -> str:
        """
        Calculate risk level for a user.

        Args:
            user: User data
            compliance_checks: Compliance check results

        Returns:
            Risk level: critical, high, medium, low
        """
        # Check for critical issues
        for check in compliance_checks.values():
            if check.get("status") == "critical":
                return "critical"

        # Admin without MFA is critical (handled above via admin_mfa check)
        is_admin = user.get("isAdmin") or user.get("isDelegatedAdmin")
        mfa_enabled = self._check_mfa_status(user)

        if is_admin and not mfa_enabled:
            return "critical"

        # Regular user without MFA is high risk
        if not mfa_enabled and not user.get("suspended"):
            return "high"

        # Check for warnings
        for check in compliance_checks.values():
            if check.get("status") == "warning":
                return "medium"

        return "low"

    async def get_user_mfa_report(self) -> Dict[str, Any]:
        """
        Generate MFA adoption report.

        Returns:
            Dict with MFA statistics
        """
        users_data = await self.api_paginate(
            "GET",
            f"{ADMIN_API_BASE}/users",
            items_key="users",
            params={
                "customer": self.customer_id,
                "projection": "full",
                "maxResults": 500
            },
            max_items=MAX_USERS
        )

        total = len(users_data)
        mfa_enabled = sum(1 for u in users_data if self._check_mfa_status(u))
        admins = [u for u in users_data if u.get("isAdmin") or u.get("isDelegatedAdmin")]
        admins_with_mfa = sum(1 for u in admins if self._check_mfa_status(u))
        suspended = sum(1 for u in users_data if u.get("suspended"))

        return {
            "total_users": total,
            "active_users": total - suspended,
            "mfa_enabled": mfa_enabled,
            "mfa_disabled": total - mfa_enabled - suspended,
            "mfa_percentage": round(mfa_enabled / (total - suspended) * 100, 1) if total > suspended else 0,
            "admin_count": len(admins),
            "admins_with_mfa": admins_with_mfa,
            "admin_mfa_percentage": round(admins_with_mfa / len(admins) * 100, 1) if admins else 100,
            "suspended_users": suspended
        }
