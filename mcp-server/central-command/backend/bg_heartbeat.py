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
# Add new loops here as you instrument them.
EXPECTED_INTERVAL_S: Dict[str, int] = {
    "privileged_notifier": 60,
    "chain_tamper_detector": 3600,
    "fleet_order_expiry": 300,
    "merkle_batch": 600,
    "audit_log_retention": 86400,
    "health_monitor": 60,
    "ots_upgrade": 1800,
    "ots_resubmit": 3600,
    "evidence_chain_check": 1800,
    "alert_digest": 600,
    "compliance_packets": 3600,
    "healing_sla": 600,
    "recurrence_velocity": 300,
    "recurrence_auto_promotion": 3600,
    "cross_incident_correlation": 3600,
    "temporal_decay": 86400,
    "regime_change_detector": 3600,
    "threshold_tuner": 86400,
    "exemplar_miner": 86400,
}


def assess_staleness(entry: Dict[str, Any]) -> str:
    """Returns 'fresh' | 'stale' | 'unknown' for a heartbeat entry."""
    expected = EXPECTED_INTERVAL_S.get(entry["loop_name"])
    if expected is None:
        return "unknown"
    return "stale" if entry["age_s"] > 3 * expected else "fresh"
