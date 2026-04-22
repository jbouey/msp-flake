"""Regression test for FIX-7 (2026-04-22): the appliance_disk_pressure
invariant must match BOTH ENOSPC surfaces.

Before FIX-7 the query only matched `%no space left%`. The sqlite layer
translates ENOSPC into `database or disk is full` when committing to
/nix/var/nix/db/db.sqlite or the eval-cache — so an appliance whose
sqlite fails to commit but whose kernel banner never surfaces would
silently evade the invariant.

Evidence: canary-048-412a5a1d6e4a (appliance 7C:D3:0A:7C:55:18,
2026-04-22) had a 2 KB error_message containing `database or disk is
full` three times and no `no space left` anywhere. The substrate_violations
table did NOT open a row — same structural condition as 84:3A:5B:1D:0F:E5
but the regex missed it.

This test pins the invariant to both phrases by reading the SQL source
of `_check_appliance_disk_pressure`. Pure unit test — no Postgres
required.
"""
from __future__ import annotations

import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import _check_appliance_disk_pressure  # noqa: E402


# The 4 UNION ALL branches in the CTE (admin_orders.error_message,
# admin_orders.result->>'error_message', fleet_order_completions.error_message,
# fleet_order_completions.output->>'error_message'). Each must carry
# both ENOSPC phrases.
EXPECTED_BRANCHES = 4
SOURCE = inspect.getsource(_check_appliance_disk_pressure)


def test_no_space_left_phrase_in_every_branch():
    """The kernel ENOSPC banner must be matched on all 4 union branches."""
    count = SOURCE.lower().count("ilike '%no space left%'")
    assert count == EXPECTED_BRANCHES, (
        f"Expected {EXPECTED_BRANCHES} `ILIKE '%no space left%'` clauses "
        f"(one per union branch), found {count}. "
        "If you added or removed a union branch, update EXPECTED_BRANCHES."
    )


def test_sqlite_enospc_phrase_in_every_branch():
    """The sqlite-translated ENOSPC phrase must be matched on all 4 branches
    too — this is the pattern FIX-7 added. Regressing on this phrase
    means the 7C:D3:0A:7C:55:18 class of failure goes silent again.
    """
    count = SOURCE.lower().count("ilike '%database or disk is full%'")
    assert count == EXPECTED_BRANCHES, (
        f"FIX-7 regression: expected {EXPECTED_BRANCHES} "
        "`ILIKE '%database or disk is full%'` clauses (one per union "
        f"branch), found {count}. "
        "The sqlite layer translates ENOSPC to this phrase when "
        "/nix/var/nix/db/db.sqlite can't commit; without this match "
        "the invariant is deaf to the canary that surfaced FIX-7."
    )


def test_branches_use_or_not_and():
    """Each branch must `OR` the two phrases — an AND would require BOTH
    phrases to appear in the same error, which is the opposite of what
    we want."""
    lowered = SOURCE.lower()
    # The structural shape we want: `(col ILIKE '%no space left%'
    #                                  OR col ILIKE '%database or disk is full%')`
    assert "or ao.error_message ilike '%database or disk is full%'" in lowered, (
        "admin_orders.error_message branch must OR the sqlite phrase "
        "onto the kernel phrase, not AND."
    )
    assert "or ao.result->>'error_message' ilike '%database or disk is full%'" in lowered, (
        "admin_orders.result JSONB branch must OR the sqlite phrase."
    )
    assert "or foc.error_message ilike '%database or disk is full%'" in lowered, (
        "fleet_order_completions.error_message branch must OR the sqlite phrase."
    )
    assert "or foc.output->>'error_message' ilike '%database or disk is full%'" in lowered, (
        "fleet_order_completions.output JSONB branch must OR the sqlite phrase."
    )
