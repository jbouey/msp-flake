"""
Azure AD (Microsoft Entra ID) Connector.

Collects compliance-relevant data from Microsoft Graph API:
- Users (MFA status via authentication methods, last sign-in)
- Groups (membership, type)
- Conditional Access Policies
- Directory roles

Required OAuth scopes (minimal read-only):
- User.Read.All
- Group.Read.All
- Policy.Read.All
- Directory.Read.All
- AuditLog.Read.All

HIPAA Relevance:
- User MFA methods (164.312(d) - Person or Entity Authentication)
- Conditional Access (164.312(a)(1) - Access Control)
- Directory roles (164.308(a)(3) - Workforce Security)
- Sign-in logs (164.312(b) - Audit Controls)
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


# Azure AD / Microsoft Graph configuration
AZURE_AUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
AZURE_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_API_BETA = "https://graph.microsoft.com/beta"

# Required scopes
AZURE_AD_SCOPES = [
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/Group.Read.All",
    "https://graph.microsoft.com/Policy.Read.All",
    "https://graph.microsoft.com/Directory.Read.All",
    "https://graph.microsoft.com/AuditLog.Read.All",
]

# Resource limits
MAX_USERS = 5000
MAX_GROUPS = 5000


class AzureADConnector(BaseOAuthConnector):
    """
    Azure AD (Microsoft Entra ID) connector via Microsoft Graph API.

    Collects users, groups, conditional access policies with compliance checks.
    """

    PROVIDER = "azure_ad"
    SCOPES = AZURE_AD_SCOPES

    def __init__(self, *args, tenant_id: str = "common", **kwargs):
        """
        Initialize Azure AD connector.

        Args:
            tenant_id: Azure AD tenant ID (GUID) or "common" for multi-tenant
            *args, **kwargs: Passed to BaseOAuthConnector
        """
        super().__init__(*args, **kwargs)
        self.tenant_id = tenant_id

    @property
    def AUTH_URL(self) -> str:
        return AZURE_AUTH_URL.format(tenant_id=self.tenant_id)

    @property
    def TOKEN_URL(self) -> str:
        return AZURE_TOKEN_URL.format(tenant_id=self.tenant_id)

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connection by fetching organization info.

        Returns:
            Dict with connection status and org details
        """
        try:
            response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/organization"
            )

            orgs = response.get("value", [])
            org = orgs[0] if orgs else {}

            return {
                "status": "connected",
                "provider": self.PROVIDER,
                "tenant_id": org.get("id"),
                "display_name": org.get("displayName"),
                "verified_domains": [
                    d.get("name") for d in org.get("verifiedDomains", [])
                    if d.get("isDefault")
                ]
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
        Collect all resources from Azure AD.

        Returns:
            List of IntegrationResource for users, groups, policies
        """
        resources = []

        # Collect users with auth methods
        users = await self._collect_users()
        resources.extend(users)

        # Collect groups
        groups = await self._collect_groups()
        resources.extend(groups)

        # Collect conditional access policies
        policies = await self._collect_conditional_access_policies()
        resources.extend(policies)

        # Collect directory roles
        roles = await self._collect_directory_roles()
        resources.extend(roles)

        logger.info(
            f"Azure AD collection complete: integration={self.integration_id} "
            f"users={len(users)} groups={len(groups)} policies={len(policies)} roles={len(roles)}"
        )

        return resources

    async def _collect_users(self) -> List[IntegrationResource]:
        """Collect users with authentication methods."""
        # Get users with sign-in activity
        users_data = await self.api_paginate(
            "GET",
            f"{GRAPH_API_BASE}/users",
            items_key="value",
            params={
                "$select": "id,displayName,userPrincipalName,mail,accountEnabled,"
                          "createdDateTime,signInActivity,userType",
                "$top": 999
            },
            page_token_param="$skiptoken",
            next_page_key="@odata.nextLink",
            max_items=MAX_USERS
        )

        resources = []
        now = datetime.utcnow()

        for user in users_data:
            user_id = user.get("id")
            upn = user.get("userPrincipalName", "")
            is_enabled = user.get("accountEnabled", True)
            user_type = user.get("userType", "Member")

            # Get authentication methods for MFA status
            auth_methods = await self._get_user_auth_methods(user_id)
            has_mfa = self._check_mfa_methods(auth_methods)

            # Parse last sign-in
            sign_in_activity = user.get("signInActivity", {})
            last_sign_in = sign_in_activity.get("lastSignInDateTime")
            days_since_login = None

            if last_sign_in:
                try:
                    last_sign_in_dt = datetime.fromisoformat(last_sign_in.replace("Z", "+00:00"))
                    days_since_login = (now - last_sign_in_dt.replace(tzinfo=None)).days
                except (ValueError, TypeError):
                    pass

            # Compliance checks
            compliance_checks = {
                "mfa_configured": {
                    "check": "MFA Configured",
                    "status": "pass" if has_mfa else "fail",
                    "control": "164.312(d)",
                    "description": "Person or Entity Authentication",
                    "details": f"Auth methods: {', '.join(auth_methods) if auth_methods else 'password only'}"
                },
                "account_enabled": {
                    "check": "Account Status",
                    "status": "pass" if is_enabled else "info",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": "Account enabled" if is_enabled else "Account disabled"
                }
            }

            # Guest user check
            if user_type == "Guest":
                compliance_checks["guest_user"] = {
                    "check": "Guest User",
                    "status": "warning",
                    "control": "164.308(a)(4)",
                    "description": "External guest user - verify business need"
                }

            # Stale account check
            if days_since_login and days_since_login > 90 and is_enabled:
                compliance_checks["stale_account"] = {
                    "check": "Account Activity",
                    "status": "warning",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": f"No sign-in in {days_since_login} days"
                }

            # Determine risk
            risk_level = self._calculate_user_risk(user, has_mfa, compliance_checks)

            resources.append(IntegrationResource(
                resource_type="user",
                resource_id=user_id,
                name=upn,
                raw_data={
                    "user_principal_name": upn,
                    "display_name": user.get("displayName"),
                    "email": user.get("mail"),
                    "account_enabled": is_enabled,
                    "user_type": user_type,
                    "mfa_configured": has_mfa,
                    "auth_methods": auth_methods,
                    "last_sign_in": last_sign_in,
                    "days_since_login": days_since_login,
                    "created_datetime": user.get("createdDateTime")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    async def _get_user_auth_methods(self, user_id: str) -> List[str]:
        """Get authentication methods configured for a user."""
        try:
            response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/users/{user_id}/authentication/methods"
            )

            methods = []
            for method in response.get("value", []):
                method_type = method.get("@odata.type", "")

                if "microsoftAuthenticatorAuthenticationMethod" in method_type:
                    methods.append("microsoft_authenticator")
                elif "phoneAuthenticationMethod" in method_type:
                    methods.append("phone")
                elif "fido2AuthenticationMethod" in method_type:
                    methods.append("fido2")
                elif "windowsHelloForBusinessAuthenticationMethod" in method_type:
                    methods.append("windows_hello")
                elif "softwareOathAuthenticationMethod" in method_type:
                    methods.append("software_oath")
                elif "temporaryAccessPassAuthenticationMethod" in method_type:
                    methods.append("temporary_access_pass")
                elif "emailAuthenticationMethod" in method_type:
                    methods.append("email")
                elif "passwordAuthenticationMethod" in method_type:
                    # Don't include password as it's always there
                    pass

            return methods

        except ProviderAPIError as e:
            logger.debug(f"Failed to get auth methods for user {user_id}: {e}")
            return []

    def _check_mfa_methods(self, auth_methods: List[str]) -> bool:
        """Check if user has MFA-capable methods configured."""
        mfa_methods = {
            "microsoft_authenticator",
            "phone",
            "fido2",
            "windows_hello",
            "software_oath"
        }
        return bool(set(auth_methods) & mfa_methods)

    async def _collect_groups(self) -> List[IntegrationResource]:
        """Collect security and M365 groups."""
        groups_data = await self.api_paginate(
            "GET",
            f"{GRAPH_API_BASE}/groups",
            items_key="value",
            params={
                "$select": "id,displayName,description,groupTypes,securityEnabled,"
                          "mailEnabled,membershipRule,membershipRuleProcessingState",
                "$top": 999
            },
            page_token_param="$skiptoken",
            next_page_key="@odata.nextLink",
            max_items=MAX_GROUPS
        )

        resources = []

        for group in groups_data:
            group_id = group.get("id")
            display_name = group.get("displayName", "")
            group_types = group.get("groupTypes", [])
            security_enabled = group.get("securityEnabled", False)
            mail_enabled = group.get("mailEnabled", False)

            # Determine group type
            if "DynamicMembership" in group_types:
                group_type = "dynamic"
            elif security_enabled and not mail_enabled:
                group_type = "security"
            elif mail_enabled and "Unified" in group_types:
                group_type = "microsoft_365"
            elif mail_enabled:
                group_type = "distribution"
            else:
                group_type = "other"

            # Compliance checks
            compliance_checks = {
                "group_documented": {
                    "check": "Group Tracked",
                    "status": "pass",
                    "control": "164.308(a)(3)",
                    "description": f"Group type: {group_type}"
                }
            }

            # Dynamic groups are good for automated management
            if group_type == "dynamic":
                compliance_checks["dynamic_membership"] = {
                    "check": "Dynamic Membership",
                    "status": "pass",
                    "control": "164.308(a)(3)(ii)(A)",
                    "description": "Automated membership based on rules"
                }

            resources.append(IntegrationResource(
                resource_type="group",
                resource_id=group_id,
                name=display_name,
                raw_data={
                    "display_name": display_name,
                    "description": group.get("description"),
                    "group_type": group_type,
                    "group_types": group_types,
                    "security_enabled": security_enabled,
                    "mail_enabled": mail_enabled,
                    "membership_rule": group.get("membershipRule"),
                    "membership_rule_state": group.get("membershipRuleProcessingState")
                },
                compliance_checks=compliance_checks,
                risk_level="low"
            ))

        return resources

    async def _collect_conditional_access_policies(self) -> List[IntegrationResource]:
        """Collect Conditional Access policies."""
        try:
            response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/identity/conditionalAccess/policies"
            )

            policies = response.get("value", [])

        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch conditional access policies: {e}")
            return []

        resources = []

        for policy in policies:
            policy_id = policy.get("id")
            display_name = policy.get("displayName", "")
            state = policy.get("state", "disabled")
            conditions = policy.get("conditions", {})
            grant_controls = policy.get("grantControls", {})

            # Analyze policy
            compliance_checks = self._analyze_conditional_access_policy(
                policy, conditions, grant_controls
            )

            risk_level = "low" if state == "enabled" else "medium"

            resources.append(IntegrationResource(
                resource_type="conditional_access_policy",
                resource_id=policy_id,
                name=display_name,
                raw_data={
                    "display_name": display_name,
                    "state": state,
                    "conditions": conditions,
                    "grant_controls": grant_controls,
                    "session_controls": policy.get("sessionControls", {}),
                    "created_datetime": policy.get("createdDateTime"),
                    "modified_datetime": policy.get("modifiedDateTime")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    def _analyze_conditional_access_policy(
        self,
        policy: Dict[str, Any],
        conditions: Dict[str, Any],
        grant_controls: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze conditional access policy for compliance."""
        checks = {}
        state = policy.get("state", "disabled")

        # Check if policy is enabled
        checks["policy_enabled"] = {
            "check": "Policy Status",
            "status": "pass" if state == "enabled" else "warning",
            "control": "164.312(a)(1)",
            "description": f"Policy is {state}"
        }

        # Check for MFA requirement
        built_in_controls = grant_controls.get("builtInControls", [])
        if "mfa" in built_in_controls:
            checks["requires_mfa"] = {
                "check": "Requires MFA",
                "status": "pass",
                "control": "164.312(d)",
                "description": "Policy requires multi-factor authentication"
            }

        # Check for compliant device requirement
        if "compliantDevice" in built_in_controls:
            checks["requires_compliant_device"] = {
                "check": "Requires Compliant Device",
                "status": "pass",
                "control": "164.312(a)(2)(iv)",
                "description": "Policy requires compliant device"
            }

        # Check for managed device requirement
        if "domainJoinedDevice" in built_in_controls or "compliantDevice" in built_in_controls:
            checks["device_management"] = {
                "check": "Device Management",
                "status": "pass",
                "control": "164.310(d)(1)",
                "description": "Policy enforces device management"
            }

        # Check scope - all users vs specific groups
        users = conditions.get("users", {})
        include_users = users.get("includeUsers", [])
        if "All" in include_users:
            checks["broad_scope"] = {
                "check": "Policy Scope",
                "status": "info",
                "control": "164.312(a)(1)",
                "description": "Policy applies to all users"
            }

        return checks

    async def _collect_directory_roles(self) -> List[IntegrationResource]:
        """Collect directory role assignments."""
        try:
            # Get all directory roles
            roles_response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/directoryRoles"
            )
            roles = roles_response.get("value", [])

        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch directory roles: {e}")
            return []

        resources = []

        # Privileged roles to flag
        privileged_roles = {
            "Global Administrator",
            "Privileged Role Administrator",
            "User Administrator",
            "Exchange Administrator",
            "SharePoint Administrator",
            "Security Administrator"
        }

        for role in roles:
            role_id = role.get("id")
            display_name = role.get("displayName", "")
            description = role.get("description", "")

            # Get role members
            try:
                members_response = await self.api_request(
                    "GET",
                    f"{GRAPH_API_BASE}/directoryRoles/{role_id}/members"
                )
                members = members_response.get("value", [])
            except ProviderAPIError:
                members = []

            member_count = len(members)
            is_privileged = display_name in privileged_roles

            # Compliance checks
            compliance_checks = {
                "role_documented": {
                    "check": "Role Tracked",
                    "status": "pass",
                    "control": "164.308(a)(3)",
                    "description": f"{member_count} member(s) assigned"
                }
            }

            # Privileged role checks
            if is_privileged:
                compliance_checks["privileged_role"] = {
                    "check": "Privileged Role",
                    "status": "warning" if member_count > 3 else "info",
                    "control": "164.308(a)(4)(ii)(B)",
                    "description": f"High-privilege role with {member_count} members"
                }

            # Too many members in privileged role
            if is_privileged and member_count > 5:
                compliance_checks["excessive_members"] = {
                    "check": "Excessive Privileged Members",
                    "status": "warning",
                    "control": "164.308(a)(4)",
                    "description": f"{member_count} users in privileged role (review recommended)"
                }

            risk_level = "medium" if is_privileged and member_count > 3 else "low"

            resources.append(IntegrationResource(
                resource_type="directory_role",
                resource_id=role_id,
                name=display_name,
                raw_data={
                    "display_name": display_name,
                    "description": description,
                    "role_template_id": role.get("roleTemplateId"),
                    "member_count": member_count,
                    "members": [
                        {
                            "id": m.get("id"),
                            "display_name": m.get("displayName"),
                            "user_principal_name": m.get("userPrincipalName")
                        }
                        for m in members
                    ],
                    "is_privileged": is_privileged
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    def _calculate_user_risk(
        self,
        user: Dict[str, Any],
        has_mfa: bool,
        compliance_checks: Dict[str, Any]
    ) -> str:
        """Calculate risk level for a user."""
        is_enabled = user.get("accountEnabled", True)
        user_type = user.get("userType", "Member")

        # Disabled users are low risk
        if not is_enabled:
            return "low"

        # Guest users without MFA are high risk
        if user_type == "Guest" and not has_mfa:
            return "high"

        # No MFA is high risk
        if not has_mfa:
            return "high"

        # Check for warnings
        for check in compliance_checks.values():
            if check.get("status") == "warning":
                return "medium"

        return "low"

    async def get_security_posture_report(self) -> Dict[str, Any]:
        """
        Generate security posture report.

        Returns:
            Dict with security statistics
        """
        # Collect data
        users = await self._collect_users()
        policies = await self._collect_conditional_access_policies()
        roles = await self._collect_directory_roles()

        # Calculate statistics
        total_users = len([u for u in users if u.raw_data.get("account_enabled")])
        users_with_mfa = len([u for u in users if u.raw_data.get("mfa_configured")])
        guest_users = len([u for u in users if u.raw_data.get("user_type") == "Guest"])

        active_policies = len([p for p in policies if p.raw_data.get("state") == "enabled"])
        mfa_policies = len([
            p for p in policies
            if "mfa" in p.raw_data.get("grant_controls", {}).get("builtInControls", [])
        ])

        privileged_role_members = sum(
            r.raw_data.get("member_count", 0)
            for r in roles
            if r.raw_data.get("is_privileged")
        )

        return {
            "total_users": total_users,
            "users_with_mfa": users_with_mfa,
            "mfa_percentage": round(users_with_mfa / total_users * 100, 1) if total_users else 0,
            "guest_users": guest_users,
            "conditional_access_policies": len(policies),
            "active_policies": active_policies,
            "mfa_enforcing_policies": mfa_policies,
            "privileged_role_assignments": privileged_role_members
        }
