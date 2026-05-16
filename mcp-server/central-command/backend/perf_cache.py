"""
Process-local TTL cache for hot read paths.

Coach perf-efficiency sweep 2026-05-08 (`audit/coach-perf-sweep-2026-05-08.md`):
the canonical compliance-score helper costs 2.4s on a 155K-bundle org and is
called by THREE customer-facing surfaces (dashboard + reports + per-site).
A small in-memory TTL cache shared across surfaces drops the dashboard
perceived latency from 2.4s × 3 = 7.2s to ~2.5s on cold path and ~150ms on
warm path.

Design choices:
  - Process-local. Single VPS deploy today; one process holds one cache.
    Swap for Redis when Central Command scales out (interface preserved).
  - Async-safe. The wrapper accepts coroutine functions and stores the
    awaited result keyed by the call args.
  - TTL-only invalidation. Score precision is ±60s; that's acceptable for
    customer-facing aggregates. Auditor-grade exports BYPASS the cache by
    going through `window_days=None` (which the wrapper refuses to cache —
    see `_should_cache`).
  - Tenant-key-aware. The cache key MUST include the tenant scope (site_ids
    tuple OR org_id) so RLS-implicit isolation is preserved at the cache
    layer. NEVER cache by a non-tenant-scoped key.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Hashable, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Gate B P1 #110 closure: bounded LRU caps replace unbounded dicts.
# Phase A (Task #83) added window_start/window_end to the cache key,
# multiplying cardinality (12 months × ~100 orgs = 1200+ entries
# per monthly-packet regen cycle). Pre-cap _STORE + _LOCKS grew
# without bound — only lazy TTL-on-GET evicted, and _LOCKS had no
# expiry at all. 5000-entry cap gives ~50× headroom over expected
# steady-state while bounding worst-case memory; LRU eviction is
# safe because expired entries are cheap to recompute (60s for
# rolling, deterministic for fixed-window so cache miss = exactly
# one recompute).
_MAX_ENTRIES = 5000

# (key) -> (expires_at_monotonic, value) — OrderedDict so we can
# move_to_end on access (LRU touch) and popitem(last=False) for
# oldest-first eviction.
_STORE: OrderedDict[Hashable, Tuple[float, Any]] = OrderedDict()

# Coalescing locks — concurrent calls with the same key wait on the
# same in-flight call instead of stampeding the underlying function.
# Same LRU cap to bound memory.
_LOCKS: OrderedDict[Hashable, asyncio.Lock] = OrderedDict()


def _now() -> float:
    return time.monotonic()


def _evict_if_over_cap(store: OrderedDict) -> None:
    """LRU eviction: pop oldest entries until size <= _MAX_ENTRIES.
    Called after every cache_set / lock-setdefault to keep memory
    bounded. O(1) per popitem; amortized cost is negligible."""
    while len(store) > _MAX_ENTRIES:
        store.popitem(last=False)


def cache_get(key: Hashable) -> Optional[Any]:
    entry = _STORE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if _now() >= expires_at:
        _STORE.pop(key, None)
        return None
    # LRU touch — move freshly-accessed entries to the end so they
    # outlive cold entries during eviction.
    _STORE.move_to_end(key)
    return value


def cache_set(key: Hashable, value: Any, ttl_seconds: float) -> None:
    _STORE[key] = (_now() + ttl_seconds, value)
    _STORE.move_to_end(key)
    _evict_if_over_cap(_STORE)


def cache_invalidate(key: Hashable) -> None:
    _STORE.pop(key, None)
    # Drop matching lock — release-time pruning to bound _LOCKS even
    # when callers explicitly invalidate.
    _LOCKS.pop(key, None)


def cache_clear() -> None:
    """Test-only — clear the entire cache. Production code should never
    call this; rely on TTL expiry + LRU eviction."""
    _STORE.clear()
    _LOCKS.clear()


async def cached_call(
    key: Hashable,
    ttl_seconds: float,
    coro_factory: Callable[[], Awaitable[T]],
) -> T:
    """Run ``coro_factory()`` if no fresh cached value for ``key``;
    otherwise return the cached value. Concurrent calls with the same
    key coalesce on a per-key asyncio.Lock so the underlying coroutine
    executes once even under load.
    """
    cached = cache_get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    lock = _LOCKS.setdefault(key, asyncio.Lock())
    _LOCKS.move_to_end(key)
    _evict_if_over_cap(_LOCKS)
    async with lock:
        # Another waiter may have populated the cache while we waited.
        cached = cache_get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        try:
            result = await coro_factory()
        except Exception:
            # Don't cache failures. Re-raise to caller.
            raise
        cache_set(key, result, ttl_seconds)
        return result


def cache_stats() -> dict[str, int]:
    """Return a small snapshot for observability / tests."""
    return {
        "entries": len(_STORE),
        "live_locks": sum(1 for ll in _LOCKS.values() if ll.locked()),
        "max_entries": _MAX_ENTRIES,
    }
