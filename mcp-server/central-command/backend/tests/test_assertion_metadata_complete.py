"""Every substrate invariant MUST have a human display_name +
recommended_action. v36 round-table mandate (Session 207).

Rationale: engineering names like `provisioning_stalled` are noise to
an operator. The dashboard surfaces display_name + recommended_action
prominently; without them, the operator sees only raw invariant names
and a JSONB blob, which is exactly the failure that turned tonight's
t740 debug into a 3-hour session.

If you add a new invariant to ALL_ASSERTIONS, you MUST also add a
matching entry to assertions._DISPLAY_METADATA. This test catches it."""
from __future__ import annotations

import sys
import pathlib

# Let pytest find the backend dir when run from the repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import ALL_ASSERTIONS  # noqa: E402


def test_every_assertion_has_display_name():
    missing = [a.name for a in ALL_ASSERTIONS if not a.display_name]
    assert not missing, (
        f"Invariants without display_name: {missing}. "
        f"Add each to assertions._DISPLAY_METADATA."
    )


def test_every_assertion_has_recommended_action():
    missing = [a.name for a in ALL_ASSERTIONS if not a.recommended_action]
    assert not missing, (
        f"Invariants without recommended_action: {missing}. "
        f"Add each to assertions._DISPLAY_METADATA."
    )


def test_recommended_actions_are_sentences():
    """Recommended_action should be at least 20 chars (rough proxy for
    'not a placeholder'). If it's shorter, the author probably stubbed
    it — the dashboard needs something actionable, not 'TODO'."""
    short = [
        (a.name, a.recommended_action)
        for a in ALL_ASSERTIONS
        if len(a.recommended_action) < 20
    ]
    assert not short, (
        f"Invariants with too-short recommended_action (<20 chars): {short}. "
        f"Write a real one-sentence remediation."
    )


def test_display_names_are_sentences():
    short = [
        (a.name, a.display_name)
        for a in ALL_ASSERTIONS
        if len(a.display_name) < 8
    ]
    assert not short, (
        f"Invariants with too-short display_name (<8 chars): {short}. "
        f"Write a real operator-facing name."
    )


def test_no_duplicate_display_names():
    """Two different invariants shouldn't have identical display_names —
    would confuse the operator about which rule fired."""
    names = [a.display_name for a in ALL_ASSERTIONS]
    dupes = [n for n in set(names) if names.count(n) > 1]
    assert not dupes, f"Duplicate display_names: {dupes}"
