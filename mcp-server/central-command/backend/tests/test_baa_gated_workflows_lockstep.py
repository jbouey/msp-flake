"""3-list lockstep CI gate for BAA workflow enforcement (Task #52,
Counsel Rule 6). Mirrors test_privileged_order_four_list_lockstep.py.

The contract (baa_enforcement.py module docstring):
  List 1  BAA_GATED_WORKFLOWS                — the canonical set.
  List 2  the enforcing callsites           — every workflow key
          passed as a string literal to a recognized enforcement
          entrypoint in some backend .py.
  List 3  the sensitive_workflow_advanced_without_baa substrate
          invariant (assertions.py) — checked by
          test_assertion_metadata_complete.py + the substrate suite,
          not re-derived here.

This gate asserts List 1 == List 2: every active gated workflow has
≥1 enforcing callsite, and no callsite passes a key that isn't an
active gated workflow (a typo or a still-deferred key wired early).

Recognized enforcement entrypoints (baa_enforcement.py):
  require_active_baa(workflow)            — client-owner dependency factory
  enforce_or_log_admin_bypass(..., workflow, ...) — admin carve-out path
  baa_gate_passes(conn, org_id, workflow) — inline predicate
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import baa_enforcement  # noqa: E402

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Call targets that take a workflow-key string literal. require_active_baa
# takes it as arg 0; enforce_or_log_admin_bypass as arg 2; baa_gate_passes
# as arg 2. We collect ALL string-constant args of these calls — the key
# is always among them and no other string literal is passed to these
# calls, so collecting all is safe and simple.
_ENTRYPOINTS = {
    "require_active_baa",
    "enforce_or_log_admin_bypass",
    "baa_gate_passes",
}


def _string_args_of_entrypoint_calls() -> set[str]:
    """AST-walk every backend .py; return the set of string-literal
    arguments passed to any recognized enforcement entrypoint call."""
    found: set[str] = set()
    for py in _BACKEND.glob("*.py"):
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name not in _ENTRYPOINTS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


def test_every_gated_workflow_has_an_enforcing_callsite():
    """List 1 ⊆ List 2 — no gated workflow is declared without being
    wired to a real enforcement entrypoint."""
    wired = _string_args_of_entrypoint_calls()
    missing = sorted(baa_enforcement.BAA_GATED_WORKFLOWS - wired)
    assert not missing, (
        f"BAA_GATED_WORKFLOWS members with NO enforcing callsite: "
        f"{missing}. Either wire each to require_active_baa() / "
        f"enforce_or_log_admin_bypass() / baa_gate_passes() in a "
        f"backend endpoint, or move it to _DEFERRED_WORKFLOWS with a "
        f"reason + a named follow-up task."
    )


def test_no_callsite_uses_an_unregistered_or_deferred_key():
    """List 2 ⊆ List 1 — no enforcement callsite passes a key that
    isn't an ACTIVE gated workflow (catches typos + a deferred key
    wired before its Gate A)."""
    wired = _string_args_of_entrypoint_calls()
    bad = sorted(wired - baa_enforcement.BAA_GATED_WORKFLOWS)
    assert not bad, (
        f"enforcement callsites pass non-active workflow keys: {bad}. "
        f"Active keys are {sorted(baa_enforcement.BAA_GATED_WORKFLOWS)}. "
        f"A deferred key ({sorted(baa_enforcement._DEFERRED_WORKFLOWS)}) "
        f"must not be wired until its own Gate A clears."
    )


def test_deferred_workflows_each_have_a_reason():
    """Every deferred workflow must carry a non-empty reason string —
    'deferred' without a why is how scope silently rots."""
    for key, reason in baa_enforcement._DEFERRED_WORKFLOWS.items():
        assert isinstance(reason, str) and len(reason) >= 20, (
            f"_DEFERRED_WORKFLOWS[{key!r}] needs a real reason "
            f"(>=20 chars); got {reason!r}"
        )


def test_active_and_deferred_sets_are_disjoint():
    """A workflow is either actively enforced or deferred — never
    both. Overlap means the lockstep test above contradicts itself."""
    overlap = baa_enforcement.BAA_GATED_WORKFLOWS & set(
        baa_enforcement._DEFERRED_WORKFLOWS
    )
    assert not overlap, (
        f"workflows in BOTH BAA_GATED_WORKFLOWS and _DEFERRED_WORKFLOWS: "
        f"{sorted(overlap)}"
    )


def test_assert_workflow_registered_rejects_unknown_and_deferred():
    """The runtime guard must reject both an unknown key and a
    deferred key — only active keys pass."""
    import pytest

    for active in baa_enforcement.BAA_GATED_WORKFLOWS:
        baa_enforcement.assert_workflow_registered(active)  # no raise

    with pytest.raises(RuntimeError):
        baa_enforcement.assert_workflow_registered("definitely_not_a_workflow")

    for deferred in baa_enforcement._DEFERRED_WORKFLOWS:
        with pytest.raises(RuntimeError):
            baa_enforcement.assert_workflow_registered(deferred)
