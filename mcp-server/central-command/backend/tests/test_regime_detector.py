"""Unit test for classify_regime_delta (Phase 15 A-spec).

Round-table QA list: test_regime_detector.py — threshold math + 24h
idempotency window.

The threshold math lives in the pure function classify_regime_delta().
This test is the regression fence on those two constants: the 15%
drop threshold and the 30% critical threshold. Changing either value
is a policy decision, not a refactor — the test failure forces a
review conversation.
"""
from __future__ import annotations

import pytest


def test_no_drop_returns_none():
    from flywheel_math import classify_regime_delta
    # Perfect stability
    assert classify_regime_delta(0.95, 0.95) is None
    # Drop smaller than 15% — still within noise
    assert classify_regime_delta(0.85, 0.90) is None
    assert classify_regime_delta(0.80, 0.90) is None


def test_borderline_threshold_just_above_15_percent_drop():
    """A drop smaller than -0.15 (in magnitude) returns None.
    Note: IEEE-754 subtraction can produce tiny overshoots, so we
    test well-inside-the-range values rather than the exact
    boundary."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.86, 1.00) is None  # delta = -0.14
    assert classify_regime_delta(0.89, 1.00) is None  # delta = -0.11


def test_borderline_threshold_well_below_15_percent_drop():
    """A drop larger than -0.15 triggers warning."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.82, 1.00) == "warning"  # delta = -0.18
    assert classify_regime_delta(0.75, 0.95) == "warning"  # delta = -0.20


def test_warning_severity_range():
    """Between -0.15 and -0.30 → warning."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.70, 0.90) == "warning"
    assert classify_regime_delta(0.60, 0.85) == "warning"
    assert classify_regime_delta(0.50, 0.70) == "warning"


def test_critical_at_30_percent_drop():
    """Exactly -0.30 IS critical (<=)."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.60, 0.90) == "critical"
    assert classify_regime_delta(0.50, 0.80) == "critical"


def test_catastrophic_drop_still_critical():
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.0, 1.0) == "critical"
    assert classify_regime_delta(0.1, 0.9) == "critical"


def test_improvements_never_flagged():
    """A rule getting BETTER should never be flagged — positive deltas
    return None regardless of magnitude."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(1.0, 0.5) is None
    assert classify_regime_delta(0.95, 0.50) is None


def test_symmetric_rates_do_not_panic():
    """0.0 / 0.0 rates (all failures) → delta 0 → no regime event.
    1.0 / 1.0 rates (all successes) → same. Edge cases that must
    not fire false positives."""
    from flywheel_math import classify_regime_delta
    assert classify_regime_delta(0.0, 0.0) is None
    assert classify_regime_delta(1.0, 1.0) is None


def test_threshold_constants_exported():
    """Make the thresholds public constants so documentation + SLO
    doc can reference them by name. Values MUST match the numbers
    baked into the Phase 6 policy doc."""
    from flywheel_math import (
        REGIME_DROP_THRESHOLD, REGIME_CRITICAL_THRESHOLD,
    )
    assert REGIME_DROP_THRESHOLD == -0.15
    assert REGIME_CRITICAL_THRESHOLD == -0.30
    assert REGIME_CRITICAL_THRESHOLD < REGIME_DROP_THRESHOLD, (
        "critical threshold must be more negative than drop threshold"
    )
