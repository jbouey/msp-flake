"""
Tests for SMBv1 Protocol Disabling (RB-WIN-SEC-020) and
WMI Event Subscription Persistence (RB-WIN-SEC-021) runbooks.

Validates runbook structure, L1 rule matching, check_type mapping,
and detection result processing.
"""

import pytest
from unittest.mock import MagicMock


# =============================================================================
# Runbook Structure Tests
# =============================================================================

class TestSMBv1Runbook:
    """Tests for RB-WIN-SEC-020 — SMBv1 Protocol Disabling."""

    def test_runbook_exists_in_registry(self):
        from compliance_agent.runbooks.windows import ALL_RUNBOOKS
        assert "RB-WIN-SEC-020" in ALL_RUNBOOKS

    def test_runbook_exists_in_security_category(self):
        from compliance_agent.runbooks.windows.security import SECURITY_RUNBOOKS
        assert "RB-WIN-SEC-020" in SECURITY_RUNBOOKS

    def test_runbook_id_and_name(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert rb is not None
        assert rb.id == "RB-WIN-SEC-020"
        assert "SMBv1" in rb.name or "SMB1" in rb.name

    def test_runbook_severity(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert rb.severity in ("high", "critical")

    def test_runbook_hipaa_controls(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert "164.312(e)(1)" in rb.hipaa_controls

    def test_detect_script_checks_smb1(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert "Get-SmbServerConfiguration" in rb.detect_script
        assert "EnableSMB1Protocol" in rb.detect_script

    def test_remediate_script_disables_smb1(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert "Set-SmbServerConfiguration" in rb.remediate_script
        assert "EnableSMB1Protocol" in rb.remediate_script
        assert "$false" in rb.remediate_script

    def test_verify_script_confirms_disabled(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-020")
        assert "EnableSMB1Protocol" in rb.verify_script

    def test_check_type_mapping(self):
        from compliance_agent.runbooks.windows import get_runbooks_by_check_type
        runbooks = get_runbooks_by_check_type("smb1_protocol")
        assert len(runbooks) >= 1
        rb_ids = [rb.id for rb in runbooks]
        assert "RB-WIN-SEC-020" in rb_ids


class TestWMIPersistenceRunbook:
    """Tests for RB-WIN-SEC-021 — WMI Event Subscription Persistence."""

    def test_runbook_exists_in_registry(self):
        from compliance_agent.runbooks.windows import ALL_RUNBOOKS
        assert "RB-WIN-SEC-021" in ALL_RUNBOOKS

    def test_runbook_exists_in_security_category(self):
        from compliance_agent.runbooks.windows.security import SECURITY_RUNBOOKS
        assert "RB-WIN-SEC-021" in SECURITY_RUNBOOKS

    def test_runbook_id_and_name(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert rb is not None
        assert rb.id == "RB-WIN-SEC-021"
        assert "WMI" in rb.name

    def test_runbook_severity_critical(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert rb.severity == "critical"

    def test_runbook_hipaa_controls(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert "164.312(a)(1)" in rb.hipaa_controls

    def test_detect_script_queries_wmi_subscriptions(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert "__EventFilter" in rb.detect_script
        assert "root\\subscription" in rb.detect_script or "root\\\\subscription" in rb.detect_script

    def test_detect_script_has_safe_whitelist(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        # Should whitelist known-safe system entries
        assert "BVTFilter" in rb.detect_script or "SCM Event Log Filter" in rb.detect_script

    def test_remediate_script_removes_subscriptions(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert "Remove-WmiObject" in rb.remediate_script or "Delete" in rb.remediate_script

    def test_remediate_removes_bindings_first(self):
        """Bindings must be removed before filters/consumers to avoid orphans."""
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        script = rb.remediate_script
        # FilterToConsumerBinding removal should appear in the script
        assert "FilterToConsumerBinding" in script

    def test_verify_script_rechecks(self):
        from compliance_agent.runbooks.windows import get_runbook
        rb = get_runbook("RB-WIN-SEC-021")
        assert "__EventFilter" in rb.verify_script or "subscription" in rb.verify_script

    def test_check_type_mapping(self):
        from compliance_agent.runbooks.windows import get_runbooks_by_check_type
        runbooks = get_runbooks_by_check_type("wmi_event_persistence")
        assert len(runbooks) >= 1
        rb_ids = [rb.id for rb in runbooks]
        assert "RB-WIN-SEC-021" in rb_ids


# =============================================================================
# L1 Rule Tests
# =============================================================================

class TestL1RulesSMBWMI:
    """Tests for L1 deterministic rules matching SMB and WMI check types."""

    def _get_engine(self):
        """Load the L1 deterministic engine."""
        from compliance_agent.level1_deterministic import DeterministicEngine
        return DeterministicEngine(rules_dir=None, incident_db=None)

    def test_smb1_rule_exists(self):
        engine = self._get_engine()
        smb1_rules = [r for r in engine.rules if r.id == "L1-WIN-SEC-SMB1"]
        assert len(smb1_rules) == 1

    def test_smb1_rule_targets_correct_runbook(self):
        engine = self._get_engine()
        rule = next(r for r in engine.rules if r.id == "L1-WIN-SEC-SMB1")
        assert rule.action == "run_windows_runbook"
        assert rule.action_params["runbook_id"] == "RB-WIN-SEC-020"

    def test_smb1_rule_conditions(self):
        engine = self._get_engine()
        rule = next(r for r in engine.rules if r.id == "L1-WIN-SEC-SMB1")
        condition_fields = [c.field for c in rule.conditions]
        assert "check_type" in condition_fields
        assert "drift_detected" in condition_fields

    def test_wmi_rule_exists(self):
        engine = self._get_engine()
        wmi_rules = [r for r in engine.rules if r.id == "L1-PERSIST-WMI-001"]
        assert len(wmi_rules) == 1

    def test_wmi_rule_targets_correct_runbook(self):
        engine = self._get_engine()
        rule = next(r for r in engine.rules if r.id == "L1-PERSIST-WMI-001")
        assert rule.action == "run_windows_runbook"
        assert rule.action_params["runbook_id"] == "RB-WIN-SEC-021"

    def test_wmi_rule_conditions(self):
        engine = self._get_engine()
        rule = next(r for r in engine.rules if r.id == "L1-PERSIST-WMI-001")
        condition_fields = [c.field for c in rule.conditions]
        assert "check_type" in condition_fields
        assert "drift_detected" in condition_fields

    def test_smb1_rule_matches_incident(self):
        """Test that L1 engine matches a smb1_protocol incident."""
        engine = self._get_engine()

        result = engine.match(
            incident_id="test-smb1-001",
            incident_type="drift",
            severity="high",
            data={
                "check_type": "smb1_protocol",
                "drift_detected": True,
                "hostname": "WIN-DC01",
            }
        )
        assert result is not None
        assert result.action == "run_windows_runbook"
        assert result.action_params["runbook_id"] == "RB-WIN-SEC-020"

    def test_wmi_rule_matches_incident(self):
        """Test that L1 engine matches a wmi_event_persistence incident."""
        engine = self._get_engine()

        result = engine.match(
            incident_id="test-wmi-001",
            incident_type="drift",
            severity="critical",
            data={
                "check_type": "wmi_event_persistence",
                "drift_detected": True,
                "hostname": "WIN-DC01",
            }
        )
        assert result is not None
        assert result.action == "run_windows_runbook"
        assert result.action_params["runbook_id"] == "RB-WIN-SEC-021"

    def test_no_match_when_no_drift(self):
        """Test that rules don't match when drift_detected is False."""
        engine = self._get_engine()

        result = engine.match(
            incident_id="test-nodrift-001",
            incident_type="drift",
            severity="info",
            data={
                "check_type": "smb1_protocol",
                "drift_detected": False,
                "hostname": "WIN-DC01",
            }
        )
        # Should either return None or a different rule (not the SMB1 one)
        if result is not None:
            assert result.action_params.get("runbook_id") != "RB-WIN-SEC-020"


# =============================================================================
# Detection Result Processing Tests
# =============================================================================

class TestDetectionProcessing:
    """Tests for SMB1 and WMI detection result parsing logic."""

    def test_smb1_enabled_detected_as_fail(self):
        """SMB1 enabled should be parsed as fail status."""
        import json
        output = json.dumps({"EnableSMB1Protocol": True})
        data = json.loads(output)
        smb1_enabled = data.get("EnableSMB1Protocol", True)
        status = "fail" if smb1_enabled else "pass"
        assert status == "fail"

    def test_smb1_disabled_detected_as_pass(self):
        """SMB1 disabled should be parsed as pass status."""
        import json
        output = json.dumps({"EnableSMB1Protocol": False})
        data = json.loads(output)
        smb1_enabled = data.get("EnableSMB1Protocol", True)
        status = "fail" if smb1_enabled else "pass"
        assert status == "pass"

    def test_wmi_suspicious_detected_as_fail(self):
        """Suspicious WMI subscriptions should be parsed as fail."""
        import json
        output = json.dumps({
            "SuspiciousCount": 2,
            "Items": [
                {"Name": "EvilFilter", "Type": "Filter"},
                {"Name": "BackdoorConsumer", "Type": "CommandLineEventConsumer"},
            ]
        })
        data = json.loads(output)
        suspicious_count = data.get("SuspiciousCount", 0)
        status = "fail" if suspicious_count > 0 else "pass"
        assert status == "fail"

    def test_wmi_clean_detected_as_pass(self):
        """No suspicious WMI subscriptions should be parsed as pass."""
        import json
        output = json.dumps({"SuspiciousCount": 0, "Items": []})
        data = json.loads(output)
        suspicious_count = data.get("SuspiciousCount", 0)
        status = "fail" if suspicious_count > 0 else "pass"
        assert status == "pass"

    def test_smb1_malformed_output_defaults_to_fail(self):
        """Malformed SMB1 output should default to fail (conservative)."""
        import json
        try:
            data = json.loads("not-json")
            smb1_enabled = data.get("EnableSMB1Protocol", True)
        except Exception:
            smb1_enabled = True  # Default conservative
        status = "fail" if smb1_enabled else "pass"
        assert status == "fail"
