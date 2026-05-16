"""Pin test: compute_compliance_score must accept _skip_cache kwarg
(Task #64 Phase 2c).

Substrate invariant `_check_canonical_compliance_score_drift` calls
the helper with `_skip_cache=True` to bypass the 60s TTL cache —
without it, a sample captured at t=0 would be compared against the
cached version of itself within cache TTL → false-negative drift
detection.

This test pins the parameter so a future PR can't silently remove it
or rename it and break the substrate invariant.
"""
from __future__ import annotations

import inspect

import compliance_score


def test_compute_compliance_score_has_skip_cache_kwarg():
    """The kwarg exists with the right name + default + KEYWORD_ONLY."""
    sig = inspect.signature(compliance_score.compute_compliance_score)
    params = sig.parameters
    assert "_skip_cache" in params, (
        "compute_compliance_score() must accept `_skip_cache` keyword "
        "argument. Substrate invariant Phase 2c relies on it to bypass "
        "the 60s TTL cache during drift detection — without it, the "
        "sample-vs-recompute comparison collapses to a no-op within "
        "cache TTL window."
    )
    p = params["_skip_cache"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        "_skip_cache must be KEYWORD_ONLY (after *,) — positional "
        "passing would be a silent API drift class."
    )
    assert p.default is False, (
        "_skip_cache default must be False so production callers are "
        "unaffected. Only substrate invariant passes True."
    )


def test_skip_cache_kwarg_bypasses_cache_logic():
    """Source-walk: when _skip_cache=True, the cache-enabled gate must
    short-circuit. This is the LOAD-BEARING contract the substrate
    invariant depends on. Pin loosened in Phase A (Task #83) to allow
    _should_cache_score() to take additional bound args (window_start/
    window_end) — the contract is `_skip_cache` ANDed via `not`, not
    the exact arg list."""
    import re
    src = inspect.getsource(compliance_score.compute_compliance_score)
    # Match: `_should_cache_score(...) and not _skip_cache` with any
    # arg list in the parens.
    pat = re.compile(
        r"_should_cache_score\s*\([^)]*\)\s+and\s+not\s+_skip_cache",
        re.DOTALL,
    )
    assert pat.search(src), (
        "Cache-enabled gate must AND in `not _skip_cache`. Without "
        "this, `_skip_cache=True` would still hit the cache and "
        "produce stale comparisons in the substrate invariant. "
        "Phase A (Task #83) — _should_cache_score may take additional "
        "args (window_start/window_end) but the AND-in must remain."
    )
