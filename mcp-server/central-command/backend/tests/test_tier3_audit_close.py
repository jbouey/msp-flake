"""Tests for Session 203 Tier 3 — close H1 / H6 / H8 / H7-partner.

Source-level checks across the four files touched in this batch:
  - PortalScorecard.tsx — H1 legacy badge + H6/H8 dynamic framework labels
  - portal.py — frameworks payload in /api/portal/site/{id}
  - partners.py — /api/partners/me/audit-log endpoint
  - PartnerAuditLog.tsx — partner self-service audit log page
"""

import ast
import os


_HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL_PY = os.path.normpath(os.path.join(_HERE, "..", "portal.py"))
PARTNERS_PY = os.path.normpath(os.path.join(_HERE, "..", "partners.py"))
SCORECARD_TSX = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "portal", "PortalScorecard.tsx")
)
PARTNER_AUDIT_LOG_TSX = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "partner", "PartnerAuditLog.tsx")
)
PARTNER_INDEX_TS = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "partner", "index.ts")
)
APP_TSX = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "App.tsx")
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _get_func(path: str, name: str) -> str:
    src = _load(path)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found in {path}")


# =============================================================================
# H1 — Chain status badge surfaces legacy ratio
# =============================================================================

class TestH1ChainStatusLegacyDisclosure:
    def test_legacy_count_computed(self):
        src = _load(SCORECARD_TSX)
        assert "legacyCount" in src
        assert "legacyPct" in src

    def test_chain_label_includes_signed_pct_when_legacy_present(self):
        src = _load(SCORECARD_TSX)
        # The badge label is built dynamically — when hasLegacy, the label
        # appends "% signed" so the badge cannot say only "Valid".
        assert "% signed" in src
        assert "hasLegacy" in src

    def test_chain_status_amber_when_legacy_present(self):
        """A green Valid badge while 22.6% is legacy is the bug we're
        fixing — when hasLegacy, the tone shifts to amber."""
        src = _load(SCORECARD_TSX)
        assert "text-amber-700" in src
        assert "chainTone" in src

    def test_legacy_warning_panel_links_merkle_disclosure(self):
        src = _load(SCORECARD_TSX)
        assert "SECURITY_ADVISORY_2026-04-09_MERKLE.md" in src
        assert "Merkle disclosure" in src

    def test_legacy_warning_panel_explains_legacy_label(self):
        src = _load(SCORECARD_TSX)
        assert "pre-Ed25519 legacy" in src
        # JSX line wrapping splits "honestly labeled" across two lines
        assert "honestly" in src
        assert "labeled" in src

    def test_legacy_panel_only_shows_when_legacy_exists(self):
        """Don't render the warning for sites with 100% signed bundles."""
        src = _load(SCORECARD_TSX)
        assert "{hasLegacy && (" in src


# =============================================================================
# H6 / H8 — Multi-framework display
# =============================================================================

class TestH6H8MultiFrameworkBackend:
    def test_portal_data_has_frameworks_field(self):
        src = _load(PORTAL_PY)
        assert "frameworks: PortalFrameworks" in src

    def test_portal_frameworks_model_defined(self):
        src = _load(PORTAL_PY)
        assert "class PortalFrameworks(BaseModel)" in src
        assert "primary:" in src
        assert "primary_label:" in src
        assert "enabled:" in src
        assert "enabled_labels:" in src

    def test_get_site_framework_info_helper_exists(self):
        src = _load(PORTAL_PY)
        assert "async def _get_site_framework_info" in src

    def test_helper_queries_appliance_framework_configs_table(self):
        body = _get_func(PORTAL_PY, "_get_site_framework_info")
        assert "appliance_framework_configs" in body

    def test_helper_defaults_to_hipaa_when_no_config(self):
        body = _get_func(PORTAL_PY, "_get_site_framework_info")
        assert '"hipaa"' in body
        assert "HIPAA" in body

    def test_helper_includes_all_9_framework_labels(self):
        src = _load(PORTAL_PY)
        for code in ("hipaa", "soc2", "pci_dss", "nist_csf", "cis", "sox", "gdpr", "cmmc", "iso_27001"):
            assert f'"{code}"' in src

    def test_get_portal_data_calls_framework_helper(self):
        body = _get_func(PORTAL_PY, "get_portal_data")
        assert "_get_site_framework_info" in body
        assert "frameworks=framework_info" in body


class TestH6H8MultiFrameworkFrontend:
    def test_portal_data_interface_has_frameworks_optional(self):
        src = _load(SCORECARD_TSX)
        assert "frameworks?: PortalFrameworks" in src

    def test_portal_frameworks_interface_defined(self):
        src = _load(SCORECARD_TSX)
        assert "interface PortalFrameworks" in src
        assert "primary_label" in src

    def test_hero_title_uses_framework_label(self):
        src = _load(SCORECARD_TSX)
        assert "{frameworkLabel}-relevant controls" in src

    def test_auditor_section_uses_framework_label(self):
        src = _load(SCORECARD_TSX)
        assert "Full {frameworkLabel} control mapping" in src
        assert "{frameworkLabel} Control Mapping" in src

    def test_auditor_table_column_label_dynamic(self):
        src = _load(SCORECARD_TSX)
        assert "codeColumnLabel" in src
        assert "SOC 2 Trust Service" in src
        assert "PCI DSS Req" in src

    def test_multi_framework_chip_list_renders(self):
        """When the site has more than one enabled framework, show a chip
        list so the user understands the scorecard covers multiple."""
        src = _load(SCORECARD_TSX)
        assert "isMultiFramework" in src
        assert "enabledLabels" in src

    def test_header_summary_uses_framework_label(self):
        src = _load(SCORECARD_TSX)
        # Search for the header summary line
        assert "data.frameworks?.primary_label" in src


# =============================================================================
# H7-partner — Partner self-service audit log
# =============================================================================

class TestH7PartnerAuditLogBackend:
    def test_endpoint_registered(self):
        src = _load(PARTNERS_PY)
        assert '@router.get("/me/audit-log")' in src

    def test_endpoint_handler_uses_self_service_role(self):
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        assert 'require_partner_role("admin", "tech", "billing")' in body

    def test_endpoint_filters_by_partner_id(self):
        """The whole point — partner sees only their own activity."""
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        assert "partner_id=str(partner['id'])" in body

    def test_endpoint_supports_category_filter(self):
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        assert "event_category" in body

    def test_endpoint_supports_lookback_window(self):
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        assert "days:" in body
        assert "le=2555" in body  # 7-year HIPAA retention max

    def test_endpoint_returns_total_for_pagination(self):
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        assert '"total"' in body

    def test_endpoint_response_shape(self):
        body = _get_func(PARTNERS_PY, "get_my_audit_log")
        for key in ('"events"', '"limit"', '"offset"', '"days_lookback"', '"category_filter"'):
            assert key in body


class TestH7PartnerAuditLogFrontend:
    def test_page_file_exists(self):
        assert os.path.isfile(PARTNER_AUDIT_LOG_TSX)

    def test_page_exported_from_index(self):
        src = _load(PARTNER_INDEX_TS)
        assert "export { PartnerAuditLog }" in src

    def test_route_registered_in_app(self):
        src = _load(APP_TSX)
        assert "PartnerAuditLog" in src
        assert 'path="audit-log"' in src

    def test_page_calls_self_service_endpoint(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "/api/partners/me/audit-log" in src

    def test_page_has_category_filter(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "CATEGORY_OPTIONS" in src
        for cat in ("auth", "admin", "site", "credential"):
            assert cat in src

    def test_page_has_lookback_options_up_to_7_years(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "2555" in src
        assert "HIPAA max" in src

    def test_page_offers_csv_export(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "exportCsv" in src
        assert "Export CSV" in src

    def test_page_has_event_label_humanizer(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "EVENT_LABELS" in src
        assert "humanizeEvent" in src

    def test_page_references_hipaa_section(self):
        """The user-facing description must reference §164.528 + retention
        so the partner knows what regulation this view satisfies."""
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "164.528" in src
        assert "retention" in src.lower()

    def test_page_uses_partner_context(self):
        src = _load(PARTNER_AUDIT_LOG_TSX)
        assert "usePartner" in src
