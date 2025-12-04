"""
Tests for approval policy module.
"""

import pytest
from pathlib import Path
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from compliance_agent.approval import (
    ActionCategory,
    ApprovalStatus,
    ACTION_POLICIES,
    ApprovalRequest,
    ApprovalManager,
    get_action_policy
)


@pytest.fixture
def approval_db(tmp_path):
    """Create temporary approval database."""
    db_path = tmp_path / "approvals.db"
    return ApprovalManager(db_path)


class TestActionPolicies:
    """Test action policy definitions."""

    def test_policies_exist(self):
        """Test that expected actions have policies."""
        expected_actions = [
            "update_to_baseline_generation",
            "restart_av_service",
            "run_backup_job",
            "restart_logging_services",
            "restore_firewall_baseline",
            "enable_volume_encryption",
            "enable_bitlocker",
            "apply_windows_updates"
        ]
        for action in expected_actions:
            assert action in ACTION_POLICIES, f"Missing policy for {action}"

    def test_disruptive_actions_require_approval(self):
        """Test that disruptive actions require approval."""
        disruptive_actions = [
            "update_to_baseline_generation",
            "restore_firewall_baseline",
            "enable_bitlocker",
            "apply_windows_updates"
        ]
        for action in disruptive_actions:
            policy = ACTION_POLICIES[action]
            assert policy["requires_approval"] is True

    def test_encryption_never_auto_approves(self):
        """Test that encryption actions never auto-approve."""
        encryption_actions = ["enable_volume_encryption", "enable_bitlocker"]
        for action in encryption_actions:
            policy = ACTION_POLICIES[action]
            assert policy["auto_approve_in_maintenance"] is False

    def test_non_disruptive_actions(self):
        """Test that non-disruptive actions don't require approval."""
        non_disruptive = ["restart_av_service", "run_backup_job", "restart_logging_services"]
        for action in non_disruptive:
            policy = ACTION_POLICIES[action]
            assert policy["requires_approval"] is False

    def test_get_action_policy_known(self):
        """Test getting policy for known action."""
        policy = get_action_policy("restart_av_service")
        assert policy["category"] == ActionCategory.SERVICE_RESTART
        assert policy["requires_approval"] is False

    def test_get_action_policy_unknown(self):
        """Test getting policy for unknown action returns safe default."""
        policy = get_action_policy("unknown_dangerous_action")
        assert policy["requires_approval"] is True
        assert policy["risk_level"] == "high"


class TestApprovalManager:
    """Test ApprovalManager class."""

    def test_init_creates_db(self, tmp_path):
        """Test database is created on init."""
        db_path = tmp_path / "test_approvals.db"
        assert not db_path.exists()

        manager = ApprovalManager(db_path)
        assert db_path.exists()

    def test_init_creates_nested_dirs(self, tmp_path):
        """Test nested directories are created."""
        db_path = tmp_path / "nested" / "deep" / "approvals.db"
        manager = ApprovalManager(db_path)
        assert db_path.exists()

    def test_requires_approval_known_action(self, approval_db):
        """Test requires_approval for known action."""
        # Disruptive - requires approval
        assert approval_db.requires_approval("update_to_baseline_generation") is True

        # Non-disruptive - no approval needed
        assert approval_db.requires_approval("restart_av_service") is False

    def test_requires_approval_unknown_action(self, approval_db):
        """Test requires_approval for unknown action (safe default)."""
        assert approval_db.requires_approval("unknown_action") is True

    def test_requires_approval_maintenance_window(self, approval_db):
        """Test auto-approve in maintenance window."""
        # Normally requires approval
        assert approval_db.requires_approval("update_to_baseline_generation") is True

        # Auto-approve in maintenance
        assert approval_db.requires_approval(
            "update_to_baseline_generation",
            in_maintenance_window=True
        ) is False

        # Encryption never auto-approves
        assert approval_db.requires_approval(
            "enable_bitlocker",
            in_maintenance_window=True
        ) is True

    def test_create_request(self, approval_db):
        """Test creating approval request."""
        request = approval_db.create_request(
            action_name="update_to_baseline_generation",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={"generation": 123}
        )

        assert request.request_id.startswith("APR-")
        assert request.action_name == "update_to_baseline_generation"
        assert request.status == "pending"
        assert request.site_id == "site-001"
        assert request.pre_state == {"generation": 123}

    def test_get_request(self, approval_db):
        """Test getting request by ID."""
        created = approval_db.create_request(
            action_name="enable_bitlocker",
            drift_check="encryption",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        retrieved = approval_db.get_request(created.request_id)
        assert retrieved is not None
        assert retrieved.request_id == created.request_id
        assert retrieved.action_name == "enable_bitlocker"

    def test_get_request_not_found(self, approval_db):
        """Test getting non-existent request."""
        result = approval_db.get_request("APR-nonexistent")
        assert result is None

    def test_approve_request(self, approval_db):
        """Test approving request."""
        created = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        approved = approval_db.approve(created.request_id, approved_by="admin@clinic.com")

        assert approved is not None
        assert approved.status == "approved"
        assert approved.approved_by == "admin@clinic.com"
        assert approved.approved_at is not None

    def test_approve_already_approved(self, approval_db):
        """Test approving already approved request."""
        created = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        # First approval succeeds
        approval_db.approve(created.request_id, approved_by="admin@clinic.com")

        # Second approval fails
        result = approval_db.approve(created.request_id, approved_by="other@clinic.com")
        assert result is None

    def test_reject_request(self, approval_db):
        """Test rejecting request."""
        created = approval_db.create_request(
            action_name="restore_firewall_baseline",
            drift_check="firewall",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        rejected = approval_db.reject(
            created.request_id,
            rejected_by="admin@clinic.com",
            reason="Not in change window"
        )

        assert rejected is not None
        assert rejected.status == "rejected"
        assert rejected.rejection_reason == "Not in change window"

    def test_is_approved(self, approval_db):
        """Test is_approved check."""
        created = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        assert approval_db.is_approved(created.request_id) is False

        approval_db.approve(created.request_id, approved_by="admin")

        assert approval_db.is_approved(created.request_id) is True

    def test_get_pending(self, approval_db):
        """Test getting pending requests."""
        # Create multiple requests
        for i in range(3):
            approval_db.create_request(
                action_name="apply_windows_updates",
                drift_check="patching",
                site_id="site-001",
                host_id=f"host-{i:03d}",
                pre_state={}
            )

        pending = approval_db.get_pending()
        assert len(pending) == 3
        assert all(r.status == "pending" for r in pending)

    def test_get_pending_by_site(self, approval_db):
        """Test filtering pending by site."""
        # Create requests for different sites
        approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )
        approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-002",
            host_id="host-002",
            pre_state={}
        )

        site1_pending = approval_db.get_pending(site_id="site-001")
        assert len(site1_pending) == 1
        assert site1_pending[0].site_id == "site-001"

    def test_expire_old_requests(self, approval_db):
        """Test expiring old requests."""
        # Create request with short expiry
        request = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={},
            expires_hours=0  # Already expired
        )

        # Mock time to be in the future
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch('compliance_agent.approval.datetime') as mock_datetime:
            mock_datetime.now.return_value = future
            mock_datetime.fromisoformat = datetime.fromisoformat

            expired_count = approval_db.expire_old_requests()

        assert expired_count == 1

        # Verify status changed
        updated = approval_db.get_request(request.request_id)
        assert updated.status == "expired"

    def test_approve_expired_request(self, approval_db):
        """Test approving expired request fails."""
        request = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={},
            expires_hours=0  # Already expired
        )

        # Try to approve after expiry
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch('compliance_agent.approval.datetime') as mock_datetime:
            mock_datetime.now.return_value = future
            mock_datetime.fromisoformat = datetime.fromisoformat

            result = approval_db.approve(request.request_id, approved_by="admin")

        assert result is None

    def test_audit_log(self, approval_db):
        """Test audit log entries."""
        request = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )

        approval_db.approve(request.request_id, approved_by="admin@clinic.com")

        log = approval_db.get_audit_log(request_id=request.request_id)
        assert len(log) == 2  # created + approved

        actions = [entry['action'] for entry in log]
        assert 'created' in actions
        assert 'approved' in actions

    def test_get_stats(self, approval_db):
        """Test getting approval statistics."""
        # Create various requests
        req1 = approval_db.create_request(
            action_name="apply_windows_updates",
            drift_check="patching",
            site_id="site-001",
            host_id="host-001",
            pre_state={}
        )
        req2 = approval_db.create_request(
            action_name="enable_bitlocker",
            drift_check="encryption",
            site_id="site-001",
            host_id="host-002",
            pre_state={}
        )

        approval_db.approve(req1.request_id, approved_by="admin")
        approval_db.reject(req2.request_id, rejected_by="admin", reason="test")

        stats = approval_db.get_stats()
        assert "by_status" in stats
        assert "by_action" in stats
        assert stats["by_status"].get("approved", 0) == 1
        assert stats["by_status"].get("rejected", 0) == 1


class TestApprovalRequest:
    """Test ApprovalRequest dataclass."""

    def test_to_dict(self):
        """Test converting request to dict."""
        request = ApprovalRequest(
            request_id="APR-test",
            action_name="test_action",
            drift_check="test",
            site_id="site-001",
            host_id="host-001",
            category="disruptive",
            description="Test action",
            risk_level="high",
            pre_state={"key": "value"},
            created_at="2025-01-01T00:00:00+00:00",
            expires_at="2025-01-02T00:00:00+00:00"
        )

        d = request.to_dict()
        assert d["request_id"] == "APR-test"
        assert d["action_name"] == "test_action"
        assert d["pre_state"] == {"key": "value"}
