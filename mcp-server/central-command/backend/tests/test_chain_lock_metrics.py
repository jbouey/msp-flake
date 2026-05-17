"""CI gates for #117 Sub-C.1 — chain_lock_metrics module + timer
wrap in evidence_chain.create_compliance_bundle.

Per audit/coach-130-chain-lock-metrics-gate-a-2026-05-16.md
(APPROVE-WITH-FIXES; this is Sub-C.1, the foundation. Sub-C.2
ships the admin endpoint + k6 + soak contract).

Behavioral tests use the module directly (no DB). Structural
sentinels pin the evidence_chain.py wrap shape so future refactors
don't strip the timer.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MODULE = _BACKEND / "chain_lock_metrics.py"
_EVIDENCE = _BACKEND / "evidence_chain.py"


@pytest.fixture(autouse=True)
def _reset():
    sys.path.insert(0, str(_BACKEND))
    import chain_lock_metrics as clm
    clm._reset_for_test()
    yield
    clm._reset_for_test()


# ── Allowlist behavior (P1-1) ──────────────────────────────────────


def test_non_allowlisted_site_id_is_no_op():
    """Production sites pay zero allocation cost. Per Gate A P1-1
    binding: chain_lock_timer for a non-allowlisted site_id must
    NOT touch _wait_samples / _contention_total."""
    from chain_lock_metrics import (
        chain_lock_timer, _wait_samples, _contention_total,
        _serialization_violations_total,
    )

    async def go():
        async with chain_lock_timer("prod-site-xyz"):
            await asyncio.sleep(0.001)

    asyncio.run(go())
    assert "prod-site-xyz" not in _wait_samples
    assert "prod-site-xyz" not in _contention_total
    assert "prod-site-xyz" not in _serialization_violations_total


def test_allowlisted_site_records_sample():
    from chain_lock_metrics import chain_lock_timer, _wait_samples

    async def go():
        async with chain_lock_timer("load-test-chain-contention-site"):
            await asyncio.sleep(0.001)

    asyncio.run(go())
    samples = list(_wait_samples["load-test-chain-contention-site"])
    assert len(samples) == 1
    assert samples[0] >= 0.001


def test_allowlist_literal_is_the_load_test_site():
    """Per Gate A P1-1: ONLY 'load-test-chain-contention-site' is
    enabled. Adding production site_ids to the allowlist requires
    fresh Gate A + Gate B per TWO-GATE."""
    from chain_lock_metrics import _ENABLED_SITE_IDS
    assert _ENABLED_SITE_IDS == frozenset({"load-test-chain-contention-site"})


# ── Contention threshold ───────────────────────────────────────────


def test_contention_counter_increments_above_50ms():
    from chain_lock_metrics import chain_lock_timer, _contention_total

    async def go():
        async with chain_lock_timer("load-test-chain-contention-site"):
            await asyncio.sleep(0.060)  # 60ms — above the 50ms threshold

    asyncio.run(go())
    assert _contention_total["load-test-chain-contention-site"] == 1


def test_contention_counter_not_incremented_below_50ms():
    from chain_lock_metrics import chain_lock_timer, _contention_total

    async def go():
        async with chain_lock_timer("load-test-chain-contention-site"):
            pass  # fast — well below 50ms

    asyncio.run(go())
    # Counter not incremented for fast waits (entry creates the
    # key with 0 value — that's expected).
    assert _contention_total.get(
        "load-test-chain-contention-site", 0
    ) == 0


# ── Serialization-violation detector ──────────────────────────────


def test_serialization_violation_detected_when_two_tasks_overlap():
    """If two tasks enter the timer for the same site_id without
    one exiting first, _serialization_violations_total increments.
    Postgres's pg_advisory_xact_lock would normally prevent this
    overlap — observing it indicates the lock isn't serializing."""
    from chain_lock_metrics import (
        chain_lock_timer, _serialization_violations_total,
    )

    async def go():
        async def hold():
            async with chain_lock_timer("load-test-chain-contention-site"):
                await asyncio.sleep(0.020)

        # Two concurrent tasks — both enter timer; since there's no
        # actual lock between them, the second-to-enter sees the
        # first in _critical_section_holders → violation tripped.
        await asyncio.gather(hold(), hold())

    asyncio.run(go())
    assert _serialization_violations_total[
        "load-test-chain-contention-site"
    ] >= 1


# ── Cardinality cap (P1-2) ────────────────────────────────────────


def test_site_id_cardinality_cap_prevents_unbounded_growth():
    """Per Gate A P1-2: cap to 8 site_ids. Currently the allowlist
    has 1 — but the gate exists in case the allowlist is expanded
    accidentally."""
    from chain_lock_metrics import _SITE_ID_CARDINALITY_CAP
    assert _SITE_ID_CARDINALITY_CAP == 8


# ── Renderer ──────────────────────────────────────────────────────


def test_render_includes_help_and_type_lines():
    """Prometheus text format requires HELP + TYPE lines per metric
    family."""
    from chain_lock_metrics import render_chain_lock_metrics

    out = render_chain_lock_metrics()
    assert "# HELP chain_lock_wait_duration_seconds" in out
    assert "# TYPE chain_lock_wait_duration_seconds summary" in out
    assert "# HELP chain_lock_contention_total" in out
    assert "# TYPE chain_lock_contention_total counter" in out
    assert "# HELP chain_lock_serialization_violations_total" in out
    assert "# TYPE chain_lock_serialization_violations_total counter" in out


def test_render_documents_process_local_caveat():
    """Per Gate A P1-3 binding: HELP text MUST document the
    process-local nature so on-call doesn't get false confidence
    from a single-replica scrape."""
    from chain_lock_metrics import render_chain_lock_metrics
    out = render_chain_lock_metrics()
    assert "process-local" in out, (
        "metric HELP text must say 'process-local' (P1-3 binding) so "
        "on-call knows to aggregate across replicas before alerting."
    )


def test_render_serialization_violation_help_warns_on_nonzero():
    from chain_lock_metrics import render_chain_lock_metrics
    out = render_chain_lock_metrics()
    assert "ANY non-zero value" in out, (
        "serialization_violations_total HELP must explicitly say "
        "ANY non-zero value is a problem — operator-actionable wording."
    )


def test_render_includes_percentile_quantiles():
    """The renderer must emit p50, p95, p99 quantiles per Gate A
    spec (Sub-D consumes these for verdict decision)."""
    from chain_lock_metrics import chain_lock_timer, render_chain_lock_metrics

    async def go():
        for _ in range(10):
            async with chain_lock_timer("load-test-chain-contention-site"):
                await asyncio.sleep(0.001)

    asyncio.run(go())
    out = render_chain_lock_metrics()
    assert 'quantile="0.5"' in out
    assert 'quantile="0.95"' in out
    assert 'quantile="0.99"' in out
    # Per-site label
    assert 'site_id="load-test-chain-contention-site"' in out


def test_render_handles_empty_state_without_crash():
    """Fresh process, zero samples — renderer must produce valid
    text-format output (HELP + TYPE blocks, no data rows)."""
    from chain_lock_metrics import render_chain_lock_metrics
    out = render_chain_lock_metrics()
    # Must not crash + must contain at least the three HELP lines.
    assert out.count("# HELP") >= 3


# ── Structural pin on evidence_chain wrap ─────────────────────────


def test_evidence_chain_wraps_advisory_lock_in_timer():
    """Sub-C.1 structural sentinel: the pg_advisory_xact_lock call
    in create_compliance_bundle must be wrapped in
    `async with chain_lock_timer(site_id):`. Future refactors that
    strip the timer must explicitly delete this test."""
    src = _EVIDENCE.read_text(encoding="utf-8")
    # Find the lock acquisition line + verify it's preceded by the
    # context manager within ~10 lines.
    lock_idx = src.find("pg_advisory_xact_lock(hashtext($1))")
    assert lock_idx != -1, "pg_advisory_xact_lock call not found"
    # Look for chain_lock_timer in the preceding window.
    window_start = src.rfind("\n", 0, lock_idx - 1)
    window = src[max(0, window_start - 800) : lock_idx]
    assert "async with chain_lock_timer(site_id)" in window, (
        "create_compliance_bundle must wrap the pg_advisory_xact_lock "
        "call in `async with chain_lock_timer(site_id):` per Sub-C.1. "
        "If this test fails after a refactor, restore the wrapper — "
        "the wait-time + serialization-violation metrics depend on it."
    )


def test_evidence_chain_imports_chain_lock_timer():
    """The import is function-scope with relative-then-absolute
    fallback per CLAUDE.md 2026-05-13 dashboard-outage class rule."""
    src = _EVIDENCE.read_text(encoding="utf-8")
    assert "from .chain_lock_metrics import chain_lock_timer" in src
    assert "from chain_lock_metrics import chain_lock_timer" in src, (
        "evidence_chain.py must include the absolute-import fallback "
        "(CLAUDE.md rule: production package context requires both "
        "relative AND absolute import shapes)."
    )
