"""Regression gate — `resolution_tier='L1'` MUST be guarded by a
nearby write to `incident_remediation_steps`.

Substrate invariant `l1_resolution_without_remediation_step` (Session
219, 2026-05-11) detected 1131 of 2327 L1 resolutions (49%) on chaos-
lab (north-valley-branch-2) with `resolution_tier='L1'` but no
matching `incident_remediation_steps` row. branch-1 (paying customer)
has zero exposure in the 30-day window.

Mig 137 moved remediation tracking from `incidents.remediation_history`
JSONB to the relational `incident_remediation_steps` table.
`resolution_tier='L1'` is the customer-facing "auto-healed" label —
a missing relational step is a false claim on the audit chain.

Sibling of `test_l2_resolution_requires_decision_record.py` (Session
219 mig 300). Same shape, same 80-line lookback, same allowlist
mechanism via `# l1-orphan-allowed: <reason>` opt-out comment.

Algorithm:
  1. Scan agent_api.py + sites.py + main.py (matching L2 sibling scope
     per Gate A v2 P0-3) for any literal that ASSIGNS
     resolution_tier='L1' (Python OR SQL UPDATE/INSERT shapes).
  2. For each match, look back ≤80 lines for an
     `incident_remediation_steps` write OR an
     `l1_remediation_step_recorded` guard flag (matching L2 sibling
     pattern).
  3. Fail with file:line if no guard found, unless the line has a
     `# l1-orphan-allowed: <rationale>` opt-out comment.

Allowed exceptions:
  - Proposal-time tier assignment (incident_create flow where the
    L1 dispatch decision is being made — the relational step is
    written by the daemon callback at agent_api.py:1248-1262 AFTER
    the runbook completes).

Phase 1 ships this gate as a RATCHET — existing callsites are
allowlisted; new callsites must follow the pattern. Phase 3
(post-Phase-2 root cause) will tighten the gate by removing the
proposal-write allowlist after the auto-clean race is fixed.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent  # mcp-server/

# Three-file scope matches L2 sibling per Gate A v2 P0-3 — broader
# walks hit false positives in test fixtures + migration files +
# comment strings.
_FILES_TO_SCAN = [
    _BACKEND / "agent_api.py",
    _BACKEND / "sites.py",
    _REPO / "main.py",
]

# Match python literal assignment:
#   resolution_tier = "L1"
#   resolution_tier = 'L1'
#   resolution_tier="L1"
_RESOLUTION_L1_RE = re.compile(
    r"^(\s*)resolution_tier\s*=\s*['\"]L1['\"]"
)

# SQL UPDATE/INSERT shape:
#   resolution_tier = 'L1'  (inside SQL string)
_SQL_SET_L1_RE = re.compile(
    r"resolution_tier\s*=\s*'L1'"
)

# Same 80-line lookback as L2 sibling — realistic handler body shape.
_LOOKBACK = 80

# Acceptable guards within the lookback window:
#   - INSERT INTO incident_remediation_steps (canonical write path)
#   - l1_remediation_step_recorded (sibling pattern to l2 flag)
_GUARD_RE = re.compile(
    r"(incident_remediation_steps|l1_remediation_step_recorded)\b"
)


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
            # Match python-literal AND SQL shapes.
            py_match = _RESOLUTION_L1_RE.search(line)
            sql_match = _SQL_SET_L1_RE.search(line) and not py_match
            if not (py_match or sql_match):
                continue
            # Skip if line is inside a comment.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Skip if the line is a WHERE/FILTER clause (substrate
            # invariant SQL legitimately mentions resolution_tier='L1'
            # for scanning purposes).
            if "WHERE i.resolution_tier" in line or "WHERE resolution_tier" in line:
                continue
            if "FILTER (WHERE" in line and "resolution_tier" in line:
                continue
            # Skip equality-comparison shapes (resolution_tier == "L1").
            if "==" in line and "resolution_tier" in line:
                continue
            # Skip body.get(..., "L1") fallback-default — that's a
            # caller-provided value, not a write.
            if ".get(" in line and "resolution_tier" in line:
                continue
            # Look back for guard.
            window = lines[max(0, i - _LOOKBACK) : i + 1]
            window_text = "\n".join(window)
            if _GUARD_RE.search(window_text):
                continue
            # Allow same-line opt-out comment with explicit rationale.
            if "# l1-orphan-allowed" in line:
                continue
            out.append(f"{rel}:{i + 1} — {line.strip()[:120]}")
    return out


def test_resolution_tier_l1_requires_remediation_step_guard():
    """Every assignment of `resolution_tier='L1'` (Python literal OR
    inside an SQL UPDATE/INSERT) MUST be preceded within 80 lines by
    either an `incident_remediation_steps` write OR an
    `l1_remediation_step_recorded` guard reference. Closes Session 219
    L1-orphan class detected by substrate invariant
    `l1_resolution_without_remediation_step`."""
    viols = _violations()
    assert not viols, (
        "resolution_tier='L1' set without `incident_remediation_steps` "
        "write or `l1_remediation_step_recorded` guard. Substrate "
        "invariant l1_resolution_without_remediation_step (Session 219, "
        "2026-05-11). Add the write or mark with "
        "`# l1-orphan-allowed: <rationale>` if it's a proposal-write "
        "(daemon callback writes the relational step later).\n\n"
        + "\n".join(f"  - {v}" for v in viols)
    )


def test_synthetic_unguarded_caught():
    """Positive control — synthetic source with the bad pattern is
    caught by the matcher. Prevents the gate from silently rotting if
    the regex breaks."""
    bad = (
        "        try:\n"
        "            order = await dispatch_l1(...)\n"
        "        except Exception:\n"
        "            pass\n"
        "        if order:\n"
        "            resolution_tier = 'L1'\n"
    )
    lines = bad.splitlines()
    found_unguarded = False
    for i, line in enumerate(lines):
        if _RESOLUTION_L1_RE.search(line):
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i + 1])
            if not _GUARD_RE.search(window):
                found_unguarded = True
    assert found_unguarded, (
        "matcher should have caught the synthetic unguarded "
        "resolution_tier='L1' assignment"
    )


def test_synthetic_guarded_passes():
    """Negative control — the same shape with the relational-step
    write nearby should NOT be flagged."""
    good = (
        "        await db.execute(text('''\n"
        "            INSERT INTO incident_remediation_steps\n"
        "                (incident_id, step_idx, tier, runbook_id, result)\n"
        "            VALUES (:id, 0, 'L1', :rb, 'dispatched')\n"
        "        '''), {'id': inc_id, 'rb': runbook_id})\n"
        "        resolution_tier = 'L1'\n"
    )
    lines = good.splitlines()
    for i, line in enumerate(lines):
        if _RESOLUTION_L1_RE.search(line):
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i + 1])
            assert _GUARD_RE.search(window), (
                "matcher should NOT flag the synthetic guarded "
                "resolution_tier='L1' assignment; line=" + line
            )


def test_synthetic_allowlist_comment_passes():
    """Negative control — opt-out comment allows the proposal-write
    case (relational step is written elsewhere)."""
    good = (
        "        runbook_id = matched_runbook\n"
        "        resolution_tier = 'L1'  # l1-orphan-allowed: proposal-write\n"
    )
    lines = good.splitlines()
    found_unguarded_no_optout = False
    for i, line in enumerate(lines):
        if _RESOLUTION_L1_RE.search(line):
            window = "\n".join(lines[max(0, i - _LOOKBACK) : i + 1])
            if not _GUARD_RE.search(window) and "# l1-orphan-allowed" not in line:
                found_unguarded_no_optout = True
    assert not found_unguarded_no_optout, (
        "synthetic with `# l1-orphan-allowed` opt-out comment should "
        "NOT be flagged"
    )
