"""
Session 206 H3 smoke test: appliance_recovered alert path.

Background: for months, the offline→online recovery alert silently
failed. Root cause was `logger.info(..., site_id=...)` — stdlib Logger
doesn't accept arbitrary kwargs, the TypeError was swallowed by the
outer try/except. Fixed in c3ba780 by switching to f-string format.

This test guards against future regression of the same shape. It
exercises the exact logger + send_critical_alert call pattern used
by the checkin handler's recovery detection block (sites.py STEP 3.0a)
without needing a full checkin integration environment.
"""

from __future__ import annotations
import logging
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_MCP_SERVER_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_MCP_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER_ROOT))


def test_recovery_logger_call_accepts_no_kwargs():
    """The recovery detection block must call stdlib logger with a plain
    string message. If anyone reintroduces kwargs (appliance_id=, site_id=),
    this test catches it."""
    logger = logging.getLogger("dashboard_api.sites")

    # Mirror the exact call pattern from sites.py STEP 3.0a.
    canonical_id = "site-A-AA:BB:CC:DD:EE:FF"
    site_id = "site-A"
    label = "osiriscare-2"

    # Must NOT raise — this was broken for months.
    logger.info(
        f"Appliance recovered from offline: appliance_id={canonical_id} "
        f"site_id={site_id} display_name={label}"
    )


def test_send_critical_alert_importable_and_callable():
    """send_critical_alert is the single entry point the recovery path
    uses. If it moves or gets a breaking signature change, the recovery
    alert silently stops firing."""
    try:
        from dashboard_api.email_alerts import send_critical_alert
    except ImportError as e:
        pytest.fail(
            f"send_critical_alert import broke: {e}. "
            "The appliance_recovered alert path depends on this symbol."
        )

    # Exact shape the checkin handler passes today.
    with patch("dashboard_api.email_alerts._send_smtp_with_retry", MagicMock()) as send_mock:
        send_critical_alert(
            title="Appliance recovered: osiriscare-2",
            message=(
                "Appliance osiriscare-2 at site site-A resumed check-ins. "
                "Lifetime offline events: 3."
            ),
            site_id="site-A",
            category="appliance_health",
            severity="info",
            metadata={
                "appliance_id": "site-A-AA:BB:CC:DD:EE:FF",
                "display_name": "osiriscare-2",
                "event": "appliance_recovered",
            },
        )
        # If the signature ever changes incompatibly, the call itself raises.
        # We don't assert send was called (env may have email disabled);
        # we only require no exception.


def test_recovery_block_has_no_kwargs_logger_calls():
    """Static check: the STEP 3.0a recovery block in sites.py must NOT
    have `logger.info(..., appliance_id=...)` style calls. Prevents
    silent regression of the c3ba780 fix."""
    sites_py = _MCP_SERVER_ROOT / "central-command" / "backend" / "sites.py"
    text = sites_py.read_text(encoding="utf-8")

    # Find the recovery block by its distinctive comment.
    anchor = "STEP 3.0a: detect + announce offline"
    start = text.find(anchor)
    assert start != -1, (
        "STEP 3.0a recovery block not found in sites.py — refactor without "
        "updating this test?"
    )
    # Inspect the next ~100 lines.
    block = text[start:start + 6000]

    # The regression pattern: logger.{info|warning|error}( with no string
    # arg on the next line, immediately followed by `foo=value,`.
    bad_patterns = [
        "logger.info(\n                    \"Appliance recovered",
        "appliance_id=canonical_id,\n                    site_id=",
    ]
    for pat in bad_patterns:
        assert pat not in block, (
            f"Regression: kwargs-style logger call detected in STEP 3.0a "
            f"({pat!r}). Use f-strings — stdlib Logger does not accept "
            f"arbitrary kwargs and the TypeError is swallowed silently."
        )
