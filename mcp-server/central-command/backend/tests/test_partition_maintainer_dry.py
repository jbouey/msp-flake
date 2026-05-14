"""Coverage tests for partition_maintainer_dry invariant.

D7 followup 2026-05-01 — closes the fixture coverage gap for
`partition_maintainer_dry`. Doesn't fit the parametrized JSON-fixture
pattern in `test_substrate_prod_fixtures.py` because the expected
next-month suffix depends on `datetime.now()`. Mocks datetime to
get deterministic test cases.

The invariant queries pg_inherits via asyncpg fetch + checks each
parent's children for next-month substring patterns:
- compliance_bundles:       `_YYYY_MM`
- portal_access_log:        `_YYYY_MM`
- appliance_heartbeats:     `_yYYYYMM`
- promoted_rule_events:     `_YYYYMM`
- canonical_metric_samples: `_YYYY_MM`
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import assertions  # noqa: E402


class _FakeConn:
    """Minimal asyncpg fake — fetch returns a pre-set list."""

    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, _sql, *_args):
        return self._rows


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# Frozen test date: 2026-04-30 → next month suffix patterns are:
#   compliance_bundles:       `2026_05`
#   portal_access_log:        `2026_05`
#   appliance_heartbeats:     `y202605`
#   promoted_rule_events:     `202605`
#   canonical_metric_samples: `2026_05`
_FROZEN_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def _mock_now():
    """Patch the assertions module's datetime import to freeze now()."""
    return patch.object(
        assertions,
        "_check_partition_maintainer_dry",
        wraps=assertions._check_partition_maintainer_dry,
    )


def test_partition_maintainer_dry_clean_all_5_have_next_month():
    """All 5 critical partitioned tables have next-month coverage.
    Returns 0 violations — steady-state for a healthy
    partition_maintainer_loop."""
    rows = [
        {"parent_table": "compliance_bundles",
         "children": ["compliance_bundles_2026_05", "compliance_bundles_2026_04",
                      "compliance_bundles_2026_03"]},
        {"parent_table": "portal_access_log",
         "children": ["portal_access_log_2026_05", "portal_access_log_2026_04"]},
        {"parent_table": "appliance_heartbeats",
         "children": ["appliance_heartbeats_y202605", "appliance_heartbeats_y202604"]},
        {"parent_table": "promoted_rule_events",
         "children": ["promoted_rule_events_202605", "promoted_rule_events_202604"]},
        {"parent_table": "canonical_metric_samples",
         "children": ["canonical_metric_samples_2026_05",
                      "canonical_metric_samples_2026_04"]},
    ]
    conn = _FakeConn(rows)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = _FROZEN_NOW
        # Also need timezone import to work
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        from datetime import timezone as tz
        violations = _run(assertions._check_partition_maintainer_dry(conn))
    assert violations == [], (
        f"All-fresh partitions must not fire; got {len(violations)}"
    )


def test_partition_maintainer_dry_fires_when_compliance_bundles_missing():
    """compliance_bundles is missing next-month partition. Fires sev1
    with parent_table + next_year + next_month populated. THIS is
    the catastrophic case (HIPAA evidence chain)."""
    rows = [
        # compliance_bundles: NO 2026_05 — fires
        {"parent_table": "compliance_bundles",
         "children": ["compliance_bundles_2026_04", "compliance_bundles_2026_03"]},
        {"parent_table": "portal_access_log",
         "children": ["portal_access_log_2026_05", "portal_access_log_2026_04"]},
        {"parent_table": "appliance_heartbeats",
         "children": ["appliance_heartbeats_y202605", "appliance_heartbeats_y202604"]},
        {"parent_table": "promoted_rule_events",
         "children": ["promoted_rule_events_202605", "promoted_rule_events_202604"]},
        {"parent_table": "canonical_metric_samples",
         "children": ["canonical_metric_samples_2026_05",
                      "canonical_metric_samples_2026_04"]},
    ]
    conn = _FakeConn(rows)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = _FROZEN_NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        violations = _run(assertions._check_partition_maintainer_dry(conn))
    assert len(violations) == 1, (
        f"compliance_bundles missing must fire 1 violation, got {len(violations)}"
    )
    v = violations[0]
    assert v.site_id is None  # global
    assert v.details["parent_table"] == "compliance_bundles"
    assert v.details["next_year"] == 2026
    assert v.details["next_month"] == 5
    assert v.details["latest_existing"] == "compliance_bundles_2026_04"


def test_partition_maintainer_dry_year_boundary_dec_to_jan():
    """December → January year wrap. Test the year-rollover branch."""
    rows = [
        {"parent_table": "compliance_bundles",
         "children": ["compliance_bundles_2026_12"]},  # missing 2027_01
        {"parent_table": "portal_access_log",
         "children": ["portal_access_log_2027_01"]},  # has next-month
        {"parent_table": "appliance_heartbeats",
         "children": ["appliance_heartbeats_y202701"]},
        {"parent_table": "promoted_rule_events",
         "children": ["promoted_rule_events_202701"]},
        {"parent_table": "canonical_metric_samples",
         "children": ["canonical_metric_samples_2027_01"]},  # has next-month
    ]
    conn = _FakeConn(rows)
    dec_now = datetime(2026, 12, 15, 12, 0, 0, tzinfo=timezone.utc)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = dec_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        violations = _run(assertions._check_partition_maintainer_dry(conn))
    assert len(violations) == 1, (
        f"Year-wrap missing partition must fire 1, got {len(violations)}"
    )
    v = violations[0]
    assert v.details["parent_table"] == "compliance_bundles"
    assert v.details["next_year"] == 2027
    assert v.details["next_month"] == 1


def test_partition_maintainer_dry_fires_per_missing_parent():
    """All 5 tables missing next-month. Expect 5 violations."""
    rows = [
        {"parent_table": "compliance_bundles",
         "children": ["compliance_bundles_2026_04"]},
        {"parent_table": "portal_access_log",
         "children": ["portal_access_log_2026_04"]},
        {"parent_table": "appliance_heartbeats",
         "children": ["appliance_heartbeats_y202604"]},
        {"parent_table": "promoted_rule_events",
         "children": ["promoted_rule_events_202604"]},
        {"parent_table": "canonical_metric_samples",
         "children": ["canonical_metric_samples_2026_04"]},
    ]
    conn = _FakeConn(rows)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = _FROZEN_NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        violations = _run(assertions._check_partition_maintainer_dry(conn))
    assert len(violations) == 5, (
        f"All 5 missing must fire 5, got {len(violations)}"
    )
    parents = {v.details["parent_table"] for v in violations}
    assert parents == {
        "compliance_bundles", "portal_access_log",
        "appliance_heartbeats", "promoted_rule_events",
        "canonical_metric_samples",
    }
