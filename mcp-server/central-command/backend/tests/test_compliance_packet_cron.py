"""Tests for Session 203 C5 — compliance_packet cron fix.

The original loop gated on `now.day == 1 AND now.hour == 2 UTC`, a one-
hour window per month. Any container restart during that window skipped
the month with no catch-up, leaving the compliance_packets table empty
in production (the round-table audit found 0 rows).

The new loop runs every hour regardless of day and catches up any
missing month in the last N=3 completed months. HIPAA §164.316(b)(2)(i)
requires 6-year packet retention, so a gap is a real compliance defect.

Source-level checks — the loop lives in mcp-server/main.py which isn't
part of the dashboard_api package and can't be imported under pytest
without standing up the full FastAPI app.
"""

import os
import re


_HERE = os.path.dirname(os.path.abspath(__file__))
# tests → backend → central-command → mcp-server/main.py
MAIN_PY = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "main.py"))


def _load_main() -> str:
    with open(MAIN_PY) as f:
        return f.read()


def _get_loop_body() -> str:
    """Extract the body of _compliance_packet_loop from main.py."""
    src = _load_main()
    m = re.search(
        r'async def _compliance_packet_loop\(\).*?(?=\n    async def )',
        src,
        re.DOTALL,
    )
    assert m is not None, "_compliance_packet_loop not found"
    return m.group(0)


class TestComplianceCronResilience:
    def test_loop_exists(self):
        src = _load_main()
        assert "async def _compliance_packet_loop(" in src

    def test_no_longer_gates_on_day_and_hour(self):
        """The old gate was:
            if now.day == 1 and now.hour == 2:
        which caused the 0-row state in prod. The new loop must not
        have this restrictive conditional — docstrings that reference
        the history are fine but an actual `if now.day == 1:` line
        is not."""
        body = _get_loop_body()
        # The executable gate must be gone — look for the conditional form
        assert "if now.day == 1" not in body
        assert "if now.day == 1 and now.hour == 2:" not in body

    def test_loop_has_catch_up_window(self):
        """The new loop must walk backwards through recently ended
        months to catch up any missing packets."""
        body = _get_loop_body()
        assert "CATCH_UP_MONTHS" in body
        assert "_ended_months" in body

    def test_only_generates_one_missing_month_per_site_per_pass(self):
        """Explicit `break` after successful generation prevents a
        startup-after-outage burst that would overload the generator."""
        body = _get_loop_body()
        # Must have a break inside the month-walking loop
        assert "break" in body

    def test_loop_idempotent_via_on_conflict(self):
        """The INSERT should use ON CONFLICT so re-running the loop
        after a partial crash is safe."""
        body = _get_loop_body()
        assert "ON CONFLICT (site_id, month, year, framework)" in body
        assert "DO UPDATE" in body

    def test_loop_checks_for_existing_packet_before_generating(self):
        body = _get_loop_body()
        assert "SELECT 1 FROM compliance_packets" in body

    def test_loop_skips_decommissioned_sites(self):
        body = _get_loop_body()
        assert "status != 'decommissioned'" in body

    def test_loop_logs_pass_summary(self):
        """Each loop iteration should log how many packets it generated,
        skipped, and errored so ops can see it's working."""
        body = _get_loop_body()
        assert "generated" in body
        assert "skipped" in body
        assert "errors" in body
        assert "Compliance packet loop pass complete" in body

    def test_loop_references_session_203_c5(self):
        """The fix must carry a comment explaining the historical bug."""
        body = _get_loop_body()
        assert "C5" in body or "Session 203" in body
        assert "HIPAA" in body

    def test_loop_handles_markdown_read_failure_gracefully(self):
        """If the generated markdown can't be read off disk, still
        persist the rest of the packet metadata — don't lose the row."""
        body = _get_loop_body()
        assert "Could not read generated markdown" in body or "markdown = None" in body

    def test_loop_hourly_sleep(self):
        body = _get_loop_body()
        assert "asyncio.sleep(3600)" in body
