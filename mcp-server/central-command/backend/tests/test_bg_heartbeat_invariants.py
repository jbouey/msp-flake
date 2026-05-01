"""Coverage tests for bg_heartbeat-reading invariants.

D7 followup 2026-05-01 — closes the fixture coverage gap for
`substrate_assertions_meta_silent` and `bg_loop_silent`. These
two invariants read in-process bg_heartbeat state (NOT the DB),
so the standard `test_substrate_prod_fixtures.py` parametrized
pattern doesn't apply. We mock the bg_heartbeat module's
read functions directly.

Pattern: each test sets up a synthetic heartbeat state (cold,
fresh, stuck), calls the invariant function, and asserts the
violation count + interpretation matches the spec.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest.mock import patch

import pytest

# Make backend module importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import assertions  # noqa: E402


# ─── substrate_assertions_meta_silent — sev1 META watcher ────────────


def _run(coro):
    """Helper to run an async assertion fn synchronously."""
    return asyncio.new_event_loop().run_until_complete(coro)


def test_meta_silent_quiet_during_cold_start():
    """Process just started; no heartbeat yet recorded. Function
    returns 0 violations (give the loop a cycle to register)."""
    with patch("bg_heartbeat.get_heartbeat", return_value=None):
        violations = _run(
            assertions._check_substrate_assertions_meta_silent(None)
        )
    assert violations == [], (
        "Cold-start state must not fire — pre-fix this would have "
        "false-fired on every container restart for ~60s."
    )


def test_meta_silent_fires_when_loop_stuck_180s_threshold():
    """Heartbeat exists but age > 180s (3x the 60s expected cadence).
    Function fires sev1 with structured details. Threshold matches
    the assertions.py:415 phantom_detector pattern."""
    fake_heartbeat = {
        "loop_name": "substrate_assertions",
        "first_seen": 1000.0,
        "last_seen": 1100.0,
        "age_s": 200.0,  # > 180s threshold
        "iterations": 50,
        "errors": 0,
    }
    with patch("bg_heartbeat.get_heartbeat", return_value=fake_heartbeat):
        violations = _run(
            assertions._check_substrate_assertions_meta_silent(None)
        )
    assert len(violations) == 1, (
        f"Expected 1 violation at age=200s, got {len(violations)}"
    )
    v = violations[0]
    assert v.site_id is None  # global invariant
    assert v.details["loop"] == "substrate_assertions"
    assert v.details["age_s"] == 200.0
    assert "watcher" in v.details["interpretation"].lower(), (
        "Interpretation must mention the meta-watcher concept"
    )


def test_meta_silent_quiet_at_threshold_boundary():
    """Heartbeat at exactly 180s — edge case. Function uses strict
    `> 180` so equality does NOT fire; verify the boundary."""
    fake_heartbeat = {
        "loop_name": "substrate_assertions",
        "first_seen": 1000.0,
        "last_seen": 1100.0,
        "age_s": 180.0,  # exactly threshold
        "iterations": 100,
        "errors": 0,
    }
    with patch("bg_heartbeat.get_heartbeat", return_value=fake_heartbeat):
        violations = _run(
            assertions._check_substrate_assertions_meta_silent(None)
        )
    assert violations == [], (
        f"Threshold boundary (age==180) must NOT fire; got {len(violations)}"
    )


# ─── bg_loop_silent — sev2 generic stuck-loop watcher ───────────────


def test_bg_loop_silent_quiet_when_all_fresh():
    """All registered loops within their expected cadence. Returns
    0 violations."""
    fake_heartbeats = {
        "fleet_order_expiry": {
            "loop_name": "fleet_order_expiry", "age_s": 30.0,
            "iterations": 100, "errors": 0,
        },
        "substrate_assertions": {
            "loop_name": "substrate_assertions", "age_s": 5.0,
            "iterations": 1000, "errors": 0,
        },
    }
    def _stale(entry):
        return "fresh"
    with patch("bg_heartbeat.get_all_heartbeats", return_value=fake_heartbeats), \
         patch("bg_heartbeat.assess_staleness", side_effect=_stale):
        violations = _run(assertions._check_bg_loop_silent(None))
    assert violations == [], (
        f"All-fresh state must not fire; got {len(violations)} violations"
    )


def test_bg_loop_silent_fires_per_stale_loop():
    """Two loops stale; one fresh. Function emits 2 violations
    (one per stale loop). Excludes substrate_assertions + phantom_detector
    which have dedicated sev1 invariants."""
    fake_heartbeats = {
        "fleet_order_expiry": {
            "loop_name": "fleet_order_expiry", "age_s": 30.0,
            "iterations": 100, "errors": 0,
        },
        "merkle_batch": {
            "loop_name": "merkle_batch", "age_s": 12000.0,
            "iterations": 1, "errors": 0,
        },
        "alert_digest": {
            "loop_name": "alert_digest", "age_s": 4000.0,
            "iterations": 2, "errors": 0,
        },
    }
    def _stale(entry):
        return "stale" if entry["age_s"] > 1800 else "fresh"
    with patch("bg_heartbeat.get_all_heartbeats", return_value=fake_heartbeats), \
         patch("bg_heartbeat.assess_staleness", side_effect=_stale):
        violations = _run(assertions._check_bg_loop_silent(None))
    assert len(violations) == 2, (
        f"Expected 2 stale loops to fire, got {len(violations)}"
    )
    stuck_names = {v.details["loop"] for v in violations}
    assert stuck_names == {"merkle_batch", "alert_digest"}


def test_bg_loop_silent_excludes_dedicated_meta_watchers():
    """substrate_assertions and phantom_detector have their own sev1
    invariants. bg_loop_silent must NOT fire on them even if stale —
    avoids double-fire."""
    fake_heartbeats = {
        "substrate_assertions": {
            "loop_name": "substrate_assertions", "age_s": 5000.0,
            "iterations": 1, "errors": 0,
        },
        "phantom_detector": {
            "loop_name": "phantom_detector", "age_s": 5000.0,
            "iterations": 1, "errors": 0,
        },
    }
    def _stale(entry):
        return "stale"
    with patch("bg_heartbeat.get_all_heartbeats", return_value=fake_heartbeats), \
         patch("bg_heartbeat.assess_staleness", side_effect=_stale):
        violations = _run(assertions._check_bg_loop_silent(None))
    assert violations == [], (
        f"Dedicated meta-watchers must be excluded from generic gate; "
        f"got {len(violations)} violations"
    )


def test_bg_loop_silent_skips_unknown_cadence():
    """Loops not in EXPECTED_INTERVAL_S return assess_staleness='unknown'.
    bg_loop_silent skips those — backfilling the dict is the operator
    path to coverage. Avoid noise."""
    fake_heartbeats = {
        "uncalibrated_loop": {
            "loop_name": "uncalibrated_loop", "age_s": 99999.0,
            "iterations": 1, "errors": 0,
        },
    }
    def _stale(entry):
        return "unknown"
    with patch("bg_heartbeat.get_all_heartbeats", return_value=fake_heartbeats), \
         patch("bg_heartbeat.assess_staleness", side_effect=_stale):
        violations = _run(assertions._check_bg_loop_silent(None))
    assert violations == [], (
        "Unknown-cadence loops must NOT fire — design choice to avoid "
        "noise from un-calibrated loops"
    )
