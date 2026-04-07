"""Tests for the framework-agnostic template router and individual framework templates."""

import sys
import os

# Ensure backend dir is in path (conftest.py also does this, but be explicit)
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from framework_templates import (
    get_assessment_questions,
    get_policy_templates,
    get_framework_display_name,
    get_reference_field_name,
)


# ---------------------------------------------------------------------------
# Question count tests
# ---------------------------------------------------------------------------

def test_hipaa_returns_40_questions():
    questions = get_assessment_questions("hipaa")
    assert len(questions) == 40, f"Expected 40 HIPAA questions, got {len(questions)}"


def test_soc2_returns_30_questions():
    questions = get_assessment_questions("soc2")
    assert len(questions) == 30, f"Expected 30 SOC 2 questions, got {len(questions)}"


def test_glba_returns_25_questions():
    questions = get_assessment_questions("glba")
    assert len(questions) == 25, f"Expected 25 GLBA questions, got {len(questions)}"


# ---------------------------------------------------------------------------
# Reference field tests
# ---------------------------------------------------------------------------

def test_soc2_question_has_soc2_reference():
    questions = get_assessment_questions("soc2")
    first = questions[0]
    assert "soc2_reference" in first, (
        f"SOC 2 question missing 'soc2_reference' field. Keys: {list(first.keys())}"
    )


def test_glba_question_has_glba_reference():
    questions = get_assessment_questions("glba")
    first = questions[0]
    assert "glba_reference" in first, (
        f"GLBA question missing 'glba_reference' field. Keys: {list(first.keys())}"
    )


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

def test_unknown_framework_falls_back():
    questions = get_assessment_questions("unknown")
    assert len(questions) == 40, (
        f"Unknown framework should fall back to HIPAA (40 questions), got {len(questions)}"
    )


# ---------------------------------------------------------------------------
# Display name tests
# ---------------------------------------------------------------------------

def test_display_names():
    assert get_framework_display_name("soc2") == "SOC 2 Type II"
    assert get_framework_display_name("hipaa") == "HIPAA"
    assert get_framework_display_name("glba") == "GLBA Safeguards Rule"
    assert get_framework_display_name("pci_dss") == "PCI DSS"
    assert get_framework_display_name("nist_csf") == "NIST Cybersecurity Framework"
    # Unknown framework uppercased
    assert get_framework_display_name("foo_bar") == "FOO_BAR"


# ---------------------------------------------------------------------------
# Policy template tests
# ---------------------------------------------------------------------------

def test_hipaa_policy_templates_has_8_policies():
    templates = get_policy_templates("hipaa")
    assert len(templates) == 8, f"Expected 8 HIPAA policy templates, got {len(templates)}"


def test_soc2_policy_templates_has_8_policies():
    templates = get_policy_templates("soc2")
    assert len(templates) == 8, f"Expected 8 SOC 2 policy templates, got {len(templates)}"


def test_glba_policy_templates_has_6_policies():
    templates = get_policy_templates("glba")
    assert len(templates) == 6, f"Expected 6 GLBA policy templates, got {len(templates)}"


def test_soc2_policy_has_soc2_references():
    templates = get_policy_templates("soc2")
    for key, template in templates.items():
        assert "soc2_references" in template, (
            f"SOC 2 policy '{key}' missing 'soc2_references' field"
        )
        assert isinstance(template["soc2_references"], list), (
            f"SOC 2 policy '{key}' soc2_references should be a list"
        )


def test_glba_policy_has_glba_references():
    templates = get_policy_templates("glba")
    for key, template in templates.items():
        assert "glba_references" in template, (
            f"GLBA policy '{key}' missing 'glba_references' field"
        )
        assert isinstance(template["glba_references"], list), (
            f"GLBA policy '{key}' glba_references should be a list"
        )


# ---------------------------------------------------------------------------
# Reference field name tests
# ---------------------------------------------------------------------------

def test_reference_field_names():
    assert get_reference_field_name("hipaa") == "hipaa_reference"
    assert get_reference_field_name("soc2") == "soc2_reference"
    assert get_reference_field_name("glba") == "glba_reference"
    assert get_reference_field_name("pci_dss") == "pci_dss_reference"
    assert get_reference_field_name("unknown") == "unknown_reference"


# ---------------------------------------------------------------------------
# Question structure validation
# ---------------------------------------------------------------------------

def test_soc2_questions_have_required_fields():
    questions = get_assessment_questions("soc2")
    required = {"key", "category", "soc2_reference", "text"}
    for q in questions:
        missing = required - set(q.keys())
        assert not missing, f"SOC 2 question '{q.get('key')}' missing fields: {missing}"


def test_glba_questions_have_required_fields():
    questions = get_assessment_questions("glba")
    required = {"key", "category", "glba_reference", "text"}
    for q in questions:
        missing = required - set(q.keys())
        assert not missing, f"GLBA question '{q.get('key')}' missing fields: {missing}"


def test_soc2_categories_present():
    questions = get_assessment_questions("soc2")
    categories = {q["category"] for q in questions}
    expected = {"common_criteria", "availability", "processing_integrity", "confidentiality", "privacy"}
    assert expected == categories, f"SOC 2 categories mismatch. Got: {categories}"


def test_glba_categories_present():
    questions = get_assessment_questions("glba")
    categories = {q["category"] for q in questions}
    expected = {"administrative", "technical", "physical", "privacy", "disposal"}
    assert expected == categories, f"GLBA categories mismatch. Got: {categories}"
