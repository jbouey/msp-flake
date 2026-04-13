"""Unit test for clamp_threshold_drift (Phase 15 A-spec).

Round-table QA list: test_promotion_threshold_tuner.py — drift cap,
clamping.

The Bayesian-proposed threshold can move by at most 0.02 per day
since last update, and is hard-clamped to [floor, ceiling]. This
test is the regression fence on both behaviors.
"""
from __future__ import annotations


def test_no_drift_when_proposed_equals_current():
    from flywheel_math import clamp_threshold_drift
    result = clamp_threshold_drift(0.80, 0.80, days_since_last_update=1)
    assert result == 0.80


def test_small_proposed_move_within_cap_is_accepted():
    """Cap = 0.02/day × days. 0.02 move over 2 days is 0.04 allowed."""
    from flywheel_math import clamp_threshold_drift
    # 1 day elapsed, 0.01 proposed move → within cap 0.02
    result = clamp_threshold_drift(0.80, 0.81, days_since_last_update=1)
    assert result == 0.81


def test_large_proposed_move_capped_to_daily_limit():
    from flywheel_math import clamp_threshold_drift
    # 1 day elapsed, 0.10 proposed up-move — cap to 0.80 + 0.02
    result = clamp_threshold_drift(0.80, 0.90, days_since_last_update=1)
    assert abs(result - 0.82) < 1e-9


def test_downward_proposed_move_also_capped():
    from flywheel_math import clamp_threshold_drift
    result = clamp_threshold_drift(0.80, 0.50, days_since_last_update=1)
    # Cap to 0.80 - 0.02 = 0.78
    assert abs(result - 0.78) < 1e-9


def test_multiple_days_accumulate_drift_budget():
    """Waited 5 days, so allowed drift is 5 × 0.02 = 0.10."""
    from flywheel_math import clamp_threshold_drift
    result = clamp_threshold_drift(0.70, 0.85, days_since_last_update=5)
    # Capped to 0.70 + 0.10 = 0.80
    assert abs(result - 0.80) < 1e-9


def test_same_day_reupdate_forbidden():
    """days_since_last_update <= 0 → zero allowed drift.
    Prevents a tight loop from accelerating."""
    from flywheel_math import clamp_threshold_drift
    result = clamp_threshold_drift(0.80, 0.90, days_since_last_update=0)
    assert result == 0.80
    result = clamp_threshold_drift(0.80, 0.90, days_since_last_update=-1)
    assert result == 0.80


def test_floor_hard_clamp():
    from flywheel_math import clamp_threshold_drift
    # Current is at the floor, 10 days elapsed → drift budget 0.20
    # but proposed would go below floor anyway
    result = clamp_threshold_drift(
        current=0.50, proposed=0.10,
        days_since_last_update=10, floor=0.50, ceiling=0.95,
    )
    assert result == 0.50


def test_ceiling_hard_clamp():
    from flywheel_math import clamp_threshold_drift
    result = clamp_threshold_drift(
        current=0.95, proposed=1.50,
        days_since_last_update=10, floor=0.50, ceiling=0.95,
    )
    assert result == 0.95


def test_constants_exported():
    from flywheel_math import (
        THRESHOLD_DRIFT_CAP_PER_DAY,
        THRESHOLD_FLOOR_DEFAULT,
        THRESHOLD_CEILING_DEFAULT,
    )
    assert THRESHOLD_DRIFT_CAP_PER_DAY == 0.02
    assert THRESHOLD_FLOOR_DEFAULT == 0.50
    assert THRESHOLD_CEILING_DEFAULT == 0.95
    assert THRESHOLD_FLOOR_DEFAULT < THRESHOLD_CEILING_DEFAULT
