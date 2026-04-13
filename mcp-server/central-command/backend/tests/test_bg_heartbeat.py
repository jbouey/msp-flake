"""Unit test for bg_heartbeat (Phase 15 A-spec hygiene)."""
from __future__ import annotations

import time

import pytest


def test_record_and_get_single_loop():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()

    bg_heartbeat.record_heartbeat("test_loop")
    got = bg_heartbeat.get_heartbeat("test_loop")

    assert got is not None
    assert got["loop_name"] == "test_loop"
    assert got["iterations"] == 1
    assert got["errors"] == 0
    assert got["age_s"] >= 0


def test_iterations_increment():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()

    for _ in range(5):
        bg_heartbeat.record_heartbeat("counter_loop")

    got = bg_heartbeat.get_heartbeat("counter_loop")
    assert got["iterations"] == 5
    assert got["errors"] == 0


def test_ok_false_increments_errors_only_for_errors():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()

    bg_heartbeat.record_heartbeat("err_loop", ok=True)
    bg_heartbeat.record_heartbeat("err_loop", ok=False)
    bg_heartbeat.record_heartbeat("err_loop", ok=False)

    got = bg_heartbeat.get_heartbeat("err_loop")
    assert got["iterations"] == 3
    assert got["errors"] == 2


def test_get_all_heartbeats_returns_snapshot_with_age():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()

    bg_heartbeat.record_heartbeat("a")
    bg_heartbeat.record_heartbeat("b")

    snap = bg_heartbeat.get_all_heartbeats()
    assert set(snap.keys()) == {"a", "b"}
    assert "age_s" in snap["a"]
    assert "age_s" in snap["b"]


def test_assess_staleness_known_loop_fresh():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()
    bg_heartbeat.record_heartbeat("privileged_notifier")
    snap = bg_heartbeat.get_all_heartbeats()
    assert bg_heartbeat.assess_staleness(snap["privileged_notifier"]) == "fresh"


def test_assess_staleness_known_loop_stale():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()
    bg_heartbeat.record_heartbeat("privileged_notifier")
    # Backdate by > 3x the expected 60s interval
    bg_heartbeat._heartbeats["privileged_notifier"]["last_seen"] = time.time() - 500
    snap = bg_heartbeat.get_all_heartbeats()
    assert bg_heartbeat.assess_staleness(snap["privileged_notifier"]) == "stale"


def test_assess_staleness_unknown_loop():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()
    bg_heartbeat.record_heartbeat("never_listed_loop")
    snap = bg_heartbeat.get_all_heartbeats()
    assert bg_heartbeat.assess_staleness(snap["never_listed_loop"]) == "unknown"


def test_get_heartbeat_returns_none_for_missing():
    import bg_heartbeat
    bg_heartbeat._heartbeats.clear()
    assert bg_heartbeat.get_heartbeat("nothing_recorded") is None


def test_thread_safety_basic():
    """Smoke test — many concurrent record calls don't drop iterations."""
    import bg_heartbeat
    import threading

    bg_heartbeat._heartbeats.clear()
    n_threads = 8
    n_iters = 100

    def worker():
        for _ in range(n_iters):
            bg_heartbeat.record_heartbeat("concurrent_loop")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    got = bg_heartbeat.get_heartbeat("concurrent_loop")
    assert got["iterations"] == n_threads * n_iters, (
        f"Expected {n_threads * n_iters} iterations under threading, got {got['iterations']}"
    )
