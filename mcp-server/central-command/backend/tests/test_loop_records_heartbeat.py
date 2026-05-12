"""CI gate: every supervised loop with EXPECTED_INTERVAL_S must call
record_heartbeat() inside its body — not just at startup.

Class of bug closed (2026-05-02): `_supervised` records ONE heartbeat at
startup so the loop appears in bg_heartbeat. If the loop body itself
never calls record_heartbeat (or _hb), the dict entry's last_seen
freezes at startup time. After `3 × EXPECTED_INTERVAL_S` the
`bg_loop_silent` substrate invariant fires — a permanent false positive.

This bit healing_sla and digest_sender_loop. The earlier calibration
sweep (2026-05-01) corrected the threshold but didn't fix the missing
instrumentation, so the false-positive came back ~3h later.

Lockstep guarantee: any new loop registered in `EXPECTED_INTERVAL_S`
MUST also call record_heartbeat (or _hb) inside its function body.
This test fails CI on any drift.

Approach: same per-loop manual-map pattern as
`test_expected_interval_calibration.py`. For each loop in
_LOOP_LOCATIONS, AST-walk the function body looking for any of:
  - record_heartbeat("name")
  - _hb("name")
  - any call whose name resolves to record_heartbeat

If the call exists anywhere in the function body, the loop is
considered instrumented. Verifying the heartbeat is INSIDE the
while-True (not in the startup-delay region) is a stricter check
that future iterations of this gate could add — for now, presence
in the function body is a strong-enough lockstep guarantee since
no loop deliberately calls record_heartbeat at startup-only.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from typing import Tuple

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bg_heartbeat import DRAIN_LOOPS, EXPECTED_INTERVAL_S  # noqa: E402

# Reuse the same per-loop file mapping as the calibration gate.
# Loops nested inside main.py's lifespan() are listed separately as
# _LIFESPAN_INLINE_LOOPS — they're verified manually because nested
# closures aren't AST-walkable cleanly.
_LOOP_LOCATIONS: dict[str, Tuple[str, str]] = {
    "healing_sla": ("healing_sla", "healing_sla_loop"),
    "alert_digest": ("alert_router", "digest_sender_loop"),
    # NOTE: ots_upgrade + fleet_order_expiry have DEAD duplicate
    # definitions in background_tasks.py — the wired versions are
    # inline in main.py (_ots_upgrade_loop / expire_fleet_orders_loop).
    # Listed in _LIFESPAN_INLINE_LOOPS until the dead copies are
    # removed (followup: dead-loop-cleanup 2026-05-08).
    "temporal_decay": ("background_tasks", "temporal_decay_loop"),
    "regime_change_detector": ("background_tasks", "regime_change_detector_loop"),
    "recurrence_velocity": ("background_tasks", "recurrence_velocity_loop"),
    "recurrence_auto_promotion": ("background_tasks", "recurrence_auto_promotion_loop"),
    "cross_incident_correlation": ("background_tasks", "cross_incident_correlation_loop"),
    "threshold_tuner": ("background_tasks", "threshold_tuner_loop"),
    "exemplar_miner": ("background_tasks", "exemplar_miner_loop"),
    "phantom_detector": ("background_tasks", "phantom_detector_loop"),
    "heartbeat_rollup": ("background_tasks", "heartbeat_rollup_loop"),
    "mark_stale_appliances": ("background_tasks", "mark_stale_appliances_loop"),
    "owner_transfer_sweep": ("client_owner_transfer", "owner_transfer_sweep_loop"),
    "partner_admin_transfer_sweep": ("partner_admin_transfer", "partner_admin_transfer_sweep_loop"),
    "mfa_revocation_expiry_sweep": ("mfa_admin", "mfa_revocation_expiry_sweep_loop"),
    # 2026-05-12 BUG 2 followup — 17 previously-uncovered task_defs loops
    # registered. Lockstep with EXPECTED_INTERVAL_S + the calibration
    # test's own _LOOP_LOCATIONS map.
    "ots_reverify": ("background_tasks", "ots_reverify_sample_loop"),
    "mesh_consistency": ("background_tasks", "mesh_consistency_check_loop"),
    "flywheel_reconciliation": ("background_tasks", "flywheel_reconciliation_loop"),
    "l2_auto_candidate": ("background_tasks", "l2_auto_candidate_loop"),
    "framework_sync": ("framework_sync", "framework_sync_loop"),
    "companion_alerts": ("companion", "companion_alert_check_loop"),
    "flywheel_orchestrator": ("background_tasks", "flywheel_orchestrator_loop"),
    "partition_maintainer": ("background_tasks", "partition_maintainer_loop"),
    "weekly_rollup_refresh": ("background_tasks", "weekly_rollup_refresh_loop"),
    "partner_weekly_digest": ("background_tasks", "partner_weekly_digest_loop"),
    "expire_consent_request_tokens": ("background_tasks", "expire_consent_request_tokens_loop"),
    "heartbeat_partition_maintainer": ("background_tasks", "heartbeat_partition_maintainer_loop"),
    "mesh_reassignment": ("background_tasks", "mesh_reassignment_loop"),
    "sigauth_auto_promotion": ("sigauth_enforcement", "sigauth_auto_promotion_loop"),
    "client_telemetry_retention": ("background_tasks", "client_telemetry_retention_loop"),
    "data_hygiene_gc": ("background_tasks", "data_hygiene_gc_loop"),
    "relocation_finalize": ("background_tasks", "relocation_finalize_loop"),
}

# Loops nested inside main.py's lifespan() — manually verified to call
# record_heartbeat. If you change a lifespan-inline loop's body, run
# `grep -nE "record_heartbeat|_hb\\(\"<loop>\"" mcp-server/main.py`
# to confirm the instrumentation didn't get refactored away.
_LIFESPAN_INLINE_LOOPS = {
    "privileged_notifier",
    "chain_tamper_detector",
    "retention_verifier",
    "merkle_batch",
    "audit_log_retention",
    "evidence_chain_check",
    "compliance_packets",
    "health_monitor",
    "substrate_assertions",
    "go_agent_status_decay",
    # ots_upgrade + fleet_order_expiry are inline in main.py's lifespan
    # (the dead duplicates in background_tasks.py were removed
    # 2026-05-02 followup #44). They're nested closures so AST-walking
    # them is unreliable — manual verification on edit.
    "ots_upgrade",
    "fleet_order_expiry",
}

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent


def _parse_function(module_basename: str, function_name: str) -> ast.AsyncFunctionDef:
    file_path = _BACKEND_DIR / f"{module_basename}.py"
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return node
    raise AssertionError(
        f"Could not find async function {function_name!r} in {file_path}"
    )


def _is_heartbeat_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    # Bare-name call: record_heartbeat(...) or _hb(...)
    if isinstance(node.func, ast.Name) and node.func.id in {"record_heartbeat", "_hb"}:
        return True
    # Attribute call: bg_heartbeat.record_heartbeat(...)
    if isinstance(node.func, ast.Attribute) and node.func.attr == "record_heartbeat":
        return True
    return False


def _calls_heartbeat(func: ast.AsyncFunctionDef, loop_name: str) -> bool:
    """True if the function body contains a record_heartbeat()/_hb() call
    INSIDE a `while True:` block (the inter-iteration scope).

    Tightened 2026-05-02 (followup #45): the original version only
    checked function-body presence. A future engineer placing the
    call BEFORE the while-True (one-time, like the buggy startup-only
    pattern that bg_loop_silent was originally meant to catch) would
    have falsely passed. Restricting to while-True descendants closes
    that loophole.

    `_supervised`'s startup heartbeat is OUTSIDE the loop body, so it
    doesn't satisfy this gate — only loop-body self-instrumentation does.
    """
    for node in ast.walk(func):
        if not (
            isinstance(node, ast.While)
            and (
                (isinstance(node.test, ast.Constant) and node.test.value is True)
                or (isinstance(node.test, ast.Name) and node.test.id == "True")
            )
        ):
            continue
        for inner in ast.walk(node):
            if _is_heartbeat_call(inner):
                return True
    return False


@pytest.mark.parametrize(
    "loop_name",
    [name for name in EXPECTED_INTERVAL_S if name in _LOOP_LOCATIONS],
)
def test_loop_body_records_heartbeat(loop_name: str):
    """Lockstep: every loop in EXPECTED_INTERVAL_S whose function lives
    in a separate file (registered in _LOOP_LOCATIONS) MUST call
    record_heartbeat or _hb inside its body. Without this call,
    bg_loop_silent will false-fire once 3x EXPECTED_INTERVAL_S elapses
    after process start."""
    module_basename, function_name = _LOOP_LOCATIONS[loop_name]
    func = _parse_function(module_basename, function_name)

    assert _calls_heartbeat(func, loop_name), (
        f"Loop {loop_name} ({module_basename}.{function_name}) does not "
        f"call record_heartbeat() or _hb() in its function body. The "
        f"_supervised wrapper records ONE startup heartbeat — without a "
        f"per-iteration call, bg_loop_silent will false-fire after "
        f"3x EXPECTED_INTERVAL_S ({3 * EXPECTED_INTERVAL_S[loop_name]}s) "
        f"of process uptime. Add `_hb('{loop_name}')` or "
        f"`record_heartbeat('{loop_name}')` at the top of the while-True body."
    )


def test_every_expected_interval_entry_is_locatable_or_inline():
    """Lockstep: every loop in EXPECTED_INTERVAL_S must be registered
    in _LOOP_LOCATIONS (auto-verified) or _LIFESPAN_INLINE_LOOPS
    (manually verified). Mirrors the calibration gate's contract so
    the two stay in sync."""
    locatable = set(_LOOP_LOCATIONS) | _LIFESPAN_INLINE_LOOPS | DRAIN_LOOPS
    expected = set(EXPECTED_INTERVAL_S)
    missing = expected - locatable
    assert not missing, (
        f"Loops in EXPECTED_INTERVAL_S without location registration: "
        f"{sorted(missing)}. Add to _LOOP_LOCATIONS (preferred) or "
        f"_LIFESPAN_INLINE_LOOPS (escape hatch for nested-closure loops)."
    )
