"""Tests for Session 203 Tier 2.3 — Genesis Block panel on PortalScorecard,
and Tier 2.6 — public /changelog page.

Source-level checks. The frontend Vitest suite covers behavior; these
tests are guard-rails so the genesis panel and changelog page can never
be silently deleted in a refactor.
"""

import os


_HERE = os.path.dirname(os.path.abspath(__file__))
PORTAL_SCORECARD = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "portal",
        "PortalScorecard.tsx",
    )
)
CHANGELOG_TSX = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "pages",
        "PublicChangelog.tsx",
    )
)
APP_TSX = os.path.normpath(
    os.path.join(
        _HERE,
        "..",
        "..",
        "frontend",
        "src",
        "App.tsx",
    )
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


# =============================================================================
# T2.3 — Genesis block panel on PortalScorecard
# =============================================================================

class TestGenesisBlockPanel:
    def test_panel_section_present(self):
        src = _load(PORTAL_SCORECARD)
        assert "Chain Genesis Block" in src

    def test_panel_shows_chain_origin_date(self):
        src = _load(PORTAL_SCORECARD)
        assert "Chain origin" in src
        assert "first_timestamp" in src

    def test_panel_shows_chain_depth(self):
        src = _load(PORTAL_SCORECARD)
        assert "Chain depth" in src
        assert "chain_length" in src

    def test_panel_shows_genesis_prev_hash_sentinel(self):
        """The 64-zero genesis sentinel must be shown literally so an
        auditor can confirm it matches the value they see when running
        verify.sh."""
        src = _load(PORTAL_SCORECARD)
        assert "0000000000000000000000000000000000000000000000000000000000000000" in src
        assert "Genesis prev_hash" in src

    def test_panel_names_hash_algorithms(self):
        src = _load(PORTAL_SCORECARD)
        assert "SHA-256" in src
        assert "Ed25519" in src
        assert "OpenTimestamps" in src

    def test_panel_links_to_verify_sh(self):
        """Panel must point the auditor at the verifier they should run
        for independent confirmation."""
        src = _load(PORTAL_SCORECARD)
        assert "verify.sh" in src

    def test_panel_only_renders_when_chain_data_exists(self):
        """Conditional render: `chain.first_timestamp && (...)` so portals
        with no evidence yet don't show an empty box."""
        src = _load(PORTAL_SCORECARD)
        assert "chain.first_timestamp &&" in src

    def test_panel_shows_signed_percentage(self):
        """The percentage signed is the most useful single metric for an
        auditor evaluating the chain quality at a glance."""
        src = _load(PORTAL_SCORECARD)
        assert "signed" in src.lower()
        assert "signatures_valid" in src
        assert "signatures_total" in src


# =============================================================================
# T2.6 — Public /changelog page
# =============================================================================

class TestPublicChangelog:
    def test_changelog_file_exists(self):
        assert os.path.isfile(CHANGELOG_TSX), f"missing {CHANGELOG_TSX}"

    def test_changelog_has_entries_array(self):
        src = _load(CHANGELOG_TSX)
        assert "ENTRIES" in src
        assert "ChangelogEntry" in src

    def test_changelog_has_four_categories(self):
        src = _load(CHANGELOG_TSX)
        for cat in ("'security'", "'feature'", "'fix'", "'disclosure'"):
            assert cat in src

    def test_changelog_includes_merkle_disclosure(self):
        """The Merkle disclosure must appear in the changelog with a link
        to the security advisory — public dated evidence we proactively
        disclose."""
        src = _load(CHANGELOG_TSX)
        assert "OSIRIS-2026-04-09-MERKLE-COLLISION" in src
        assert "SECURITY_ADVISORY_2026-04-09_MERKLE.md" in src

    def test_changelog_includes_auditor_kit_entry(self):
        src = _load(CHANGELOG_TSX)
        assert "auditor-kit" in src.lower() or "Auditor verification kit" in src

    def test_changelog_includes_disclosure_accounting_entry(self):
        src = _load(CHANGELOG_TSX)
        assert "164.528" in src or "disclosure accounting" in src.lower()

    def test_changelog_groups_by_month(self):
        src = _load(CHANGELOG_TSX)
        assert "grouped" in src
        assert "month" in src

    def test_changelog_route_registered_in_app(self):
        src = _load(APP_TSX)
        assert "PublicChangelog" in src
        assert 'path="/changelog"' in src

    def test_changelog_lazy_loaded(self):
        """The changelog should not bloat the main bundle — must be lazy."""
        src = _load(APP_TSX)
        assert "lazy(() => import('./pages/PublicChangelog'))" in src

    def test_changelog_entries_have_iso_dates(self):
        """Every entry must have an ISO 8601 date so JS Date can parse it."""
        src = _load(CHANGELOG_TSX)
        # The 2026-04-* dates from the seeded entries
        assert "2026-04-09" in src
