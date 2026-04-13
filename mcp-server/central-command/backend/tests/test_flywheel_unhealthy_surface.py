"""Source-level guardrail: /flywheel-intelligence must surface
underperforming promoted rules.

Phase 15 closing: the original flywheel intelligence card had no
operator-facing way to spot a promoted rule that's silently failing
in production (e.g. L1-AUTO-SCREEN-LOCK-POLICY at 0%/31). The
absolute-floor regime detector auto-disables at 30% — but operators
need an EARLIER warning band so they can intervene before the
trigger pulls.

This test asserts the response shape includes `unhealthy_promoted_rules`
and the SQL filter uses the correct < 0.50 threshold. If a refactor
silently drops this section, CI fails loud.
"""
from __future__ import annotations

import pathlib


ROUTES_PATH = pathlib.Path(__file__).parent.parent / "routes.py"
FRONTEND_HOOKS = (
    pathlib.Path(__file__).parent.parent.parent
    / "frontend" / "src" / "hooks" / "useFleet.ts"
)
DASHBOARD_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "frontend" / "src" / "pages" / "Dashboard.tsx"
)


def test_routes_exposes_unhealthy_promoted_rules_field():
    src = ROUTES_PATH.read_text()
    assert "unhealthy_promoted_rules" in src, (
        "/flywheel-intelligence must include unhealthy_promoted_rules in response"
    )


def test_routes_filters_below_50_percent_success_rate():
    """50% is the operator early-warning threshold. The auto-disable
    regime at 30% (ABSOLUTE_LOW_RATE_CEILING) is the floor; this is
    the band ABOVE that floor where humans should look first."""
    src = ROUTES_PATH.read_text()
    assert "(r.s7::float / r.n7) < 0.50" in src, (
        "Unhealthy filter must be < 0.50 (early warning above auto-disable floor of 0.30)"
    )


def test_routes_requires_minimum_sample_size():
    """Need >= 5 calls in 7d to avoid noise on rarely-fired rules."""
    src = ROUTES_PATH.read_text()
    assert "HAVING COUNT(*) FILTER (WHERE et.created_at > NOW() - INTERVAL '7 days') >= 5" in src


def test_routes_only_promoted_rules():
    """Only show L2→L1 promoted rules — built-in rules aren't candidates
    for retirement via the flywheel."""
    src = ROUTES_PATH.read_text()
    assert "promoted_from_l2 = true" in src
    assert "l.enabled = true" in src


def test_frontend_hook_types_unhealthy_promoted_rules():
    src = FRONTEND_HOOKS.read_text()
    assert "unhealthy_promoted_rules" in src, (
        "FlywheelIntelligence interface must include unhealthy_promoted_rules"
    )
    # Spot-check field names so frontend doesn't drift from backend
    for field in ("rule_id", "runbook_id", "n7", "s7", "success_rate"):
        assert field in src, f"FlywheelIntelligence missing field: {field}"


def test_dashboard_renders_unhealthy_section():
    src = DASHBOARD_PATH.read_text()
    assert "unhealthy_promoted_rules" in src, (
        "Dashboard.tsx must render the unhealthy_promoted_rules section"
    )
    # Don't lock the exact copy, but the band concept must be communicated
    assert "underperforming" in src.lower() or "underperform" in src.lower()
