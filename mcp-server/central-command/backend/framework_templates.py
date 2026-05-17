"""Framework-agnostic template router.

Returns assessment questions, policy templates, and display labels
for a supported compliance framework.

#41 Phase A (audit/coach-41-soc2-glba-question-banks-gate-a-2026-05-17.md)
Gate A P0-1 closure: SILENT HIPAA FALLBACK REMOVED. Pre-fix any
unknown framework returned HIPAA's SRA_QUESTIONS / POLICY_TEMPLATES
which is a misleading-data class. Now raises UnsupportedFrameworkError;
caller (frameworks.py endpoints) converts to HTTPException(400).

Single source of truth for the allowlist:
  compliance_frameworks.SUPPORTED_FRAMEWORKS
"""
from __future__ import annotations

from typing import Optional


class UnsupportedFrameworkError(ValueError):
    """Raised when the caller requests a framework outside the
    SUPPORTED_FRAMEWORKS allowlist. The endpoint translates to
    HTTPException(400) — never silently fall back to HIPAA."""


def _supported_frameworks() -> frozenset[str]:
    # Import from the zero-dep module to avoid dragging
    # SQLAlchemy/asyncpg through this function's call graph
    # (compliance_frameworks.py has FastAPI + DB deps).
    try:
        from .framework_allowlist import SUPPORTED_FRAMEWORKS
    except ImportError:
        from framework_allowlist import SUPPORTED_FRAMEWORKS  # type: ignore[no-redef]
    return SUPPORTED_FRAMEWORKS


def get_assessment_questions(framework: str) -> list:
    """Get SRA/assessment questions for the specified framework.

    Per #41 Gate A P0-1: raises UnsupportedFrameworkError for any
    framework NOT in SUPPORTED_FRAMEWORKS. NO silent fallback.
    """
    if framework not in _supported_frameworks():
        raise UnsupportedFrameworkError(
            f"Unsupported framework: {framework!r}. "
            f"Supported: {sorted(_supported_frameworks())}."
        )
    if framework == "hipaa":
        try:
            from .hipaa_templates import SRA_QUESTIONS
        except ImportError:
            from hipaa_templates import SRA_QUESTIONS
        return SRA_QUESTIONS
    if framework == "soc2":
        try:
            from .soc2_templates import SOC2_ASSESSMENT_QUESTIONS
        except ImportError:
            from soc2_templates import SOC2_ASSESSMENT_QUESTIONS
        return SOC2_ASSESSMENT_QUESTIONS
    if framework == "glba":
        try:
            from .glba_templates import GLBA_ASSESSMENT_QUESTIONS
        except ImportError:
            from glba_templates import GLBA_ASSESSMENT_QUESTIONS
        return GLBA_ASSESSMENT_QUESTIONS
    # Defensive — reachable only if SUPPORTED_FRAMEWORKS grows without
    # a matching branch above.
    raise UnsupportedFrameworkError(
        f"framework {framework!r} is in SUPPORTED_FRAMEWORKS but has "
        f"no question-bank branch wired in framework_templates.py — "
        f"add the branch in the same commit that extends the allowlist."
    )


def get_policy_templates(framework: str) -> dict:
    """Get policy document templates for the specified framework.

    Per #41 Gate A P0-1: same allowlist + raise-on-unknown shape as
    get_assessment_questions.
    """
    if framework not in _supported_frameworks():
        raise UnsupportedFrameworkError(
            f"Unsupported framework: {framework!r}. "
            f"Supported: {sorted(_supported_frameworks())}."
        )
    if framework == "hipaa":
        try:
            from .hipaa_templates import POLICY_TEMPLATES
        except ImportError:
            from hipaa_templates import POLICY_TEMPLATES
        return POLICY_TEMPLATES
    if framework == "soc2":
        try:
            from .soc2_templates import SOC2_POLICY_TEMPLATES
        except ImportError:
            from soc2_templates import SOC2_POLICY_TEMPLATES
        return SOC2_POLICY_TEMPLATES
    if framework == "glba":
        try:
            from .glba_templates import GLBA_POLICY_TEMPLATES
        except ImportError:
            from glba_templates import GLBA_POLICY_TEMPLATES
        return GLBA_POLICY_TEMPLATES
    raise UnsupportedFrameworkError(
        f"framework {framework!r} is in SUPPORTED_FRAMEWORKS but has "
        f"no policy-template branch wired in framework_templates.py."
    )


def get_framework_display_name(framework: str) -> str:
    """Human-readable framework name. Returns the lowercased framework
    upper-cased as fallback for unknown — display-only, non-load-bearing
    (unlike the question/policy fns, this never returns wrong DATA).
    """
    names = {
        "hipaa": "HIPAA",
        "soc2": "SOC 2 Type II",
        "pci_dss": "PCI DSS",
        "nist_csf": "NIST Cybersecurity Framework",
        "nist_800_171": "NIST SP 800-171",
        "glba": "GLBA Safeguards Rule",
        "sox": "SOX IT Controls",
        "gdpr": "GDPR",
        "cmmc": "CMMC",
        "iso_27001": "ISO 27001",
    }
    return names.get(framework, framework.upper())


def get_reference_field_name(framework: str) -> str:
    """The field name used for control references in this framework."""
    fields = {
        "hipaa": "hipaa_reference",
        "soc2": "soc2_reference",
        "glba": "glba_reference",
        "pci_dss": "pci_dss_reference",
        "nist_csf": "nist_csf_reference",
    }
    return fields.get(framework, f"{framework}_reference")
