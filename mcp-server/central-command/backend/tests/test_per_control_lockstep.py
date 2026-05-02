"""CI gate: per-control aggregation taxonomy three-list lockstep.

D1 fix 2026-05-02 — close the score-whiplash class. The per-control
status taxonomy must stay in lockstep across THREE places:
  1. Writer's `PASSING` + `FAILING` constants in evidence_chain.py
     (map_evidence_to_frameworks helper) — defines aggregation rule
  2. Migration 271's CHECK constraint values for
     evidence_framework_mappings.check_status — defines storage domain
  3. Migration 271's calculate_compliance_score function literal
     comparisons — defines reader semantics

Drift between any two = silent score regression. Mirrors the
flywheel three-list lockstep pattern (event_type CHECK +
EVENT_TYPES frozenset + lifecycle matrix).

What this gate verifies:
- The CHECK domain is exactly {pass, fail, unknown} OR a documented
  superset (extends-only — never shrinks).
- The function's WHERE/FILTER literals are a subset of the CHECK
  domain (function can't filter on a value the storage rejects).
- The writer's _agg() return values are a subset of the CHECK domain.

If any one of these falls out of sync, scores silently break.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
_EVIDENCE_CHAIN = _BACKEND_DIR / "evidence_chain.py"
_MIGRATION = _BACKEND_DIR / "migrations" / "271_evidence_framework_mappings_per_control_status.sql"


# ── Domain extracted from migration 271 CHECK constraint ────────────

_CHECK_DOMAIN_PATTERN = re.compile(
    r"check_status\s+IN\s*\(\s*([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_check_domain() -> set[str]:
    sql = _MIGRATION.read_text()
    matches = _CHECK_DOMAIN_PATTERN.findall(sql)
    if not matches:
        raise AssertionError(
            f"Could not find CHECK constraint IN-list in {_MIGRATION.name}"
        )
    # First match is the CHECK constraint definition
    raw_literals = re.findall(r"'([^']+)'", matches[0])
    return set(raw_literals)


# ── Reader values from the function body ────────────────────────────

_FUNCTION_FILTER_PATTERN = re.compile(
    r"check_status\s*=\s*'([^']+)'",
    re.IGNORECASE,
)


def _extract_function_filters() -> set[str]:
    sql = _MIGRATION.read_text()
    return set(_FUNCTION_FILTER_PATTERN.findall(sql))


# ── Writer's _agg() return values via AST ───────────────────────────

def _extract_writer_returns() -> set[str]:
    """AST-walk evidence_chain.py to find the _agg() function's return
    string literals. Bounded scope: only looks inside the
    map_evidence_to_frameworks function."""
    tree = ast.parse(_EVIDENCE_CHAIN.read_text())
    returns: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "map_evidence_to_frameworks":
            for inner in ast.walk(node):
                if isinstance(inner, ast.FunctionDef) and inner.name == "_agg":
                    for ret_node in ast.walk(inner):
                        if (
                            isinstance(ret_node, ast.Return)
                            and isinstance(ret_node.value, ast.Constant)
                            and isinstance(ret_node.value.value, str)
                        ):
                            returns.add(ret_node.value.value)
    return returns


# ── Lockstep tests ──────────────────────────────────────────────────

def test_check_domain_includes_required_values():
    """Storage CHECK constraint must accept pass, fail, unknown."""
    domain = _extract_check_domain()
    required = {"pass", "fail", "unknown"}
    missing = required - domain
    assert not missing, (
        f"CHECK constraint on evidence_framework_mappings.check_status "
        f"is missing required values: {sorted(missing)}. The writer "
        f"_agg() helper produces these — without them the INSERT will "
        f"fail with check_violation and the writer's savepoint will "
        f"swallow the error, leaving check_status NULL."
    )


def test_function_filter_values_are_subset_of_check_domain():
    """The function's `WHERE check_status = 'X'` clauses must reference
    values the CHECK constraint accepts. Otherwise the filter is
    structurally dead — it can never match."""
    function_values = _extract_function_filters()
    domain = _extract_check_domain()
    invalid = function_values - domain
    assert not invalid, (
        f"calculate_compliance_score filters on values not in the "
        f"CHECK constraint domain: {sorted(invalid)}. Storage will never "
        f"contain these values — filter is structurally dead."
    )


def test_writer_returns_are_subset_of_check_domain():
    """The writer's _agg() return literals must all be values the
    CHECK constraint accepts. Otherwise INSERT will fail."""
    writer_values = _extract_writer_returns()
    domain = _extract_check_domain()
    invalid = writer_values - domain
    assert not invalid, (
        f"Writer _agg() returns values not in the CHECK domain: "
        f"{sorted(invalid)}. INSERT will raise check_violation; the "
        f"savepoint will swallow it; check_status will remain NULL "
        f"and the score will under-report."
    )


def test_writer_returns_cover_check_domain():
    """Bidirectional check: every value the storage CHECK accepts
    should be reachable by the writer. A storage-only value with no
    writer path = dead column space (or future state we forgot to wire)."""
    writer_values = _extract_writer_returns()
    domain = _extract_check_domain()
    unreachable = domain - writer_values
    assert not unreachable, (
        f"Storage CHECK accepts values the writer never produces: "
        f"{sorted(unreachable)}. Either remove from CHECK or wire the "
        f"writer to produce them. If this is intentional (e.g. a "
        f"reserved value populated by a different pathway), document "
        f"the path here and add to an exemption set."
    )


# ── Cross-location PASSING/FAILING lockstep (D1 followup #48) ──────

def _extract_taxonomy_sets() -> list[tuple[int, set[str], set[str]]]:
    """Find all (PASSING, FAILING) constant pairs in evidence_chain.py.
    Two locations today (both within evidence_chain.py):
      - line ~1137: lowercase `passing` / `failing` in
        `derive_check_result` codepath
      - line ~1597: uppercase `PASSING` / `FAILING` in
        `map_evidence_to_frameworks` per-control aggregation
    Both MUST agree byte-for-byte; otherwise a bundle's bundle-level
    `check_result` derivation taxonomy could differ from the per-control
    aggregation taxonomy → silent score regression class.

    Returns list of (line_number, passing_set, failing_set).
    """
    text = _EVIDENCE_CHAIN.read_text()
    pairs: list[tuple[int, set[str], set[str]]] = []

    # Iterate line-by-line looking for `(?i)passing\s*=\s*\{...\}` near
    # a `(?i)failing\s*=\s*\{...\}` (within 3 lines).
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m_p = re.search(
            r"(?i)\b(passing)\s*=\s*\{([^}]+)\}",
            line,
        )
        if not m_p:
            continue
        # Look up to 3 lines after for FAILING
        for j in range(i + 1, min(i + 4, len(lines))):
            m_f = re.search(
                r"(?i)\b(failing)\s*=\s*\{([^}]+)\}",
                lines[j],
            )
            if m_f:
                passing_set = set(re.findall(r"'([^']+)'", m_p.group(2)))
                failing_set = set(re.findall(r"'([^']+)'", m_f.group(2)))
                pairs.append((i + 1, passing_set, failing_set))
                break
    return pairs


def test_all_passing_failing_taxonomy_sites_agree():
    """Cross-location: every PASSING/FAILING constant pair in
    evidence_chain.py must use IDENTICAL value sets. D1 followup #48.

    Today there are 2 sites (line ~1137 + line ~1597). If a future
    refactor splits the taxonomy across files, extend this test to
    walk a documented file list rather than just evidence_chain.py.
    """
    pairs = _extract_taxonomy_sets()
    assert len(pairs) >= 2, (
        f"Expected ≥2 PASSING/FAILING pairs in evidence_chain.py for "
        f"this gate to be meaningful; found {len(pairs)}. Either the "
        f"writer was refactored to a single source-of-truth (which is "
        f"GREAT — delete this test) or the gate's locator regex needs "
        f"updating."
    )

    canonical_passing = pairs[0][1]
    canonical_failing = pairs[0][2]
    for line_num, passing, failing in pairs[1:]:
        assert passing == canonical_passing, (
            f"PASSING taxonomy at evidence_chain.py:{line_num} "
            f"({sorted(passing)}) differs from canonical at "
            f"evidence_chain.py:{pairs[0][0]} ({sorted(canonical_passing)}). "
            f"Both writer codepaths must use identical taxonomy or "
            f"bundle-level vs per-control scoring silently diverge."
        )
        assert failing == canonical_failing, (
            f"FAILING taxonomy at evidence_chain.py:{line_num} "
            f"({sorted(failing)}) differs from canonical at "
            f"evidence_chain.py:{pairs[0][0]} ({sorted(canonical_failing)})."
        )
