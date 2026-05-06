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
    """The contract: `compute_compliance_score(conn, site_ids, *, include_incidents=False, window_days=90)`.
    Changing this signature requires updating ALL three call sites
    in lockstep — pin via this gate."""
    src = _read(_BACKEND / "compliance_score.py")
    assert "async def compute_compliance_score(" in src
    # Must accept a list of site_ids + the keyword args
    assert "site_ids: List[str]" in src
    assert "include_incidents: bool = False" in src
    # Round-table 30 (2026-05-05): canonical query now bounds at
    # 90 days by default to keep dashboard-load p95 under 1s on
    # 100K+-bundle orgs. None overrides for auditor-export contexts.
    assert "window_days: Optional[int]" in src


def test_compute_compliance_score_default_window_is_30_days():
    """Round-table 30 (2026-05-05) ratchet — pinning the 30-day default
    after empirical profiling (90d=3.7s, 30d=2.6s, 7d=632ms on the
    155K-bundle North Valley org). 30 days keeps weekly-cadence checks
    in scope (≥4 runs in window) while staying under the 3s wall-clock
    target for cold dashboard loads. Auditor-kit + evidence archive
    pass `window_days=None` to read the full chain.

    Round-table 32 (Maya P2 strengthening): pre-fix this gate was a
    pure string-match — pinned the literal `DEFAULT_WINDOW_DAYS = 30`
    + the signature default, but did NOT verify the SQL actually
    applies the filter. A refactor that ignores the constant in SQL
    would pass. Now also asserts the bounded-query SQL contains the
    `cb.checked_at > NOW() - ($N::int * INTERVAL '1 day')` pattern
    AND that the asyncpg call passes `window_days` as a parameter.
    """
    src = _read(_BACKEND / "compliance_score.py")
    assert "DEFAULT_WINDOW_DAYS = 30" in src, (
        "Round-table 30 default-window contract changed. If lowering "
        "the default further, document why in the round-table doc and "
        "update this test. If raising it, profile the canonical query "
        "for an org with 100K+ bundles first — the unbounded version "
        "took 4.7s on North Valley with 155K bundles."
    )
    assert "window_days: Optional[int] = DEFAULT_WINDOW_DAYS" in src

    # Behavior pin: the bounded-query branch must apply the
    # `checked_at > NOW() - ($N::int * INTERVAL '1 day')` filter and
    # pass window_days into the asyncpg call.
    bounded_filter_pat = re.compile(
        r"cb\.checked_at\s*>\s*NOW\s*\(\s*\)\s*-\s*\(\s*\$\d+::int\s*\*\s*INTERVAL\s*'1\s*day'\s*\)",
        re.IGNORECASE,
    )
    assert bounded_filter_pat.search(src), (
        "compute_compliance_score's bounded-query branch is missing "
        "the parameterized `checked_at > NOW() - ($N::int * INTERVAL '1 day')` "
        "filter. If you've refactored the SQL, ensure the filter is "
        "still parameterized so window_days flows through."
    )

    # Behavior pin: window_days must be passed as a query parameter.
    # The bounded-query branch's conn.fetch(...) call should include
    # `window_days` as one of the positional args.
    fetch_pat = re.compile(
        r"conn\.fetch\(\s*\"\"\"[^\"]*?cb\.checked_at\s*>\s*NOW\(\)[^\"]*?\"\"\"\s*,\s*site_ids\s*,\s*window_days",
        re.IGNORECASE | re.DOTALL,
    )
    assert fetch_pat.search(src), (
        "compute_compliance_score doesn't pass window_days as a "
        "parameter to the bounded conn.fetch. The constant-define + "
        "signature-default-set string-matches pass but the SQL would "
        "ignore the value at runtime."
    )

    # Behavior pin: the unbounded branch (window_days=None) must NOT
    # apply the time filter — auditor-kit + evidence archive depend on
    # this for full-chain reads.
    unbounded_branch_pat = re.compile(
        r"if\s+window_days\s+is\s+None\s*:[^}]*?WHERE\s+cb\.site_id\s*=\s*ANY",
        re.IGNORECASE | re.DOTALL,
    )
    assert unbounded_branch_pat.search(src), (
        "compute_compliance_score's window_days=None branch isn't "
        "wired correctly — auditor-kit / evidence archive contexts "
        "require all-time SQL with NO checked_at filter for full-chain reads."
    )


def test_score_window_unbounded_overrides_for_chain_reads():
    """Maya round-table 32: the auditor-kit + evidence archive paths
    intentionally pass `window_days=None` so the chain-of-custody view
    is unfiltered. If a future refactor removed the None branch, the
    auditor-kit ZIP would silently ship only the last 30 days of
    bundles — chain integrity break.
    """
    src = _read(_BACKEND / "compliance_score.py")
    # The signature must accept Optional[int]
    assert "window_days: Optional[int]" in src
    # The function body must branch on `window_days is None` to choose
    # the unbounded SQL.
    assert "if window_days is None:" in src, (
        "compute_compliance_score must branch on `window_days is None` "
        "to select the all-time SQL path. Without this branch the "
        "auditor-kit / evidence archive would silently lose pre-30d "
        "bundles."
    )


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
