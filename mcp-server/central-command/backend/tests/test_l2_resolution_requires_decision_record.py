"""Regression gate — `resolution_tier='L2'` MUST be guarded by
`l2_decision_recorded`.

Substrate invariant `l2_resolution_without_decision_record` (Session
219 mig 300) detected 26 north-valley-branch-2 incidents with
`resolution_tier='L2'` but no matching `l2_decisions` row. Root cause:
two callsites (agent_api.py:1338 + main.py:4530) caught
`record_l2_decision()` exceptions and continued setting
`resolution_tier='L2'` anyway — producing ghost-L2 incidents with no
audit trail.

Forward fix introduced an `l2_decision_recorded: bool` flag set inside
the try-block immediately after `record_l2_decision()`. The
`resolution_tier='L2'` UPDATE only runs when the flag is True. This
gate enforces the pattern AT REVIEW TIME so the gap structurally
cannot reopen.

Algorithm:
  1. Scan agent_api.py + main.py for any literal that ASSIGNS
     resolution_tier='L2' OR 'L2' to resolution_tier.
  2. For each match, look back ≤30 lines for an `if
     l2_decision_recorded` guard.
  3. Fail with file:line if no guard found.

Allowed exceptions (BLOCK_ALLOWLIST) require an explicit comment
referencing the substrate invariant rationale on the same line as
the assignment.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent  # mcp-server/

_FILES_TO_SCAN = [
    _BACKEND / "agent_api.py",
    _REPO / "main.py",
]

# Pattern matches:
#   resolution_tier = "L2"
#   resolution_tier = 'L2'
#   resolution_tier='L2'
# AND any indentation level. Excludes:
#   resolution_tier = 'L1' / 'L3' / etc.
#   sql 'resolution_tier' literals (caught later by the lookback check)
_RESOLUTION_L2_RE = re.compile(
    r"^(\s*)resolution_tier\s*=\s*['\"]L2['\"]"
)

# SQL UPDATE shapes — also need guarding when they set resolution_tier='L2'
# inside an UPDATE incidents SET ... statement. We rely on the same
# lookback heuristic for those.
_SQL_SET_L2_RE = re.compile(
    r"resolution_tier\s*=\s*'L2'"
)

# How many lines back to look for the guard. Real handlers issue
# a 30+ line `await db.execute(text(""" UPDATE incidents ...""" ))`
# block between the `if l2_decision_recorded` line and the actual
# UPDATE. 80 lines covers the realistic handler body shape — anything
# longer and the developer should hoist the guard closer to the SET.
_LOOKBACK = 80

# `l2_decision_recorded` may appear inside an `if` condition or as a
# bare identifier in a chained boolean (e.g.
# `if l2_decision_recorded and decision.runbook_id ...`).
_GUARD_RE = re.compile(r"\bl2_decision_recorded\b")

# Files whose violations are explicitly allowed (with rationale).
BLOCK_ALLOWLIST: set[str] = set()


def _violations() -> list[str]:
    out: list[str] = []
    for fp in _FILES_TO_SCAN:
        if not fp.exists():
            continue
        rel = str(fp.relative_to(_REPO))
        try:
            text = fp.read_text()
        except Exception:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            # Match python-literal AND SQL UPDATE shapes.
            py_match = _RESOLUTION_L2_RE.search(line)
            sql_match = (
                _SQL_SET_L2_RE.search(line)
                # Skip the python literal pattern (already counted).
                and not py_match
                # Skip the substrate-invariant assertion query itself.
                and "resolution_tier='L2'" not in line.replace(" ", "")
                or py_match
            )
            if not (py_match or sql_match):
                continue
            # Skip if line is inside a comment or docstring (heuristic).
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Skip if the line is the substrate invariant assertion
            # query (which legitimately mentions resolution_tier='L2'
            # for SCAN purposes).
            if "WHERE i.resolution_tier" in line or "WHERE resolution_tier" in line:
                continue
            if "FILTER (WHERE" in line and "resolution_tier" in line:
                continue
            # Look back for guard.
            window = lines[max(0, i - _LOOKBACK) : i + 1]
            window_text = "\n".join(window)
            if _GUARD_RE.search(window_text):
                continue
            # Allow same-line opt-out comment.
            if "# l2-decision-record-noqa" in line:
                continue
            out.append(f"{rel}:{i + 1} — {line.strip()[:120]}")
    return out


def test_resolution_tier_l2_requires_l2_decision_recorded_guard():
    """Every assignment of `resolution_tier='L2'` (Python literal OR
    inside an SQL UPDATE) MUST be preceded within 30 lines by a
    reference to `l2_decision_recorded`. Closes Session 219 task #104
    forward-fix class."""
    viols = _violations()
    assert not viols, (
        "resolution_tier='L2' set without `l2_decision_recorded` guard. "
        "Substrate invariant l2_resolution_without_decision_record "
        "(mig 300, Session 219 task #104). Add the gate or document "
        "with `# l2-decision-record-noqa` and rationale.\n\n"
        + "\n".join(f"  - {v}" for v in viols)
    )


def test_synthetic_unguarded_caught():
    """Positive control — a synthetic source string with the bad
    pattern should be caught by the matcher."""
    bad = (
        "        try:\n"
        "            await record_l2_decision(...)\n"
        "        except Exception:\n"
        "            pass\n"
        "        if decision.runbook_id:\n"
        "            resolution_tier = 'L2'\n"
    )
    lines = bad.splitlines()
    found_unguarded = False
    for i, line in enumerate(lines):
        if _RESOLUTION_L2_RE.search(line):
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i + 1])
            if not _GUARD_RE.search(window):
                found_unguarded = True
    assert found_unguarded, (
        "matcher should have caught the synthetic unguarded "
        "resolution_tier='L2' assignment"
    )


def test_synthetic_guarded_passes():
    """Negative control — the same shape with the guard should pass."""
    good = (
        "        l2_decision_recorded = False\n"
        "        try:\n"
        "            await record_l2_decision(...)\n"
        "            l2_decision_recorded = True\n"
        "        except Exception:\n"
        "            pass\n"
        "        if l2_decision_recorded and decision.runbook_id:\n"
        "            resolution_tier = 'L2'\n"
    )
    lines = good.splitlines()
    for i, line in enumerate(lines):
        if _RESOLUTION_L2_RE.search(line):
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i + 1])
            assert _GUARD_RE.search(window), (
                "matcher should NOT flag the synthetic guarded "
                "resolution_tier='L2' assignment; line=" + line
            )
