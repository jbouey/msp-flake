"""
Tests for the L3/L4 Escalation Engine (central-command backend).

Tests notification senders (Slack, PagerDuty, Email, Teams, Webhook),
the EscalationEngine class (ticket creation, routing, recurrence detection,
SLA breach checking), and helper methods (priority, HIPAA controls, etc.).
"""

import json
import sys
import hmac
import hashlib
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from contextlib import asynccontextmanager

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: escalation_engine.py uses relative imports (from .fleet, from
# .tenant_middleware) that require a parent package.  We stub out the parent
# package and the two dependency modules so the module can be imported as a
# standalone file by the test runner.
# ---------------------------------------------------------------------------
_BACKEND_DIR = "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend"
sys.path.insert(0, _BACKEND_DIR)

# Create a fake parent package so relative imports resolve
_pkg_name = "dashboard_api"
if _pkg_name not in sys.modules:
    _pkg = types.ModuleType(_pkg_name)
    _pkg.__path__ = [_BACKEND_DIR]
    _pkg.__package__ = _pkg_name
    sys.modules[_pkg_name] = _pkg

# Stub the two relative-import dependencies
for _sub in ("fleet", "tenant_middleware"):
    _fqn = f"{_pkg_name}.{_sub}"
    if _fqn not in sys.modules:
        _mod = types.ModuleType(_fqn)
        _mod.__package__ = _pkg_name
        if _sub == "fleet":
            _mod.get_pool = AsyncMock()
        elif _sub == "tenant_middleware":
            @asynccontextmanager
            async def _stub_admin(pool):
                yield MagicMock()
            _mod.admin_connection = _stub_admin
        sys.modules[_fqn] = _mod

# Now import escalation_engine — its relative imports will resolve to our stubs
import importlib
_ee_fqn = f"{_pkg_name}.escalation_engine"
if _ee_fqn in sys.modules:
    del sys.modules[_ee_fqn]
_spec = importlib.util.spec_from_file_location(
    _ee_fqn,
    f"{_BACKEND_DIR}/escalation_engine.py",
    submodule_search_locations=[],
)
escalation_engine = importlib.util.module_from_spec(_spec)
escalation_engine.__package__ = _pkg_name
sys.modules[_ee_fqn] = escalation_engine
sys.modules["escalation_engine"] = escalation_engine
_spec.loader.exec_module(escalation_engine)

# Module-level aliases for the classes/functions under test
send_slack_notification = escalation_engine.send_slack_notification
send_pagerduty_notification = escalation_engine.send_pagerduty_notification
send_email_notification = escalation_engine.send_email_notification
send_teams_notification = escalation_engine.send_teams_notification
send_webhook_notification = escalation_engine.send_webhook_notification
EscalationEngine = escalation_engine.EscalationEngine
get_escalation_engine = escalation_engine.get_escalation_engine

# The fully-qualified module name for patch() targets
_MOD = "dashboard_api.escalation_engine"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Simulate an aiohttp response."""

    def __init__(self, status=200, text_body="ok", json_body=None):
        self.status = status
        self._text = text_body
        self._json = json_body or {}

    async def text(self):
        return self._text

    async def json(self):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    """Simulate aiohttp.ClientSession."""

    def __init__(self, response: FakeResponse):
        self._resp = response

    def post(self, url, **kwargs):
        self._last_url = url
        self._last_kwargs = kwargs
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeConn:
    """Fake asyncpg connection with tracking."""

    def __init__(
        self,
        fetchrow_results=None,
        fetch_results=None,
    ):
        # fetchrow_results: list of dicts returned sequentially per call
        self._fetchrow_results = list(fetchrow_results or [])
        self._fetchrow_idx = 0
        self._fetch_results = fetch_results or []
        self.executed = []

    async def fetchrow(self, query, *args):
        if self._fetchrow_idx < len(self._fetchrow_results):
            result = self._fetchrow_results[self._fetchrow_idx]
            self._fetchrow_idx += 1
            return result
        return None

    async def fetch(self, query, *args):
        return self._fetch_results

    async def execute(self, query, *args):
        self.executed.append((query, args))

    def transaction(self):
        """Support async with conn.transaction()."""
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_payload(**overrides):
    """Build a standard notification payload."""
    base = {
        "ticket_id": "ESC-20260323-abc12345",
        "title": "Test Alert",
        "summary": "Something went wrong",
        "severity": "high",
        "site_id": "site-001",
        "site_name": "Test Clinic",
        "incident_type": "patching",
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "attempted_actions": ["L1 restart failed"],
        "recommended_action": "Apply patches manually",
    }
    base.update(overrides)
    return base


def _make_incident(**overrides):
    """Build a standard incident dict."""
    base = {
        "id": "inc-001",
        "type": "patching",
        "severity": "high",
        "host": "workstation-1",
        "description": "Patches are 30 days overdue",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. send_slack_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slack_notification_success():
    """Successful Slack webhook post returns status=sent."""


    settings = {"slack_webhook_url": "https://hooks.slack.com/test"}
    payload = _make_payload()

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(FakeResponse(status=200))
        result = await send_slack_notification(settings, payload)

    assert result["status"] == "sent"
    assert result["channel"] == "slack"


@pytest.mark.asyncio
async def test_slack_notification_missing_url():
    """Missing webhook URL returns status=skipped."""


    result = await send_slack_notification({}, _make_payload())
    assert result["status"] == "skipped"
    assert "No Slack webhook" in result["reason"]


@pytest.mark.asyncio
async def test_slack_notification_webhook_failure():
    """Non-200 response from Slack returns status=failed with error."""


    settings = {"slack_webhook_url": "https://hooks.slack.com/test"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(
            FakeResponse(status=500, text_body="internal error")
        )
        result = await send_slack_notification(settings, _make_payload())

    assert result["status"] == "failed"
    assert result["code"] == 500
    assert "internal error" in result["error"]


@pytest.mark.asyncio
async def test_slack_notification_exception():
    """Network exception returns status=error."""


    settings = {"slack_webhook_url": "https://hooks.slack.com/test"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.side_effect = Exception("connection refused")
        result = await send_slack_notification(settings, _make_payload())

    assert result["status"] == "error"
    assert "connection refused" in result["error"]


@pytest.mark.asyncio
async def test_slack_notification_includes_hipaa_controls():
    """When payload has hipaa_controls, they appear in the Slack blocks."""


    settings = {"slack_webhook_url": "https://hooks.slack.com/test"}
    payload = _make_payload(hipaa_controls=["164.312(b)"])

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        fake_session = FakeSession(FakeResponse(status=200))
        mock_cs.return_value = fake_session
        await send_slack_notification(settings, payload)

    # The post was called with json containing blocks with HIPAA controls
    posted_json = fake_session._last_kwargs.get("json", {})
    blocks_text = json.dumps(posted_json.get("blocks", []))
    assert "164.312(b)" in blocks_text


# ---------------------------------------------------------------------------
# 2. send_pagerduty_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagerduty_notification_success():
    """Successful PagerDuty trigger returns status=sent with dedup_key."""


    settings = {"pagerduty_routing_key": "pk-test-key"}
    payload = _make_payload(ticket_id="ESC-123")

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(
            FakeResponse(status=202, json_body={"message": "Event processed"})
        )
        result = await send_pagerduty_notification(settings, payload)

    assert result["status"] == "sent"
    assert result["channel"] == "pagerduty"
    assert result["dedup_key"] == "ESC-123"
    assert result["message"] == "Event processed"


@pytest.mark.asyncio
async def test_pagerduty_no_routing_key():
    """Missing routing key returns skipped."""


    result = await send_pagerduty_notification({}, _make_payload())
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_pagerduty_api_error():
    """Non-200/202 response returns failed."""


    settings = {"pagerduty_routing_key": "pk-test-key"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(
            FakeResponse(status=400, text_body="bad routing key")
        )
        result = await send_pagerduty_notification(settings, _make_payload())

    assert result["status"] == "failed"
    assert result["code"] == 400


@pytest.mark.asyncio
async def test_pagerduty_is_test_flag():
    """is_test=True prepends 'test-' to dedup_key."""


    settings = {"pagerduty_routing_key": "pk-test-key"}
    payload = _make_payload(ticket_id="ESC-456")

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(
            FakeResponse(status=202, json_body={"message": "ok"})
        )
        result = await send_pagerduty_notification(settings, payload, is_test=True)

    assert result["dedup_key"].startswith("test-")


# ---------------------------------------------------------------------------
# 3. send_email_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_notification_success():
    """Successful SMTP send returns status=sent with recipient count."""


    settings = {"email_recipients": ["admin@test.com", "ops@test.com"]}

    with patch(f"{_MOD}.SMTP_PASS", "secret123"), \
         patch(f"{_MOD}.asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
        result = await send_email_notification(settings, _make_payload())

    assert result["status"] == "sent"
    assert result["channel"] == "email"
    assert result["recipients"] == 2


@pytest.mark.asyncio
async def test_email_notification_no_recipients():
    """Empty recipients list returns skipped."""


    result = await send_email_notification({"email_recipients": []}, _make_payload())
    assert result["status"] == "skipped"
    assert "No email recipients" in result["reason"]


@pytest.mark.asyncio
async def test_email_notification_smtp_not_configured():
    """Missing SMTP password returns skipped."""


    settings = {"email_recipients": ["admin@test.com"]}

    with patch(f"{_MOD}.SMTP_PASS", ""):
        result = await send_email_notification(settings, _make_payload())

    assert result["status"] == "skipped"
    assert "SMTP not configured" in result["reason"]


@pytest.mark.asyncio
async def test_email_notification_smtp_failure():
    """SMTP exception returns status=error."""


    settings = {"email_recipients": ["admin@test.com"]}

    with patch(f"{_MOD}.SMTP_PASS", "secret123"), \
         patch(f"{_MOD}.asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(
            side_effect=Exception("SMTP timeout")
        )
        result = await send_email_notification(settings, _make_payload())

    assert result["status"] == "error"
    assert "SMTP timeout" in result["error"]


# ---------------------------------------------------------------------------
# 4. send_teams_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_teams_notification_success():
    """Successful Teams webhook post returns status=sent."""


    settings = {"teams_webhook_url": "https://outlook.office.com/webhook/test"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(FakeResponse(status=200))
        result = await send_teams_notification(settings, _make_payload())

    assert result["status"] == "sent"
    assert result["channel"] == "teams"


@pytest.mark.asyncio
async def test_teams_notification_missing_url():
    """Missing Teams webhook URL returns skipped."""


    result = await send_teams_notification({}, _make_payload())
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_teams_notification_failure():
    """Non-200 response from Teams returns failed."""


    settings = {"teams_webhook_url": "https://outlook.office.com/webhook/test"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(
            FakeResponse(status=403, text_body="forbidden")
        )
        result = await send_teams_notification(settings, _make_payload())

    assert result["status"] == "failed"
    assert result["code"] == 403


# ---------------------------------------------------------------------------
# 5. send_webhook_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_notification_success():
    """Successful generic webhook returns status=sent."""


    settings = {"webhook_url": "https://example.com/hook"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        mock_cs.return_value = FakeSession(FakeResponse(status=201))
        result = await send_webhook_notification(settings, _make_payload())

    assert result["status"] == "sent"
    assert result["channel"] == "webhook"
    assert result["code"] == 201


@pytest.mark.asyncio
async def test_webhook_notification_missing_url():
    """Missing webhook URL returns skipped."""


    result = await send_webhook_notification({}, _make_payload())
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_webhook_notification_with_hmac_signature():
    """Webhook with secret includes X-OsirisCare-Signature header."""


    settings = {
        "webhook_url": "https://example.com/hook",
        "webhook_secret": "my-secret-key",
    }
    payload = _make_payload()

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        fake_session = FakeSession(FakeResponse(status=200))
        mock_cs.return_value = fake_session
        result = await send_webhook_notification(settings, payload)

    assert result["status"] == "sent"
    # Verify the HMAC signature was computed and attached
    headers = fake_session._last_kwargs.get("headers", {})
    assert "X-OsirisCare-Signature" in headers
    sig = headers["X-OsirisCare-Signature"]
    assert sig.startswith("sha256=")

    # Verify the signature is correct
    body_json = fake_session._last_kwargs["data"]
    expected = hmac.new(
        b"my-secret-key", body_json.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert sig == f"sha256={expected}"


@pytest.mark.asyncio
async def test_webhook_notification_without_hmac():
    """Webhook without secret does not include signature header."""


    settings = {"webhook_url": "https://example.com/hook"}

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        fake_session = FakeSession(FakeResponse(status=200))
        mock_cs.return_value = fake_session
        result = await send_webhook_notification(settings, _make_payload())

    headers = fake_session._last_kwargs.get("headers", {})
    assert "X-OsirisCare-Signature" not in headers


@pytest.mark.asyncio
async def test_webhook_notification_custom_headers_dict():
    """Custom headers (as dict) are merged into the request."""


    settings = {
        "webhook_url": "https://example.com/hook",
        "webhook_headers": {"X-Custom": "value1"},
    }

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        fake_session = FakeSession(FakeResponse(status=200))
        mock_cs.return_value = fake_session
        await send_webhook_notification(settings, _make_payload())

    headers = fake_session._last_kwargs.get("headers", {})
    assert headers.get("X-Custom") == "value1"


@pytest.mark.asyncio
async def test_webhook_notification_custom_headers_json_string():
    """Custom headers as JSON string are parsed and merged."""


    settings = {
        "webhook_url": "https://example.com/hook",
        "webhook_headers": '{"X-Api-Key": "abc123"}',
    }

    with patch(f"{_MOD}.aiohttp.ClientSession") as mock_cs:
        fake_session = FakeSession(FakeResponse(status=200))
        mock_cs.return_value = fake_session
        await send_webhook_notification(settings, _make_payload())

    headers = fake_session._last_kwargs.get("headers", {})
    assert headers.get("X-Api-Key") == "abc123"


# ---------------------------------------------------------------------------
# 6. EscalationEngine — helper methods
# ---------------------------------------------------------------------------

class TestEscalationEngineHelpers:
    """Test EscalationEngine's synchronous helper methods."""

    def setup_method(self):
    
        self.engine = EscalationEngine()

    def test_determine_priority_maps_severity(self):
        """Normal severity maps directly to priority."""
        assert self.engine._determine_priority("critical", {}) == "critical"
        assert self.engine._determine_priority("high", {}) == "high"
        assert self.engine._determine_priority("medium", {}) == "medium"
        assert self.engine._determine_priority("low", {}) == "low"

    def test_determine_priority_security_types_force_critical(self):
        """Security-sensitive incident types always return critical."""
        for itype in ["encryption_failure", "ransomware_detected",
                      "data_breach_alert", "unauthorized_access"]:
            result = self.engine._determine_priority("low", {"type": itype})
            assert result == "critical", f"{itype} should be critical, got {result}"

    def test_determine_priority_unknown_severity_defaults_medium(self):
        """Unknown severity string defaults to medium."""
        assert self.engine._determine_priority("banana", {}) == "medium"

    def test_generate_title_basic(self):
        """Title includes severity and incident type."""
        incident = {"severity": "high", "type": "patching", "host": "ws-01"}
        title = self.engine._generate_title(incident)
        assert "[HIGH]" in title
        assert "patching" in title
        assert "ws-01" in title

    def test_generate_title_no_host(self):
        """Title works without a host."""
        incident = {"severity": "low", "type": "backup"}
        title = self.engine._generate_title(incident)
        assert "backup" in title
        assert "[LOW]" in title

    def test_generate_title_truncated(self):
        """Title is truncated at 200 characters."""
        incident = {"severity": "high", "type": "x" * 300}
        title = self.engine._generate_title(incident)
        assert len(title) <= 200

    def test_generate_summary_with_description(self):
        """Summary uses incident description."""
        incident = {"description": "Patch drift detected"}
        summary = self.engine._generate_summary(incident)
        assert "Patch drift detected" in summary

    def test_generate_summary_with_message_fallback(self):
        """Summary falls back to message field."""
        incident = {"message": "Service stopped"}
        summary = self.engine._generate_summary(incident)
        assert "Service stopped" in summary

    def test_generate_summary_with_attempted_actions(self):
        """Summary includes attempted remediation actions."""
        incident = {"description": "Issue"}
        actions = ["L1 restart", "L2 plan generated"]
        summary = self.engine._generate_summary(incident, actions)
        assert "Attempted remediation" in summary
        assert "L1 restart" in summary

    def test_generate_summary_empty_incident(self):
        """Empty incident produces default summary."""
        summary = self.engine._generate_summary({})
        assert "requires human review" in summary

    def test_get_hipaa_controls_known_type(self):
        """Known incident types map to HIPAA control references."""
        assert self.engine._get_hipaa_controls({"type": "patching"}) == [
            "164.308(a)(5)(ii)(B)"
        ]
        assert len(self.engine._get_hipaa_controls({"type": "backup_failure"})) == 2
        assert len(self.engine._get_hipaa_controls({"type": "firewall"})) == 2

    def test_get_hipaa_controls_unknown_type(self):
        """Unknown incident types return empty list."""
        assert self.engine._get_hipaa_controls({"type": "cosmic_ray"}) == []

    def test_suggest_action_known_type(self):
        """Known types get specific suggestions."""
        action = self.engine._suggest_action({"type": "patching"})
        assert "patch" in action.lower()

    def test_suggest_action_unknown_type(self):
        """Unknown types get generic suggestion."""
        action = self.engine._suggest_action({"type": "cosmic_ray"})
        assert "Review incident" in action

    def test_get_recipient_for_channel(self):
        """Recipient strings are sensible for each channel."""
        settings = {
            "slack_channel": "#ops",
            "pagerduty_service_id": "PD123",
            "email_recipients": ["a@b.com", "c@d.com"],
            "webhook_url": "https://example.com/hook",
        }
        assert self.engine._get_recipient_for_channel(settings, "slack") == "#ops"
        assert self.engine._get_recipient_for_channel(settings, "pagerduty") == "PD123"
        assert "a@b.com" in self.engine._get_recipient_for_channel(settings, "email")
        assert self.engine._get_recipient_for_channel(settings, "teams") == "teams-channel"
        assert self.engine._get_recipient_for_channel(settings, "webhook").startswith("https://")
        assert self.engine._get_recipient_for_channel(settings, "fax") == "unknown"


# ---------------------------------------------------------------------------
# 7. Priority-based channel selection
# ---------------------------------------------------------------------------

class TestChannelSelection:
    """Test _get_channels_for_priority routing logic."""

    def setup_method(self):
    
        self.engine = EscalationEngine()
        self.all_enabled = {
            "pagerduty_enabled": True,
            "slack_enabled": True,
            "teams_enabled": True,
            "email_enabled": True,
            "webhook_enabled": True,
        }

    def test_critical_enables_all_channels(self):
        """Critical priority enables all configured channels."""
        channels = self.engine._get_channels_for_priority(self.all_enabled, "critical")
        assert "pagerduty" in channels
        assert "slack" in channels
        assert "teams" in channels
        assert "email" in channels
        assert "webhook" in channels

    def test_high_enables_pagerduty_and_slack(self):
        """High priority enables PagerDuty + Slack (or Teams)."""
        channels = self.engine._get_channels_for_priority(self.all_enabled, "high")
        assert "pagerduty" in channels
        assert "slack" in channels
        # webhook always added if enabled
        assert "webhook" in channels

    def test_high_teams_fallback_when_no_slack(self):
        """High priority uses Teams if Slack is not enabled."""
        settings = {
            "pagerduty_enabled": True,
            "slack_enabled": False,
            "teams_enabled": True,
            "webhook_enabled": False,
        }
        channels = self.engine._get_channels_for_priority(settings, "high")
        assert "teams" in channels
        assert "slack" not in channels

    def test_medium_enables_slack_and_email(self):
        """Medium priority enables Slack/Teams + email."""
        channels = self.engine._get_channels_for_priority(self.all_enabled, "medium")
        assert "slack" in channels
        assert "email" in channels
        assert "pagerduty" not in channels

    def test_low_enables_email_only(self):
        """Low priority enables only email (plus webhook if enabled)."""
        channels = self.engine._get_channels_for_priority(self.all_enabled, "low")
        assert "email" in channels
        assert "pagerduty" not in channels
        assert "slack" not in channels
        # webhook always appended
        assert "webhook" in channels

    def test_webhook_always_added_if_enabled(self):
        """Webhook is appended for any priority when enabled."""
        settings = {"email_enabled": True, "webhook_enabled": True}
        channels = self.engine._get_channels_for_priority(settings, "low")
        assert "webhook" in channels

    def test_no_channels_if_none_enabled(self):
        """No channels returned when nothing is enabled."""
        channels = self.engine._get_channels_for_priority({}, "critical")
        assert channels == []


# ---------------------------------------------------------------------------
# 8. EscalationEngine.create_escalation — main flow
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _fake_admin_connection(pool):
    """Replacement for admin_connection that yields a FakeConn from the pool."""
    yield pool._fake_conn


def _make_site_row(**overrides):
    """Build a site row result dict."""
    base = {
        "id": 1,
        "site_id": "site-001",
        "clinic_name": "Test Clinic",
        "partner_id": 42,
        "status": "active",
        "client_org_id": "org-001",
        "partner_name": "Test Partner",
    }
    base.update(overrides)
    return base


def _make_partner_settings(**overrides):
    """Build a partner_notification_settings row."""
    base = {
        "id": 1,
        "partner_id": 42,
        "email_enabled": True,
        "email_recipients": ["ops@partner.com"],
        "slack_enabled": False,
        "slack_webhook_url": None,
        "slack_channel": None,
        "pagerduty_enabled": False,
        "pagerduty_routing_key": None,
        "teams_enabled": False,
        "teams_webhook_url": None,
        "webhook_enabled": False,
        "webhook_url": None,
        "webhook_secret": None,
        "escalation_timeout_minutes": 30,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_escalation_full_flow():
    """Full escalation: creates ticket, sends notifications, returns result."""


    site_row = _make_site_row()
    client_prefs = None  # no client prefs => escalation_mode = 'partner'
    settings_row = _make_partner_settings()
    overrides_row = None
    sla_row = {"response_time_minutes": 30}
    prev_ticket = None  # no recurrence

    conn = FakeConn(
        fetchrow_results=[
            site_row,        # site lookup
            client_prefs,    # client_escalation_preferences
            settings_row,    # partner_notification_settings
            overrides_row,   # site_notification_overrides
            sla_row,         # sla_definitions
            prev_ticket,     # recurrence check
        ]
    )

    # Attach FakeConn to a mock pool
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool), \
         patch(f"{_MOD}.send_email_notification", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = {"status": "sent", "channel": "email"}

        result = await engine.create_escalation(
            site_id="site-001",
            incident=_make_incident(),
            attempted_actions=["L1 restart failed"],
            severity="high",
        )

    assert result["ticket_id"].startswith("ESC-")
    assert result["site_id"] == "site-001"
    assert result["partner_id"] == 42
    assert result["escalation_mode"] == "partner"
    assert "sla_target" in result
    # Verify ticket INSERT was executed
    insert_queries = [q for q, _ in conn.executed if "INSERT INTO escalation_tickets" in q]
    assert len(insert_queries) == 1


@pytest.mark.asyncio
async def test_create_escalation_site_not_found():
    """Raises ValueError when site is not found."""


    conn = FakeConn(fetchrow_results=[None])  # site lookup returns None
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool):
        with pytest.raises(ValueError, match="Site .* not found"):
            await engine.create_escalation(
                site_id="nonexistent",
                incident=_make_incident(),
            )


@pytest.mark.asyncio
async def test_create_escalation_no_partner_internal_fallback():
    """Site with no partner and partner mode falls back to internal escalation."""


    site_row = _make_site_row(partner_id=None, client_org_id=None)
    client_prefs = None  # no client prefs => mode='partner'

    conn = FakeConn(fetchrow_results=[site_row, client_prefs])
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool):
        result = await engine.create_escalation(
            site_id="site-orphan",
            incident=_make_incident(),
        )

    assert result["ticket_id"].startswith("INT-")
    assert result["partner_id"] is None
    assert "no partner" in result["note"].lower()


# ---------------------------------------------------------------------------
# 9. Recurrence detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recurrence_detection_increments_count():
    """When a resolved ticket exists for same type+site, recurrence_count is incremented."""


    site_row = _make_site_row()
    client_prefs = None
    settings_row = _make_partner_settings(
        email_enabled=False, slack_enabled=False, webhook_enabled=False
    )
    overrides_row = None
    sla_row = {"response_time_minutes": 60}
    prev_ticket = {"id": "ESC-prev", "recurrence_count": 2}  # 3rd recurrence

    conn = FakeConn(
        fetchrow_results=[
            site_row,
            client_prefs,
            settings_row,
            overrides_row,
            sla_row,
            prev_ticket,
        ]
    )
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool):
        result = await engine.create_escalation(
            site_id="site-001",
            incident=_make_incident(),
        )

    # Verify the INSERT included recurrence_count=3 and previous_ticket_id
    insert_calls = [
        (q, args) for q, args in conn.executed
        if "INSERT INTO escalation_tickets" in q
    ]
    assert len(insert_calls) == 1
    _, args = insert_calls[0]
    # recurrence_count is arg index 14 (0-based)
    assert args[14] == 3  # 2 + 1
    assert args[15] == "ESC-prev"  # previous_ticket_id


# ---------------------------------------------------------------------------
# 10. _send_and_log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_and_log_records_delivery():
    """_send_and_log sends notification and logs delivery to DB."""


    engine = EscalationEngine()
    conn = FakeConn()

    settings = {"slack_webhook_url": "https://hooks.slack.com/test", "slack_channel": "#test"}
    payload = _make_payload()

    with patch(f"{_MOD}.send_slack_notification", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"status": "sent", "channel": "slack"}
        result = await engine._send_and_log(conn, "ESC-001", "slack", settings, payload)

    assert result["status"] == "sent"
    assert result["channel"] == "slack"
    # Verify notification_deliveries INSERT
    delivery_inserts = [
        (q, args) for q, args in conn.executed
        if "notification_deliveries" in q
    ]
    assert len(delivery_inserts) == 1
    _, args = delivery_inserts[0]
    assert args[0] == "ESC-001"  # ticket_id
    assert args[1] == "slack"     # channel
    assert args[3] == "sent"      # status


@pytest.mark.asyncio
async def test_send_and_log_handles_exception():
    """_send_and_log catches exceptions from send functions and logs error."""


    engine = EscalationEngine()
    conn = FakeConn()

    settings = {"slack_webhook_url": "https://hooks.slack.com/test"}

    with patch(f"{_MOD}.send_slack_notification", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = RuntimeError("boom")
        result = await engine._send_and_log(conn, "ESC-002", "slack", settings, _make_payload())

    assert result["status"] == "error"
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_send_and_log_unknown_channel():
    """Unknown channel returns error without calling any send function."""


    engine = EscalationEngine()
    conn = FakeConn()

    result = await engine._send_and_log(conn, "ESC-003", "carrier_pigeon", {}, _make_payload())
    assert result["status"] == "error"
    assert "Unknown channel" in result["error"]


# ---------------------------------------------------------------------------
# 11. Client direct notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_client_notifications_dispatches_enabled_channels():
    """_send_client_notifications sends to all enabled client channels."""


    engine = EscalationEngine()
    conn = FakeConn()

    client_prefs = {
        "email_enabled": True,
        "email_recipients": ["client@org.com"],
        "slack_enabled": True,
        "slack_webhook_url": "https://hooks.slack.com/client",
        "teams_enabled": False,
        "teams_webhook_url": None,
    }

    with patch(f"{_MOD}.send_email_notification", new_callable=AsyncMock) as mock_email, \
         patch(f"{_MOD}.send_slack_notification", new_callable=AsyncMock) as mock_slack:
        mock_email.return_value = {"status": "sent", "channel": "email"}
        mock_slack.return_value = {"status": "sent", "channel": "slack"}

        results = await engine._send_client_notifications(
            conn, "ESC-010", client_prefs, _make_payload(), "high"
        )

    assert len(results) == 2
    channels_sent = {r["channel"] for r in results}
    assert "email" in channels_sent
    assert "slack" in channels_sent


# ---------------------------------------------------------------------------
# 12. SLA breach checking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_sla_breaches_returns_breached_tickets():
    """check_sla_breaches returns list of breached ticket dicts."""


    breached_rows = [
        {
            "id": "ESC-100",
            "partner_id": 1,
            "site_id": "site-001",
            "title": "Patching drift",
            "priority": "high",
            "sla_target_at": datetime.now(timezone.utc) - timedelta(hours=1),
        }
    ]

    conn = FakeConn(fetch_results=breached_rows)
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool):
        result = await engine.check_sla_breaches()

    assert len(result) == 1
    assert result[0]["id"] == "ESC-100"


@pytest.mark.asyncio
async def test_check_sla_breaches_empty():
    """check_sla_breaches returns empty list when no tickets are breached."""


    conn = FakeConn(fetch_results=[])
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool):
        result = await engine.check_sla_breaches()

    assert result == []


# ---------------------------------------------------------------------------
# 13. Singleton accessor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_escalation_engine_returns_singleton():
    """get_escalation_engine returns the same instance on repeated calls."""
    # Reset singleton
    escalation_engine._engine = None

    e1 = await get_escalation_engine()
    e2 = await get_escalation_engine()
    assert e1 is e2

    # Clean up
    escalation_engine._engine = None


# ---------------------------------------------------------------------------
# 14. Escalation mode: direct (no partner)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_escalation_direct_mode_no_partner():
    """Site with no partner but client prefs in 'direct' mode sends client notifications."""


    site_row = _make_site_row(partner_id=None, client_org_id="org-001")
    client_prefs = {
        "escalation_mode": "direct",
        "email_enabled": True,
        "email_recipients": ["client@org.com"],
        "slack_enabled": False,
        "slack_webhook_url": None,
        "teams_enabled": False,
        "teams_webhook_url": None,
        "escalation_timeout_minutes": 60,
    }
    # partner_notification_settings (partner_id=None passed to query, returns None)
    settings_row = None
    overrides_row = None
    sla_row = {"response_time_minutes": 60}
    prev_ticket = None

    conn = FakeConn(
        fetchrow_results=[
            site_row,
            client_prefs,
            settings_row,
            overrides_row,
            sla_row,
            prev_ticket,
        ]
    )
    mock_pool = MagicMock()
    mock_pool._fake_conn = conn

    engine = EscalationEngine()

    with patch(f"{_MOD}.admin_connection", _fake_admin_connection), \
         patch.object(engine, "_get_pool", return_value=mock_pool), \
         patch(f"{_MOD}.send_email_notification", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = {"status": "sent", "channel": "email"}
        result = await engine.create_escalation(
            site_id="site-001",
            incident=_make_incident(),
        )

    assert result["escalation_mode"] == "direct"
    assert len(result["notifications"]) >= 1


# ---------------------------------------------------------------------------
# 15. Internal escalation (no partner, no client prefs)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_internal_escalation_creates_int_ticket():
    """_create_internal_escalation returns INT- prefixed ticket."""


    engine = EscalationEngine()
    site = {"id": "site-orphan", "clinic_name": "Orphan Clinic"}
    incident = _make_incident()

    result = await engine._create_internal_escalation(incident, site)

    assert result["ticket_id"].startswith("INT-")
    assert result["partner_id"] is None
    assert result["priority"] == "medium"
    assert result["notifications"] == []
