"""Tests for the incident dedup race fix.

Session 201 round-table finding: check-then-act in `report_incident` was
vulnerable to a race where two appliances reporting the same incident
simultaneously could both pass the existence SELECT and both try to
INSERT, causing a unique constraint violation (500) on the second one.

Migration 142 added the partial unique index on
`incidents.dedup_key WHERE status NOT IN ('resolved','closed')`.
This test verifies that the INSERT statement in `agent_api.report_incident`
actually USES `ON CONFLICT (dedup_key) DO NOTHING` so the index closes the
race instead of just surfacing it as a 500.

Source-level inspection — consistent with the project idiom for endpoints
that are awkward to fixture (see test_site_activity_audit.py).
"""

import ast
import os


AGENT_API = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "agent_api.py",
)


def _load() -> str:
    with open(AGENT_API) as f:
        return f.read()


class TestIncidentDedupRace:
    def test_insert_uses_on_conflict_dedup_key(self):
        """The INSERT in report_incident must use ON CONFLICT (dedup_key)
        — not just rely on the SELECT-then-INSERT path which is racy."""
        src = _load()
        assert "ON CONFLICT (dedup_key)" in src, (
            "report_incident INSERT missing ON CONFLICT (dedup_key) — the "
            "partial unique index from migration 142 is load-bearing and "
            "must be matched by this clause"
        )

    def test_on_conflict_filters_resolved_and_closed(self):
        """The ON CONFLICT index predicate must match the migration 142
        partial unique index (WHERE status NOT IN ('resolved','closed'))."""
        src = _load()
        # Must reference both terminal states so the ON CONFLICT matches
        # the partial unique index's WHERE clause exactly
        assert "'resolved'" in src and "'closed'" in src
        assert "NOT IN ('resolved', 'closed')" in src or "NOT IN ('resolved','closed')" in src

    def test_on_conflict_do_nothing_returning(self):
        """Must use DO NOTHING RETURNING id so the caller can detect the
        race (returned row is None) and fall back to the existing row."""
        src = _load()
        # Find the INSERT INTO incidents block
        idx = src.find("INSERT INTO incidents")
        assert idx != -1
        block = src[idx : idx + 2000]
        assert "DO NOTHING" in block
        assert "RETURNING id" in block

    def test_race_fallback_selects_existing(self):
        """When ON CONFLICT fires, the race handler must SELECT the
        existing concurrent row and return 'deduplicated' instead of
        failing with a 500 or returning the never-inserted incident_id."""
        src = _load()
        # The fallback branch looks up the winning row
        assert "inserted_row" in src
        assert "concurrent_row" in src
        assert '"status": "deduplicated"' in src

    def test_has_comment_explaining_race_fix(self):
        """Non-obvious race fix must carry a comment so future readers
        (and the round-table) can see the intent."""
        src = _load()
        assert "Migration 142" in src or "migration 142" in src
        # Should mention "race" so a grep for 'race' turns it up
        idx = src.find("ON CONFLICT (dedup_key)")
        # Look ~400 chars before the statement for the explanation
        preamble = src[max(0, idx - 600) : idx]
        assert "race" in preamble.lower()
