"""
Microsoft Graph Connector - Defender for Endpoint + Intune.

Extends Azure AD capabilities with security and device management data:
- Microsoft Defender for Endpoint (security alerts, recommendations)
- Microsoft Intune (managed devices, compliance policies)
- Microsoft Secure Score (security posture)
- Azure AD devices (registered/joined devices)

Required OAuth scopes (read-only):
- SecurityEvents.Read.All (Defender alerts)
- DeviceManagementManagedDevices.Read.All (Intune devices)
- DeviceManagementConfiguration.Read.All (Intune policies)
- Device.Read.All (Azure AD devices)
- SecurityActions.Read.All (Secure Score)

HIPAA Relevance:
- Defender alerts (164.308(a)(1)(ii)(D) - Incident Procedures)
- Device compliance (164.312(d)(1) - Device Authentication)
- Endpoint protection (164.308(a)(5)(ii)(B) - Protection from Malware)
- Security posture (164.308(a)(1)(i) - Security Management Process)
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


# Microsoft Graph API endpoints
AZURE_AUTH_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
AZURE_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_API_BETA = "https://graph.microsoft.com/beta"

# Security-focused scopes (extends Azure AD scopes)
MICROSOFT_SECURITY_SCOPES = [
    # Azure AD basics (for device correlation)
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/Device.Read.All",
    # Defender for Endpoint
    "https://graph.microsoft.com/SecurityEvents.Read.All",
    "https://graph.microsoft.com/SecurityActions.Read.All",
    # Intune device management
    "https://graph.microsoft.com/DeviceManagementManagedDevices.Read.All",
    "https://graph.microsoft.com/DeviceManagementConfiguration.Read.All",
]

# Resource limits
MAX_ALERTS = 1000
MAX_DEVICES = 5000
MAX_POLICIES = 500


class MicrosoftGraphConnector(BaseOAuthConnector):
    """
    Microsoft Graph connector for Defender + Intune.

    Collects security alerts, device compliance, and posture data.
    """

    PROVIDER = "microsoft_security"
    SCOPES = MICROSOFT_SECURITY_SCOPES

    def __init__(self, *args, tenant_id: str = "common", **kwargs):
        """
        Initialize Microsoft Graph connector.

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

            # Also test security access
            security_status = "unknown"
            try:
                await self.api_request(
                    "GET",
                    f"{GRAPH_API_BASE}/security/alerts",
                    params={"$top": 1}
                )
                security_status = "connected"
            except ProviderAPIError as e:
                security_status = f"error: {e.status_code}"

            return {
                "status": "connected",
                "provider": self.PROVIDER,
                "tenant_id": org.get("id"),
                "display_name": org.get("displayName"),
                "security_api": security_status,
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
        Collect all security resources from Microsoft Graph.

        Returns:
            List of IntegrationResource for alerts, devices, policies
        """
        resources = []

        # Collect Defender alerts
        alerts = await self._collect_security_alerts()
        resources.extend(alerts)

        # Collect Intune managed devices
        devices = await self._collect_intune_devices()
        resources.extend(devices)

        # Collect device compliance policies
        policies = await self._collect_compliance_policies()
        resources.extend(policies)

        # Collect Secure Score
        secure_score = await self._collect_secure_score()
        resources.extend(secure_score)

        # Collect Azure AD devices (for correlation)
        aad_devices = await self._collect_azure_ad_devices()
        resources.extend(aad_devices)

        logger.info(
            f"Microsoft Graph collection complete: integration={self.integration_id} "
            f"alerts={len(alerts)} devices={len(devices)} policies={len(policies)} "
            f"secure_score={len(secure_score)} aad_devices={len(aad_devices)}"
        )

        return resources

    # =========================================================================
    # Defender for Endpoint
    # =========================================================================

    async def _collect_security_alerts(self) -> List[IntegrationResource]:
        """
        Collect security alerts from Microsoft Defender.

        Uses the Security API to get alerts from Defender for Endpoint,
        Defender for Office 365, and other security products.
        """
        try:
            alerts_data = await self.api_paginate(
                "GET",
                f"{GRAPH_API_BASE}/security/alerts_v2",
                items_key="value",
                params={
                    "$top": 100,
                    "$orderby": "createdDateTime desc",
                    "$filter": "status ne 'resolved'"
                },
                page_token_param="$skiptoken",
                next_page_key="@odata.nextLink",
                max_items=MAX_ALERTS
            )
        except ProviderAPIError as e:
            # Fallback to legacy alerts endpoint
            if e.status_code == 404:
                logger.info("alerts_v2 not available, trying legacy endpoint")
                try:
                    alerts_data = await self.api_paginate(
                        "GET",
                        f"{GRAPH_API_BASE}/security/alerts",
                        items_key="value",
                        params={
                            "$top": 100,
                            "$orderby": "createdDateTime desc",
                            "$filter": "status ne 'resolved'"
                        },
                        page_token_param="$skiptoken",
                        next_page_key="@odata.nextLink",
                        max_items=MAX_ALERTS
                    )
                except ProviderAPIError:
                    logger.warning(f"Failed to fetch security alerts: {e}")
                    return []
            else:
                logger.warning(f"Failed to fetch security alerts: {e}")
                return []

        resources = []

        for alert in alerts_data:
            alert_id = alert.get("id")
            title = alert.get("title", alert.get("alertDisplayName", "Unknown Alert"))
            severity = alert.get("severity", "unknown").lower()
            status = alert.get("status", "unknown").lower()
            category = alert.get("category", "unknown")
            created_at = alert.get("createdDateTime")

            # Get affected entities
            evidence = alert.get("evidence", [])
            affected_devices = [
                e.get("deviceDnsName") or e.get("mdeDeviceId")
                for e in evidence
                if e.get("@odata.type") == "#microsoft.graph.security.deviceEvidence"
            ]
            affected_users = [
                e.get("userPrincipalName")
                for e in evidence
                if e.get("@odata.type") == "#microsoft.graph.security.userEvidence"
            ]

            # Compliance checks based on severity and status
            compliance_checks = self._analyze_security_alert(
                alert, severity, status, category
            )

            # Risk level mapping
            risk_map = {"high": "critical", "medium": "high", "low": "medium", "informational": "low"}
            risk_level = risk_map.get(severity, "medium")

            resources.append(IntegrationResource(
                resource_type="security_alert",
                resource_id=alert_id,
                name=title,
                raw_data={
                    "title": title,
                    "severity": severity,
                    "status": status,
                    "category": category,
                    "description": alert.get("description"),
                    "recommendation": alert.get("recommendedActions"),
                    "created_datetime": created_at,
                    "last_updated": alert.get("lastUpdateDateTime"),
                    "detection_source": alert.get("detectionSource"),
                    "service_source": alert.get("serviceSource"),
                    "affected_devices": affected_devices,
                    "affected_users": affected_users,
                    "mitre_techniques": alert.get("mitreTechniques", []),
                    "incident_id": alert.get("incidentId"),
                    "provider_alert_id": alert.get("providerAlertId")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    def _analyze_security_alert(
        self,
        alert: Dict[str, Any],
        severity: str,
        status: str,
        category: str
    ) -> Dict[str, Any]:
        """Analyze security alert for HIPAA compliance."""
        checks = {}

        # Alert severity check
        checks["alert_severity"] = {
            "check": "Alert Severity",
            "status": "fail" if severity in ["high", "critical"] else "warning" if severity == "medium" else "info",
            "control": "164.308(a)(1)(ii)(D)",
            "description": f"Security alert: {severity} severity"
        }

        # Response status check
        if status == "new":
            checks["alert_response"] = {
                "check": "Alert Response",
                "status": "warning",
                "control": "164.308(a)(6)(ii)",
                "description": "Alert is new and requires investigation"
            }
        elif status == "inProgress":
            checks["alert_response"] = {
                "check": "Alert Response",
                "status": "info",
                "control": "164.308(a)(6)(ii)",
                "description": "Alert is being investigated"
            }

        # Category-specific checks
        if category.lower() in ["malware", "ransomware"]:
            checks["malware_detection"] = {
                "check": "Malware Detection",
                "status": "fail",
                "control": "164.308(a)(5)(ii)(B)",
                "description": f"Malware detected: {category}"
            }
        elif category.lower() in ["phishing", "compromisedaccount"]:
            checks["account_compromise"] = {
                "check": "Account Compromise",
                "status": "fail",
                "control": "164.312(d)",
                "description": f"Potential account compromise: {category}"
            }
        elif category.lower() in ["unauthorizedaccess", "suspiciousactivity"]:
            checks["unauthorized_access"] = {
                "check": "Unauthorized Access",
                "status": "fail",
                "control": "164.312(a)(1)",
                "description": f"Suspicious activity: {category}"
            }

        return checks

    # =========================================================================
    # Microsoft Intune
    # =========================================================================

    async def _collect_intune_devices(self) -> List[IntegrationResource]:
        """
        Collect managed devices from Microsoft Intune.

        Returns device compliance status, OS version, encryption status, etc.
        """
        try:
            devices_data = await self.api_paginate(
                "GET",
                f"{GRAPH_API_BASE}/deviceManagement/managedDevices",
                items_key="value",
                params={
                    "$select": "id,deviceName,operatingSystem,osVersion,complianceState,"
                              "managementState,enrolledDateTime,lastSyncDateTime,"
                              "isEncrypted,isSupervised,model,manufacturer,"
                              "serialNumber,userPrincipalName,deviceEnrollmentType,"
                              "managedDeviceOwnerType,azureADRegistered,azureADDeviceId",
                    "$top": 999
                },
                page_token_param="$skiptoken",
                next_page_key="@odata.nextLink",
                max_items=MAX_DEVICES
            )
        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch Intune devices: {e}")
            return []

        resources = []
        now = datetime.utcnow()

        for device in devices_data:
            device_id = device.get("id")
            device_name = device.get("deviceName", "Unknown Device")
            os_type = device.get("operatingSystem", "Unknown")
            os_version = device.get("osVersion", "")
            compliance_state = device.get("complianceState", "unknown")
            management_state = device.get("managementState", "unknown")
            is_encrypted = device.get("isEncrypted", False)
            last_sync = device.get("lastSyncDateTime")

            # Calculate days since last sync
            days_since_sync = None
            if last_sync:
                try:
                    last_sync_dt = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
                    days_since_sync = (now - last_sync_dt.replace(tzinfo=None)).days
                except (ValueError, TypeError):
                    pass

            # Compliance checks
            compliance_checks = self._analyze_intune_device(
                device, compliance_state, is_encrypted, days_since_sync, os_type
            )

            # Risk level based on compliance
            if compliance_state == "noncompliant":
                risk_level = "high"
            elif compliance_state == "unknown" or not is_encrypted:
                risk_level = "medium"
            else:
                risk_level = "low"

            resources.append(IntegrationResource(
                resource_type="intune_device",
                resource_id=device_id,
                name=device_name,
                raw_data={
                    "device_name": device_name,
                    "operating_system": os_type,
                    "os_version": os_version,
                    "compliance_state": compliance_state,
                    "management_state": management_state,
                    "is_encrypted": is_encrypted,
                    "is_supervised": device.get("isSupervised"),
                    "enrolled_datetime": device.get("enrolledDateTime"),
                    "last_sync_datetime": last_sync,
                    "days_since_sync": days_since_sync,
                    "model": device.get("model"),
                    "manufacturer": device.get("manufacturer"),
                    "serial_number": device.get("serialNumber"),
                    "user_principal_name": device.get("userPrincipalName"),
                    "enrollment_type": device.get("deviceEnrollmentType"),
                    "owner_type": device.get("managedDeviceOwnerType"),
                    "azure_ad_registered": device.get("azureADRegistered"),
                    "azure_ad_device_id": device.get("azureADDeviceId")
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    def _analyze_intune_device(
        self,
        device: Dict[str, Any],
        compliance_state: str,
        is_encrypted: bool,
        days_since_sync: Optional[int],
        os_type: str
    ) -> Dict[str, Any]:
        """Analyze Intune device for HIPAA compliance."""
        checks = {}

        # Compliance state check
        checks["device_compliance"] = {
            "check": "Device Compliance",
            "status": "pass" if compliance_state == "compliant" else "fail" if compliance_state == "noncompliant" else "warning",
            "control": "164.312(d)(1)",
            "description": f"Device compliance state: {compliance_state}"
        }

        # Encryption check
        checks["device_encryption"] = {
            "check": "Device Encryption",
            "status": "pass" if is_encrypted else "fail",
            "control": "164.312(a)(2)(iv)",
            "description": "Device storage encrypted" if is_encrypted else "Device storage NOT encrypted"
        }

        # Management state
        management_state = device.get("managementState", "")
        checks["management_state"] = {
            "check": "Management State",
            "status": "pass" if management_state == "managed" else "warning",
            "control": "164.310(d)(1)",
            "description": f"Management state: {management_state}"
        }

        # Sync freshness
        if days_since_sync is not None:
            if days_since_sync > 30:
                checks["device_sync"] = {
                    "check": "Device Sync",
                    "status": "warning",
                    "control": "164.308(a)(1)(ii)(D)",
                    "description": f"No sync in {days_since_sync} days"
                }
            elif days_since_sync > 7:
                checks["device_sync"] = {
                    "check": "Device Sync",
                    "status": "info",
                    "control": "164.308(a)(1)(ii)(D)",
                    "description": f"Last sync {days_since_sync} days ago"
                }

        # OS-specific checks
        os_version = device.get("osVersion", "")
        if os_type.lower() == "windows":
            # Check for Windows 10/11 (supported versions)
            if "10.0.1" in os_version or "10.0.2" in os_version:
                checks["os_support"] = {
                    "check": "OS Support Status",
                    "status": "pass",
                    "control": "164.308(a)(5)(ii)(B)",
                    "description": f"Supported Windows version: {os_version}"
                }
        elif os_type.lower() in ["ios", "ipados"]:
            # iOS devices need supervision for full control
            if not device.get("isSupervised"):
                checks["device_supervision"] = {
                    "check": "Device Supervision",
                    "status": "info",
                    "control": "164.310(d)(1)",
                    "description": "iOS device not supervised (limited management)"
                }

        return checks

    async def _collect_compliance_policies(self) -> List[IntegrationResource]:
        """Collect device compliance policies from Intune."""
        try:
            policies_data = await self.api_paginate(
                "GET",
                f"{GRAPH_API_BASE}/deviceManagement/deviceCompliancePolicies",
                items_key="value",
                params={"$top": 100},
                page_token_param="$skiptoken",
                next_page_key="@odata.nextLink",
                max_items=MAX_POLICIES
            )
        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch compliance policies: {e}")
            return []

        resources = []

        for policy in policies_data:
            policy_id = policy.get("id")
            display_name = policy.get("displayName", "Unknown Policy")
            policy_type = policy.get("@odata.type", "").replace("#microsoft.graph.", "")

            # Get assignment info
            assignments = await self._get_policy_assignments(policy_id)

            compliance_checks = {
                "policy_tracked": {
                    "check": "Policy Tracked",
                    "status": "pass",
                    "control": "164.308(a)(1)(i)",
                    "description": f"Compliance policy type: {policy_type}"
                }
            }

            # Check if policy is assigned
            if not assignments:
                compliance_checks["policy_assignment"] = {
                    "check": "Policy Assignment",
                    "status": "warning",
                    "control": "164.308(a)(1)(i)",
                    "description": "Policy not assigned to any groups"
                }

            resources.append(IntegrationResource(
                resource_type="compliance_policy",
                resource_id=policy_id,
                name=display_name,
                raw_data={
                    "display_name": display_name,
                    "description": policy.get("description"),
                    "policy_type": policy_type,
                    "created_datetime": policy.get("createdDateTime"),
                    "last_modified": policy.get("lastModifiedDateTime"),
                    "version": policy.get("version"),
                    "assignments": assignments
                },
                compliance_checks=compliance_checks,
                risk_level="low"
            ))

        return resources

    async def _get_policy_assignments(self, policy_id: str) -> List[Dict[str, Any]]:
        """Get assignments for a compliance policy."""
        try:
            response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/deviceManagement/deviceCompliancePolicies/{policy_id}/assignments"
            )
            return response.get("value", [])
        except ProviderAPIError:
            return []

    # =========================================================================
    # Microsoft Secure Score
    # =========================================================================

    async def _collect_secure_score(self) -> List[IntegrationResource]:
        """
        Collect Microsoft Secure Score.

        Provides an overall security posture score and recommendations.
        """
        try:
            response = await self.api_request(
                "GET",
                f"{GRAPH_API_BASE}/security/secureScores",
                params={"$top": 1}
            )
            scores = response.get("value", [])
        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch Secure Score: {e}")
            return []

        if not scores:
            return []

        resources = []
        score_data = scores[0]  # Most recent score

        current_score = score_data.get("currentScore", 0)
        max_score = score_data.get("maxScore", 1)
        percentage = round((current_score / max_score) * 100, 1) if max_score else 0

        # Control scores breakdown
        control_scores = score_data.get("controlScores", [])

        # Find improvement actions
        failing_controls = [
            c for c in control_scores
            if c.get("score", 0) < c.get("maxScore", 0)
        ]

        # Compliance checks
        compliance_checks = {
            "secure_score": {
                "check": "Microsoft Secure Score",
                "status": "pass" if percentage >= 70 else "warning" if percentage >= 50 else "fail",
                "control": "164.308(a)(1)(i)",
                "description": f"Security posture score: {percentage}%"
            }
        }

        if len(failing_controls) > 10:
            compliance_checks["improvement_needed"] = {
                "check": "Security Improvements",
                "status": "warning",
                "control": "164.308(a)(1)(ii)(B)",
                "description": f"{len(failing_controls)} security controls need attention"
            }

        # Risk level based on score
        if percentage >= 70:
            risk_level = "low"
        elif percentage >= 50:
            risk_level = "medium"
        else:
            risk_level = "high"

        resources.append(IntegrationResource(
            resource_type="secure_score",
            resource_id=score_data.get("id", "secure_score"),
            name="Microsoft Secure Score",
            raw_data={
                "current_score": current_score,
                "max_score": max_score,
                "percentage": percentage,
                "created_datetime": score_data.get("createdDateTime"),
                "enabled_services": score_data.get("enabledServices", []),
                "licensed_user_count": score_data.get("licensedUserCount"),
                "active_user_count": score_data.get("activeUserCount"),
                "control_scores": [
                    {
                        "name": c.get("controlName"),
                        "score": c.get("score"),
                        "max_score": c.get("maxScore"),
                        "category": c.get("controlCategory")
                    }
                    for c in control_scores[:20]  # Top 20 for summary
                ],
                "failing_controls_count": len(failing_controls),
                "top_improvements": [
                    {
                        "name": c.get("controlName"),
                        "category": c.get("controlCategory"),
                        "potential_improvement": c.get("maxScore", 0) - c.get("score", 0)
                    }
                    for c in sorted(
                        failing_controls,
                        key=lambda x: x.get("maxScore", 0) - x.get("score", 0),
                        reverse=True
                    )[:5]
                ]
            },
            compliance_checks=compliance_checks,
            risk_level=risk_level
        ))

        return resources

    # =========================================================================
    # Azure AD Devices
    # =========================================================================

    async def _collect_azure_ad_devices(self) -> List[IntegrationResource]:
        """
        Collect devices registered in Azure AD.

        Provides device trust and compliance correlation.
        """
        try:
            devices_data = await self.api_paginate(
                "GET",
                f"{GRAPH_API_BASE}/devices",
                items_key="value",
                params={
                    "$select": "id,displayName,operatingSystem,operatingSystemVersion,"
                              "trustType,isCompliant,isManaged,approximateLastSignInDateTime,"
                              "deviceId,registrationDateTime,accountEnabled",
                    "$top": 999
                },
                page_token_param="$skiptoken",
                next_page_key="@odata.nextLink",
                max_items=MAX_DEVICES
            )
        except ProviderAPIError as e:
            logger.warning(f"Failed to fetch Azure AD devices: {e}")
            return []

        resources = []
        now = datetime.utcnow()

        for device in devices_data:
            device_id = device.get("id")
            display_name = device.get("displayName", "Unknown Device")
            trust_type = device.get("trustType", "unknown")
            is_compliant = device.get("isCompliant")
            is_managed = device.get("isManaged")
            account_enabled = device.get("accountEnabled", True)
            last_sign_in = device.get("approximateLastSignInDateTime")

            # Calculate days since last sign-in
            days_since_signin = None
            if last_sign_in:
                try:
                    last_signin_dt = datetime.fromisoformat(last_sign_in.replace("Z", "+00:00"))
                    days_since_signin = (now - last_signin_dt.replace(tzinfo=None)).days
                except (ValueError, TypeError):
                    pass

            # Compliance checks
            compliance_checks = {}

            # Trust type check
            if trust_type == "AzureAd":
                compliance_checks["device_trust"] = {
                    "check": "Device Trust",
                    "status": "pass",
                    "control": "164.312(d)(1)",
                    "description": "Azure AD joined device"
                }
            elif trust_type == "Workplace":
                compliance_checks["device_trust"] = {
                    "check": "Device Trust",
                    "status": "info",
                    "control": "164.312(d)(1)",
                    "description": "Workplace joined (BYOD)"
                }
            elif trust_type == "ServerAd":
                compliance_checks["device_trust"] = {
                    "check": "Device Trust",
                    "status": "pass",
                    "control": "164.312(d)(1)",
                    "description": "Hybrid Azure AD joined"
                }

            # Compliance status
            if is_compliant is not None:
                compliance_checks["device_compliance"] = {
                    "check": "Device Compliance",
                    "status": "pass" if is_compliant else "fail",
                    "control": "164.312(d)(1)",
                    "description": "Compliant" if is_compliant else "Non-compliant"
                }

            # Management status
            if is_managed is not None:
                compliance_checks["device_management"] = {
                    "check": "Device Management",
                    "status": "pass" if is_managed else "warning",
                    "control": "164.310(d)(1)",
                    "description": "Managed" if is_managed else "Not managed"
                }

            # Stale device check
            if days_since_signin and days_since_signin > 90:
                compliance_checks["stale_device"] = {
                    "check": "Device Activity",
                    "status": "warning",
                    "control": "164.308(a)(3)(ii)(C)",
                    "description": f"No sign-in in {days_since_signin} days"
                }

            # Risk level
            if is_compliant is False or (is_managed is False and trust_type != "Workplace"):
                risk_level = "medium"
            elif not account_enabled:
                risk_level = "low"
            else:
                risk_level = "low"

            resources.append(IntegrationResource(
                resource_type="azure_ad_device",
                resource_id=device_id,
                name=display_name,
                raw_data={
                    "display_name": display_name,
                    "operating_system": device.get("operatingSystem"),
                    "os_version": device.get("operatingSystemVersion"),
                    "trust_type": trust_type,
                    "is_compliant": is_compliant,
                    "is_managed": is_managed,
                    "account_enabled": account_enabled,
                    "device_id": device.get("deviceId"),
                    "registration_datetime": device.get("registrationDateTime"),
                    "last_sign_in_datetime": last_sign_in,
                    "days_since_signin": days_since_signin
                },
                compliance_checks=compliance_checks,
                risk_level=risk_level
            ))

        return resources

    # =========================================================================
    # Security Posture Report
    # =========================================================================

    async def get_security_posture_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive security posture report.

        Returns:
            Dict with security statistics across all resources
        """
        resources = await self.collect_resources()

        # Categorize resources
        alerts = [r for r in resources if r.resource_type == "security_alert"]
        intune_devices = [r for r in resources if r.resource_type == "intune_device"]
        aad_devices = [r for r in resources if r.resource_type == "azure_ad_device"]
        secure_scores = [r for r in resources if r.resource_type == "secure_score"]

        # Alert statistics
        critical_alerts = len([a for a in alerts if a.risk_level == "critical"])
        high_alerts = len([a for a in alerts if a.risk_level == "high"])
        unresolved_alerts = len([a for a in alerts if a.raw_data.get("status") != "resolved"])

        # Device compliance
        compliant_devices = len([d for d in intune_devices if d.raw_data.get("compliance_state") == "compliant"])
        encrypted_devices = len([d for d in intune_devices if d.raw_data.get("is_encrypted")])
        total_managed_devices = len(intune_devices)

        # Secure Score
        secure_score_pct = 0
        if secure_scores:
            secure_score_pct = secure_scores[0].raw_data.get("percentage", 0)

        return {
            "alerts": {
                "total": len(alerts),
                "critical": critical_alerts,
                "high": high_alerts,
                "unresolved": unresolved_alerts
            },
            "intune_devices": {
                "total": total_managed_devices,
                "compliant": compliant_devices,
                "compliance_rate": round(compliant_devices / total_managed_devices * 100, 1) if total_managed_devices else 0,
                "encrypted": encrypted_devices,
                "encryption_rate": round(encrypted_devices / total_managed_devices * 100, 1) if total_managed_devices else 0
            },
            "azure_ad_devices": {
                "total": len(aad_devices),
                "managed": len([d for d in aad_devices if d.raw_data.get("is_managed")]),
                "compliant": len([d for d in aad_devices if d.raw_data.get("is_compliant")])
            },
            "secure_score": {
                "percentage": secure_score_pct,
                "status": "good" if secure_score_pct >= 70 else "fair" if secure_score_pct >= 50 else "poor"
            },
            "hipaa_risk_summary": {
                "critical_items": critical_alerts + len([d for d in intune_devices if d.raw_data.get("compliance_state") == "noncompliant"]),
                "warnings": high_alerts + len([d for d in intune_devices if not d.raw_data.get("is_encrypted")])
            }
        }
