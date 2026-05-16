"""CI gate: go_agent terminal-status exclusion lockstep.

D4 closure 2026-05-02. Two locations must use the SAME terminal-status
exclusion set or the operator's decommission decision silently flaps:

  1. main.py::_go_agent_status_decay_loop — the SQL `WHERE status NOT IN
     (...)` filter that protects terminal rows from being overwritten by
     the heartbeat-age decay machine.
  2. assertions.py::_check_go_agent_heartbeat_stale — the predicate's
     `AND status NOT IN (...)` exclusion so terminal-status agents don't
     fire bg sev2 anymore.

If list (1) excludes 'decommissioned' but list (2) doesn't: operator
marks decommissioned, alarm STILL fires forever.
If list (2) excludes 'decommissioned' but list (1) doesn't: operator
marks decommissioned, alarm clears, but next state-machine tick reverts
to 'dead' and the alarm comes back.

Lockstep prevents both regressions.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"
_ASSERTIONS_PY = _BACKEND / "assertions.py"


# Match the literal-list inside both locations:
#   WHERE status NOT IN ('decommissioned', 'archived')
#   AND status NOT IN ('decommissioned', 'archived')
_PATTERN = re.compile(
    r"status\s+NOT\s+IN\s*\(\s*([^)]+)\)",
    re.IGNORECASE,
)

# Anchor the search to the specific function/loop that owns the
# terminal-status filter — otherwise the first `status NOT IN (...)`
# in the file wins, which silently flaps when an unrelated sibling
# check adds its own. Update if either function is renamed.
_ANCHORS = {
    "main.py": "_go_agent_status_decay_loop",
    "assertions.py": "_check_go_agent_heartbeat_stale",
}


def _extract_terminal_set(path: pathlib.Path) -> set[str]:
    text = path.read_text()
    anchor = _ANCHORS.get(path.name)
    if not anchor:
        raise AssertionError(f"No anchor registered for {path.name}")
    # Find the anchor function body, then the first NOT IN inside it.
    anchor_match = re.search(
        rf"(?:async\s+)?def\s+{re.escape(anchor)}\b(.*?)(?=\n(?:async\s+)?def\s+\w+\b|\Z)",
        text,
        re.DOTALL,
    )
    if not anchor_match:
        raise AssertionError(
            f"Could not find function `{anchor}` in {path.name}. Either "
            f"the function was renamed (update _ANCHORS) or removed "
            f"(regression)."
        )
    matches = _PATTERN.findall(anchor_match.group(1))
    if not matches:
        raise AssertionError(
            f"Could not find `status NOT IN (...)` pattern inside "
            f"{anchor} in {path.name}. Either the exclusion was removed "
            f"(regression) or refactored — update this test."
        )
    raw = matches[0]
    values = set(re.findall(r"'([^']+)'", raw))
    return values


def test_state_machine_and_predicate_share_terminal_status_set():
    state_machine = _extract_terminal_set(_MAIN_PY)
    predicate = _extract_terminal_set(_ASSERTIONS_PY)
    assert state_machine == predicate, (
        f"Lockstep violation: go_agent terminal-status exclusion sets "
        f"differ.\n"
        f"  main.py state machine: {sorted(state_machine)}\n"
        f"  assertions.py predicate: {sorted(predicate)}\n"
        f"Both MUST be identical or operator decommission decisions "
        f"silently regress (state machine reverts the row OR predicate "
        f"keeps alarming despite operator action). See D4 design "
        f"(2026-05-02) for rationale."
    )


def test_terminal_status_set_includes_required_values():
    """At minimum, both must exclude 'decommissioned' — that's the
    operator's primary signal. Add 'archived' for symmetry."""
    state_machine = _extract_terminal_set(_MAIN_PY)
    required = {"decommissioned", "archived"}
    missing = required - state_machine
    assert not missing, (
        f"Required terminal-status values missing from state machine "
        f"exclusion: {sorted(missing)}. Operator can't suppress alarms "
        f"on retired/archived workstations."
    )
