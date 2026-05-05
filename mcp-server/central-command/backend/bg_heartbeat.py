"""Background-loop heartbeat registry (Phase 15 A-spec hygiene).

Today, the supervised background loops in main.py log on crash but
have no way to detect a SILENTLY STUCK loop — one that's running
but not making progress (deadlocked on a connection, blocked on a
lock, in an infinite retry of an unrecoverable error).

This module gives every loop a single line to call at the top of
each iteration:

    from .bg_heartbeat import record_heartbeat
    record_heartbeat("privileged_notifier")

The admin /api/admin/health/loops endpoint then surfaces the
last-iteration timestamp + age per loop, so a stuck loop becomes
visible in seconds rather than days.

Why a process-local dict instead of a DB write per iteration:
loops can fire every second; each DB write would be wasteful and
itself a potential failure mode. Process-local is sufficient
because the supervisor restarts loops in-process and a CRASHED
process produces a separate signal (container restart).
"""
from __future__ import annotations

import time
import threading
from typing import Dict, Any


_lock = threading.Lock()
_heartbeats: Dict[str, Dict[str, Any]] = {}


def record_heartbeat(loop_name: str, *, ok: bool = True) -> None:
    """Mark a loop iteration as having just completed.

    Args:
        loop_name: stable identifier (e.g. 'privileged_notifier')
        ok: True if the iteration succeeded, False if it caught and
            handled an exception. Both update last_seen but the latter
            increments error_count for visibility.
    """
    now = time.time()
    with _lock:
        entry = _heartbeats.setdefault(loop_name, {
            "loop_name": loop_name,
            "first_seen": now,
            "last_seen": now,
            "iterations": 0,
            "errors": 0,
        })
        entry["last_seen"] = now
        entry["iterations"] += 1
        if not ok:
            entry["errors"] += 1


def get_all_heartbeats() -> Dict[str, Dict[str, Any]]:
    """Snapshot of the registry. Adds derived `age_s` field."""
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    with _lock:
        for name, entry in _heartbeats.items():
            out[name] = {
                **entry,
                "age_s": round(now - entry["last_seen"], 2),
            }
    return out


def get_heartbeat(loop_name: str) -> Dict[str, Any] | None:
    """Single-loop snapshot or None if never seen."""
    return get_all_heartbeats().get(loop_name)


# Expected iteration interval per loop. Used by the health endpoint
# to decide if a loop is "stale" (last_seen older than 3x interval).
# MUST match the `await asyncio.sleep(...)` cadence of the actual loop.
# A miscalibrated entry produces a permanent false positive — classified
# "stale" forever — which hides real stuck loops in the noise.
# Add new loops here as you instrument them.
EXPECTED_INTERVAL_S: Dict[str, int] = {
    "privileged_notifier": 60,
    "chain_tamper_detector": 3600,
    "retention_verifier": 2592000,  # 30d
    "fleet_order_expiry": 300,
    "merkle_batch": 3600,  # main.py:1593 sleeps 3600
    "audit_log_retention": 86400,
    "health_monitor": 300,  # health_monitor.py:101 sleeps 300s (5 min)
    "ots_upgrade": 900,  # evidence_chain.py loop sleeps 900s (15 min)
    "evidence_chain_check": 86400,  # main.py:1571 sleeps 86400 (daily)
    "alert_digest": 14400,  # alert_router.py:488 ALERT_DIGEST_INTERVAL_HOURS=4 default
    "compliance_packets": 3600,
    "healing_sla": 3600,  # healing_sla.py:35 sleeps 3600 (hourly)
    "recurrence_velocity": 300,
    "recurrence_auto_promotion": 3600,
    "cross_incident_correlation": 3600,
    "temporal_decay": 21600,  # background_tasks.py:566 sleeps 21600 (6h)
    "regime_change_detector": 1800,  # background_tasks.py:922 sleeps 1800 (30 min)
    "threshold_tuner": 86400,
    "exemplar_miner": 86400,
    "phantom_detector": 300,
    "heartbeat_rollup": 60,
    "substrate_assertions": 60,
    "go_agent_status_decay": 60,  # Session 214 fleet-edge liveness
    "mark_stale_appliances": 120,  # background_tasks.py:1676 APPLIANCE_OFFLINE_SCAN_SECONDS=120
    "owner_transfer_sweep": 60,  # client_owner_transfer.py:owner_transfer_sweep_loop sleeps 60s
    "partner_admin_transfer_sweep": 60,  # partner_admin_transfer.py:partner_admin_transfer_sweep_loop sleeps 60s
}


# Loops whose job is to drain-and-idle rather than tick on a schedule.
# They wake up, consume work if any exists, and then sleep a long time.
# Their heartbeat cadence is driven by *work arriving*, not by a clock,
# so the 3x-expected threshold doesn't apply. The health classifier
# treats them as 'fresh' as long as the loop is still registered.
DRAIN_LOOPS: set = {
    "ots_resubmit",
}


def assess_staleness(entry: Dict[str, Any]) -> str:
    """Returns 'fresh' | 'stale' | 'unknown' for a heartbeat entry."""
    name = entry["loop_name"]
    if name in DRAIN_LOOPS:
        # Drain loops heartbeat when work shows up; idle is healthy.
        return "fresh"
    expected = EXPECTED_INTERVAL_S.get(name)
    if expected is None:
        return "unknown"
    return "stale" if entry["age_s"] > 3 * expected else "fresh"
