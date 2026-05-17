"""#139 Phase B — YAML coverage gates.

Pins:
  1. GLBA coverage ≥ 12 checks (ratchet baseline)
  2. SOC2 coverage ≥ 34 control_ids (ratchet baseline)
  3. Every GLBA control_id matches the canonical CFR format
     (16 CFR 314.4(...) or 16 CFR 313 or 16 CFR 682)
  4. Every new SOC2 control_id matches the TSC pattern
     (CC1-CC9 / A1-A2 / C1-C2 / P1-P8 / PI1-PI3)
  5. Honesty invariant: mapping_strength=supportive ⇒ required=false
     (auditor-defensibility — supportive mappings cannot publish
     as "required: true" because the check doesn't independently
     close the cited subsection)
  6. Every GLBA mapping cites a question_bank_key that exists in
     glba_templates.GLBA_ASSESSMENT_QUESTIONS
  7. mapping_strength is one of {satisfying, supportive} when present
  8. The 6 GLBA rejections (per Phase B design) are NOT in YAML —
     time_sync, integrity_monitoring, database_exposure, snmp_security,
     rdp_exposure, device_inventory

Per Counsel Rule 1: customer-facing compliance reports cite real
control IDs auditors can look up. Failing any of these gates means
we'd publish unverifiable or dishonest mappings.

TIER-1 — no DB, no asyncpg, just YAML + the question-bank module.
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml


_REPO = pathlib.Path(__file__).resolve().parents[4]
_YAML = (
    _REPO / "packages" / "compliance-agent" / "src" / "compliance_agent"
    / "frameworks" / "mappings" / "control_mappings.yaml"
)
_BACKEND = pathlib.Path(__file__).resolve().parent.parent


_GLBA_COVERAGE_BASELINE = 12
_SOC2_COVERAGE_BASELINE = 34


def _load_yaml() -> dict:
    return yaml.safe_load(_YAML.read_text(encoding="utf-8"))


def _glba_mappings():
    data = _load_yaml()
    return [
        (name, m)
        for name, check in data["checks"].items()
        for m in check.get("framework_mappings", {}).get("glba", [])
    ]


def _glba_checks_with_mapping():
    data = _load_yaml()
    return [
        name
        for name, check in data["checks"].items()
        if "glba" in check.get("framework_mappings", {})
    ]


def _soc2_mappings():
    data = _load_yaml()
    return [
        m
        for check in data["checks"].values()
        for m in check.get("framework_mappings", {}).get("soc2", [])
    ]


def test_glba_coverage_minimum():
    """GLBA mappings must cover ≥ 12 checks. Per Phase B design
    (audit/.agent/plans/139-phase-b-yaml-coverage-2026-05-17.md),
    the 12 picks are the defensible subset of the 18 infrastructure
    checks. Future regressions (e.g. accidentally removing a check
    from the YAML) would drop coverage."""
    checks = _glba_checks_with_mapping()
    assert len(checks) >= _GLBA_COVERAGE_BASELINE, (
        f"GLBA coverage {len(checks)} < baseline "
        f"{_GLBA_COVERAGE_BASELINE}. Checks with GLBA mappings: "
        f"{sorted(checks)}. To intentionally lower the baseline, "
        f"justify in design doc + adjust _GLBA_COVERAGE_BASELINE."
    )


def test_soc2_coverage_minimum():
    """SOC2 control_ids must total ≥ 34. Phase B backfilled 28→34
    by adding +CC4.1 / +CC7.2 / +CC7.3 / +CC7.1 / +C1.1 / +PI1.3
    across 6 existing checks. Closes the zero-Confidentiality-coverage
    gap (C1.1 on encryption_at_rest)."""
    mappings = _soc2_mappings()
    assert len(mappings) >= _SOC2_COVERAGE_BASELINE, (
        f"SOC2 control_id count {len(mappings)} < baseline "
        f"{_SOC2_COVERAGE_BASELINE}. To intentionally lower, "
        f"justify + adjust _SOC2_COVERAGE_BASELINE."
    )


_GLBA_CFR_PATTERN = re.compile(
    r"^16 CFR (314\.4\([a-z]\)(\(\d+\))?|313|682)$"
)


def test_glba_control_ids_match_cfr_format():
    """Every GLBA mapping must cite a real 16 CFR subsection format:
    16 CFR 314.4(X) or 16 CFR 314.4(X)(N) (Safeguards Rule), 16 CFR
    313 (Privacy Rule), 16 CFR 682 (Disposal Rule). Free-text strings
    or invented subsections fail the auditor lookup test."""
    for name, m in _glba_mappings():
        cid = m["control_id"]
        assert _GLBA_CFR_PATTERN.match(cid), (
            f"check {name!r}: GLBA control_id {cid!r} doesn't match "
            f"canonical CFR format. Auditor cannot look this up in "
            f"the Code of Federal Regulations. Use shape "
            f"'16 CFR 314.4(c)(3)' or '16 CFR 313' / '16 CFR 682'."
        )


# AICPA TSC criterion pattern:
#   Common Criteria CC1-CC9 (CC1.1 etc)
#   Availability A1-A2
#   Confidentiality C1-C2
#   Processing Integrity PI1 (PI1.1-PI1.5)
#   Privacy P1-P8
_SOC2_TSC_PATTERN = re.compile(
    r"^(CC[1-9]\.\d+|A[12]\.\d+|C[12]\.\d+|PI[1-3]\.\d+|P[1-8]\.\d+)$"
)


def test_soc2_control_ids_match_tsc_format():
    """Every SOC2 control_id must match the AICPA Trust Service
    Criteria pattern. Invented IDs fail SOC2 auditor lookup."""
    for m in _soc2_mappings():
        cid = m["control_id"]
        assert _SOC2_TSC_PATTERN.match(cid), (
            f"SOC2 control_id {cid!r} doesn't match TSC format. "
            f"Use shape CC1.1 / A1.2 / C1.1 / PI1.3 / P1.1."
        )


def test_honesty_invariant_supportive_not_required():
    """Counsel Rule 1: a mapping marked `mapping_strength: supportive`
    MUST have `required: false`. Supportive means the check produces
    relevant evidence but doesn't independently satisfy the subsection
    — publishing it as required:true would over-claim to auditors."""
    for name, m in _glba_mappings():
        if m.get("mapping_strength") == "supportive":
            assert m.get("required") is False, (
                f"check {name!r}: GLBA mapping to {m['control_id']!r} "
                f"is mapping_strength=supportive but required={m.get('required')!r}. "
                f"Supportive mappings cannot publish as required:true "
                f"— auditor-defensibility honesty invariant."
            )


def test_glba_question_bank_keys_resolve():
    """Every GLBA mapping cites a question_bank_key. That key must
    exist in glba_templates.GLBA_ASSESSMENT_QUESTIONS (else the kit
    renderer would chain on a missing question)."""
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    import glba_templates  # noqa: E402

    valid_keys = {q["key"] for q in glba_templates.GLBA_ASSESSMENT_QUESTIONS}
    for name, m in _glba_mappings():
        key = m.get("question_bank_key")
        assert key in valid_keys, (
            f"check {name!r}: GLBA mapping cites question_bank_key="
            f"{key!r} which doesn't exist in glba_templates. Valid "
            f"keys: {sorted(valid_keys)}."
        )


def test_mapping_strength_values_constrained():
    """When `mapping_strength` is present, it must be one of
    {satisfying, supportive}. Other values would be ignored by
    downstream renderers."""
    allowed = {"satisfying", "supportive"}
    for name, m in _glba_mappings():
        strength = m.get("mapping_strength")
        if strength is not None:
            assert strength in allowed, (
                f"check {name!r}: GLBA mapping_strength={strength!r} "
                f"not in {sorted(allowed)}."
            )


_REJECTED_GLBA_CHECKS = {
    "time_sync",
    "integrity_monitoring",
    "database_exposure",
    "snmp_security",
    "rdp_exposure",
    "device_inventory",
}


def test_glba_rejections_stay_unmapped():
    """The 6 GLBA rejections per Phase B design rationale MUST NOT
    accidentally gain a glba: block in a future commit. Each was
    rejected with explicit auditor-defensibility reasoning. If a new
    contributor wants to add one, they MUST update the design doc
    + this test in lockstep."""
    checks_with_glba = set(_glba_checks_with_mapping())
    accidental = checks_with_glba & _REJECTED_GLBA_CHECKS
    assert not accidental, (
        f"Checks {sorted(accidental)} gained GLBA mappings but Phase "
        f"B design explicitly rejected them. Update "
        f".agent/plans/139-phase-b-yaml-coverage-2026-05-17.md + "
        f"_REJECTED_GLBA_CHECKS in this test if the rejection is "
        f"being overturned."
    )
