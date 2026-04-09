"""Tests for Session 203 H2 / H3 in-app download UI.

H2 — Public-key download panel renders the per-appliance Ed25519 keys
     in the portal UI with copy + download buttons. Backend endpoint
     already exists from Batch 5; this batch wires the frontend.

H3 — Per-bundle .ots download endpoint + UI button. Adds a new backend
     endpoint that returns the raw .ots bytes for a single bundle, and a
     download button on each row of the BundleTimeline component in
     PortalVerify.
"""

import ast
import os


_HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_PY = os.path.normpath(os.path.join(_HERE, "..", "evidence_chain.py"))
PORTAL_VERIFY_TSX = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "portal", "PortalVerify.tsx")
)
PUBLIC_KEYS_PANEL_TSX = os.path.normpath(
    os.path.join(_HERE, "..", "..", "frontend", "src", "portal", "PublicKeysPanel.tsx")
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
# H3 — Per-bundle .ots download endpoint
# =============================================================================

class TestPerBundleOtsDownloadEndpoint:
    def test_endpoint_registered(self):
        src = _load(EVIDENCE_PY)
        assert '@router.get("/sites/{site_id}/bundles/{bundle_id}/ots")' in src
        assert "async def download_bundle_ots_file(" in src

    def test_uses_evidence_view_guard(self):
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "require_evidence_view_access" in body

    def test_404_unknown_bundle(self):
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert '"Bundle not found for this site"' in body
        assert "status_code=404" in body

    def test_404_no_proof_recorded(self):
        """Legacy/pending bundles have no .ots — must 404 explicitly so
        the UI can show a friendly message instead of returning empty bytes."""
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "No OpenTimestamps proof recorded" in body
        assert "legacy or pending" in body

    def test_returns_octet_stream_with_attachment_header(self):
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert 'application/octet-stream' in body
        assert "Content-Disposition" in body
        assert "attachment" in body
        assert ".ots" in body

    def test_decodes_base64_proof_data(self):
        """The stored format is base64; we must decode before returning."""
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "base64.b64decode" in body
        assert "row.proof_data" in body

    def test_query_joins_bundle_and_proof_tables(self):
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "compliance_bundles" in body
        assert "ots_proofs" in body
        assert "LEFT JOIN" in body

    def test_response_includes_audit_headers(self):
        """X-Bundle-Hash + X-OTS-Status + X-Calendar-URL let an auditor
        sanity-check what they downloaded without re-querying the API."""
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "X-Bundle-Hash" in body
        assert "X-OTS-Status" in body
        assert "X-Calendar-URL" in body

    def test_filename_is_sanitized(self):
        body = _get_func(EVIDENCE_PY, "download_bundle_ots_file")
        assert "safe_bundle" in body
        assert "isalnum" in body


# =============================================================================
# H2 — PublicKeysPanel
# =============================================================================

class TestPublicKeysPanel:
    def test_panel_file_exists(self):
        assert os.path.isfile(PUBLIC_KEYS_PANEL_TSX)

    def test_panel_fetches_public_keys_endpoint(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "/public-keys" in src

    def test_panel_computes_fingerprint_in_browser(self):
        """The fingerprint must be computed locally so the auditor can
        verify it matches independently — not trust a server-supplied
        fingerprint string."""
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "sha256Fingerprint" in src
        assert "subtle.digest" in src

    def test_panel_has_download_button(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "downloadKeys" in src
        assert "Download pubkeys.json" in src

    def test_panel_has_copy_button(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "copyKey" in src
        assert "navigator.clipboard.writeText" in src

    def test_panel_renders_each_key_with_metadata(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "display_name" in src
        assert "first_checkin" in src
        assert "public_key_hex" in src

    def test_panel_links_merkle_disclosure(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "SECURITY_ADVISORY_2026-04-09_MERKLE.md" in src

    def test_panel_explains_offline_pinning(self):
        """Auditors need to be told to pin keys offline before verifying."""
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "Pin these offline" in src or "pin these offline" in src.lower()
        assert "audit working papers" in src

    def test_panel_handles_empty_state(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "No public keys" in src

    def test_panel_handles_loading_state(self):
        src = _load(PUBLIC_KEYS_PANEL_TSX)
        assert "Loading appliance" in src


# =============================================================================
# H3 — UI integration in PortalVerify
# =============================================================================

class TestPortalVerifyIntegration:
    def test_panel_imported_in_portal_verify(self):
        src = _load(PORTAL_VERIFY_TSX)
        assert "import { PublicKeysPanel }" in src

    def test_panel_rendered_in_verify_page(self):
        src = _load(PORTAL_VERIFY_TSX)
        assert "<PublicKeysPanel siteId={siteId} />" in src

    def test_bundle_timeline_takes_site_id(self):
        src = _load(PORTAL_VERIFY_TSX)
        assert "BundleTimeline: React.FC<{ bundles: BundleInfo[]; siteId?: string }>" in src

    def test_bundle_timeline_has_ots_download_button(self):
        src = _load(PORTAL_VERIFY_TSX)
        assert "Download .ots proof file" in src
        assert "/bundles/${bundle.bundle_id}/ots" in src

    def test_bundle_timeline_only_shows_ots_button_when_anchored(self):
        """Don't show the button on legacy/pending bundles — they have
        no .ots file and the endpoint would 404."""
        src = _load(PORTAL_VERIFY_TSX)
        assert "hasOtsFile" in src
        assert "anchored" in src
        assert "verified" in src

    def test_bundle_timeline_shows_ots_verify_hint(self):
        """The hint next to the download button must show the literal
        `ots verify` command so an auditor knows what to do with the file."""
        src = _load(PORTAL_VERIFY_TSX)
        assert "ots verify" in src

    def test_bundle_timeline_called_with_site_id(self):
        src = _load(PORTAL_VERIFY_TSX)
        assert "<BundleTimeline bundles={bundles} siteId={siteId} />" in src
