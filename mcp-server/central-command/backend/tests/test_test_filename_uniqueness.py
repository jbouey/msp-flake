"""Pure-source guard: test file names are unambiguously distinct.

Round-table 2026-04-28 angle 2 P1 + angle 4 P2: today's deploy
cascade root cause was that `test_flywheel_promote.py` and
`test_flywheel_promotion.py` are similar enough that running one
locally pre-push silently misses the other. The pre-push stem-match
catches this when PRE_PUSH_FULL=1 is set; this test catches it
unconditionally at CI time.

Discipline: no two test files in the same directory may differ by
≤ 2 characters in their stem. Add a third character (descriptive
suffix) to disambiguate. Pre-2026-04-28 collisions:

  test_flywheel_promote.py     (10-line FakeConn promote_candidate suite)
  test_flywheel_promotion.py   (TestPromotePatternEndpoint endpoint tests)

Renamed to:
  test_flywheel_promote_candidate.py   (commit b62c91d2)
  test_flywheel_promotion.py           (kept; tests the /promote endpoint)

This test fails CI if a new collision lands.
"""
from __future__ import annotations

import pathlib
from itertools import combinations
from typing import List, Tuple


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TESTS_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend" / "tests"

# Maximum allowed character-edit distance between any two test files.
# 0 = exact-match dupe (impossible in one filesystem). 2 = the
# singular/plural class (`promote` vs `promotion` is 4 — safe;
# `promote` vs `promote_` is 1 — caught).
MIN_EDIT_DISTANCE = 3


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance — small string sizes, plain DP."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                cur[j - 1] + 1,        # insert
                prev[j] + 1,           # delete
                prev[j - 1] + cost,    # substitute
            )
        prev = cur
    return prev[-1]


def test_no_near_duplicate_test_filenames():
    """For every pair of test_*.py files in tests/, their stems must
    differ by at least MIN_EDIT_DISTANCE characters."""
    test_files: List[pathlib.Path] = sorted(
        p for p in TESTS_DIR.glob("test_*.py")
    )
    stems = [p.stem for p in test_files]

    near_collisions: List[Tuple[str, str, int]] = []
    for a, b in combinations(stems, 2):
        d = _levenshtein(a, b)
        if d < MIN_EDIT_DISTANCE:
            near_collisions.append((a, b, d))

    assert not near_collisions, (
        "Test filenames too similar — the 2026-04-28 deploy-cascade "
        "class. Disambiguate by adding a descriptive suffix. Each "
        "near-collision below is at risk of being silently skipped "
        "when a developer runs the wrong one pre-push.\n\n"
        + "\n".join(
            f"  - {a}.py vs {b}.py (edit distance {d})"
            for a, b, d in near_collisions
        )
    )
