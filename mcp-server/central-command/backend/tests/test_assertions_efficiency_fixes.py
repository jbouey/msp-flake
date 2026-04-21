"""Unit tests for the substrate-efficiency fixes shipped after the
2026-04-21 round-table:

  P1 (engine hysteresis) — resolve only fires after RESOLVE_HYSTERESIS_MINUTES
     of no refresh; prevents open/resolve thrash (observed 60×/day on
     journal_upload_never_received, 25× on discovered_devices_freshness,
     23× on agent_version_lag).

  P2 (agent_version_lag correctness) — running AHEAD of expected is NOT
     a lag; _version_tuple parses semver-ish strings; the check only
     flags running < expected. The `running=0.4.4, expected=0.3.91`
     prod SEV1 false-positive must stop firing.

Both fixes are unit-testable without a real Postgres — the engine-side
resolve path is SQL-only (tested via DB fixtures elsewhere); here we
pin _version_tuple semantics and the filter decision.
"""
from __future__ import annotations

import pathlib
import sys

# Backend dir on sys.path so `assertions` imports cleanly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assertions import RESOLVE_HYSTERESIS_MINUTES, _version_tuple  # noqa: E402


# ---------------------------------------------------------------------------
# P2 — _version_tuple semantics
# ---------------------------------------------------------------------------


class TestVersionTuple:
    def test_parses_plain_semver(self):
        assert _version_tuple("0.4.5") == (0, 4, 5)

    def test_strips_v_prefix(self):
        assert _version_tuple("v0.3.91") == (0, 3, 91)
        assert _version_tuple("V1.2.3") == (1, 2, 3)

    def test_strips_rc_suffix(self):
        assert _version_tuple("0.4.5-rc1") == (0, 4, 5)
        assert _version_tuple("1.0.0-alpha") == (1, 0, 0)

    def test_strips_build_metadata(self):
        assert _version_tuple("0.4.5+abc123") == (0, 4, 5)

    def test_none_and_empty_sort_lowest(self):
        assert _version_tuple(None) == (0,)
        assert _version_tuple("") == (0,)

    def test_non_numeric_clamps_to_zero(self):
        # "0.4.x" → third segment unparseable → 0
        assert _version_tuple("0.4.x") == (0, 4, 0)

    def test_ordering_is_numeric_not_lexical(self):
        # Lex order would put "0.10.0" < "0.9.0" (because '1' < '9').
        # Our tuple comparator must order numerically.
        assert _version_tuple("0.10.0") > _version_tuple("0.9.0")
        assert _version_tuple("0.4.4") > _version_tuple("0.3.91")

    def test_prod_observed_pair(self):
        # The literal pair that produced the SEV1 false-positive in prod
        # on 2026-04-21: running=0.4.4, expected=0.3.91. Running must
        # sort STRICTLY GREATER than expected so the new check filter
        # (running >= expected → skip) suppresses it.
        running = _version_tuple("0.4.4")
        expected = _version_tuple("0.3.91")
        assert running > expected
        assert not (running < expected)


# ---------------------------------------------------------------------------
# P1 — hysteresis constant guardrails
# ---------------------------------------------------------------------------


class TestHysteresisConstant:
    def test_hysteresis_is_positive(self):
        # 0 would restore the pre-fix thrash behavior.
        assert RESOLVE_HYSTERESIS_MINUTES > 0

    def test_hysteresis_covers_checkin_jitter(self):
        # Appliances check in every ~10s; tick cadence is 60s. A single
        # missed match must not resolve. 2 minutes minimum covers 2 ticks
        # of jitter + a safety buffer. Fail loudly if someone drops this
        # to "1 minute" as a "cleanup".
        assert RESOLVE_HYSTERESIS_MINUTES >= 2

    def test_hysteresis_bounded_above(self):
        # 60+ minutes would mask real recoveries from the dashboard for
        # longer than an operator's attention span. Keep it glanceable.
        assert RESOLVE_HYSTERESIS_MINUTES <= 30
