"""CI gate: data_completeness_pct field must be present on compliance
score API response.

D1 followup #47 closure 2026-05-02 (Steve delta #3 from D1 design).
The field exists so dashboard UX can surface "based on N% of available
evidence" during partial-data windows (post-mig backfill, freshly-added
framework, paused ingest).

Today (post-D1 backfill, 0 NULL rows in evidence_framework_mappings)
the field is 100.0 for every site/framework — but the field MUST be in
the API response shape so the frontend can wire to it once partial
windows occur.

This is a source-level structural test — verifies the field is in the
return-dict comprehension, not a runtime test.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_FRAMEWORKS_PY = _BACKEND / "frameworks.py"


def _get_function_body_text(name: str) -> str:
    """Find the named async function in frameworks.py and return its
    body as text."""
    text = _FRAMEWORKS_PY.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            # Slice the source between node.lineno and node.end_lineno
            lines = text.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"Function {name!r} not found in frameworks.py")


def test_get_compliance_scores_returns_data_completeness_field():
    """The dict returned by get_compliance_scores MUST include
    data_completeness_pct."""
    body = _get_function_body_text("get_compliance_scores")
    assert '"data_completeness_pct"' in body, (
        "get_compliance_scores in frameworks.py does not return the "
        "data_completeness_pct field. D1 followup #47 (Steve delta #3) "
        "requires this field on every compliance-score API response so "
        "the dashboard can surface 'based on N% of available evidence' "
        "during partial-data windows. Add to the return dict."
    )


def test_get_compliance_scores_computes_completeness_from_null_count():
    """The function must compute data_completeness from the actual
    NULL-vs-total ratio, not hardcode it to 100. Catches the regression
    where someone might 'simplify' by removing the count query."""
    body = _get_function_body_text("get_compliance_scores")
    assert "null_count" in body or "check_status IS NULL" in body, (
        "get_compliance_scores must compute data_completeness from the "
        "actual NULL count in evidence_framework_mappings. If you "
        "removed the count query and hardcoded 100, restore it — the "
        "field is meant to surface partial-data windows, not be a "
        "constant."
    )


def test_get_compliance_scores_documents_d1_delta():
    """The function docstring or inline comments must reference D1
    followup #47 / Steve delta #3 so future engineers know why the
    field exists."""
    body = _get_function_body_text("get_compliance_scores")
    assert "#47" in body or "Steve delta" in body or "D1 followup" in body, (
        "get_compliance_scores docstring/comments must reference the "
        "D1 followup #47 / Steve delta #3 origin so future engineers "
        "understand why data_completeness_pct exists. Don't strip the "
        "provenance."
    )
