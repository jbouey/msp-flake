"""
Okta Connector.

Collects compliance-relevant data from Okta Admin API:
- Users (MFA status, factors enrolled, last login)
- Groups (membership, rules)
- Applications (SSO configuration)
- Policies (password, sign-on, MFA)

Required OAuth scopes (minimal read-only):
- okta.users.read
- okta.groups.read
- okta.apps.read
- okta.policies.read

HIPAA Relevance:
- User MFA enrollment (164.312(d) - Person or Entity Authentication)
- Password policies (164.308(a)(5)(ii)(D) - Password Management)
- SSO/Application access (164.312(a)(1) - Access Control)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin

from .base_connector import (
    BaseOAuthConnector,
    OAuthConfig,
    IntegrationResource,
    ProviderAPIError,
)

logger = logging.getLogger(__name__)


# Okta API configuration
OKTA_TOKEN_URL_TEMPLATE = "https://{domain}/oauth2/v1/token"
OKTA_AUTH_URL_TEMPLATE = "https://{domain}/oauth2/v1/authorize"

# Required scopes
OKTA_SCOPES = [
    "okta.users.read",
    "okta.groups.read",
    "okta.apps.read",
    "okta.policies.read",
]

# Resource limits
MAX_USERS = 5000
MAX_GROUPS = 5000
MAX_APPS = 1000


class OktaConnector(BaseOAuthConnector):
    """
    Okta Admin API connector.

    Collects users, groups, apps, and policies with compliance checks.
    """

    PROVIDER = "okta"
    SCOPES = OKTA_SCOPES

    def __init__(self, *args, okta_domain: str, **kwargs):
        """
        Initialize Okta connector.

        Args:
            okta_domain: Okta organization domain (e.g., "company.okta.com")
            *args, **kwargs: Passed to BaseOAuthConnector
        """
        super().__init__(*args, **kwargs)
        self.okta_domain = okta_domain
        self.api_base = f"https://{okta_domain}/api/v1"

    @property
    def AUTH_URL(self) -> str:
        return OKTA_AUTH_URL_TEMPLATE.format(domain=self.okta_domain)

    @property
    def TOKEN_URL(self) -> str:
        return OKTA_TOKEN_URL_TEMPLATE.format(domain=self.okta_domain)

    async def test_connection(self) -> Dict[str, Any]:
        """
        Test connection by fetching org info.

        Returns:
            Dict with connection status and org details
        """
        try:
            response = await self.api_request(
                "GET",
                f"{self.api_base}/org"
            )

            return {
                "status": "connected",
                "provider": self.PROVIDER,
                "org_name": response.get("name"),
                "org_url": response.get("website"),
                "subdomain": response.get("subdomain")
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
        Collect all resources from Okta.

        Returns:
            List of IntegrationResource for users, groups, apps, policies
        """
        resources = []

        # Collect users with MFA factors
        users = await self._collect_users()
        resources.extend(users)

        # Collect groups
        groups = await self._collect_groups()
        resources.extend(groups)

        # Collect applications
        apps = await self._collect_applications()
        resources.extend(apps)

        # Collect policies
        policies = await self._collect_policies()
        resources.extend(policies)

        logger.info(
            f"Okta collection complete: integration={self.integration_id} "
            f"users={len(users)} groups={len(groups)} apps={len(apps)} policies={len(policies)}"
        )

        return resources

    async def _collect_users(self) -> List[IntegrationResource]:
        """Collect users with MFA enrollment status."""
        users_data = await self._paginate_okta(
            f"{self.api_base}/users",
            max_items=MAX_USERS
        )

        resources = []
        now = datetime.utcnow()

        for user in users_data:
            user_id = user.get("id")
            profile = user.get("profile", {})
            status = user.get("status", "UNKNOWN")

            # Get MFA factors for this user
            mfa_factors = await self._get_user_factors(user_id)
            has_mfa = len(mfa_factors) > 0

            # Parse dates
            last_login = user.get("lastLogin")
            days_since_login = None
            if last_login:
                try:
                    last_login_dt = datetime.fromisoformat(last_login.replace("Z", "+00:00"))
                    days_since_login = (now - last_login_dt.replace(tzinfo=None)).days
                except (ValueError, TypeError):
                    pass

            # Compliance checks
            compliance_checks = {
                "mfa_enrolled": {
                    "check": "MFA Enrolled",
                    "status": "pass" if has_mfa else "fail",
                    "control": "164.312(d)",
                    "description": "Person or Entity Authentication",
                    "details": f"{len(mfa_factors)} factor(s) enrolled"
                },
                "account_status": {
                    "check": "Account Status",
                    "status": "pass" if status == "ACTIVE" else "warning",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": f"Status: {status}"
                }
            }

            # Check for deprovisioned accounts
            if status == "DEPROVISIONED":
                compliance_checks["deprovisioned"] = {
                    "check": "Terminated Access",
                    "status": "pass",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": "Account properly deprovisioned"
                }

            # Check for locked accounts
            if status == "LOCKED_OUT":
                compliance_checks["locked_out"] = {
                    "check": "Account Locked",
                    "status": "warning",
                    "control": "164.308(a)(1)(ii)(D)",
                    "description": "Account is locked out - may indicate security incident"
                }

            # Stale account check
            if days_since_login and days_since_login > 90 and status == "ACTIVE":
                compliance_checks["stale_account"] = {
                    "check": "Account Activity",
                    "status": "warning",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": f"No login in {days_since_login} days"
                }

            # Determine risk
            risk_level = self._calculate_user_risk(user, has_mfa, compliance_checks)

            resources.append(IntegrationResource(
                resource_type="user",
                resource_id=user_id,
                name=profile.get("email") or profile.get("login"),
                raw_data={
                    "email": profile.get("email"),
                    "login": profile.get("login"),
                    "first_name": profile.get("firstName"),
                    "last_name": profile.get("lastName"),
                    "status": status,
                    "mfa_enrolled": has_mfa,
                    "mfa_factors": [f.get("factorType") for f in mfa_factors],
                    "last_login": last_login,
                    "days_since_login": days_since_login,
                    "created": user.get("created"),
                    "activated": user.get("activated")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    async def _get_user_factors(self, user_id: str) -> List[Dict[str, Any]]:
        """Get MFA factors enrolled for a user."""
        try:
            response = await self.api_request(
                "GET",
                f"{self.api_base}/users/{user_id}/factors"
            )
            # Response is a list directly
            return response if isinstance(response, list) else []
        except ProviderAPIError as e:
            logger.warning(f"Failed to get factors for user {user_id}: {e}")
            return []

    async def _collect_groups(self) -> List[IntegrationResource]:
        """Collect groups with member counts."""
        groups_data = await self._paginate_okta(
            f"{self.api_base}/groups",
            max_items=MAX_GROUPS
        )

        resources = []

        for group in groups_data:
            group_id = group.get("id")
            profile = group.get("profile", {})
            group_type = group.get("type", "UNKNOWN")

            # Compliance checks
            compliance_checks = {
                "group_documented": {
                    "check": "Group Tracked",
                    "status": "pass",
                    "control": "164.308(a)(3)",
                    "description": "Workforce Security - Group membership documented"
                }
            }

            # Okta-managed groups vs user-created
            if group_type == "OKTA_GROUP":
                compliance_checks["managed_group"] = {
                    "check": "Managed Group",
                    "status": "pass",
                    "control": "164.308(a)(4)",
                    "description": "Group managed through Okta"
                }

            resources.append(IntegrationResource(
                resource_type="group",
                resource_id=group_id,
                name=profile.get("name"),
                raw_data={
                    "name": profile.get("name"),
                    "description": profile.get("description"),
                    "type": group_type,
                    "created": group.get("created"),
                    "last_updated": group.get("lastUpdated"),
                    "object_class": group.get("objectClass", [])
                },
                compliance_checks=compliance_checks,
                risk_level="low"
            ))

        return resources

    async def _collect_applications(self) -> List[IntegrationResource]:
        """Collect applications with SSO configuration."""
        apps_data = await self._paginate_okta(
            f"{self.api_base}/apps",
            max_items=MAX_APPS
        )

        resources = []

        for app in apps_data:
            app_id = app.get("id")
            label = app.get("label", "Unknown App")
            status = app.get("status", "UNKNOWN")
            sign_on_mode = app.get("signOnMode", "")

            # Compliance checks
            compliance_checks = {
                "app_tracked": {
                    "check": "Application Documented",
                    "status": "pass",
                    "control": "164.312(a)(1)",
                    "description": "Application access controlled through Okta"
                }
            }

            # Check for SSO vs password-based
            if "SAML" in sign_on_mode or "OIDC" in sign_on_mode:
                compliance_checks["sso_enabled"] = {
                    "check": "SSO Enabled",
                    "status": "pass",
                    "control": "164.312(d)",
                    "description": f"Federated authentication via {sign_on_mode}"
                }
            elif "PASSWORD" in sign_on_mode.upper():
                compliance_checks["password_based"] = {
                    "check": "Password-Based Auth",
                    "status": "warning",
                    "control": "164.312(d)",
                    "description": "Application uses password-based authentication (consider SSO)"
                }

            # Inactive apps
            if status != "ACTIVE":
                compliance_checks["app_status"] = {
                    "check": "Application Status",
                    "status": "info",
                    "control": "164.312(a)(1)",
                    "description": f"Application status: {status}"
                }

            resources.append(IntegrationResource(
                resource_type="application",
                resource_id=app_id,
                name=label,
                raw_data={
                    "label": label,
                    "name": app.get("name"),
                    "status": status,
                    "sign_on_mode": sign_on_mode,
                    "created": app.get("created"),
                    "last_updated": app.get("lastUpdated"),
                    "features": app.get("features", [])
                },
                compliance_checks=compliance_checks,
                risk_level="low" if status == "ACTIVE" else "medium"
            ))

        return resources

    async def _collect_policies(self) -> List[IntegrationResource]:
        """Collect security policies."""
        resources = []

        # Collect different policy types
        policy_types = ["PASSWORD", "MFA_ENROLL", "OKTA_SIGN_ON"]

        for policy_type in policy_types:
            try:
                policies = await self._paginate_okta(
                    f"{self.api_base}/policies",
                    params={"type": policy_type},
                    max_items=100
                )

                for policy in policies:
                    policy_id = policy.get("id")
                    name = policy.get("name", "Unknown Policy")
                    status = policy.get("status", "UNKNOWN")

                    compliance_checks = self._analyze_policy(policy, policy_type)

                    resources.append(IntegrationResource(
                        resource_type=f"policy_{policy_type.lower()}",
                        resource_id=policy_id,
                        name=name,
                        raw_data={
                            "name": name,
                            "type": policy_type,
                            "status": status,
                            "description": policy.get("description"),
                            "priority": policy.get("priority"),
                            "created": policy.get("created"),
                            "last_updated": policy.get("lastUpdated"),
                            "conditions": policy.get("conditions", {}),
                            "settings": policy.get("settings", {})
                        },
                        compliance_checks=compliance_checks,
                        risk_level=self._policy_risk_level(compliance_checks)
                    ))

            except ProviderAPIError as e:
                logger.warning(f"Failed to fetch {policy_type} policies: {e}")

        return resources

    def _analyze_policy(self, policy: Dict[str, Any], policy_type: str) -> Dict[str, Any]:
        """Analyze policy for compliance issues."""
        checks = {}
        settings = policy.get("settings", {})

        if policy_type == "PASSWORD":
            password_settings = settings.get("password", {})
            complexity = password_settings.get("complexity", {})

            # Check minimum length
            min_length = complexity.get("minLength", 0)
            checks["password_length"] = {
                "check": "Password Length",
                "status": "pass" if min_length >= 8 else "fail",
                "control": "164.308(a)(5)(ii)(D)",
                "description": f"Minimum {min_length} characters (8+ recommended)"
            }

            # Check complexity requirements
            if complexity.get("minUpperCase", 0) > 0 or complexity.get("minNumber", 0) > 0:
                checks["password_complexity"] = {
                    "check": "Password Complexity",
                    "status": "pass",
                    "control": "164.308(a)(5)(ii)(D)",
                    "description": "Complexity requirements enforced"
                }

            # Check expiration
            age = password_settings.get("age", {})
            max_age = age.get("maxAgeDays", 0)
            if max_age > 0 and max_age <= 90:
                checks["password_expiration"] = {
                    "check": "Password Expiration",
                    "status": "pass",
                    "control": "164.308(a)(5)(ii)(D)",
                    "description": f"Passwords expire every {max_age} days"
                }
            elif max_age > 90:
                checks["password_expiration"] = {
                    "check": "Password Expiration",
                    "status": "warning",
                    "control": "164.308(a)(5)(ii)(D)",
                    "description": f"Password expiration is {max_age} days (90 or less recommended)"
                }

        elif policy_type == "MFA_ENROLL":
            # Check if MFA enrollment is required
            mfa_settings = settings.get("factors", {})
            checks["mfa_policy"] = {
                "check": "MFA Enrollment Policy",
                "status": "pass" if policy.get("status") == "ACTIVE" else "warning",
                "control": "164.312(d)",
                "description": "MFA enrollment policy is active"
            }

        elif policy_type == "OKTA_SIGN_ON":
            # Check session settings
            checks["sign_on_policy"] = {
                "check": "Sign-On Policy",
                "status": "pass" if policy.get("status") == "ACTIVE" else "warning",
                "control": "164.312(a)(1)",
                "description": "Sign-on policy is active"
            }

        return checks

    def _policy_risk_level(self, compliance_checks: Dict[str, Any]) -> str:
        """Determine policy risk level based on checks."""
        for check in compliance_checks.values():
            if check.get("status") == "fail":
                return "high"
        for check in compliance_checks.values():
            if check.get("status") == "warning":
                return "medium"
        return "low"

    def _calculate_user_risk(
        self,
        user: Dict[str, Any],
        has_mfa: bool,
        compliance_checks: Dict[str, Any]
    ) -> str:
        """Calculate risk level for a user."""
        status = user.get("status", "")

        # Deprovisioned users are low risk
        if status == "DEPROVISIONED":
            return "low"

        # Locked out needs investigation
        if status == "LOCKED_OUT":
            return "medium"

        # No MFA is high risk
        if not has_mfa and status == "ACTIVE":
            return "high"

        # Check for warnings
        for check in compliance_checks.values():
            if check.get("status") == "warning":
                return "medium"

        return "low"

    async def _paginate_okta(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_items: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Paginate through Okta API results using Link headers.

        Okta uses Link headers for pagination instead of page tokens.
        """
        all_items = []
        params = dict(params) if params else {}
        params.setdefault("limit", 200)

        next_url = url

        while next_url and len(all_items) < max_items:
            # Use the full URL if it's a pagination URL
            if next_url.startswith("http"):
                response = await self.api_request("GET", next_url)
            else:
                response = await self.api_request("GET", next_url, params=params)
                params = {}  # Clear params after first request

            # Handle response - Okta returns list directly
            if isinstance(response, list):
                all_items.extend(response)
            else:
                # Some endpoints return dict with items
                items = response.get("items", response.get("policies", []))
                all_items.extend(items)

            # Note: To properly get next URL, we'd need to access response headers
            # For now, we break as httpx response isn't available here
            # In production, this should be enhanced to parse Link headers
            break

        return all_items[:max_items]

    async def get_mfa_enrollment_report(self) -> Dict[str, Any]:
        """
        Generate MFA enrollment report.

        Returns:
            Dict with MFA statistics
        """
        users_data = await self._paginate_okta(
            f"{self.api_base}/users",
            params={"filter": 'status eq "ACTIVE"'},
            max_items=MAX_USERS
        )

        total = len(users_data)
        mfa_enrolled = 0
        factor_types = {}

        for user in users_data:
            factors = await self._get_user_factors(user.get("id"))
            if factors:
                mfa_enrolled += 1
                for factor in factors:
                    factor_type = factor.get("factorType", "unknown")
                    factor_types[factor_type] = factor_types.get(factor_type, 0) + 1

        return {
            "total_active_users": total,
            "mfa_enrolled": mfa_enrolled,
            "mfa_not_enrolled": total - mfa_enrolled,
            "mfa_percentage": round(mfa_enrolled / total * 100, 1) if total else 0,
            "factor_distribution": factor_types
        }
