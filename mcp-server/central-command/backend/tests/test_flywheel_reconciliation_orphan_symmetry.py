"""Regression: flywheel reconciliation joins on runbook_id, not rule_id.

Pre-Session-209 the Check-3 reconciliation filter in background_tasks.py
joined runbooks on `lr.rule_id`:

    LEFT JOIN runbooks rb ON rb.runbook_id = lr.rule_id

but `l1_rules` has TWO identifier columns — `rule_id` (the L1 rule's own
key) and `runbook_id` (the foreign-key-like pointer into the runbooks
library). Joining on the wrong column under-reported orphans by ~9% on
the production DB (11 reported vs 12 actual).

The fix joins on `lr.runbook_id`. This test locks the behavior so a
future refactor cannot silently flip the column back.

Two assertions:
  1. The canonical orphan-detection query in background_tasks.py joins
     via `lr.runbook_id`, not `lr.rule_id`.
  2. No surviving code path in background_tasks.py contains the buggy
     `rb.runbook_id = lr.rule_id` string.
"""
from __future__ import annotations

import pathlib
import re


BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
BG_TASKS = BACKEND_DIR / "background_tasks.py"
# Session 210-B deleted the dead duplicate _flywheel_promotion_loop
# from background_tasks.py. The live loop (the one the scheduler actually
# runs) lives in `mcp-server/main.py`. Platform-pattern aggregation +
# L2-synthetic-ID filtering happen there now.
REPO_ROOT = BACKEND_DIR.parents[2]
MAIN_PY = REPO_ROOT / "mcp-server" / "main.py"


def _source() -> str:
    return BG_TASKS.read_text(encoding="utf-8")


def _live_flywheel_source() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def test_orphan_runbooks_query_joins_on_runbook_id():
    src = _source()
    # The Check-3 block starts with the comment we added in Session 209;
    # grep the nearest LEFT JOIN below it.
    marker = "Check 3: l1_rules promoted with no runbooks entry"
    idx = src.find(marker)
    assert idx != -1, (
        "Cannot find the 'Check 3' reconciliation block in "
        "background_tasks.py — did someone rename the comment?"
    )
    # Grab the next 20 lines after the marker — the JOIN must be in there.
    block = src[idx : idx + 1500]
    assert "rb.runbook_id = lr.runbook_id" in block, (
        "Orphan reconciliation filter lost its correct JOIN. Expected "
        "`rb.runbook_id = lr.runbook_id` in the Check-3 block (joining "
        "the runbooks library on the l1_rules.runbook_id FK). Pre-Session-209 "
        "this joined on lr.rule_id and under-reported orphans. Block seen:\n"
        + block[:400]
    )


def test_no_surviving_rule_id_join_against_runbooks():
    """No line in background_tasks.py may re-introduce the buggy join.

    We allow `rb.runbook_id = lr.runbook_id` (correct) and forbid
    `rb.runbook_id = lr.rule_id` (buggy).
    """
    src = _source()
    buggy = re.compile(r"rb\.runbook_id\s*=\s*lr\.rule_id")
    hits = buggy.findall(src)
    assert not hits, (
        f"Found {len(hits)} occurrence(s) of the buggy JOIN pattern "
        "`rb.runbook_id = lr.rule_id` in background_tasks.py. This was "
        "the Session-209 reconciliation bug — always join runbooks on "
        "l1_rules.runbook_id, never on l1_rules.rule_id."
    )


def test_l2_synthetic_ids_excluded_from_platform_aggregation():
    """Platform-pattern aggregation must filter out legacy L2-* IDs.

    Session 209 added an explicit `et.runbook_id NOT LIKE 'L2-%'` guard
    to the aggregation WHERE clause. Migration 237 cleared the pre-existing
    phantom rows. Removing the guard would cause the 30-min aggregation
    loop to re-create those rows and spam `Skipping platform promotion:
    invalid runbook_id` warnings forever.

    Session 210-B: the live `_flywheel_promotion_loop` lives in
    `mcp-server/main.py` (background_tasks.py had a dead duplicate that
    was deleted). The filter MUST live in the live copy.
    """
    live_src = _live_flywheel_source()
    assert "NOT LIKE 'L2-%'" in live_src, (
        "Platform-pattern aggregation query in mcp-server/main.py lost its "
        "synthetic-ID filter. Re-add `AND et.runbook_id NOT LIKE 'L2-%'` to "
        "the WHERE clause in the Step-3 INSERT INTO platform_pattern_stats "
        "block inside _flywheel_promotion_loop."
    )
    # Also assert the dead path is still dead: background_tasks.py must
    # NOT re-introduce a flywheel_promotion_loop (Session 209 banned the
    # pattern of two schedulers writing to the same tables).
    bg_src = _source()
    assert "async def flywheel_promotion_loop" not in bg_src, (
        "background_tasks.py defines a flywheel_promotion_loop. Session "
        "210-B deleted that duplicate because it shadowed the live copy "
        "in mcp-server/main.py and re-created phantom L2-* rows. The live "
        "loop is the one main.py schedules; don't resurrect the duplicate."
    )
