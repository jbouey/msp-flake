"""Tests for CheckinContext — the shared state dataclass for checkin decomposition."""
from datetime import datetime, timezone

from dashboard_api.checkin.context import CheckinContext


def test_context_defaults_empty_collections():
    """Collections default to empty lists/dicts, not None."""
    ctx = CheckinContext(
        checkin=None,
        request_ip="1.2.3.4",
        user_agent="test-agent",
        auth_site_id="site-1",
        now=datetime.now(timezone.utc),
    )
    assert ctx.windows_targets == []
    assert ctx.linux_targets == []
    assert ctx.pending_orders == []
    assert ctx.fleet_orders == []
    assert ctx.disabled_checks == []
    assert ctx.mesh_peers == []
    assert ctx.peer_bundle_hashes == []
    assert ctx.pending_devices == []
    assert ctx.merge_from_ids == []
    assert ctx.all_mac_addresses == []
    assert ctx.runbook_config == {}
    assert ctx.target_assignment == {}
    assert ctx.deployment_triggers == {}
    assert ctx.billing_status == {}


def test_context_ghost_flag_defaults_false():
    ctx = CheckinContext(
        checkin=None,
        request_ip="1.2.3.4",
        user_agent="test",
        auth_site_id="site-1",
        now=datetime.now(timezone.utc),
    )
    assert ctx.is_ghost is False


def test_context_mutation_isolation():
    """Default_factory prevents shared mutable state across instances."""
    a = CheckinContext(
        checkin=None, request_ip="1.1.1.1", user_agent="a",
        auth_site_id="s1", now=datetime.now(timezone.utc),
    )
    b = CheckinContext(
        checkin=None, request_ip="2.2.2.2", user_agent="b",
        auth_site_id="s2", now=datetime.now(timezone.utc),
    )
    a.windows_targets.append({"host": "win1"})
    assert b.windows_targets == []  # b's list is separate


def test_context_alert_mode_default():
    ctx = CheckinContext(
        checkin=None, request_ip="1.1.1.1", user_agent="a",
        auth_site_id="s1", now=datetime.now(timezone.utc),
    )
    assert ctx.alert_mode == "standard"
    assert ctx.boot_source == ""
    assert ctx.wg_access_state == ""
