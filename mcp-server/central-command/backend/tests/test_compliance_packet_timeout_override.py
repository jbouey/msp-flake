"""CI gate: compliance_packet generate_packet sets statement_timeout.

Followup #42 closure 2026-05-02. The mcp_app role has 30s
statement_timeout. Packet generation runs ~10 sequential analytical
queries that exceed the budget for sites with thousands of bundles —
caught the morning of 2026-05-02 as compliance_packets_stalled sev1
on physical-appliance-pilot-1aea78 (962 April bundles).

The fix: SET LOCAL statement_timeout = '120s' at the start of
generate_packet (transaction-scoped, doesn't leak through PgBouncer).

Without the override, the auto-gen loop will fail again next time a
site grows past the budget. CI gate prevents accidental removal.
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_PACKET_PY = _BACKEND / "compliance_packet.py"


def _read_packet_py() -> str:
    return _PACKET_PY.read_text()


def test_generate_packet_sets_statement_timeout():
    src = _read_packet_py()
    assert "SET LOCAL statement_timeout" in src, (
        "compliance_packet.py::generate_packet does not SET LOCAL "
        "statement_timeout. mcp_app role default is 30s; large-site "
        "packet generation will time out and trigger "
        "compliance_packets_stalled sev1. Followup #42."
    )


def test_timeout_override_uses_set_local_not_set():
    """SET LOCAL is transaction-scoped (safe with PgBouncer pool).
    Plain SET is session-scoped + leaks through PgBouncer's transaction
    pool to other connections."""
    src = _read_packet_py()
    # Find the override line; verify it's SET LOCAL not bare SET
    import re
    matches = re.findall(r"SET\s+(LOCAL\s+)?statement_timeout", src, re.IGNORECASE)
    assert matches, "Override missing"
    has_local = all("LOCAL" in m.upper() for m in matches)
    assert has_local, (
        "compliance_packet.py uses plain SET statement_timeout (not SET "
        "LOCAL). Plain SET is session-scoped and leaks through "
        "PgBouncer's transaction pool to other queries on the same "
        "backend connection. Use SET LOCAL — transaction-scoped, safe."
    )


def test_timeout_override_value_documented():
    """Verify the value (120s) is documented + reasonable."""
    src = _read_packet_py()
    import re
    m = re.search(
        r"SET\s+LOCAL\s+statement_timeout\s*=\s*'(\d+)\s*s'",
        src,
        re.IGNORECASE,
    )
    assert m, "Could not find statement_timeout value"
    seconds = int(m.group(1))
    assert 60 <= seconds <= 600, (
        f"statement_timeout override value {seconds}s is suspicious. "
        f"Below 60s likely doesn't help (default is 30s); above 600s "
        f"masks query-optimization needs. Document in the PR if you "
        f"deliberately want a value outside this range."
    )


def test_timeout_override_failure_logged():
    """If SET LOCAL fails (autocommit mode, missing perm), the failure
    must log so substrate notices a silent no-op."""
    src = _read_packet_py()
    assert "compliance_packet_statement_timeout_set_failed" in src, (
        "SET LOCAL failure path is silent. If a future change puts the "
        "session into autocommit mode, the override would silently "
        "no-op and the 30s timeout returns. Log so substrate catches it."
    )
