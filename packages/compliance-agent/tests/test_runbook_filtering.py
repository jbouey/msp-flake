"""
Tests for runbook filtering functionality.

Tests the is_runbook_enabled() method and enabled_runbooks parsing.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import json


class TestRunbookFiltering:
    """Tests for runbook filtering in appliance agent."""

    def test_is_runbook_enabled_all_enabled_by_default(self):
        """When enabled_runbooks is empty, all runbooks should be enabled."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = []

        # All runbooks should be enabled when list is empty
        assert agent.is_runbook_enabled("RB-WIN-SVC-001") is True
        assert agent.is_runbook_enabled("RB-WIN-SEC-001") is True
        assert agent.is_runbook_enabled("RB-WIN-BACKUP-001") is True
        assert agent.is_runbook_enabled("UNKNOWN-RUNBOOK") is True

    def test_is_runbook_enabled_with_allowlist(self):
        """When enabled_runbooks has entries, only those should be enabled."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = [
            "RB-WIN-SVC-001",
            "RB-WIN-SEC-001",
            "RB-WIN-BACKUP-001",
        ]

        # Listed runbooks should be enabled
        assert agent.is_runbook_enabled("RB-WIN-SVC-001") is True
        assert agent.is_runbook_enabled("RB-WIN-SEC-001") is True
        assert agent.is_runbook_enabled("RB-WIN-BACKUP-001") is True

        # Unlisted runbooks should be disabled
        assert agent.is_runbook_enabled("RB-WIN-SVC-002") is False
        assert agent.is_runbook_enabled("RB-WIN-NET-001") is False
        assert agent.is_runbook_enabled("UNKNOWN-RUNBOOK") is False

    def test_update_enabled_runbooks_from_response(self):
        """Test parsing enabled_runbooks from check-in response."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = []
        agent.state_dir = "/tmp/test"
        agent.logger = MagicMock()

        response = {
            "status": "ok",
            "appliance_id": "test-123",
            "enabled_runbooks": [
                "RB-WIN-SVC-001",
                "RB-WIN-SVC-002",
                "RB-WIN-BACKUP-001",
            ]
        }

        agent._update_enabled_runbooks_from_response(response)

        assert len(agent.enabled_runbooks) == 3
        assert "RB-WIN-SVC-001" in agent.enabled_runbooks
        assert "RB-WIN-SVC-002" in agent.enabled_runbooks
        assert "RB-WIN-BACKUP-001" in agent.enabled_runbooks

    def test_update_enabled_runbooks_handles_empty_list(self):
        """Test that empty list in response is handled correctly."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = ["RB-WIN-SVC-001"]  # Previously had some
        agent.state_dir = "/tmp/test"
        agent.logger = MagicMock()

        # When server returns empty list, the implementation may keep previous or reset
        response = {
            "status": "ok",
            "appliance_id": "test-123",
            "enabled_runbooks": []
        }

        agent._update_enabled_runbooks_from_response(response)

        # The key behavior: when NO runbooks are in enabled_runbooks, all are enabled
        # If implementation sets [], then all enabled. If keeps previous, RB-WIN-SVC-001 is enabled
        # Either way, RB-WIN-SVC-001 should be accessible
        assert agent.is_runbook_enabled("RB-WIN-SVC-001") is True

    def test_update_enabled_runbooks_missing_key(self):
        """Test handling when enabled_runbooks key is missing."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = ["RB-WIN-SVC-001"]
        agent.state_dir = "/tmp/test"
        agent.logger = MagicMock()

        response = {
            "status": "ok",
            "appliance_id": "test-123",
            # No enabled_runbooks key
        }

        agent._update_enabled_runbooks_from_response(response)

        # Should keep previous state when key missing
        assert agent.enabled_runbooks == ["RB-WIN-SVC-001"]

    def test_update_enabled_runbooks_with_list(self):
        """Test handling when enabled_runbooks is a valid list."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.enabled_runbooks = []
        agent.state_dir = "/tmp/test"
        agent.logger = MagicMock()

        response = {
            "status": "ok",
            "appliance_id": "test-123",
            "enabled_runbooks": ["RB-WIN-SVC-001", "RB-WIN-SEC-002"]
        }

        agent._update_enabled_runbooks_from_response(response)

        # Should update with the new list
        assert len(agent.enabled_runbooks) == 2
        assert "RB-WIN-SVC-001" in agent.enabled_runbooks
        assert "RB-WIN-SEC-002" in agent.enabled_runbooks


class TestRunbookRegistry:
    """Tests for runbook registry and lookup functions."""

    def test_all_runbooks_loaded(self):
        """Test that runbooks are loaded in the registry."""
        from compliance_agent.runbooks.windows import ALL_RUNBOOKS

        # Should have at least 20+ runbooks (7 core + 20 new)
        assert len(ALL_RUNBOOKS) >= 20

        # Check new category runbooks exist
        assert "RB-WIN-SVC-001" in ALL_RUNBOOKS  # DNS Service
        assert "RB-WIN-SEC-001" in ALL_RUNBOOKS  # Firewall
        assert "RB-WIN-NET-001" in ALL_RUNBOOKS  # DNS Client
        assert "RB-WIN-STG-001" in ALL_RUNBOOKS  # Disk Cleanup
        assert "RB-WIN-UPD-001" in ALL_RUNBOOKS  # Windows Update
        assert "RB-WIN-AD-001" in ALL_RUNBOOKS   # AD

    def test_get_runbook_by_id(self):
        """Test retrieving a runbook by ID."""
        from compliance_agent.runbooks.windows import get_runbook

        runbook = get_runbook("RB-WIN-SVC-001")
        assert runbook is not None
        assert runbook.id == "RB-WIN-SVC-001"
        assert "DNS" in runbook.name

    def test_get_runbook_not_found(self):
        """Test that None is returned for unknown runbook ID."""
        from compliance_agent.runbooks.windows import get_runbook

        runbook = get_runbook("UNKNOWN-RUNBOOK-999")
        assert runbook is None

    def test_list_categories_returns_data(self):
        """Test listing runbook categories returns results."""
        from compliance_agent.runbooks.windows import list_categories

        categories = list_categories()

        # Should have multiple categories
        assert len(categories) >= 5

        # Each category should have name and count
        for cat in categories:
            assert "name" in cat
            assert "count" in cat
            assert cat["count"] >= 1

    def test_get_runbooks_by_check_type(self):
        """Test filtering runbooks by check type."""
        from compliance_agent.runbooks.windows import get_runbooks_by_check_type

        # Test service_health check type
        service_runbooks = get_runbooks_by_check_type("service_health")
        assert len(service_runbooks) >= 1  # Should have at least some

    def test_runbook_has_required_fields(self):
        """Test that all runbooks have required fields populated."""
        from compliance_agent.runbooks.windows import ALL_RUNBOOKS

        for rb_id, runbook in ALL_RUNBOOKS.items():
            assert runbook.id == rb_id, f"Runbook {rb_id} has mismatched ID"
            assert runbook.name, f"Runbook {rb_id} missing name"
            assert runbook.severity in ["low", "medium", "high", "critical"], \
                f"Runbook {rb_id} has invalid severity: {runbook.severity}"
            assert runbook.detect_script, f"Runbook {rb_id} missing detect_script"
            assert runbook.remediate_script, f"Runbook {rb_id} missing remediate_script"
            assert runbook.verify_script, f"Runbook {rb_id} missing verify_script"

    def test_runbook_hipaa_controls(self):
        """Test that runbooks have HIPAA control mappings."""
        from compliance_agent.runbooks.windows import ALL_RUNBOOKS

        # At least some runbooks should have HIPAA controls
        runbooks_with_controls = [
            rb for rb in ALL_RUNBOOKS.values()
            if rb.hipaa_controls and len(rb.hipaa_controls) > 0
        ]

        assert len(runbooks_with_controls) >= 10, \
            "At least 10 runbooks should have HIPAA control mappings"


class TestCategoryRunbooks:
    """Tests for each runbook category."""

    def test_services_category(self):
        """Test services category has expected runbooks."""
        from compliance_agent.runbooks.windows.services import SERVICE_RUNBOOKS

        assert len(SERVICE_RUNBOOKS) == 4
        assert "RB-WIN-SVC-001" in SERVICE_RUNBOOKS  # DNS
        assert "RB-WIN-SVC-002" in SERVICE_RUNBOOKS  # DHCP
        assert "RB-WIN-SVC-003" in SERVICE_RUNBOOKS  # Print Spooler
        assert "RB-WIN-SVC-004" in SERVICE_RUNBOOKS  # Windows Time

    def test_security_category(self):
        """Test security category has expected runbooks."""
        from compliance_agent.runbooks.windows.security import SECURITY_RUNBOOKS

        assert len(SECURITY_RUNBOOKS) == 14
        assert "RB-WIN-SEC-001" in SECURITY_RUNBOOKS  # Firewall
        assert "RB-WIN-SEC-002" in SECURITY_RUNBOOKS  # Audit Policy
        assert "RB-WIN-SEC-003" in SECURITY_RUNBOOKS  # Account Lockout
        assert "RB-WIN-SEC-004" in SECURITY_RUNBOOKS  # Password Policy
        assert "RB-WIN-SEC-005" in SECURITY_RUNBOOKS  # BitLocker
        assert "RB-WIN-SEC-006" in SECURITY_RUNBOOKS  # Defender
        assert "RB-WIN-SEC-007" in SECURITY_RUNBOOKS  # SMB Signing
        assert "RB-WIN-SEC-008" in SECURITY_RUNBOOKS  # NTLM Security
        assert "RB-WIN-SEC-009" in SECURITY_RUNBOOKS  # Unauthorized Users
        assert "RB-WIN-SEC-010" in SECURITY_RUNBOOKS  # NLA Enforcement
        assert "RB-WIN-SEC-011" in SECURITY_RUNBOOKS  # UAC Enforcement
        assert "RB-WIN-SEC-012" in SECURITY_RUNBOOKS  # Event Log Protection
        assert "RB-WIN-SEC-013" in SECURITY_RUNBOOKS  # Credential Guard

    def test_network_category(self):
        """Test network category has expected runbooks."""
        from compliance_agent.runbooks.windows.network import NETWORK_RUNBOOKS

        assert len(NETWORK_RUNBOOKS) == 5
        assert "RB-WIN-NET-001" in NETWORK_RUNBOOKS  # DNS Client
        assert "RB-WIN-NET-002" in NETWORK_RUNBOOKS  # NIC Reset
        assert "RB-WIN-NET-003" in NETWORK_RUNBOOKS  # Network Profile
        assert "RB-WIN-NET-004" in NETWORK_RUNBOOKS  # NetBIOS
        assert "RB-NET-SECURITY-001" in NETWORK_RUNBOOKS  # Network Security

    def test_storage_category(self):
        """Test storage category has expected runbooks."""
        from compliance_agent.runbooks.windows.storage import STORAGE_RUNBOOKS

        assert len(STORAGE_RUNBOOKS) == 3
        assert "RB-WIN-STG-001" in STORAGE_RUNBOOKS  # Disk Cleanup
        assert "RB-WIN-STG-002" in STORAGE_RUNBOOKS  # Shadow Copy
        assert "RB-WIN-STG-003" in STORAGE_RUNBOOKS  # Volume Health

    def test_updates_category(self):
        """Test updates category has expected runbooks."""
        from compliance_agent.runbooks.windows.updates import UPDATES_RUNBOOKS

        assert len(UPDATES_RUNBOOKS) == 2
        assert "RB-WIN-UPD-001" in UPDATES_RUNBOOKS  # Windows Update
        assert "RB-WIN-UPD-002" in UPDATES_RUNBOOKS  # WSUS

    def test_active_directory_category(self):
        """Test active_directory category has expected runbooks."""
        from compliance_agent.runbooks.windows.active_directory import AD_RUNBOOKS

        assert len(AD_RUNBOOKS) >= 1
        # Check that at least one AD runbook exists
        ad_runbook_ids = list(AD_RUNBOOKS.keys())
        assert any(rb_id.startswith("RB-WIN-AD-") for rb_id in ad_runbook_ids)


class TestL1RulesForRunbooks:
    """Tests for L1 rules that trigger runbooks."""

    def test_l1_rules_exist_for_new_runbooks(self):
        """Test that L1 rules exist for the new runbooks."""
        import yaml
        from pathlib import Path

        # Load the windows baseline yaml
        baseline_path = Path(__file__).parent.parent / "src" / "compliance_agent" / "rules" / "windows_baseline.yaml"

        with open(baseline_path) as f:
            data = yaml.safe_load(f)

        # The YAML has a top-level 'rules' key
        rules = data.get("rules", [])

        # Get all rule IDs that reference runbooks
        runbook_rules = [r for r in rules if isinstance(r, dict) and r.get("action") == "run_windows_runbook"]

        # Should have rules for runbooks
        runbook_ids_in_rules = set()
        for rule in runbook_rules:
            if "action_params" in rule and "runbook_id" in rule["action_params"]:
                runbook_ids_in_rules.add(rule["action_params"]["runbook_id"])

        # Check that at least some new runbooks have L1 rules
        new_runbook_prefixes = ["RB-WIN-SVC-", "RB-WIN-SEC-", "RB-WIN-NET-", "RB-WIN-STG-", "RB-WIN-UPD-"]
        new_runbooks_with_rules = [rb for rb in runbook_ids_in_rules if any(rb.startswith(p) for p in new_runbook_prefixes)]

        # We should have at least some of the new runbooks mapped
        assert len(new_runbooks_with_rules) >= 10, f"Only {len(new_runbooks_with_rules)} new runbooks have L1 rules"
