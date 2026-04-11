"""Tests for H4 + H5 audit finding closure.

H4: Operational monitoring types excluded from compliance scoring.
    - evidence_chain.py uses explicit OPERATIONAL_MONITORING_TYPES set (not prefix heuristic)
    - db_queries.py CATEGORY_CHECKS excludes operational types from scoring
    - Compliance-relevant network checks (windows_network_profile, etc.) still score

H5: Client-side bundle_hash in Go daemon.
    - submitter.go bundlePayload includes BundleHash field
    - SHA256 computed from signedBytes before submission
    - Backend logs warning when fallback hash is used
"""

import ast
import os
import re


_HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_CHAIN_PY = os.path.normpath(os.path.join(_HERE, "..", "evidence_chain.py"))
DB_QUERIES_PY = os.path.normpath(os.path.join(_HERE, "..", "db_queries.py"))
SUBMITTER_GO = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "appliance", "internal", "evidence", "submitter.go")
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _parse_category_checks(src: str) -> dict:
    """Extract CATEGORY_CHECKS dict from db_queries.py source using AST."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CATEGORY_CHECKS":
                    # Safe: only evaluates literal dict from our own source
                    return ast.literal_eval(node.value)
    raise AssertionError("CATEGORY_CHECKS not found in db_queries.py")


# =============================================================================
# H4 — Operational monitoring types excluded from compliance
# =============================================================================

class TestH4OperationalMonitoringExclusion:
    """H4: Network monitoring bundles must not tank compliance scores."""

    def test_evidence_chain_uses_explicit_set_not_prefix(self):
        """evidence_chain.py must use OPERATIONAL_MONITORING_TYPES set, not startswith."""
        src = _load(EVIDENCE_CHAIN_PY)
        assert "OPERATIONAL_MONITORING_TYPES" in src, (
            "evidence_chain.py must define OPERATIONAL_MONITORING_TYPES set"
        )
        assert 'startswith("net_")' not in src, (
            "evidence_chain.py still uses startswith('net_') heuristic — use explicit set"
        )

    def test_operational_types_match_daemon(self):
        """OPERATIONAL_MONITORING_TYPES must include all 4 daemon networkCheckTypes."""
        src = _load(EVIDENCE_CHAIN_PY)
        daemon_types = {
            "net_unexpected_ports", "net_expected_service",
            "net_host_reachability", "net_dns_resolution",
        }
        for ct in daemon_types:
            assert f'"{ct}"' in src, (
                f"OPERATIONAL_MONITORING_TYPES missing daemon type: {ct}"
            )

    def test_scoring_excludes_operational_types(self):
        """CATEGORY_CHECKS['network'] must NOT include operational monitoring types."""
        src = _load(DB_QUERIES_PY)
        category_checks = _parse_category_checks(src)

        network_types = set(category_checks.get("network", []))
        operational = {"net_unexpected_ports", "net_expected_service",
                       "net_host_reachability", "net_dns_resolution", "network"}
        leaked = network_types & operational
        assert not leaked, (
            f"CATEGORY_CHECKS['network'] still includes operational types: {leaked}"
        )

    def test_compliance_network_checks_still_scored(self):
        """Legitimate compliance network checks must remain in scoring."""
        src = _load(DB_QUERIES_PY)
        category_checks = _parse_category_checks(src)

        network_types = set(category_checks.get("network", []))
        compliance_network = {
            "windows_network_profile", "windows_dns_config",
            "linux_network", "ntp_sync", "windows_smb1_protocol",
        }
        missing = compliance_network - network_types
        assert not missing, (
            f"Compliance network checks removed from scoring: {missing}"
        )

    def test_evidence_chain_skips_operational_bundles(self):
        """The skip path must return 'accepted' with ots_status='skipped'."""
        src = _load(EVIDENCE_CHAIN_PY)
        assert '"ots_status": "skipped"' in src, (
            "Operational bundle skip path must return ots_status='skipped'"
        )


# =============================================================================
# H5 — Client-side bundle hash
# =============================================================================

class TestH5ClientSideBundleHash:
    """H5: Go daemon must compute and send bundle_hash."""

    def test_submitter_payload_has_bundle_hash(self):
        """bundlePayload struct must include BundleHash field."""
        src = _load(SUBMITTER_GO)
        assert 'BundleHash' in src, (
            "bundlePayload in submitter.go must have BundleHash field"
        )
        assert '`json:"bundle_hash"`' in src, (
            "BundleHash must serialize as bundle_hash"
        )

    def test_sha256_computed_from_signed_bytes(self):
        """Daemon must compute SHA256 hash from signedBytes."""
        src = _load(SUBMITTER_GO)
        assert "sha256.Sum256(signedBytes)" in src, (
            "Bundle hash must be SHA256 of signedBytes (canonical JSON)"
        )

    def test_crypto_sha256_imported(self):
        """crypto/sha256 must be imported."""
        src = _load(SUBMITTER_GO)
        assert '"crypto/sha256"' in src, (
            "submitter.go must import crypto/sha256"
        )

    def test_backend_logs_fallback_warning(self):
        """Backend must log warning when server-side hash fallback is used."""
        src = _load(EVIDENCE_CHAIN_PY)
        assert "Server-side hash fallback" in src, (
            "evidence_chain.py must log warning on hash fallback"
        )
        assert "upgrade daemon" in src, (
            "Fallback warning must mention daemon upgrade"
        )

    def test_bundle_hash_populated_in_payload(self):
        """The bundleHash variable must be assigned to payload.BundleHash."""
        src = _load(SUBMITTER_GO)
        assert re.search(r'BundleHash:\s+bundleHash', src), (
            "payload must set BundleHash: bundleHash"
        )
