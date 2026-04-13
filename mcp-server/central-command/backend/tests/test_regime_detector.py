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


# ─── Phase 15 closing: classify_absolute_floor ────────────────────


def test_absolute_floor_fires_when_rate_below_threshold():
    """A rule with rate < 0.30 + N >= 20 + age > 24h must fire."""
    from flywheel_math import classify_absolute_floor
    assert classify_absolute_floor(0.0, 31, 100) == "absolute_low"
    assert classify_absolute_floor(0.10, 50, 200) == "absolute_low"
    assert classify_absolute_floor(0.29, 25, 25) == "absolute_low"


def test_absolute_floor_silent_when_rate_at_or_above_threshold():
    """0.30 is the ceiling — at or above is fine."""
    from flywheel_math import classify_absolute_floor
    assert classify_absolute_floor(0.30, 100, 100) is None
    assert classify_absolute_floor(0.50, 100, 100) is None
    assert classify_absolute_floor(0.99, 100, 100) is None


def test_absolute_floor_silent_below_min_samples():
    """N=19 too few — could be noise. N=20 is the floor."""
    from flywheel_math import classify_absolute_floor
    assert classify_absolute_floor(0.0, 19, 100) is None
    assert classify_absolute_floor(0.0, 1, 100) is None
    assert classify_absolute_floor(0.0, 20, 100) == "absolute_low"


def test_absolute_floor_silent_during_canary_window():
    """First 24 hours belong to the 48h canary; absolute_low waits."""
    from flywheel_math import classify_absolute_floor
    assert classify_absolute_floor(0.0, 100, 0) is None       # just promoted
    assert classify_absolute_floor(0.0, 100, 12) is None      # 12h old
    assert classify_absolute_floor(0.0, 100, 23) is None      # 23h old
    assert classify_absolute_floor(0.0, 100, 25) == "absolute_low"


def test_absolute_floor_constants_exported():
    from flywheel_math import (
        ABSOLUTE_LOW_RATE_CEILING,
        ABSOLUTE_LOW_MIN_SAMPLES,
        ABSOLUTE_LOW_RULE_AGE_HOURS,
    )
    assert ABSOLUTE_LOW_RATE_CEILING == 0.30
    assert ABSOLUTE_LOW_MIN_SAMPLES == 20
    assert ABSOLUTE_LOW_RULE_AGE_HOURS == 24


def test_absolute_floor_screen_lock_scenario():
    """Reproduces the prod incident that motivated this:
    L1-AUTO-SCREEN-LOCK-POLICY at 0% over 31 calls, rule promoted
    weeks ago. The 48h canary missed it; regime delta returns None
    because rate_30 ≈ rate_7 ≈ 0. Absolute floor MUST catch it."""
    from flywheel_math import classify_absolute_floor, classify_regime_delta
    rate_7 = 0.0
    rate_30 = 0.0
    n_7 = 31
    rule_age_hours = 24 * 14  # 14 days old
    # Delta branch returns None
    assert classify_regime_delta(rate_7, rate_30) is None
    # Absolute branch catches it
    assert classify_absolute_floor(rate_7, n_7, rule_age_hours) == "absolute_low"
