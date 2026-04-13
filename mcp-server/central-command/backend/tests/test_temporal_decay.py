"""Unit test for decay_factor + decayed_count (Phase 15 A-spec).

Round-table QA list: test_temporal_decay.py — decay formula +
min_count_floor.

The Phase 6 decay gives older evidence exponentially less weight.
Half-life of 90 days by default. Critical that:

  - fresh evidence (age 0) has factor 1.0
  - evidence at exactly the half-life has factor 0.5
  - the floor prevents genuinely-useful patterns from vanishing
"""
from __future__ import annotations

import math


def test_decay_factor_at_zero_age():
    from flywheel_math import decay_factor
    assert decay_factor(0, 90) == 1.0


def test_decay_factor_at_half_life():
    from flywheel_math import decay_factor
    assert abs(decay_factor(90, 90) - 0.5) < 1e-9


def test_decay_factor_at_two_half_lives():
    from flywheel_math import decay_factor
    assert abs(decay_factor(180, 90) - 0.25) < 1e-9


def test_decay_factor_at_three_half_lives():
    from flywheel_math import decay_factor
    assert abs(decay_factor(270, 90) - 0.125) < 1e-9


def test_decay_factor_monotonic_in_age():
    from flywheel_math import decay_factor
    prev = 1.0
    for days in range(1, 365, 7):
        cur = decay_factor(days, 90)
        assert cur < prev, f"decay not monotonic at {days}d"
        prev = cur


def test_decay_factor_negative_age_treated_as_zero():
    from flywheel_math import decay_factor
    assert decay_factor(-5, 90) == 1.0


def test_decay_factor_zero_half_life_raises():
    from flywheel_math import decay_factor
    import pytest
    with pytest.raises(ValueError):
        decay_factor(30, 0)


def test_decayed_count_respects_floor():
    from flywheel_math import decayed_count
    # Count of 100 → after 10 half-lives → factor ~ 0.00097 → 0.097
    # But floor 1.0 keeps it at 1.0
    result = decayed_count(100, 900, 90, min_count_floor=1.0)
    assert result == 1.0


def test_decayed_count_no_floor_effect_when_above():
    from flywheel_math import decayed_count
    # 100 × 0.5 = 50, well above floor 1.0
    result = decayed_count(100, 90, 90, min_count_floor=1.0)
    assert abs(result - 50.0) < 1e-9


def test_decayed_count_custom_floor():
    from flywheel_math import decayed_count
    result = decayed_count(100, 900, 90, min_count_floor=5.0)
    assert result == 5.0


def test_decayed_count_fresh_returns_original():
    from flywheel_math import decayed_count
    assert decayed_count(100, 0, 90) == 100.0
