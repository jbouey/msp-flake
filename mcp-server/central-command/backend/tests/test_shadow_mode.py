"""Unit test for shadow_agreement_ratio (Phase 15 A-spec).

Round-table QA list: test_shadow_mode.py — insufficient_data path +
agreement computation.

Shadow mode compares a candidate rule's decisions against the current
production policy WITHOUT applying the candidate. Over N comparisons,
agreement_ratio = agreements / (agreements + disagreements). If N is
too small, we return None rather than a misleading ratio.
"""
from __future__ import annotations


def test_agreement_ratio_with_enough_data():
    from flywheel_math import shadow_agreement_ratio
    assert shadow_agreement_ratio(9, 1) == 0.9
    assert shadow_agreement_ratio(5, 5) == 0.5
    assert shadow_agreement_ratio(10, 0) == 1.0
    assert shadow_agreement_ratio(0, 10) == 0.0


def test_insufficient_data_returns_none():
    """< 10 comparisons is noise, not signal."""
    from flywheel_math import shadow_agreement_ratio
    assert shadow_agreement_ratio(0, 0) is None
    assert shadow_agreement_ratio(1, 0) is None
    assert shadow_agreement_ratio(5, 4) is None
    # Exactly 10 is OK
    assert shadow_agreement_ratio(10, 0) == 1.0


def test_insufficient_flag_excluded_from_denominator():
    """Runs flagged 'insufficient' don't count as agreement or
    disagreement. Confirm the signature accepts the optional field
    without breaking the math."""
    from flywheel_math import shadow_agreement_ratio
    # 10 clear runs + 50 insufficient — still 10 meaningful samples
    assert shadow_agreement_ratio(8, 2, insufficient=50) == 0.8


def test_returns_float_not_decimal():
    """Callers round at their own layer; this helper is pure float."""
    from flywheel_math import shadow_agreement_ratio
    assert isinstance(shadow_agreement_ratio(7, 3), float)


def test_ratio_bounded_0_to_1():
    from flywheel_math import shadow_agreement_ratio
    for a, d in [(100, 0), (0, 100), (50, 50), (13, 87)]:
        r = shadow_agreement_ratio(a, d)
        assert r is not None
        assert 0.0 <= r <= 1.0
