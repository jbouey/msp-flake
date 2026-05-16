"""Phase A (Task #83) — pin tests for compute_compliance_score's new
window_start + window_end fixed-window params.

These tests are SOURCE-SHAPE only (no DB) — they verify the helper
exposes the new contract surface so the 4 P0 callsite migrations
(quarterly summary + 2 monthly packets + org compliance-packet) can
land cleanly without re-litigating the helper API per-callsite.

Behavioral tests against real Postgres live in test_compliance_score*_pg.py
(executed against the prod-shape pg fixture in CI).
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import compliance_score


def test_compute_compliance_score_has_window_start_kwarg():
    sig = inspect.signature(compliance_score.compute_compliance_score)
    assert "window_start" in sig.parameters, (
        "Phase A: compute_compliance_score must accept window_start "
        "kwarg (fixed-window callsites — monthly/quarterly packets)."
    )
    p = sig.parameters["window_start"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        "window_start must be KEYWORD_ONLY — positional would be an "
        "API-drift class."
    )
    assert p.default is None, (
        "window_start default must be None so the relative-window "
        "behavior (window_days) is unchanged for existing callers."
    )


def test_compute_compliance_score_has_window_end_kwarg():
    sig = inspect.signature(compliance_score.compute_compliance_score)
    assert "window_end" in sig.parameters, (
        "Phase A: compute_compliance_score must accept window_end "
        "kwarg (split-window callsite — stats-deltas 24h-ending-7d-ago)."
    )
    p = sig.parameters["window_end"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        "window_end must be KEYWORD_ONLY."
    )
    assert p.default is None, (
        "window_end default must be None."
    )


def test_fixed_window_short_circuits_window_days():
    """Source-walk: when window_start or window_end is set, the body
    must branch into a fixed-window SQL path that does NOT reference
    window_days in its WHERE clause. Pins the semantics Maya called
    out in the Phase A Gate A enumeration (`audit/coach-9-inline-
    rule-1-violations-enumeration-gate-a-2026-05-13.md` §Maya P0)."""
    src = inspect.getsource(compliance_score.compute_compliance_score)
    # The branch guard is `if window_start is not None or window_end is not None:`
    assert (
        "if window_start is not None or window_end is not None:" in src
        or "window_start is not None or window_end is not None" in src
    ), (
        "compute_compliance_score body must branch on "
        "`window_start is not None or window_end is not None` so the "
        "fixed-window SQL path runs when EITHER bound is set."
    )


def test_cache_key_includes_window_bounds():
    """_score_cache_key MUST include the resolved window_start /
    window_end bounds. Without this, two different fixed-window
    callsites (e.g., Jan vs Feb monthly packet) would collide on the
    same cache entry and serve stale results across months."""
    sig = inspect.signature(compliance_score._score_cache_key)
    assert "window_start" in sig.parameters, (
        "Phase A: _score_cache_key must accept window_start so cache "
        "keys disambiguate fixed-window callsites."
    )
    assert "window_end" in sig.parameters, (
        "Phase A: _score_cache_key must accept window_end."
    )

    # Two different bounded ranges should produce different keys.
    k1 = compliance_score._score_cache_key(
        ["site-a"], False, None,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    k2 = compliance_score._score_cache_key(
        ["site-a"], False, None,
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    assert k1 != k2, (
        "Different fixed-window ranges must produce distinct cache "
        "keys. Otherwise Jan-packet read would serve Feb-packet's "
        "cached value."
    )


def test_fixed_window_still_caches():
    """_should_cache_score must return True when EITHER window_start
    or window_end is set — fixed-window queries are deterministic on
    their bounded range, so caching is safe (and useful, since
    monthly-packet generation may pull the same range repeatedly)."""
    s = datetime(2026, 1, 1, tzinfo=timezone.utc)
    e = datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert compliance_score._should_cache_score(None, s, e) is True
    assert compliance_score._should_cache_score(None, s, None) is True
    assert compliance_score._should_cache_score(None, None, e) is True
    # Unbounded all-time still bypasses
    assert compliance_score._should_cache_score(None, None, None) is False
    # Relative-window default behavior preserved
    assert compliance_score._should_cache_score(30, None, None) is True


def test_tz_naive_window_start_raises_value_error():
    """Gate B P1 #109 closure: tz-naive datetimes silently shift by
    server-TZ hours under the ::timestamptz cast, breaking monthly-
    packet reproducibility. Helper must reject tz-naive upfront so
    the caller is forced to pass `tzinfo=timezone.utc`.

    Behavioral test — uses asyncio.run + an async stub that asserts
    the ValueError is raised before any DB call."""
    import asyncio
    naive = datetime(2026, 1, 1)  # tz-naive — the bug class

    async def _stub_conn():
        class _DummyConn:
            async def fetch(self, *a, **kw):
                raise AssertionError("must not reach conn.fetch with tz-naive")
            async def fetchrow(self, *a, **kw):
                raise AssertionError("must not reach conn.fetchrow with tz-naive")
            async def fetchval(self, *a, **kw):
                raise AssertionError("must not reach conn.fetchval with tz-naive")
        return _DummyConn()

    async def _run():
        conn = await _stub_conn()
        try:
            await compliance_score.compute_compliance_score(
                conn, ["site-a"], window_start=naive,
            )
        except ValueError as e:
            assert "window_start" in str(e) and "timezone-aware" in str(e)
            return
        raise AssertionError(
            "tz-naive window_start must raise ValueError — Gate B P1 "
            "#109 mandates tz-aware datetimes only."
        )

    asyncio.run(_run())


def test_tz_naive_window_end_raises_value_error():
    """Gate B P1 #109 closure: same as window_start, but for window_end.
    Both bounds must be tz-aware for the cast to be deterministic."""
    import asyncio
    naive = datetime(2026, 1, 1)

    async def _run():
        class _DummyConn:
            async def fetch(self, *a, **kw):
                raise AssertionError("must not reach")
        try:
            await compliance_score.compute_compliance_score(
                _DummyConn(), ["site-a"], window_end=naive,
            )
        except ValueError as e:
            assert "window_end" in str(e) and "timezone-aware" in str(e)
            return
        raise AssertionError(
            "tz-naive window_end must raise ValueError — Gate B P1 "
            "#109 mandates tz-aware datetimes only."
        )

    asyncio.run(_run())


def test_window_description_reflects_fixed_window():
    """When window_start/end set, window_description must describe
    the bounded range, not `last N days`. Customer-facing surfaces
    render this string in hover/tip copy — drift would mislead."""
    src = inspect.getsource(compliance_score.compute_compliance_score)
    # Anchor on the "to" connector that signifies date-range copy.
    assert ("to {window_end.date()" in src or "from {window_start.date()" in src), (
        "window_description for fixed-window paths must describe the "
        "date range (e.g., '2026-01-01 to 2026-02-01'), not 'last N "
        "days'. Customer hover-copy depends on this."
    )
