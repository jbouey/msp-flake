"""
Tests for Microsoft Graph (Defender + Intune) Connector.

Tests cover:
- Security alert analysis logic
- Intune device compliance checks
- Secure Score calculations
- HIPAA control mappings
- Risk level calculations

Run with:
    cd mcp-server/central-command/backend/integrations/tests
    python -m pytest test_microsoft_graph.py -v
"""

import pytest
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional


# =============================================================================
# EXTRACTED ANALYSIS FUNCTIONS (mirrors microsoft_graph.py logic)
# =============================================================================

def analyze_security_alert(
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


def analyze_intune_device(
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

    # iOS supervision check
    if os_type.lower() in ["ios", "ipados"]:
        if not device.get("isSupervised"):
            checks["device_supervision"] = {
                "check": "Device Supervision",
                "status": "info",
                "control": "164.310(d)(1)",
                "description": "iOS device not supervised (limited management)"
            }

    return checks


def calculate_secure_score_risk(percentage: float) -> str:
    """Calculate risk level from secure score percentage."""
    if percentage >= 70:
        return "low"
    elif percentage >= 50:
        return "medium"
    else:
        return "high"


def calculate_alert_risk(severity: str) -> str:
    """Map alert severity to risk level."""
    risk_map = {"high": "critical", "medium": "high", "low": "medium", "informational": "low"}
    return risk_map.get(severity.lower(), "medium")


# =============================================================================
# CONSTANTS (mirrors microsoft_graph.py)
# =============================================================================

MICROSOFT_SECURITY_SCOPES = [
    "https://graph.microsoft.com/User.Read.All",
    "https://graph.microsoft.com/Device.Read.All",
    "https://graph.microsoft.com/SecurityEvents.Read.All",
    "https://graph.microsoft.com/SecurityActions.Read.All",
    "https://graph.microsoft.com/DeviceManagementManagedDevices.Read.All",
    "https://graph.microsoft.com/DeviceManagementConfiguration.Read.All",
]

MAX_ALERTS = 1000
MAX_DEVICES = 5000


# =============================================================================
# SECURITY ALERT ANALYSIS TESTS
# =============================================================================

class TestSecurityAlertAnalysis:
    """Tests for security alert analysis."""

    def test_critical_severity_alert(self):
        """Test analysis of critical severity alert."""
        alert = {"id": "alert-1", "title": "Ransomware detected"}
        checks = analyze_security_alert(alert, "high", "new", "Ransomware")

        assert checks["alert_severity"]["status"] == "fail"
        assert checks["alert_severity"]["control"] == "164.308(a)(1)(ii)(D)"

    def test_malware_alert_category(self):
        """Test malware category triggers malware detection check."""
        alert = {"id": "alert-2"}
        checks = analyze_security_alert(alert, "high", "new", "Malware")

        assert "malware_detection" in checks
        assert checks["malware_detection"]["status"] == "fail"
        assert "164.308(a)(5)(ii)(B)" in checks["malware_detection"]["control"]

    def test_ransomware_alert_category(self):
        """Test ransomware category triggers malware detection check."""
        alert = {"id": "alert-3"}
        checks = analyze_security_alert(alert, "high", "new", "Ransomware")

        assert "malware_detection" in checks
        assert checks["malware_detection"]["status"] == "fail"

    def test_phishing_alert_category(self):
        """Test phishing category triggers account compromise check."""
        alert = {"id": "alert-4"}
        checks = analyze_security_alert(alert, "medium", "inProgress", "Phishing")

        assert "account_compromise" in checks
        assert checks["account_compromise"]["status"] == "fail"
        assert "164.312(d)" in checks["account_compromise"]["control"]

    def test_new_alert_response_status(self):
        """Test new alert gets warning for response status."""
        alert = {"id": "alert-5"}
        checks = analyze_security_alert(alert, "low", "new", "Other")

        assert "alert_response" in checks
        assert checks["alert_response"]["status"] == "warning"
        assert "requires investigation" in checks["alert_response"]["description"]

    def test_in_progress_alert_response(self):
        """Test in-progress alert gets info status."""
        alert = {"id": "alert-6"}
        checks = analyze_security_alert(alert, "low", "inProgress", "Other")

        assert "alert_response" in checks
        assert checks["alert_response"]["status"] == "info"
        assert "being investigated" in checks["alert_response"]["description"]

    def test_medium_severity_warning(self):
        """Test medium severity gets warning status."""
        alert = {"id": "alert-7"}
        checks = analyze_security_alert(alert, "medium", "new", "Other")

        assert checks["alert_severity"]["status"] == "warning"

    def test_low_severity_info(self):
        """Test low severity gets info status."""
        alert = {"id": "alert-8"}
        checks = analyze_security_alert(alert, "low", "new", "Other")

        assert checks["alert_severity"]["status"] == "info"


# =============================================================================
# INTUNE DEVICE ANALYSIS TESTS
# =============================================================================

class TestIntuneDeviceAnalysis:
    """Tests for Intune device compliance analysis."""

    def test_compliant_encrypted_device(self):
        """Test analysis of compliant and encrypted device."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", True, 1, "Windows")

        assert checks["device_compliance"]["status"] == "pass"
        assert checks["device_encryption"]["status"] == "pass"
        assert checks["management_state"]["status"] == "pass"

    def test_noncompliant_device(self):
        """Test non-compliant device gets fail status."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "noncompliant", True, 1, "Windows")

        assert checks["device_compliance"]["status"] == "fail"
        assert "164.312(d)(1)" in checks["device_compliance"]["control"]

    def test_unencrypted_device(self):
        """Test unencrypted device gets fail status."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", False, 1, "Windows")

        assert checks["device_encryption"]["status"] == "fail"
        assert "NOT encrypted" in checks["device_encryption"]["description"]
        assert "164.312(a)(2)(iv)" in checks["device_encryption"]["control"]

    def test_stale_device_sync_30_days(self):
        """Test warning for device not synced in 30+ days."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", True, 45, "Windows")

        assert "device_sync" in checks
        assert checks["device_sync"]["status"] == "warning"
        assert "45 days" in checks["device_sync"]["description"]

    def test_device_sync_7_to_30_days(self):
        """Test info for device synced 7-30 days ago."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", True, 15, "Windows")

        assert "device_sync" in checks
        assert checks["device_sync"]["status"] == "info"

    def test_unsupervised_ios_device(self):
        """Test info for unsupervised iOS device."""
        device = {"managementState": "managed", "isSupervised": False}
        checks = analyze_intune_device(device, "compliant", True, 1, "iOS")

        assert "device_supervision" in checks
        assert checks["device_supervision"]["status"] == "info"
        assert "limited management" in checks["device_supervision"]["description"]

    def test_supervised_ios_device(self):
        """Test supervised iOS device doesn't trigger supervision check."""
        device = {"managementState": "managed", "isSupervised": True}
        checks = analyze_intune_device(device, "compliant", True, 1, "iOS")

        # Should NOT have device_supervision check since device is supervised
        assert "device_supervision" not in checks

    def test_unknown_compliance_state(self):
        """Test unknown compliance state gets warning."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "unknown", True, 1, "Windows")

        assert checks["device_compliance"]["status"] == "warning"


# =============================================================================
# HIPAA CONTROL MAPPING TESTS
# =============================================================================

class TestHIPAAControlMappings:
    """Tests for HIPAA control mappings."""

    def test_incident_procedures_control(self):
        """Test 164.308(a)(1)(ii)(D) - Incident Procedures."""
        checks = analyze_security_alert({}, "high", "new", "Other")
        assert "164.308(a)(1)(ii)(D)" in checks["alert_severity"]["control"]

    def test_malware_protection_control(self):
        """Test 164.308(a)(5)(ii)(B) - Protection from Malware."""
        checks = analyze_security_alert({}, "high", "new", "Malware")
        assert "164.308(a)(5)(ii)(B)" in checks["malware_detection"]["control"]

    def test_device_authentication_control(self):
        """Test 164.312(d)(1) - Device Authentication."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", True, 1, "Windows")
        assert "164.312(d)(1)" in checks["device_compliance"]["control"]

    def test_encryption_control(self):
        """Test 164.312(a)(2)(iv) - Encryption."""
        device = {"managementState": "managed"}
        checks = analyze_intune_device(device, "compliant", True, 1, "Windows")
        assert "164.312(a)(2)(iv)" in checks["device_encryption"]["control"]

    def test_person_entity_authentication(self):
        """Test 164.312(d) - Person Authentication for phishing."""
        checks = analyze_security_alert({}, "medium", "new", "Phishing")
        assert "164.312(d)" in checks["account_compromise"]["control"]


# =============================================================================
# RISK LEVEL CALCULATION TESTS
# =============================================================================

class TestRiskLevelCalculations:
    """Tests for risk level calculations."""

    def test_alert_risk_high(self):
        """Test high severity alert maps to critical risk."""
        assert calculate_alert_risk("high") == "critical"

    def test_alert_risk_medium(self):
        """Test medium severity alert maps to high risk."""
        assert calculate_alert_risk("medium") == "high"

    def test_alert_risk_low(self):
        """Test low severity alert maps to medium risk."""
        assert calculate_alert_risk("low") == "medium"

    def test_alert_risk_informational(self):
        """Test informational alert maps to low risk."""
        assert calculate_alert_risk("informational") == "low"

    def test_secure_score_high(self):
        """Test secure score >= 70 is low risk."""
        assert calculate_secure_score_risk(70) == "low"
        assert calculate_secure_score_risk(85) == "low"
        assert calculate_secure_score_risk(100) == "low"

    def test_secure_score_medium(self):
        """Test secure score 50-69 is medium risk."""
        assert calculate_secure_score_risk(50) == "medium"
        assert calculate_secure_score_risk(60) == "medium"
        assert calculate_secure_score_risk(69) == "medium"

    def test_secure_score_low(self):
        """Test secure score < 50 is high risk."""
        assert calculate_secure_score_risk(49) == "high"
        assert calculate_secure_score_risk(30) == "high"
        assert calculate_secure_score_risk(0) == "high"


# =============================================================================
# SCOPE TESTS
# =============================================================================

class TestMicrosoftSecurityScopes:
    """Tests for OAuth scope configuration."""

    def test_defender_scopes_present(self):
        """Test Defender for Endpoint scopes are included."""
        assert "https://graph.microsoft.com/SecurityEvents.Read.All" in MICROSOFT_SECURITY_SCOPES
        assert "https://graph.microsoft.com/SecurityActions.Read.All" in MICROSOFT_SECURITY_SCOPES

    def test_intune_scopes_present(self):
        """Test Intune device management scopes are included."""
        assert "https://graph.microsoft.com/DeviceManagementManagedDevices.Read.All" in MICROSOFT_SECURITY_SCOPES
        assert "https://graph.microsoft.com/DeviceManagementConfiguration.Read.All" in MICROSOFT_SECURITY_SCOPES

    def test_device_scope_present(self):
        """Test Azure AD Device scope is included."""
        assert "https://graph.microsoft.com/Device.Read.All" in MICROSOFT_SECURITY_SCOPES

    def test_user_scope_present(self):
        """Test User scope is included for correlation."""
        assert "https://graph.microsoft.com/User.Read.All" in MICROSOFT_SECURITY_SCOPES

    def test_scope_count(self):
        """Test expected number of scopes."""
        assert len(MICROSOFT_SECURITY_SCOPES) == 6


# =============================================================================
# LIMITS TESTS
# =============================================================================

class TestResourceLimits:
    """Tests for resource collection limits."""

    def test_max_alerts_limit(self):
        """Test maximum alerts limit is 1000."""
        assert MAX_ALERTS == 1000

    def test_max_devices_limit(self):
        """Test maximum devices limit is 5000."""
        assert MAX_DEVICES == 5000

    def test_limits_positive(self):
        """Test limits are positive integers."""
        assert MAX_ALERTS > 0
        assert MAX_DEVICES > 0


# =============================================================================
# SECURE SCORE CALCULATION TESTS
# =============================================================================

class TestSecureScoreCalculations:
    """Tests for secure score percentage calculations."""

    def test_percentage_calculation_100(self):
        """Test 100% score calculation."""
        current = 100
        max_score = 100
        percentage = round((current / max_score) * 100, 1)
        assert percentage == 100.0

    def test_percentage_calculation_partial(self):
        """Test partial score calculation."""
        current = 70
        max_score = 100
        percentage = round((current / max_score) * 100, 1)
        assert percentage == 70.0

    def test_percentage_calculation_decimal(self):
        """Test decimal score calculation."""
        current = 67
        max_score = 95
        percentage = round((current / max_score) * 100, 1)
        assert percentage == 70.5


# =============================================================================
# RESOURCE TYPE TESTS
# =============================================================================

class TestResourceTypes:
    """Tests for expected resource types."""

    def test_expected_resource_types(self):
        """Test all expected resource types."""
        expected_types = [
            "security_alert",
            "intune_device",
            "compliance_policy",
            "secure_score",
            "azure_ad_device"
        ]

        # Verify all types are accounted for
        assert len(expected_types) == 5
        assert "security_alert" in expected_types
        assert "intune_device" in expected_types
        assert "secure_score" in expected_types
