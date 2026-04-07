"""Framework-agnostic template router.

Returns assessment questions, policy templates, and module definitions
for any supported compliance framework.
"""

from typing import Optional


def get_assessment_questions(framework: str = "hipaa") -> list:
    """Get SRA/assessment questions for the specified framework."""
    if framework == "hipaa":
        try:
            from .hipaa_templates import SRA_QUESTIONS
        except ImportError:
            from hipaa_templates import SRA_QUESTIONS
        return SRA_QUESTIONS
    elif framework == "soc2":
        try:
            from .soc2_templates import SOC2_ASSESSMENT_QUESTIONS
        except ImportError:
            from soc2_templates import SOC2_ASSESSMENT_QUESTIONS
        return SOC2_ASSESSMENT_QUESTIONS
    elif framework == "glba":
        try:
            from .glba_templates import GLBA_ASSESSMENT_QUESTIONS
        except ImportError:
            from glba_templates import GLBA_ASSESSMENT_QUESTIONS
        return GLBA_ASSESSMENT_QUESTIONS
    else:
        # Fallback to HIPAA for unsupported frameworks
        try:
            from .hipaa_templates import SRA_QUESTIONS
        except ImportError:
            from hipaa_templates import SRA_QUESTIONS
        return SRA_QUESTIONS


def get_policy_templates(framework: str = "hipaa") -> dict:
    """Get policy document templates for the specified framework."""
    if framework == "hipaa":
        try:
            from .hipaa_templates import POLICY_TEMPLATES
        except ImportError:
            from hipaa_templates import POLICY_TEMPLATES
        return POLICY_TEMPLATES
    elif framework == "soc2":
        try:
            from .soc2_templates import SOC2_POLICY_TEMPLATES
        except ImportError:
            from soc2_templates import SOC2_POLICY_TEMPLATES
        return SOC2_POLICY_TEMPLATES
    elif framework == "glba":
        try:
            from .glba_templates import GLBA_POLICY_TEMPLATES
        except ImportError:
            from glba_templates import GLBA_POLICY_TEMPLATES
        return GLBA_POLICY_TEMPLATES
    else:
        try:
            from .hipaa_templates import POLICY_TEMPLATES
        except ImportError:
            from hipaa_templates import POLICY_TEMPLATES
        return POLICY_TEMPLATES


def get_framework_display_name(framework: str) -> str:
    """Human-readable framework name."""
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
