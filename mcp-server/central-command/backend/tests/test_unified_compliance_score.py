"""CI gate: the three client-portal score-bearing endpoints all share
one canonical compute path.

Round-table 2026-05-05 Stage 2
(.agent/plans/25-client-portal-data-display-roundtable-2026-05-05.md).

Pre-Stage-2 each endpoint had its own formula. Post-Stage-2 they all
delegate to `compliance_score.compute_compliance_score()`. This gate
enforces that:
  1. The helper module exists with the canonical signature
  2. All three endpoints import + call it for their headline number
  3. No endpoint has its own ad-hoc score formula reintroduced

Behavior tests against synthetic-org data are out of scope here (they
need DB); this is a source-level governance gate.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _read(p: pathlib.Path) -> str:
    return p.read_text()


def _find_function(src: str, name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(src, node, padded=False) or ""
    return ""


# ─── Canonical helper present + correct shape ────────────────────


def test_compliance_score_module_present():
    assert (_BACKEND / "compliance_score.py").exists()


def test_compute_compliance_score_signature_stable():
    """The contract: `compute_compliance_score(conn, site_ids, *, include_incidents=False)`.
    Changing this signature requires updating ALL three call sites
    in lockstep — pin via this gate."""
    src = _read(_BACKEND / "compliance_score.py")
    assert "async def compute_compliance_score(" in src
    # Must accept a list of site_ids and an include_incidents kwarg
    # (used by the per-site endpoint to fold open incidents in).
    assert "site_ids: List[str]" in src
    assert "include_incidents: bool = False" in src


def test_canonical_helper_returns_structured_result():
    """The dataclass must expose: overall_score, status, counts,
    last_check_at, by_site. Adding fields is fine; removing breaks
    callers."""
    src = _read(_BACKEND / "compliance_score.py")
    for field in [
        "overall_score: Optional[float]",
        "status: str",
        "counts: Dict[str, int]",
        "last_check_at: Optional[datetime]",
        "by_site:",
    ]:
        assert field in src, f"compliance_score.py missing field `{field}`"


def test_no_dishonest_default_in_canonical_helper():
    """Maya P0 (round-table 2026-05-05): never `else 100.0`. Helper
    MUST return overall_score=None when source set is empty."""
    src = _read(_BACKEND / "compliance_score.py")
    # Find the no-data return path — must set overall_score=None
    no_data = re.search(
        r"if total == 0:.*?return ComplianceScore\((.*?)\)",
        src, re.DOTALL,
    )
    assert no_data, "no-data return path not found"
    assert "overall_score=None" in no_data.group(1)
    assert 'status="no_data"' in no_data.group(1)


# ─── All three surfaces use the canonical helper ──────────────────


def test_dashboard_uses_canonical_helper():
    src = _read(_BACKEND / "client_portal.py")
    fn = _find_function(src, "get_dashboard")
    assert fn
    assert "compute_compliance_score(" in fn, (
        "/api/client/dashboard does not delegate to "
        "compute_compliance_score — Stage 2 unification regressed."
    )
    # Pre-Stage-2 the dashboard had its own 24h-window KPI query +
    # 70/30 bundle/agent blend. Both must be gone.
    assert "INTERVAL '24 hours'" not in fn, (
        "Dashboard still has the pre-Stage-2 24h window query — should "
        "delegate to canonical."
    )
    assert "bundle_score * 0.7" not in fn, (
        "Dashboard still has the pre-Stage-2 70/30 blend — agent "
        "compliance is now its own sibling tile, not blended."
    )


def test_reports_current_uses_canonical_helper():
    src = _read(_BACKEND / "client_portal.py")
    fn = _find_function(src, "get_current_compliance_snapshot")
    assert fn
    assert "compute_compliance_score(" in fn, (
        "/api/client/reports/current does not delegate to "
        "compute_compliance_score — Stage 2 unification regressed."
    )


def test_site_compliance_health_uses_canonical_helper():
    src = _read(_BACKEND / "client_portal.py")
    fn = _find_function(src, "get_site_compliance_health")
    assert fn
    assert "compute_compliance_score(" in fn, (
        "/api/client/sites/{id}/compliance-health does not delegate to "
        "compute_compliance_score for the headline number."
    )
    # The per-category breakdown is supplementary and remains.
    assert "breakdown" in fn
    # Per-category AVERAGE remains accessible as `category_average_score`
    # for backward-compat callers but is NOT the headline.
    assert "category_average_score" in fn, (
        "Per-site endpoint should expose the legacy per-category "
        "average as a sibling field for callers that depended on it."
    )


# ─── No ad-hoc score formula reintroduction ───────────────────────


def test_no_ad_hoc_score_formula_in_endpoints():
    """Maya consistency-coach gate: the headline-score endpoints must
    NOT contain `passed / total * 100` formulas inline. The canonical
    helper owns the formula. Inline math == drift."""
    src = _read(_BACKEND / "client_portal.py")
    # Strip comments to avoid false matches on documentary references.
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    for fn_name in [
        "get_dashboard",
        "get_current_compliance_snapshot",
    ]:
        fn = _find_function(code, fn_name)
        if not fn:
            continue
        # Strict: no `passed / total * 100` style. The per-site endpoint
        # is allowed to use it for the per-category breakdown which is
        # a different concept.
        bad = re.search(
            r"\bpassed\s*/\s*total\b|\(passed\s*/\s*total\)",
            fn,
        )
        assert not bad, (
            f"{fn_name} contains an inline pass/total formula — "
            f"should delegate to compute_compliance_score helper."
        )
