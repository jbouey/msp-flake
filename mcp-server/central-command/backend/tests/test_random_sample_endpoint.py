"""Tests for Session 203 Tier 2.4 — auditor random-sample endpoint.

Source-level checks of `evidence_chain.py::get_random_bundle_sample`. The
endpoint exists so an auditor can request a *reproducible* random sample
of evidence bundles for spot-checking, without trusting the platform's
pagination order or having to write SQL.
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


class TestRandomSampleEndpoint:
    def test_endpoint_registered(self):
        src = _load()
        assert '@router.get("/sites/{site_id}/random-sample")' in src
        assert "async def get_random_bundle_sample(" in src

    def test_uses_evidence_view_guard(self):
        body = _get_func("get_random_bundle_sample")
        assert "require_evidence_view_access" in body

    def test_count_default_is_10(self):
        body = _get_func("get_random_bundle_sample")
        assert "count: int = 10" in body

    def test_count_capped_at_100(self):
        """count > 100 must 400 to prevent the endpoint being used as an
        alternate bulk-export route."""
        body = _get_func("get_random_bundle_sample")
        assert "count < 1 or count > 100" in body
        assert "count must be between 1 and 100" in body

    def test_404_unknown_site(self):
        body = _get_func("get_random_bundle_sample")
        assert '"Site not found"' in body
        assert "status_code=404" in body

    def test_uses_postgres_random_order(self):
        """Spot-check order must be `ORDER BY random()` so the sample is
        uniformly distributed across the entire chain, not biased toward
        recent or high-status bundles."""
        body = _get_func("get_random_bundle_sample")
        assert "ORDER BY random()" in body

    def test_supports_reproducible_seed(self):
        """An auditor saying 'verify the sample for seed 4242' must be
        able to reproduce that exact set on a future request."""
        body = _get_func("get_random_bundle_sample")
        assert "seed: Optional[int] = None" in body
        assert "setseed" in body

    def test_seed_mapped_into_postgres_range(self):
        """setseed() takes a double in [-1.0, 1.0]; the helper must map
        the user-supplied int into that range deterministically."""
        body = _get_func("get_random_bundle_sample")
        assert "% 2_000_001" in body
        assert "1_000_000" in body

    def test_response_includes_full_signature_payload(self):
        """The whole point of spot-checking is verifying signatures, so
        agent_signature and chain_hash MUST be in the SELECT."""
        body = _get_func("get_random_bundle_sample")
        assert "agent_signature" in body
        assert "chain_hash" in body

    def test_response_echoes_seed_and_count(self):
        body = _get_func("get_random_bundle_sample")
        assert '"seed":' in body
        assert '"count_requested":' in body
        assert '"count_returned":' in body

    def test_response_includes_reproducibility_flag(self):
        body = _get_func("get_random_bundle_sample")
        assert '"reproducible":' in body

    def test_response_includes_verifier_note(self):
        """The auditor opening this JSON for the first time should see a
        one-line hint of how to verify the sample, not have to guess."""
        body = _get_func("get_random_bundle_sample")
        assert '"verifier_note"' in body
        assert "verify.sh" in body

    def test_legacy_bundles_not_filtered_out(self):
        """The auditor's job is to confirm legacy bundles exist and are
        honestly labeled. The bundle-sample query must NOT filter on
        signature presence or status — only on site_id."""
        body = _get_func("get_random_bundle_sample")
        # The bundle-sample SELECT must not filter on these fields
        assert "agent_signature IS NOT NULL" not in body or (
            # It's allowed to APPEAR in the SELECT list as `... IS NOT NULL as signed`,
            # but never as a WHERE clause filter.
            "WHERE" not in body[body.find("agent_signature IS NOT NULL"):
                                body.find("agent_signature IS NOT NULL") + 200]
        )
        assert "ots_status =" not in body
        assert "ots_status IN" not in body
        assert "WHERE cb.site_id = :site_id" in body
