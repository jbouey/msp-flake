"""Unit tests for `compliance_score.compute_category_weighted_overall`
— the canonical per-category compliance score primitive.

Shipped 2026-05-16 (Task #103 Fork B close-out) as the canonical
extraction of the `(pass + 0.5*warn) / total * 100` partial-credit
formula. All 4 category_weighted_compliance_score callsites delegate:
  - db_queries.get_compliance_scores_for_site (HIPAA-weighted)
  - db_queries.get_all_compliance_scores (HIPAA-weighted)
  - routes.get_admin_compliance_health (HIPAA-weighted)
  - client_portal.get_site_compliance_health (UNWEIGHTED)

This test suite pins the primitive's contract directly — protects
against future drift in the shared formula without going through
the integration paths of the 4 callsites.
"""
from __future__ import annotations

import sys
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

# Direct import — compute_category_weighted_overall is a pure-Python
# function with no FastAPI dependencies (typing only).
try:
    from compliance_score import compute_category_weighted_overall
except ImportError:
    # Fall back to relative-import context (package-execution case)
    from .compliance_score import compute_category_weighted_overall  # type: ignore


# Standard HIPAA category weights matching db_queries.HIPAA_CATEGORY_WEIGHTS
_HIPAA_WEIGHTS = {
    "patching": 0.10,
    "antivirus": 0.10,
    "backup": 0.20,
    "logging": 0.10,
    "firewall": 0.10,
    "encryption": 0.25,
    "access_control": 0.15,
}


# ─────────────────────────────────────────────────────────────────────
# Per-category breakdown tests
# ─────────────────────────────────────────────────────────────────────


def test_all_passes_yields_100_per_category():
    """Categories with all passing checks score 100."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 5, "b": 3},
        cat_fail={"a": 0, "b": 0},
        cat_warn={"a": 0, "b": 0},
    )
    assert breakdown == {"a": 100, "b": 100}
    assert overall == 100.0


def test_all_fails_yields_0_per_category():
    """Categories with all failing checks score 0."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 0, "b": 0},
        cat_fail={"a": 5, "b": 3},
        cat_warn={"a": 0, "b": 0},
    )
    assert breakdown == {"a": 0, "b": 0}
    assert overall == 0.0


def test_partial_credit_for_warnings():
    """Warnings count as 0.5 of a pass (the load-bearing semantic)."""
    # 10 checks: 4 pass, 4 warn, 2 fail
    # raw = (4 + 0.5*4) / 10 * 100 = 60.0 → rounded to 60
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"x": 4},
        cat_fail={"x": 2},
        cat_warn={"x": 4},
    )
    assert breakdown == {"x": 60}
    assert overall == 60.0


def test_partial_credit_warning_kwarg_overrides_default():
    """Override partial_credit_warning to verify the formula uses it."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"x": 4},
        cat_fail={"x": 2},
        cat_warn={"x": 4},
        partial_credit_warning=0.0,  # warnings count as fails
    )
    # raw = (4 + 0*4) / 10 * 100 = 40.0 → rounded to 40
    assert breakdown == {"x": 40}
    assert overall == 40.0


def test_zero_total_yields_none_for_category():
    """Categories with total==0 → None in breakdown (not 0)."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 5, "empty": 0},
        cat_fail={"a": 0, "empty": 0},
        cat_warn={"a": 0, "empty": 0},
    )
    assert breakdown["a"] == 100
    assert breakdown["empty"] is None
    # overall should be the average of non-None categories
    assert overall == 100.0


def test_empty_input_yields_none_overall():
    """No categories with data → None overall (not 0, not 100)."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 0, "b": 0},
        cat_fail={"a": 0, "b": 0},
        cat_warn={"a": 0, "b": 0},
    )
    assert breakdown == {"a": None, "b": None}
    assert overall is None


# ─────────────────────────────────────────────────────────────────────
# Weighting tests
# ─────────────────────────────────────────────────────────────────────


def test_unweighted_overall_is_simple_average():
    """`category_weights=None` → unweighted average across categories
    with data. Matches client_portal.get_site_compliance_health
    behavior before the refactor."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 9, "b": 5, "c": 0},
        cat_fail={"a": 1, "b": 5, "c": 0},
        cat_warn={"a": 0, "b": 0, "c": 0},
        category_weights=None,
    )
    # a: 90, b: 50, c: None
    # overall = (90 + 50) / 2 = 70.0
    assert breakdown == {"a": 90, "b": 50, "c": None}
    assert overall == 70.0


def test_weighted_overall_uses_category_weights():
    """`category_weights={...}` → HIPAA-weighted average. Matches
    db_queries.get_compliance_scores_for_site + admin endpoint."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"encryption": 10, "patching": 5},
        cat_fail={"encryption": 0, "patching": 5},
        cat_warn={"encryption": 0, "patching": 0},
        category_weights={"encryption": 0.25, "patching": 0.10},
    )
    # encryption: 100, patching: 50
    # weighted = (100 * 0.25 + 50 * 0.10) / (0.25 + 0.10) = 30/0.35 ≈ 85.7
    assert breakdown == {"encryption": 100, "patching": 50}
    assert overall == 85.7


def test_default_weight_for_unmapped_category():
    """Categories missing from `category_weights` use `default_weight`
    (default 0.06). Prevents KeyError on novel categories."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"novel_category": 10},
        cat_fail={"novel_category": 0},
        cat_warn={"novel_category": 0},
        category_weights={"existing": 0.5},  # novel_category missing
        default_weight=0.06,
    )
    # novel_category gets weight 0.06; sole category
    # overall = 100 * 0.06 / 0.06 = 100.0
    assert breakdown == {"novel_category": 100}
    assert overall == 100.0


def test_hipaa_weights_real_world():
    """Smoke-test with the actual HIPAA_CATEGORY_WEIGHTS dict shape
    that 3 production callsites pass in."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"patching": 9, "antivirus": 10, "backup": 8,
                  "logging": 10, "firewall": 10, "encryption": 10,
                  "access_control": 7},
        cat_fail={"patching": 1, "antivirus": 0, "backup": 2,
                  "logging": 0, "firewall": 0, "encryption": 0,
                  "access_control": 3},
        cat_warn={"patching": 0, "antivirus": 0, "backup": 0,
                  "logging": 0, "firewall": 0, "encryption": 0,
                  "access_control": 0},
        category_weights=_HIPAA_WEIGHTS,
    )
    # All 7 categories have data, none None
    assert all(v is not None for v in breakdown.values())
    # Overall should be 0 <= x <= 100
    assert overall is not None
    assert 0 <= overall <= 100
    # Encryption is 100 (10/10) with weight 0.25 — pulls overall up.
    # Quick sanity check: should be > 85 (highly-weighted categories pass).
    assert overall > 85


# ─────────────────────────────────────────────────────────────────────
# Rounding tests
# ─────────────────────────────────────────────────────────────────────


def test_overall_rounding_decimals_kwarg():
    """`overall_round_decimals=0` → integer overall."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 7},
        cat_fail={"a": 3},
        cat_warn={"a": 0},
        category_weights={"a": 1.0},
        overall_round_decimals=0,
    )
    # 70/1 = 70 (already integer)
    assert overall == 70


def test_breakdown_rounding_decimals_kwarg():
    """`breakdown_round_decimals=2` → float breakdown with 2 decimals."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"a": 1},
        cat_fail={"a": 2},
        cat_warn={"a": 0},
        breakdown_round_decimals=2,
    )
    # 1/3 * 100 = 33.333... → 33.33
    assert breakdown["a"] == 33.33


# ─────────────────────────────────────────────────────────────────────
# Behavioral preservation per-callsite (regression guards)
# ─────────────────────────────────────────────────────────────────────


def test_client_portal_unweighted_behavior_preserved():
    """client_portal.get_site_compliance_health passes
    `category_weights=None`. Verifies the unweighted-average path
    matches the prior inline behavior (overall_sum / cats_with_data)."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"patching": 8, "backup": 6, "encryption": 10},
        cat_fail={"patching": 2, "backup": 4, "encryption": 0},
        cat_warn={"patching": 0, "backup": 0, "encryption": 0},
        category_weights=None,
    )
    # patching: 80, backup: 60, encryption: 100
    # unweighted = (80 + 60 + 100) / 3 = 80.0
    assert breakdown == {"patching": 80, "backup": 60, "encryption": 100}
    assert overall == 80.0


def test_db_queries_hipaa_weighted_behavior_preserved():
    """db_queries.get_compliance_scores_for_site passes HIPAA weights.
    Verifies the weighted-average path matches prior inline behavior
    (weighted_sum / weight_sum)."""
    breakdown, overall = compute_category_weighted_overall(
        cat_pass={"encryption": 10, "patching": 8, "antivirus": 10},
        cat_fail={"encryption": 0, "patching": 2, "antivirus": 0},
        cat_warn={"encryption": 0, "patching": 0, "antivirus": 0},
        category_weights={
            "encryption": 0.25, "patching": 0.10, "antivirus": 0.10,
        },
    )
    # encryption: 100, patching: 80, antivirus: 100
    # weighted = (100*0.25 + 80*0.10 + 100*0.10) / (0.25+0.10+0.10)
    #         = (25 + 8 + 10) / 0.45 = 43/0.45 ≈ 95.6
    assert breakdown == {"encryption": 100, "patching": 80, "antivirus": 100}
    assert overall == 95.6
