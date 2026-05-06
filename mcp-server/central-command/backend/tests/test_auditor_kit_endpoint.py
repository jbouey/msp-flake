"""Tests for Session 203 Tier 1.1 — auditor verification kit ZIP endpoint.

Source-level checks of `evidence_chain.py::download_auditor_kit`. The
endpoint is the centerpiece of OsirisCare's "recovery platform"
positioning after the April 2026 Delve / DeepDelver scandal — every
contract for it is load-bearing for legal defensibility.
"""

import ast
import os


EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evidence_chain.py",
)


def _load() -> str:
    with open(EVIDENCE) as f:
        return f.read()


def _get_func(name: str) -> str:
    src = _load()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# =============================================================================
# Endpoint shape
# =============================================================================

class TestAuditorKitEndpoint:
    def test_endpoint_registered(self):
        src = _load()
        assert '@router.get("/sites/{site_id}/auditor-kit")' in src
        assert "async def download_auditor_kit(" in src

    def test_endpoint_uses_evidence_view_guard(self):
        body = _get_func("download_auditor_kit")
        assert "require_evidence_view_access" in body

    def test_endpoint_returns_zip_response(self):
        body = _get_func("download_auditor_kit")
        assert "application/zip" in body
        assert "Content-Disposition" in body
        assert "attachment" in body

    def test_endpoint_caps_limit_to_5000(self):
        """Range cap prevents an attacker from requesting a 10GB ZIP."""
        body = _get_func("download_auditor_kit")
        assert "limit > 5000" in body
        assert "limit < 1" in body

    def test_endpoint_supports_pagination(self):
        body = _get_func("download_auditor_kit")
        assert "limit: int" in body
        assert "offset: int" in body
        assert "OFFSET :offset" in body

    def test_endpoint_404s_unknown_site(self):
        body = _get_func("download_auditor_kit")
        assert '404, "Site not found"' in body

    def test_endpoint_404s_empty_range(self):
        body = _get_func("download_auditor_kit")
        assert '"No evidence bundles in range"' in body


# =============================================================================
# ZIP contents
# =============================================================================

class TestAuditorKitZipContents:
    """Round-table 2026-05-06 (Coach P1-2): writes go through the
    deterministic _kit_zwrite helper. Each test now accepts either
    the legacy `zf.writestr(...)` literal OR the new
    `_zwrite(zf, ...)` / `_kit_zwrite(zf, ...)` patterns. Intent
    (entry presence) preserved; pattern modernized."""

    @staticmethod
    def _name_in_kit(body: str, name: str) -> bool:
        # Match any of:
        #   zf.writestr("name", ...)
        #   _zwrite(zf, "name", ...)
        #   _kit_zwrite(zf, "name", ...)
        # Or for the entry-list pattern:
        #   ("name", ...) inside fixed_entries
        legacy = f'zf.writestr("{name}"' in body
        legacy_f = f"zf.writestr(f'{name}'" in body  # f-string variant
        helper = f'_zwrite(zf, "{name}"' in body or f"_kit_zwrite(zf, \"{name}\"" in body
        entry = f'("{name}",' in body  # fixed_entries tuple
        return legacy or legacy_f or helper or entry

    def test_includes_readme(self):
        body = _get_func("download_auditor_kit")
        assert self._name_in_kit(body, "README.md")

    def test_includes_verify_sh(self):
        body = _get_func("download_auditor_kit")
        assert self._name_in_kit(body, "verify.sh")

    def test_includes_chain_metadata(self):
        body = _get_func("download_auditor_kit")
        assert self._name_in_kit(body, "chain.json")

    def test_includes_bundles_jsonl(self):
        body = _get_func("download_auditor_kit")
        assert self._name_in_kit(body, "bundles.jsonl")

    def test_includes_pubkeys_json(self):
        body = _get_func("download_auditor_kit")
        assert self._name_in_kit(body, "pubkeys.json")

    def test_includes_ots_files_dir(self):
        body = _get_func("download_auditor_kit")
        # OTS files use f"ots/{filename}" (or {ots_name}) in any of
        # the helper variants. Match the path prefix flexibly.
        assert (
            'zf.writestr(f"ots/' in body
            or '_zwrite(zf, f"ots/' in body
            or '_kit_zwrite(zf, f"ots/' in body
        )


# =============================================================================
# Chain metadata payload
# =============================================================================

class TestChainMetadata:
    def test_chain_metadata_has_kit_version(self):
        src = _load()
        # Kit version bumped to 2.1 when the auditor kit gained white-label
        # presenter branding (see Migration 235 + download_auditor_kit). The
        # assertion keeps the field present regardless of future bumps.
        assert '"kit_version"' in src
        assert ('"kit_version": "1.0"' in src
                or '"kit_version": "2.1"' in src
                or 'kit_version=' in src)

    def test_chain_metadata_has_disclosures(self):
        """Every kit ships with security advisories inline so an auditor
        sees remediation logs without leaving the file.

        Pre-#41: hardcoded list in chain.json referenced the Merkle
        disclosure literal directly. Post-#41 (2026-05-02): dynamic
        `_collect_security_advisories()` walks docs/security/ at
        request time. Hardcoded literal MOVED to docs/security/
        SECURITY_ADVISORY_2026-04-09_MERKLE.md.
        """
        body = _get_func("download_auditor_kit")
        assert '"disclosures"' in body
        # Post-#41 dynamic walk must appear in the function body
        assert "_collect_security_advisories()" in body, (
            "download_auditor_kit no longer iterates "
            "_collect_security_advisories(). Either #41 was reverted "
            "or someone re-hardcoded the disclosure list. Check that "
            "auditors still receive every advisory in docs/security/."
        )
        # The Merkle disclosure ID must still exist somewhere in the
        # repo (as a markdown file). Grep docs/security/.
        import pathlib as _pl
        sec_dir = _pl.Path(__file__).resolve().parent.parent.parent.parent.parent / "docs" / "security"
        merkle_files = list(sec_dir.glob("SECURITY_ADVISORY_2026-04-09_MERKLE*"))
        assert len(merkle_files) >= 1, (
            "Expected docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE*.md "
            "to exist (Session 203 disclosure-first commitment artifact). "
            "Either the file was deleted or moved — investigate."
        )

    def test_chain_metadata_has_genesis_block(self):
        body = _get_func("download_auditor_kit")
        assert '"genesis"' in body

    def test_chain_metadata_lists_appliances(self):
        body = _get_func("download_auditor_kit")
        assert '"appliances"' in body

    def test_chain_metadata_has_signing_counts(self):
        body = _get_func("download_auditor_kit")
        for key in ('"signed_count"', '"anchored_count"', '"legacy_count"', '"pending_count"'):
            assert key in body

    def test_chain_metadata_states_no_network_required(self):
        """The whole point is offline verification — the metadata must
        say so explicitly so the auditor doesn't waste time looking for
        a callback URL."""
        body = _get_func("download_auditor_kit")
        assert '"no_network_required": True' in body
        assert '"platform_dependency": "none"' in body


# =============================================================================
# Pubkey export
# =============================================================================

class TestPubkeyExport:
    def test_pubkeys_have_fingerprints(self):
        body = _get_func("download_auditor_kit")
        assert "fingerprint" in body
        assert "hashlib.sha256(r.agent_public_key" in body

    def test_pubkeys_only_includes_appliances_with_keys(self):
        body = _get_func("download_auditor_kit")
        assert "agent_public_key IS NOT NULL" in body

    def test_pubkeys_include_offline_pinning_note(self):
        """Auditors need to know to pin keys offline before verifying.
        The note in pubkeys.json explains it."""
        body = _get_func("download_auditor_kit")
        assert "Pin these public keys offline" in body


# =============================================================================
# README + verify.sh quality
# =============================================================================

class TestKitDocs:
    def test_readme_template_exists(self):
        src = _load()
        assert "_AUDITOR_KIT_README" in src
        # White-label bump: the README title is now templated on
        # {presenter_brand} (partner brand, falls back to OsirisCare when
        # the site has no partner). OsirisCare remains the cryptographic
        # substrate attribution referenced inside the body copy.
        assert ("{presenter_brand} Compliance Evidence" in src
                or "OsirisCare Compliance Evidence" in src)
        # Substrate attribution must still be visible somewhere in the
        # README template — auditors need to know who signed.
        assert "OsirisCare" in src

    def test_readme_explains_what_success_looks_like(self):
        src = _load()
        assert "What success looks like" in src

    def test_readme_documents_known_limitations(self):
        """Honesty about what the kit can't verify is the credibility
        difference vs. compliance-fraud platforms."""
        src = _load()
        assert "Known limitations" in src
        assert "pending" in src.lower()
        assert "legacy" in src.lower()

    def test_readme_explains_disclosures(self):
        src = _load()
        assert "Disclosures" in src

    def test_verify_sh_exists(self):
        src = _load()
        assert "_AUDITOR_KIT_VERIFY_SH" in src

    def test_verify_sh_uses_no_network_calls_to_osiriscare(self):
        """The verifier MUST NOT phone home — that would defeat the
        whole point of independent verification."""
        src = _load()
        # Find the verify.sh template body
        idx = src.find("_AUDITOR_KIT_VERIFY_SH")
        assert idx != -1
        # Walk forward to the end of the triple-quoted block
        end = src.find("'''", idx + 100)
        verify_sh = src[idx:end] if end != -1 else src[idx:idx + 6000]
        assert "osiriscare.net" not in verify_sh
        assert "api.osiriscare" not in verify_sh

    def test_verify_sh_runs_ed25519_verification(self):
        src = _load()
        assert "Ed25519PublicKey" in src

    def test_verify_sh_walks_hash_chain(self):
        src = _load()
        assert "chain link broken" in src or "chain_pass" in src

    def test_verify_sh_calls_ots_verify(self):
        src = _load()
        assert "ots verify" in src
