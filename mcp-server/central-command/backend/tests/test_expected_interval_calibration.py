"""CI gate: bg_heartbeat.EXPECTED_INTERVAL_S must match actual loop cadences.

Calibration sweep 2026-05-01 found 5 drifts. 2 were FALSE-FIRING the
`bg_loop_silent` substrate invariant in prod (healing_sla 6x slower than
configured, alert_digest 24x slower) for an unknown duration. 3 were
"safe drifts" (actual faster than configured) that masked weaker stuck
detection.

Class of bug: the EXPECTED_INTERVAL_S dict is the calibration ground
truth used by `assess_staleness()` — it must be kept in lockstep with
the actual `await asyncio.sleep(N)` inside each loop function. Drift
between them silently breaks the substrate's stuck-loop detector.

Approach: per-loop manual map of (file, function_name, expected_arg).
For each entry in EXPECTED_INTERVAL_S, AST-walk the function body, find
the LAST `asyncio.sleep(N)` (the inter-iteration sleep — startup-delay
sleeps come earlier in the function), and assert N matches what
EXPECTED_INTERVAL_S says. Constants and simple expressions are resolved
by importing the module.

When a new loop is added: register it in `_LOOP_LOCATIONS` AND
`bg_heartbeat.EXPECTED_INTERVAL_S` in the same PR. The test fails if
either side is missing — that's the lockstep guarantee.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys
from typing import Any, Optional, Tuple

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bg_heartbeat import DRAIN_LOOPS, EXPECTED_INTERVAL_S  # noqa: E402

# Map: loop_name → (module_basename, function_name)
# Module basename is the .py filename without extension under
# mcp-server/central-command/backend/. The CI gate parses that file's
# AST and looks for the named async function.
_LOOP_LOCATIONS: dict[str, Tuple[str, str]] = {
    "healing_sla": ("healing_sla", "healing_sla_loop"),
    "alert_digest": ("alert_router", "digest_sender_loop"),
    # ots_upgrade + fleet_order_expiry deferred to _LIFESPAN_INLINE_LOOPS
    # — dead duplicates exist in background_tasks.py; wired versions are
    # in main.py. See followup dead-loop-cleanup 2026-05-08.
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
}

# Loops registered in EXPECTED_INTERVAL_S but whose definitions live
# inside main.py's lifespan() as nested closures. AST-walking nested
# closures inside lifespan() is unreliable (ast.walk produces all
# descendant FunctionDef nodes including transient helpers). For these,
# the test asserts the cadence by direct constant inspection — the
# loop body itself is checked manually when EXPECTED_INTERVAL_S is
# updated. Add to _LOOP_LOCATIONS once they move to a separate file.
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
    # 2026-05-02 followup #44). Nested-closure parsing limitations.
    "ots_upgrade",
    "fleet_order_expiry",
}

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent


def _load_module_constants(module_basename: str) -> dict[str, Any]:
    """Return module-level integer constants for arg resolution.

    AST-parses the file rather than importing it — many backend
    modules use relative imports that fail outside the package
    context, but module-level integer constants are knowable
    statically. Handles direct int assignment + `int(os.getenv(...,
    "N"))` fallback default.
    """
    file_path = _BACKEND_DIR / f"{module_basename}.py"
    if not file_path.exists():
        return {}
    tree = ast.parse(file_path.read_text())
    out: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        # Direct integer literal: NAME = 60
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
            out[target.id] = int(node.value.value)
            continue
        # Common pattern: int(os.getenv("VAR", "N"))
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "int"
            and node.value.args
        ):
            inner = node.value.args[0]
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "getenv"
                and len(inner.args) >= 2
                and isinstance(inner.args[1], ast.Constant)
            ):
                try:
                    out[target.id] = int(inner.args[1].value)
                except (TypeError, ValueError):
                    pass
    return out


def _resolve_sleep_arg(node: ast.AST, constants: dict[str, Any]) -> Optional[int]:
    """Best-effort literal-evaluation of an asyncio.sleep argument.

    Handles: integer literal, module-level constant Name, and binary
    multiplication of those (e.g. DIGEST_INTERVAL_HOURS * 3600).
    Returns None if the expression can't be resolved without execution.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return int(node.value)
    if isinstance(node, ast.Name) and node.id in constants:
        return int(constants[node.id])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left = _resolve_sleep_arg(node.left, constants)
        right = _resolve_sleep_arg(node.right, constants)
        if left is not None and right is not None:
            return int(left * right)
    return None


def _find_inter_iteration_sleep(
    func: ast.AsyncFunctionDef, constants: dict[str, Any]
) -> Optional[int]:
    """Find the LAST `await asyncio.sleep(N)` reachable from the
    while-True body. Startup delay sleeps come BEFORE the while True;
    by walking the FunctionDef body and tracking only sleeps that
    appear inside a while-True descendant, we get the inter-iteration
    cadence."""
    for node in ast.walk(func):
        if isinstance(node, ast.While) and (
            (isinstance(node.test, ast.Constant) and node.test.value is True)
            or (isinstance(node.test, ast.Name) and node.test.id == "True")
        ):
            # Collect all asyncio.sleep calls in this while body
            sleeps: list[ast.Call] = []
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "sleep"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "asyncio"
                ):
                    sleeps.append(inner)
            if not sleeps:
                continue
            # The LAST sleep in lexical order is conventionally the
            # inter-iteration cadence (the loop body sleeps at the end
            # before looping). Earlier sleeps may be intra-iteration
            # backoffs.
            last = sleeps[-1]
            if last.args:
                return _resolve_sleep_arg(last.args[0], constants)
    return None


def _parse_function(module_basename: str, function_name: str):
    file_path = _BACKEND_DIR / f"{module_basename}.py"
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return node
    raise AssertionError(
        f"Could not find async function {function_name!r} in {file_path}"
    )


@pytest.mark.parametrize(
    "loop_name",
    [name for name in EXPECTED_INTERVAL_S if name in _LOOP_LOCATIONS],
)
def test_expected_interval_matches_loop_sleep(loop_name: str):
    """Per-loop calibration: EXPECTED_INTERVAL_S[name] must equal the
    inter-iteration `asyncio.sleep(N)` of the loop's body."""
    module_basename, function_name = _LOOP_LOCATIONS[loop_name]
    func = _parse_function(module_basename, function_name)
    constants = _load_module_constants(module_basename)

    actual = _find_inter_iteration_sleep(func, constants)
    expected = EXPECTED_INTERVAL_S[loop_name]

    assert actual is not None, (
        f"Could not resolve asyncio.sleep argument for {loop_name} "
        f"({module_basename}.{function_name}). If the cadence is "
        f"computed at runtime, add a fallback in this test."
    )
    assert actual == expected, (
        f"Calibration drift for {loop_name}: EXPECTED_INTERVAL_S says "
        f"{expected}s but {module_basename}.{function_name} actually "
        f"sleeps {actual}s. This will either false-fire bg_loop_silent "
        f"(if actual > 3x expected) or hide stuck loops (if expected > "
        f"actual). Update bg_heartbeat.EXPECTED_INTERVAL_S to match."
    )


def test_every_expected_interval_entry_is_locatable_or_inline():
    """Lockstep: every loop in EXPECTED_INTERVAL_S must be either in
    _LOOP_LOCATIONS (parseable from a separate file) or in
    _LIFESPAN_INLINE_LOOPS (manually verified inline). Adding a new
    loop to EXPECTED_INTERVAL_S without registering its location here
    fails CI."""
    locatable = set(_LOOP_LOCATIONS) | _LIFESPAN_INLINE_LOOPS | DRAIN_LOOPS
    expected = set(EXPECTED_INTERVAL_S)
    missing = expected - locatable
    assert not missing, (
        f"Loops in EXPECTED_INTERVAL_S without a location registration: "
        f"{sorted(missing)}. Add to _LOOP_LOCATIONS (preferred — gives "
        f"AST verification) or _LIFESPAN_INLINE_LOOPS (manual-verify "
        f"escape hatch for nested-closure loops in main.py)."
    )


def test_loop_locations_map_only_references_real_files():
    """Catch typos in _LOOP_LOCATIONS keys."""
    for loop_name, (module_basename, _) in _LOOP_LOCATIONS.items():
        f = _BACKEND_DIR / f"{module_basename}.py"
        assert f.exists(), (
            f"_LOOP_LOCATIONS[{loop_name!r}] points at {f} which does "
            f"not exist."
        )
