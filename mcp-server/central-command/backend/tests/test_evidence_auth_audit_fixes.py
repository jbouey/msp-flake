"""Tests for Session 203 audit-proof-display legal-emergency fixes.

Covers the Batch-1 fixes from the round-table audit:

  C2 — chain-of-custody SQL column names (prev_hash / agent_signature)
  C3 — auth guard on /verify-chain, /bundles, /blockchain-status
  C6 — audit_report.py SQL column fixes (client_org_id, signature_valid,
        compliance_packets.site_id, bundle_id varchar join)
  C7 — forbidden legal language stripped from portal frontend

Source-level verification — matches the project's test_site_activity_audit.py
idiom for endpoints whose runtime verification requires a live DB fixture
the main test suite doesn't stand up.
"""

import ast
import os


EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evidence_chain.py",
)
AUDIT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "audit_report.py",
)
PARTNERS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "partners.py",
)
FRONTEND_SCORECARD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "frontend", "src", "portal", "PortalScorecard.tsx",
)
FRONTEND_VERIFY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "frontend", "src", "portal", "PortalVerify.tsx",
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


# =============================================================================
# C3 — Auth guard on evidence endpoints
# =============================================================================

class TestC3EvidenceAuth:
    def test_require_evidence_view_access_exists(self):
        src = _load(EVIDENCE)
        assert "async def require_evidence_view_access(" in src

    def test_guard_accepts_admin_session(self):
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "require_evidence_view_access":
                body = ast.get_source_segment(src, node) or ""
                assert "require_auth" in body
                return
        assert False, "require_evidence_view_access not found"

    def test_guard_accepts_portal_session(self):
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "require_evidence_view_access":
                body = ast.get_source_segment(src, node) or ""
                # Falls through to portal.validate_session
                assert "validate_session" in body or "validate_portal_session" in body
                return
        assert False

    def test_verify_chain_has_auth_dep(self):
        src = _load(EVIDENCE)
        # Find the verify_chain_integrity function signature block
        idx = src.find("async def verify_chain_integrity(")
        assert idx != -1
        # Read ~400 chars of signature to catch multi-line dep list
        block = src[idx : idx + 600]
        assert "require_evidence_view_access" in block

    def test_list_bundles_has_auth_dep(self):
        src = _load(EVIDENCE)
        idx = src.find("async def list_evidence_bundles(")
        assert idx != -1
        block = src[idx : idx + 600]
        assert "require_evidence_view_access" in block

    def test_blockchain_status_has_auth_dep(self):
        src = _load(EVIDENCE)
        idx = src.find("async def get_blockchain_status(")
        assert idx != -1
        block = src[idx : idx + 600]
        assert "require_evidence_view_access" in block

    def test_guard_fails_closed(self):
        """When neither admin nor portal auth succeeds, must 403."""
        src = _load(EVIDENCE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "require_evidence_view_access":
                body = ast.get_source_segment(src, node) or ""
                assert "status_code=403" in body
                return
        assert False


# =============================================================================
# C2 — Chain-of-custody column-name fix
# =============================================================================

class TestC2ChainOfCustodyColumns:
    def test_uses_prev_hash_not_prev_bundle_hash(self):
        src = _load(EVIDENCE)
        # The chain-of-custody export is the ONLY place that used the wrong
        # name. Any remaining reference to prev_bundle_hash outside comments
        # is a regression.
        idx = src.find("async def export_chain_of_custody(")
        if idx == -1:
            # Function may have been renamed — just scan the SQL strings
            idx = src.find('"chain_of_custody":')
        assert idx != -1
        block = src[idx : idx + 3000]
        assert "cb.prev_hash" in block
        assert "cb.prev_bundle_hash" not in block

    def test_uses_agent_signature_not_signature(self):
        src = _load(EVIDENCE)
        idx = src.find('"chain_of_custody":')
        assert idx != -1
        # Walk backwards to the SELECT that feeds this response
        select_start = src.rfind("SELECT", max(0, idx - 3500), idx)
        assert select_start != -1
        block = src[select_start:idx]
        assert "cb.agent_signature" in block
        # Should NOT have the bare `cb.signature,` column reference anymore
        assert "cb.signature," not in block


# =============================================================================
# C6 — audit_report.py SQL column fixes
# =============================================================================

class TestC6AuditReportSQL:
    def test_sites_uses_client_org_id(self):
        src = _load(AUDIT)
        assert "FROM sites WHERE client_org_id" in src
        assert "FROM sites WHERE org_id" not in src

    def test_compliance_bundles_does_not_use_chain_valid_column(self):
        """chain_valid is not a column — must use signature_valid or compute."""
        src = _load(AUDIT)
        assert "bool_and(chain_valid)" not in src
        # Must still compute a chain_unbroken signal
        assert "chain_unbroken" in src

    def test_ots_join_uses_bundle_id_not_id(self):
        """ots_proofs.bundle_id (varchar) must join against
        compliance_bundles.bundle_id (varchar), not .id (uuid)."""
        src = _load(AUDIT)
        # Find the OTS query block
        idx = src.find("MAX(anchored_at)")
        assert idx != -1
        block = src[idx : idx + 600]
        assert "SELECT bundle_id FROM compliance_bundles" in block
        assert "SELECT id FROM compliance_bundles" not in block

    def test_compliance_packets_query_uses_site_ids(self):
        """compliance_packets has no org_id column — must query via site_ids."""
        src = _load(AUDIT)
        assert "compliance_packets\n                    WHERE org_id" not in src
        assert "compliance_packets\n                    WHERE site_id = ANY" in src


# =============================================================================
# C7 — Forbidden legal language stripped
# =============================================================================

class TestC7LegalLanguage:
    def test_scorecard_no_actively_monitored_and_maintained(self):
        src = _load(FRONTEND_SCORECARD)
        assert "actively monitored and maintained" not in src

    def test_scorecard_no_cannot_be_tampered(self):
        src = _load(FRONTEND_SCORECARD)
        assert "cannot be tampered" not in src

    def test_scorecard_no_anyone_can_independently_verify(self):
        src = _load(FRONTEND_SCORECARD)
        assert "Anyone can independently verify" not in src

    def test_verify_no_non_repudiation_cannot_be_forged(self):
        src = _load(FRONTEND_VERIFY)
        assert "non-repudiation that cannot be forged" not in src
        assert "cannot be forged" not in src


# =============================================================================
# Partner H1 + H2 — RBAC on drift config + discovery trigger
# =============================================================================

class TestPartnerRBAC:
    def test_update_drift_config_requires_admin_or_tech(self):
        src = _load(PARTNERS)
        idx = src.find("async def update_partner_drift_config(")
        assert idx != -1
        block = src[idx : idx + 500]
        assert 'require_partner_role("admin", "tech")' in block

    def test_trigger_discovery_requires_admin_or_tech(self):
        src = _load(PARTNERS)
        idx = src.find("async def trigger_discovery(")
        assert idx != -1
        block = src[idx : idx + 500]
        assert 'require_partner_role("admin", "tech")' in block
