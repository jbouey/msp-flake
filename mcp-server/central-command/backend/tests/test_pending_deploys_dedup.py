"""Semantic regression gate for the STEP 7c `pending_deploys` carve-out
(Task #75, Phase 2 Batch 2 close-out, 2026-05-14).

The hot-path `pending_deploys` reader in the appliance-checkin handler
(`sites.py`) was migrated to the canonical-devices CTE-JOIN-back pattern
so its `LIMIT 5` counts DISTINCT physical devices, not raw multi-
appliance observations. Pre-fix, at a 3-appliance site one physical
`pending_deploy` device had 3 `discovered_devices` rows, so `LIMIT 5`
could starve real devices out of the deploy batch.

WHY THIS IS A SOURCE-SHAPE GATE, NOT A BEHAVIORAL ONE:
The dedup logic lives entirely in the SQL CTE. A mock-the-fetch test
would only exercise the Python row-iteration loop, not the CTE — it
could not catch a regression that removed or reordered the dedup. A
true behavioral test needs real Postgres and belongs in a `*_pg.py`
file (DB-gated, runs server-side). This gate instead pins the three
load-bearing STRUCTURAL invariants of the migrated query (the Task #75
Gate A P0s) so a future refactor cannot silently undo them:

  P0-2  `local_device_id` flows through the CTE — the UPDATE that
        transitions device_status is keyed on it.
  P0-3  the `site_credentials` LIKE-JOIN stays OUTSIDE the CTE — inside,
        it would re-evaluate per raw observation and re-introduce the
        duplication the CTE removes.
  +     `device_status = 'pending_deploy'` stays OUTSIDE the CTE — so a
        canonical device whose freshest observation is already
        'deploying' drops out (no re-deploy), and the CTE itself picks
        the freshest observation regardless of status.

Runtime behaviour is covered by: the already-shipped sibling CTE at
sites.py:7287 (proven in prod), plus the Task #75 4h post-deploy bake.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SITES_PY = _BACKEND / "sites.py"


_FETCH_CLOSE = '"' * 3 + ", checkin.site_id)"


def _step7c_query() -> str:
    """Extract the STEP 7c pending_deploys SQL string from sites.py.

    Anchored on the `# === STEP 7c:` comment and the closing triple-
    quote + `, checkin.site_id)` of the fetch() call.
    """
    text = _SITES_PY.read_text()
    start = text.find("# === STEP 7c:")
    assert start != -1, "STEP 7c marker not found in sites.py"
    end = text.find(_FETCH_CLOSE, start)
    assert end != -1, "STEP 7c fetch() close not found after the marker"
    return text[start:end]


def test_step7c_uses_canonical_cte_join_back():
    """The query MUST use the canonical_devices CTE-JOIN-back pattern —
    the whole point of the carve-out (Task #75)."""
    q = _step7c_query()
    assert "WITH dd_freshest AS" in q, (
        "STEP 7c pending_deploys query no longer uses the dd_freshest "
        "CTE — the canonical-devices dedup was removed. This re-opens "
        "the LIMIT-5 starvation bug (Task #75)."
    )
    assert "DISTINCT ON (cd.canonical_id)" in q, (
        "STEP 7c CTE lost `DISTINCT ON (cd.canonical_id)` — without it "
        "the CTE does not collapse multi-appliance duplicates."
    )
    assert "JOIN discovered_devices dd" in q and "canonical_devices cd" in q, (
        "STEP 7c CTE no longer joins canonical_devices -> discovered_devices."
    )
    assert "# canonical-migration:" in q, (
        "STEP 7c lost its `# canonical-migration:` marker — "
        "test_no_raw_discovered_devices_count.py depends on it."
    )


def test_step7c_local_device_id_flows_through_cte():
    """P0-2: `local_device_id` must survive the CTE so the device_status
    UPDATE 30 lines below stays keyed on it."""
    q = _step7c_query()
    # `dd.*` in the CTE carries local_device_id; the outer SELECT must
    # project it explicitly.
    assert "dd.local_device_id" in q, (
        "STEP 7c outer SELECT no longer projects dd.local_device_id — "
        "the downstream `UPDATE ... WHERE local_device_id = ANY($2)` "
        "(P0-2 write-coupling) would break."
    )
    # The UPDATE must still be keyed on local_device_id.
    text = _SITES_PY.read_text()
    update_start = text.find("UPDATE discovered_devices SET device_status = 'deploying'")
    assert update_start != -1, "STEP 7c device_status UPDATE not found"
    update_block = text[update_start:update_start + 300]
    assert "local_device_id = ANY($2::text[])" in update_block, (
        "STEP 7c UPDATE is no longer keyed on local_device_id — "
        "P0-2 write-coupling broken."
    )


def test_step7c_credential_join_outside_cte():
    """P0-3: the site_credentials LIKE-JOIN must be applied to
    `dd_freshest`, NOT inside the CTE — inside, it re-evaluates per raw
    observation and re-introduces duplication."""
    q = _step7c_query()
    cte_close = q.find(")")  # end of the WITH (... ) block — first ')'
    # More robust: find the CTE body between 'AS (' and the matching
    # close right before the outer SELECT.
    cte_open = q.find("WITH dd_freshest AS (")
    outer_select = q.find("SELECT dd.local_device_id")
    assert cte_open != -1 and outer_select != -1, "STEP 7c query shape unrecognised"
    cte_body = q[cte_open:outer_select]
    assert "site_credentials" not in cte_body, (
        "STEP 7c moved the site_credentials JOIN INSIDE the dd_freshest "
        "CTE — P0-3 violation. Inside the CTE the LIKE-JOIN re-evaluates "
        "per raw observation and re-introduces the duplication the CTE "
        "exists to remove. Keep it on `FROM dd_freshest`."
    )
    outer = q[outer_select:]
    assert "JOIN site_credentials sc" in outer and "FROM dd_freshest dd" in outer, (
        "STEP 7c outer query no longer joins site_credentials onto "
        "dd_freshest — P0-3."
    )


def test_step7c_status_filter_outside_cte():
    """`device_status = 'pending_deploy'` must be in the OUTER WHERE,
    not in the CTE. Outside: the CTE picks each canonical device's
    freshest observation regardless of status, then the outer filter
    drops devices whose freshest row is already 'deploying' — no
    re-deploy. Inside the CTE it would pick the freshest *pending_deploy*
    row and a device mid-deploy could be re-picked from a stale row."""
    q = _step7c_query()
    cte_open = q.find("WITH dd_freshest AS (")
    outer_select = q.find("SELECT dd.local_device_id")
    cte_body = q[cte_open:outer_select]
    outer = q[outer_select:]
    assert "device_status" not in cte_body, (
        "STEP 7c moved `device_status` filtering INSIDE the dd_freshest "
        "CTE — this can re-deploy a device that is already 'deploying' "
        "(a stale duplicate row would be re-picked). Keep the "
        "`device_status = 'pending_deploy'` filter in the OUTER WHERE."
    )
    assert "device_status = 'pending_deploy'" in outer, (
        "STEP 7c outer WHERE lost `device_status = 'pending_deploy'`."
    )
