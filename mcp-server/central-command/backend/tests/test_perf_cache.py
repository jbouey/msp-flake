"""Source + behavior gate for the perf TTL cache.

Coach perf-sweep 2026-05-08 (REC-1): the dashboard / reports / per-
site surfaces all call `compute_compliance_score` and pre-fix paid
the 2.4s cost independently. The TTL cache amortizes that cost.
This gate pins both the cache helper's behavior AND the
compute_compliance_score integration shape.

Test scope (NOT *_pg.py; runs source-shape + asyncio behavior with
no DB dep):
  - cache get/set/expiry/coalescing.
  - _should_cache_score / _score_cache_key shape.
  - compute_compliance_score auditor-export bypass (window_days=None
    must NOT cache; AST + behavior assertion).
  - cache_clear is test-only (assertion in source).
"""
from __future__ import annotations

import asyncio
import pathlib
import time
import sys

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------- TTL cache

def test_cache_get_set_expiry():
    from perf_cache import cache_clear, cache_get, cache_set
    cache_clear()
    cache_set(("k1",), "v1", 0.5)
    assert cache_get(("k1",)) == "v1"
    time.sleep(0.6)
    assert cache_get(("k1",)) is None


def test_cache_invalidate_explicit():
    from perf_cache import cache_clear, cache_get, cache_invalidate, cache_set
    cache_clear()
    cache_set(("k1",), "v1", 60)
    cache_invalidate(("k1",))
    assert cache_get(("k1",)) is None


def test_cache_get_missing_returns_none():
    from perf_cache import cache_clear, cache_get
    cache_clear()
    assert cache_get(("never-set",)) is None


def test_cache_does_not_cache_failures():
    """Coach REC-1 design: failing coro factory MUST NOT poison the
    cache with a None or partial result. Re-raises the exception;
    next call retries fresh."""
    from perf_cache import cache_clear, cache_get, cached_call

    cache_clear()
    call_count = 0

    async def failing_factory():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("synthetic failure")

    async def run():
        with pytest.raises(RuntimeError):
            await cached_call(("fail-key",), 60, failing_factory)
        # Cache miss; same key fails again with FRESH call.
        with pytest.raises(RuntimeError):
            await cached_call(("fail-key",), 60, failing_factory)

    asyncio.run(run())
    assert call_count == 2, "failed coro factory must NOT be cached"
    assert cache_get(("fail-key",)) is None


def test_cached_call_coalesces_concurrent_callers():
    """Two simultaneous waiters with the same key should result in
    ONE underlying call (per-key asyncio.Lock). Closes the cache-
    stampede class."""
    from perf_cache import cache_clear, cached_call

    cache_clear()
    call_count = 0

    async def slow_factory():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "result"

    async def run():
        # Fire two concurrent calls. Only ONE underlying coro should run.
        results = await asyncio.gather(
            cached_call(("coalesce",), 60, slow_factory),
            cached_call(("coalesce",), 60, slow_factory),
        )
        assert results == ["result", "result"]

    asyncio.run(run())
    assert call_count == 1, (
        f"concurrent calls should coalesce; got {call_count} factory calls"
    )


# ---------------------------------------------------------------- compute_compliance_score integration


def test_should_cache_score_bounded_only():
    """Auditor-export `window_days=None` MUST bypass cache."""
    from compliance_score import _should_cache_score
    assert _should_cache_score(30) is True
    assert _should_cache_score(90) is True
    assert _should_cache_score(None) is False


def test_score_cache_key_includes_tenant_scope():
    """Cache key MUST include site_ids tuple (sorted) so two different
    orgs sharing a backend cache CANNOT pull each other's results.
    RLS-isolation-at-the-cache-layer."""
    from compliance_score import _score_cache_key
    k1 = _score_cache_key(["site-a"], False, 30)
    k2 = _score_cache_key(["site-b"], False, 30)
    assert k1 != k2, "cache key must differ across tenant scopes"
    # Same site_ids in different order → same key (sorted).
    k3 = _score_cache_key(["site-a", "site-b"], False, 30)
    k4 = _score_cache_key(["site-b", "site-a"], False, 30)
    assert k3 == k4, "cache key must be order-stable on site_ids"
    # include_incidents flips the key.
    k5 = _score_cache_key(["site-a"], True, 30)
    assert k1 != k5
    # window_days flips the key.
    k6 = _score_cache_key(["site-a"], False, 90)
    assert k1 != k6


def test_compute_compliance_score_source_shape():
    """Source-shape gate — the function MUST consult the cache for
    bounded-window paths AND write back on miss. Pinned via AST-walk
    over the source string. Phase A (Task #83) loosened the literal-
    arg-list pin to a regex so window_start/window_end can be added
    to _should_cache_score without re-litigating this gate."""
    import re
    src = (_BACKEND / "compliance_score.py").read_text()
    # Bounded-window cache check at the top of the body — accept any
    # arg list inside the parens (window_days, plus optional bounds).
    assert re.search(r"_should_cache_score\s*\([^)]*\)", src), (
        "compute_compliance_score must call _should_cache_score(...). "
        "Phase A allows additional bound args; the call itself must "
        "remain so the cache gate exists."
    )
    assert "cache_get(_cache_key)" in src
    # Write-back at the final return.
    assert "cache_set(_cache_key, _result, _SCORE_CACHE_TTL_SECONDS)" in src
    # Auditor-export bypass — `_should_cache_score` returns False
    # for window_days=None; verified above. Source comment also
    # cites this for future readers.
    assert "AUDITOR-EXPORT BYPASS" in src or "auditor-export" in src.lower()


def test_compute_compliance_score_ttl_pinned():
    """60s TTL is the contract per the perf-sweep audit. Halving or
    raising it changes user-visible freshness; re-round-table any
    change."""
    from compliance_score import _SCORE_CACHE_TTL_SECONDS
    assert _SCORE_CACHE_TTL_SECONDS == 60.0


def test_perf_cache_clear_marked_test_only():
    """Production code MUST NOT call cache_clear; the docstring
    pins this so future readers don't accidentally invalidate the
    whole cache from a request-path."""
    src = (_BACKEND / "perf_cache.py").read_text()
    assert "Test-only" in src or "test-only" in src.lower()
