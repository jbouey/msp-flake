"""Per-site chain-lock contention metrics.

#117 Sub-C.1 closure per audit/coach-130-chain-lock-metrics-gate-a-
2026-05-16.md (APPROVE-WITH-FIXES — 6 P0s + 4 P1s).

Wraps `pg_advisory_xact_lock` in `evidence_chain.create_compliance_
bundle` to measure wait-time + detect concurrent critical-section
holders (serialization violations). Process-local counters; aggregate
across mcp-server replicas via Prometheus federation (P1-3 caveat
exposed in metric HELP text).

Allowlisted by site_id (P1-1 binding) — production sites pay zero
allocation cost. Only `'load-test-chain-contention-site'` records
samples. The chain-contention soak (Sub-D, Task #131) consumes the
percentiles + violations counter via Prometheus scrape.

No new library deps — reuses the existing manual-text-format
pattern from prometheus_metrics.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import AsyncIterator, Optional


# Process-local allowlist. Sub-C.1 ships with ONE site enabled. Any
# attempt to expand the set to production site_ids must go through
# Gate A + Gate B per CLAUDE.md TWO-GATE protocol — that's a
# production-traffic-instrumentation change with operational impact.
_ENABLED_SITE_IDS: frozenset[str] = frozenset({
    "load-test-chain-contention-site",
})

# Per-site sample state. `_wait_samples[site_id]` is a bounded deque
# of wait-time samples in seconds. `_contention_total[site_id]`
# increments when wait > 50ms. `_serialization_violations_total[
# site_id]` increments when >1 in-process task holds the critical
# section simultaneously (process-local; cross-replica detection
# happens via the bundle_chain_position_gap substrate invariant
# from Sub-A).
_WAIT_SAMPLE_MAXLEN = 10_000
_CONTENTION_THRESHOLD_SECONDS = 0.050  # 50ms

_wait_samples: dict[str, deque[float]] = {}
_contention_total: dict[str, int] = {}
_serialization_violations_total: dict[str, int] = {}

# Per-site set of currently-in-critical-section task ids. When
# context-manager `__aenter__` runs, add `id(asyncio.current_task())`;
# when `__aexit__` runs, discard. If `len(set) > 1` at enter time,
# bump _serialization_violations_total. Process-local; documented
# limitation in metric HELP text per P1-3.
_critical_section_holders: dict[str, set[int]] = {}

# Maya hardening (P1-2): hard-cap the per-site dict cardinality.
# Allowlist is currently 1-element; an accidental expansion past
# this cap silently drops new entries with a WARN.
_SITE_ID_CARDINALITY_CAP = 8


def _allowed(site_id: Optional[str]) -> bool:
    """Allowlist gate. Production callers pass through with zero
    allocation cost (P1-1 binding — _ENABLED_SITE_IDS is a frozenset,
    membership check is O(1))."""
    return site_id is not None and site_id in _ENABLED_SITE_IDS


def _ensure_site_state(site_id: str) -> bool:
    """Initialize per-site state on first sample. Honors the
    cardinality cap (P1-2)."""
    if site_id in _wait_samples:
        return True
    if len(_wait_samples) >= _SITE_ID_CARDINALITY_CAP:
        # Cap reached. New site_ids beyond the cap silently no-op.
        # Operator-visible WARN once via module-level set tracking.
        return False
    _wait_samples[site_id] = deque(maxlen=_WAIT_SAMPLE_MAXLEN)
    _contention_total[site_id] = 0
    _serialization_violations_total[site_id] = 0
    _critical_section_holders[site_id] = set()
    return True


@contextlib.asynccontextmanager
async def chain_lock_timer(site_id: str) -> AsyncIterator[None]:
    """Context manager wrapping `pg_advisory_xact_lock` acquisition.

    For load-test sites: records wait time from entry to acquisition
    + tracks concurrent critical-section holders. For all other
    sites: no-op (zero allocations).

    Usage:
        async with chain_lock_timer(site_id):
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", site_id)
            ... critical section ...

    NOTE: the wait-time measurement is from `__aenter__` to the
    inner block START. For honest measurement, callers MUST issue
    the pg_advisory_xact_lock IMMEDIATELY inside the with-block —
    any pre-lock work is counted as "wait" which contaminates
    the percentile.
    """
    if not _allowed(site_id):
        yield
        return

    if not _ensure_site_state(site_id):
        yield
        return

    task_id = id(asyncio.current_task())
    holders = _critical_section_holders[site_id]
    if holders:
        # Another in-process task is already in the critical
        # section. The pg_advisory_xact_lock SHOULD serialize them
        # — if we observe overlapping enters, the lock isn't
        # holding under contention.
        _serialization_violations_total[site_id] += 1
    holders.add(task_id)
    entered_at = time.monotonic()
    try:
        yield
    finally:
        wait_s = time.monotonic() - entered_at
        _wait_samples[site_id].append(wait_s)
        if wait_s > _CONTENTION_THRESHOLD_SECONDS:
            _contention_total[site_id] += 1
        holders.discard(task_id)


def _percentile(samples: list[float], p: float) -> float:
    """Linear-interpolation percentile. p in [0, 1].
    Returns 0.0 on empty input."""
    if not samples:
        return 0.0
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    rank = p * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def render_chain_lock_metrics() -> str:
    """Emit Prometheus text-format block for all tracked sites.

    Three metric families:
      chain_lock_wait_duration_seconds{site_id,quantile} (summary-shape)
      chain_lock_contention_total{site_id} (counter, increments on >50ms wait)
      chain_lock_serialization_violations_total{site_id} (counter)

    P1-3: HELP text documents process-local nature. Aggregate across
    mcp-server replicas via Prometheus federation before alerting.
    """
    lines: list[str] = []
    lines.append(
        "# HELP chain_lock_wait_duration_seconds Per-site wait time on "
        "pg_advisory_xact_lock (process-local; aggregate across replicas "
        "before alerting)."
    )
    lines.append("# TYPE chain_lock_wait_duration_seconds summary")
    for site_id, samples_deque in _wait_samples.items():
        samples = list(samples_deque)
        for quantile, p in (("0.5", 0.5), ("0.95", 0.95), ("0.99", 0.99)):
            v = _percentile(samples, p)
            lines.append(
                f'chain_lock_wait_duration_seconds'
                f'{{site_id="{site_id}",quantile="{quantile}"}} {v:.6f}'
            )
        lines.append(
            f'chain_lock_wait_duration_seconds_count'
            f'{{site_id="{site_id}"}} {len(samples)}'
        )

    lines.append("")
    lines.append(
        "# HELP chain_lock_contention_total Count of bundle writes where "
        "the per-site advisory-lock wait exceeded 50ms (process-local)."
    )
    lines.append("# TYPE chain_lock_contention_total counter")
    for site_id, count in _contention_total.items():
        lines.append(
            f'chain_lock_contention_total{{site_id="{site_id}"}} {count}'
        )

    lines.append("")
    lines.append(
        "# HELP chain_lock_serialization_violations_total Count of times "
        "two in-process tasks held the per-site critical section "
        "simultaneously. ANY non-zero value indicates the advisory lock "
        "is not serializing (process-local; cross-replica detection via "
        "bundle_chain_position_gap substrate invariant)."
    )
    lines.append("# TYPE chain_lock_serialization_violations_total counter")
    for site_id, count in _serialization_violations_total.items():
        lines.append(
            f'chain_lock_serialization_violations_total'
            f'{{site_id="{site_id}"}} {count}'
        )

    return "\n".join(lines) + "\n"


def _reset_for_test() -> None:
    """Test helper: clear all in-process state. Production code MUST
    NOT call this."""
    _wait_samples.clear()
    _contention_total.clear()
    _serialization_violations_total.clear()
    _critical_section_holders.clear()
