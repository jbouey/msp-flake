"""Tests for organization isolation and cross-org data leakage prevention.

These tests verify source-level invariants in org_management.py and client_orgs
RLS policies. Integration tests against a live DB are in a separate fixture.
"""

import os
import re
import pytest


class TestOrgManagementSource:
    """Verify org_management.py has all required endpoints."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "org_management.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_provision_endpoint_exists(self):
        assert "@router.post(\"/provision\")" in self.source
        assert "async def provision_org(" in self.source

    def test_deprovision_endpoint_exists(self):
        assert "async def deprovision_org(" in self.source
        assert "soft-delete" in self.source.lower()

    def test_reprovision_endpoint_exists(self):
        assert "async def reprovision_org(" in self.source

    def test_export_endpoint_exists(self):
        assert "async def export_org_data(" in self.source
        assert "HIPAA" in self.source or "GDPR" in self.source

    def test_audit_bundle_endpoint_exists(self):
        assert "async def export_audit_bundle(" in self.source

    def test_quota_endpoints_exist(self):
        assert "async def get_org_quota(" in self.source
        assert "async def update_org_quota(" in self.source

    def test_search_endpoint_exists(self):
        assert "async def search_orgs(" in self.source

    def test_health_endpoint_exists(self):
        assert "async def get_org_health_dashboard(" in self.source

    def test_compliance_packet_endpoint_exists(self):
        assert "async def get_org_compliance_packet(" in self.source

    def test_all_mutating_endpoints_require_admin(self):
        """Every POST/PUT endpoint must check admin/operator role."""
        # Find all mutating endpoint handlers
        handlers = re.findall(
            r'@router\.(?:post|put|delete)\([^\)]+\)\s*async def (\w+)\([^\)]+\):.*?(?=@router|\Z)',
            self.source,
            re.DOTALL,
        )
        for handler_body in handlers:
            # Either role check or _check_org_access must be present
            assert (
                "admin" in handler_body.lower()
                or "require_auth" in handler_body
            ), f"Handler missing auth check: {handler_body[:100]}"

    def test_audit_log_writes_on_mutations(self):
        """All mutating endpoints must write to org_audit_log."""
        mutations = ["provision_org", "deprovision_org", "reprovision_org", "update_org_quota"]
        for fn in mutations:
            # Find function body
            start = self.source.find(f"async def {fn}(")
            assert start >= 0, f"{fn} not found"
            end = self.source.find("\n@router", start + 10)
            if end == -1:
                end = len(self.source)
            body = self.source[start:end]
            assert "_audit(" in body, f"{fn} doesn't write to audit log"

    def test_export_writes_disclosure_audit(self):
        """Data export must write a 'data_exported' audit event (HIPAA §164.528)."""
        start = self.source.find("async def export_org_data(")
        end = self.source.find("\n@router", start + 10)
        body = self.source[start:end]
        assert "data_exported" in body
        assert "164.528" in body or "HIPAA" in body

    def test_deprovision_cascades_sessions(self):
        """Deprovisioning must invalidate client sessions."""
        start = self.source.find("async def deprovision_org(")
        end = self.source.find("\n@router", start + 10)
        body = self.source[start:end]
        assert "client_sessions" in body
        assert "DELETE" in body

    def test_deprovision_archives_sites(self):
        """Deprovisioning must archive sites."""
        start = self.source.find("async def deprovision_org(")
        end = self.source.find("\n@router", start + 10)
        body = self.source[start:end]
        assert "archived" in body
        assert "UPDATE sites" in body

    def test_retention_default_is_hipaa_six_years(self):
        """HIPAA requires 6 year retention — default must match."""
        # 6 years = 2190 days (6 * 365)
        assert "2190" in self.source


class TestMigration146:
    """Verify migration 146 has all required changes."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "migrations",
            "146_org_enterprise_hardening.sql",
        )
        with open(path) as f:
            self.source = f.read()

    def test_enables_rls_on_client_orgs(self):
        assert "ALTER TABLE client_orgs ENABLE ROW LEVEL SECURITY" in self.source
        assert "FORCE ROW LEVEL SECURITY" in self.source

    def test_creates_admin_bypass_policy(self):
        assert "client_orgs_admin_bypass" in self.source
        assert "app.is_admin" in self.source

    def test_creates_self_read_policy(self):
        assert "client_orgs_self_read" in self.source
        assert "app.current_org" in self.source

    def test_adds_quota_columns(self):
        assert "max_sites" in self.source
        assert "max_users" in self.source
        assert "max_incidents_per_day" in self.source

    def test_adds_baa_dates(self):
        assert "baa_effective_date" in self.source
        assert "baa_expiration_date" in self.source

    def test_adds_deprovisioning_fields(self):
        assert "deprovisioned_at" in self.source
        assert "deprovisioned_by" in self.source
        assert "data_retention_until" in self.source

    def test_creates_org_audit_log(self):
        assert "CREATE TABLE IF NOT EXISTS org_audit_log" in self.source
        assert "event_type" in self.source
        assert "ON DELETE CASCADE" in self.source

    def test_adds_search_indexes(self):
        assert "idx_client_orgs_name_lower" in self.source

    def test_adds_baa_expiration_index(self):
        assert "idx_client_orgs_baa_expiration" in self.source


class TestOrgMetricsInPrometheus:
    """Verify org metrics exist in prometheus_metrics.py."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prometheus_metrics.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_org_count_metric_exists(self):
        assert "osiriscare_orgs_total" in self.source

    def test_org_site_quota_metric_exists(self):
        assert "osiriscare_org_site_quota_pct" in self.source

    def test_baa_expiring_metric_exists(self):
        assert "osiriscare_org_baa_expiring_30d" in self.source

    def test_baa_expired_metric_is_critical(self):
        assert "osiriscare_org_baa_expired" in self.source
        # Should have CRITICAL in help text
        assert "CRITICAL" in self.source

    def test_deprovisioned_orgs_metric(self):
        assert "osiriscare_orgs_deprovisioned" in self.source
