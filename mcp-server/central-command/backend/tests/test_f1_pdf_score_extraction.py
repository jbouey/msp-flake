"""F1 PDF score-extraction regression test (Task #67 Phase 2b).

Pre-fix at client_attestation_letter.py:212:
    sc = score_result.get("score") if isinstance(score_result, dict) else None

compute_compliance_score returns ComplianceScore dataclass (not dict),
so isinstance(...) was always False, sc was always None, and every F1
attestation PDF customers received showed overall_score=None.

Post-fix: `sc = score_result.overall_score` (dataclass attribute access).

This test pins the correct extraction shape so a future PR can't
silently regress to the dict-shape access.
"""
from __future__ import annotations

import ast
import pathlib
import textwrap

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_F1_FILE = _BACKEND / "client_attestation_letter.py"


def test_f1_pdf_uses_dataclass_attribute_access_for_score():
    """The F1 attestation letter MUST extract overall_score via dataclass
    attribute access, NOT via `.get('score')` (dict-shape — pre-fix bug).

    Regression class: dataclass-vs-dict type confusion. Pinned at
    compliance_score.py:55-71 where ComplianceScore is `@dataclass`.
    """
    src = _F1_FILE.read_text()
    # The compute_compliance_score result must be accessed via
    # .overall_score attribute (dataclass), not .get('score') (dict).
    assert "score_result.overall_score" in src, (
        "F1 PDF must extract overall_score via dataclass attribute "
        "access: `sc = score_result.overall_score`. Pre-fix used "
        "`score_result.get('score') if isinstance(score_result, dict)` "
        "which always returned None."
    )
    assert "isinstance(score_result, dict)" not in src, (
        "F1 PDF must NOT check isinstance(score_result, dict) — "
        "compute_compliance_score returns ComplianceScore dataclass "
        "(compliance_score.py:55-71), never a dict. The legacy dict "
        "check was dead code that silenced every F1 PDF's score."
    )


def test_f1_pdf_samples_compliance_score_with_correct_helper_input():
    """F1 PDF integration with canonical_metrics_sampler must capture
    the helper_input kwargs the call ACTUALLY uses (Counsel Rule 1
    Phase 2c invariant recomputes with these kwargs — mismatch =
    false-positive drift).
    """
    src = _F1_FILE.read_text()
    # F1 calls compute_compliance_score(conn, site_ids, window_days=DEFAULT_PERIOD_DAYS)
    # without include_incidents → defaults False. sample_metric_response
    # must capture these exact kwargs.
    assert "sample_metric_response" in src, (
        "F1 PDF must integrate the sampler (Task #67 Phase 2b)."
    )
    assert '"window_days": DEFAULT_PERIOD_DAYS' in src, (
        "F1 sampler MUST capture window_days=DEFAULT_PERIOD_DAYS "
        "(the value passed to compute_compliance_score) — Phase 2c "
        "substrate recompute uses helper_input verbatim."
    )
    assert '"include_incidents": False' in src, (
        "F1 sampler MUST capture include_incidents=False (the default "
        "the call relies on) — see canonical_compliance_score_drift v3 "
        "P0-E4 design fix."
    )
    assert 'classification="customer-facing"' in src, (
        "F1 PDF is the highest-stakes customer-facing artifact (Ed25519-"
        "signed PDF shipped to clinics). Classification MUST be "
        "'customer-facing' so it fires substrate drift in Phase 2c."
    )


def test_f1_pdf_has_endpoint_path_marker_for_sampler():
    """sample_metric_response endpoint_path must distinguish F1 PDF
    samples from /api/client/dashboard samples in canonical_metric_samples.
    """
    src = _F1_FILE.read_text()
    assert 'endpoint_path="f1:attestation_letter"' in src, (
        "F1 sampler MUST use a distinct endpoint_path marker "
        "('f1:attestation_letter') so substrate analytics can "
        "separate F1 PDF samples from API endpoint samples."
    )
