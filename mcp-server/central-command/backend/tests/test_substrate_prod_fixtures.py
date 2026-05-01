"""Substrate prod-snapshot fixture replay tests.

Session 210-B 2026-04-25 hardening #5. Substrate invariants today are
unit-tested with hand-crafted dicts (e.g. test_l2_decisions_stalled_invariant.py).
That worked but missed real prod shapes — round 1 of the
l2_decisions_stalled fix shipped with `pipeline_signal=remediation_steps_48h>=5`,
which fired a false positive for 12+ days against NVB2's L1-saturated
fleet (85 successful L1 steps + 0 L2 decisions). Round 2 fixed it
after we observed prod state.

This pattern catches that class of regression: fixtures captured from
real prod shapes (anonymized as needed) are committed alongside the
invariant. New code MUST replay them and produce the expected
violation count. Adding a new prod scenario = creating a JSON file
under tests/fixtures/substrate/<invariant>/.

Fixture format (JSON):

    {
      "_meta": { ... },
      "fetchrow_response": { ... key-value DB row ... },
      "env": { "L2_ENABLED": "true" },
      "expected_violation_count": 0,
      "expected_details": { ... fields the violation MUST carry ... }
    }

Fixtures NEVER contain identifying customer data — anonymize site_ids,
hostnames, MACs, IPs before committing. The fetchrow_response is what
the assertion's SQL would have returned for the captured time window.

Add new fixtures liberally. They're cheap; false-positive regressions
are expensive.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Whitelist of invariants that have replayable fixtures. Add a new
# entry whenever you add a new tests/fixtures/substrate/<NAME>/ directory.
INVARIANTS_WITH_FIXTURES = {
    "l2_decisions_stalled": "_check_l2_decisions_stalled",
    "sigauth_enforce_mode_rejections": "_check_sigauth_enforce_mode_rejections",
    # Session 214 fleet-edge liveness slice (round-table 2026-04-30)
    "go_agent_heartbeat_stale": "_check_go_agent_heartbeat_stale",
    "appliance_offline_extended": "_check_appliance_offline_extended",
    # D7 followup 2026-05-01 — 4 new Block 2+4 invariants. Of those,
    # only `compliance_packets_stalled` is a direct fetch-based DB
    # invariant that fits this parametrized fixture pattern.
    # `partition_maintainer_dry` depends on `datetime.now()` for the
    # next-month suffix — covered by `test_partition_maintainer_dry.py`
    # with monkeypatched datetime.
    # `substrate_assertions_meta_silent` + `bg_loop_silent` read
    # bg_heartbeat in-process state, not DB — covered by
    # `test_bg_heartbeat_invariants.py` with monkeypatched
    # bg_heartbeat module.
    "compliance_packets_stalled": "_check_compliance_packets_stalled",
}

FIXTURE_ROOT = pathlib.Path(__file__).parent / "fixtures" / "substrate"


class _FakeConn:
    """asyncpg-shaped fake — single-row fetchrow, no SQL parsing.

    Most substrate invariants use a single fetchrow that returns the
    aggregated state. The fixture's `fetchrow_response` is what that
    call would have returned. If a future invariant needs richer
    fakes (multiple fetchrow calls, or fetch returning rows), extend
    this class and bump the fixture format with a discriminator.
    """

    def __init__(self, row):
        self._row = row

    async def fetchrow(self, _sql, *_args):
        return self._row

    async def fetch(self, _sql, *_args):
        # If a fixture uses fetch instead of fetchrow, the row should
        # be wrapped in a list. Check shape.
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row else []


def _discover_fixtures() -> list[tuple[str, str, pathlib.Path]]:
    """Walk the fixture tree; yield (invariant_name, scenario_name, json_path)."""
    out = []
    for invariant_name in INVARIANTS_WITH_FIXTURES:
        invariant_dir = FIXTURE_ROOT / invariant_name
        if not invariant_dir.exists():
            continue
        for f in sorted(invariant_dir.glob("*.json")):
            out.append((invariant_name, f.stem, f))
    return out


def _load_fn(invariant_name: str):
    """Resolve invariant name → assertion function."""
    fn_name = INVARIANTS_WITH_FIXTURES[invariant_name]
    import assertions
    return getattr(assertions, fn_name)


@pytest.mark.parametrize(
    "invariant_name,scenario_name,fixture_path",
    _discover_fixtures(),
    ids=lambda x: x.stem if hasattr(x, "stem") else str(x),
)
def test_substrate_invariant_against_fixture(
    invariant_name: str, scenario_name: str, fixture_path: pathlib.Path
):
    """Each fixture under tests/fixtures/substrate/<INV>/ must match
    the invariant's actual output. Catching false positives at PR
    time before they reach prod."""
    fixture = json.loads(fixture_path.read_text())
    row = fixture["fetchrow_response"]

    # asyncpg fetchrow returns a Record-like object — but our assertion
    # code only does dict-style lookups (row["foo"]) which works on
    # plain dicts too. If a fixture uses datetime fields, decode here.
    # Datetime keys we know about; extend as new fixtures land.
    _DATETIME_KEYS = (
        "latest_decision_at",
        "last_failure",
        "last_heartbeat",
        "last_checkin",
    )

    def _decode_dt(d):
        if not isinstance(d, dict):
            return d
        for k in _DATETIME_KEYS:
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v)
        return d

    if isinstance(row, dict):
        row = _decode_dt(row)
    elif isinstance(row, list):
        row = [_decode_dt(r) for r in row]

    fn = _load_fn(invariant_name)
    conn = _FakeConn(row)

    env_overrides = fixture.get("env", {})
    with patch.dict(os.environ, env_overrides, clear=False):
        violations = asyncio.new_event_loop().run_until_complete(fn(conn))

    expected_count = fixture["expected_violation_count"]
    assert len(violations) == expected_count, (
        f"Fixture {fixture_path.name}: expected {expected_count} violation(s), "
        f"got {len(violations)}. Meta: {fixture.get('_meta', {})}"
    )

    expected_details = fixture.get("expected_details")
    if expected_details and violations:
        v = violations[0]
        if "site_id" in expected_details:
            assert v.site_id == expected_details["site_id"], (
                f"Fixture {fixture_path.name}: site_id mismatch — "
                f"expected {expected_details['site_id']!r}, got {v.site_id!r}"
            )
        for key, expected_val in expected_details.items():
            if key == "site_id":
                continue
            actual = v.details.get(key)
            assert actual == expected_val, (
                f"Fixture {fixture_path.name}: details[{key!r}] mismatch — "
                f"expected {expected_val!r}, got {actual!r}"
            )


def test_fixture_directory_structure_is_valid():
    """Smoke: every fixture file is valid JSON with the required keys.
    Catches a malformed fixture before parametrize fails cryptically."""
    for invariant_name, scenario_name, path in _discover_fixtures():
        try:
            f = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise AssertionError(f"Fixture {path}: invalid JSON — {e}")
        for required in ("fetchrow_response", "expected_violation_count"):
            assert required in f, f"Fixture {path} missing required key: {required}"


# Round-table 2026-04-30 compensating control #3 — fleet-edge liveness
# invariants (Session 214) MUST auto-resolve when the underlying signal
# recovers. Both predicates use conn.fetch(...) with WHERE clauses that
# narrow to bad rows; once those rows recover, the fetch result is
# empty and the predicate must return zero violations. The substrate
# health loop relies on this predicate flip to clear open violation
# rows. A check that always returned a synthetic violation would silently
# pin rows open forever ("fires-but-never-clears" regression class).
#
# Scoped to fetch-based predicates added in Session 214; legacy
# fetchrow-based predicates (l2_decisions_stalled) have their own clean
# fixtures already covering the recovery path.
@pytest.mark.parametrize(
    "invariant_name",
    [
        "go_agent_heartbeat_stale",
        "appliance_offline_extended",
    ],
)
def test_fetch_based_invariant_auto_resolves_when_signal_recovers(
    invariant_name: str,
):
    fn = _load_fn(invariant_name)
    conn = _FakeConn([])
    violations = asyncio.new_event_loop().run_until_complete(fn(conn))
    assert violations == [], (
        f"Invariant {invariant_name} returned {len(violations)} "
        f"violation(s) on an empty fetch result. The substrate health "
        f"loop relies on the predicate flipping to empty when the "
        f"underlying signal recovers — otherwise open violation rows "
        f"never auto-resolve."
    )
