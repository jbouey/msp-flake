"""Adversarial attestation audit — source-level regression tests.

Verifies the attestation system under adversarial conditions:
- PHI scrubbing at every egress path
- DELETE protection on evidence + audit tables (migration 151)
- Client audit log completeness for security-relevant mutations
- Fleet order immutability after completion
- Retention policy compliance (HIPAA 7-year minimum)
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
NETSCAN_GO = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "appliance", "internal", "daemon", "netscan.go")
)
DAEMON_GO = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "appliance", "internal", "daemon", "daemon.go")
)
SUBMITTER_GO = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "appliance", "internal", "evidence", "submitter.go")
)
EVIDENCE_CHAIN_PY = os.path.normpath(os.path.join(_HERE, "..", "evidence_chain.py"))
CLIENT_PORTAL_PY = os.path.normpath(os.path.join(_HERE, "..", "client_portal.py"))
ALERT_ROUTER_PY = os.path.normpath(os.path.join(_HERE, "..", "alert_router.py"))
MIGRATION_151 = os.path.normpath(
    os.path.join(_HERE, "..", "migrations", "151_evidence_delete_protection.sql")
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


# =============================================================================
# PHI EGRESS: Every path must scrub before leaving the appliance
# =============================================================================

class TestPHIEgressScrubbing:
    """Verify all appliance → Central Command paths use phiscrub."""

    def test_device_sync_hostname_scrubbed(self):
        """netscan.go: device sync entries must scrub hostname."""
        src = _load(NETSCAN_GO)
        # The deviceSyncEntry Hostname field must use phiscrub.Scrub()
        assert re.search(r'Hostname:\s+phiscrub\.Scrub\(', src), (
            "deviceSyncEntry.Hostname must be wrapped in phiscrub.Scrub()"
        )

    def test_deploy_result_hostname_scrubbed(self):
        """daemon.go: deploy results must scrub hostname before checkin."""
        src = _load(DAEMON_GO)
        assert re.search(r'dr\[i\]\.Hostname\s*=\s*phiscrub\.Scrub\(', src), (
            "DeployResult.Hostname must be scrubbed before checkin egress"
        )

    def test_deploy_result_error_scrubbed(self):
        """daemon.go: deploy result errors must be scrubbed (may contain hostnames)."""
        src = _load(DAEMON_GO)
        assert re.search(r'dr\[i\]\.Error\s*=\s*phiscrub\.Scrub\(', src), (
            "DeployResult.Error must be scrubbed before checkin egress"
        )

    def test_evidence_hostname_scrubbed(self):
        """submitter.go: evidence bundle hostnames must be scrubbed."""
        src = _load(SUBMITTER_GO)
        assert "phiscrub.Scrub(host)" in src or "phiscrub.Scrub(scrubbedHost)" in src or \
               re.search(r'phiscrub\.Scrub\(\w*[Hh]ost', src), (
            "Evidence bundle hostnames must use phiscrub.Scrub()"
        )


# =============================================================================
# DELETE PROTECTION: Evidence + audit tables must block DELETE
# =============================================================================

class TestDeleteProtection:
    """Migration 151 must add DELETE triggers to critical tables."""

    def test_migration_exists(self):
        """Migration 151 must exist."""
        assert os.path.exists(MIGRATION_151), (
            "Migration 151 (evidence DELETE protection) is missing"
        )

    def test_compliance_bundles_delete_trigger(self):
        """compliance_bundles must have a DELETE trigger."""
        sql = _load(MIGRATION_151)
        assert "compliance_bundles_no_delete" in sql, (
            "Migration 151 must create compliance_bundles_no_delete trigger"
        )

    def test_admin_audit_log_delete_trigger(self):
        """admin_audit_log must have a DELETE trigger."""
        sql = _load(MIGRATION_151)
        assert "admin_audit_log_no_delete" in sql, (
            "Migration 151 must create admin_audit_log_no_delete trigger"
        )

    def test_client_audit_log_delete_trigger(self):
        """client_audit_log must have a DELETE trigger."""
        sql = _load(MIGRATION_151)
        assert "client_audit_log_no_delete" in sql, (
            "Migration 151 must create client_audit_log_no_delete trigger"
        )

    def test_remediation_steps_delete_trigger(self):
        """incident_remediation_steps must have a DELETE trigger."""
        sql = _load(MIGRATION_151)
        assert "remediation_steps_no_delete" in sql, (
            "Migration 151 must create remediation_steps_no_delete trigger"
        )

    def test_fleet_orders_immutability(self):
        """fleet_orders must block UPDATE on completed orders."""
        sql = _load(MIGRATION_151)
        assert "fleet_orders_immutable_completed" in sql, (
            "Migration 151 must create fleet_orders_immutable_completed trigger"
        )

    def test_prevent_deletion_function(self):
        """The prevent_audit_deletion() function must raise an exception."""
        sql = _load(MIGRATION_151)
        assert "RAISE EXCEPTION" in sql, (
            "prevent_audit_deletion() must raise on DELETE attempt"
        )
        assert "append-only" in sql.lower() or "immutable" in sql.lower(), (
            "Exception message must reference immutability"
        )


# =============================================================================
# CLIENT AUDIT LOG: Security-relevant mutations must be logged
# =============================================================================

class TestClientAuditCompleteness:
    """All security-relevant client mutations must call _audit_client_action()."""

    def test_magic_link_login_audited(self):
        """Magic link login must produce an audit entry."""
        src = _load(CLIENT_PORTAL_PY)
        assert "MAGIC_LINK_LOGIN" in src, (
            "Magic link login must be audited with action=MAGIC_LINK_LOGIN"
        )

    def test_notification_read_audited(self):
        """Notification read must produce an audit entry."""
        src = _load(CLIENT_PORTAL_PY)
        assert "NOTIFICATION_READ" in src, (
            "Notification read must be audited with action=NOTIFICATION_READ"
        )

    def test_notifications_read_all_audited(self):
        """Bulk notification read must produce an audit entry."""
        src = _load(CLIENT_PORTAL_PY)
        assert "NOTIFICATIONS_READ_ALL" in src, (
            "Bulk notification read must be audited with action=NOTIFICATIONS_READ_ALL"
        )


# =============================================================================
# RETENTION POLICY: Must be HIPAA-compliant (7-year minimum)
# =============================================================================

class TestRetentionPolicy:
    """Audit log retention must comply with HIPAA 164.316(b)(2)(i)."""

    def test_no_3_year_deletion(self):
        """alert_router.py must NOT delete audit logs at 3 years (1095 days)."""
        src = _load(ALERT_ROUTER_PY)
        assert "1095 days" not in src or "removed" in src.lower(), (
            "3-year retention policy (1095 days) is non-compliant with HIPAA 7-year minimum"
        )
        # The DELETE statement should be gone
        assert "DELETE FROM admin_audit_log" not in src, (
            "Automated DELETE from admin_audit_log removed — triggers enforce immutability"
        )
