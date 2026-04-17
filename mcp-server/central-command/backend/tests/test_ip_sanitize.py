"""Unit tests for filter_routable_ips / _is_routable_ip (Session 209).

An appliance whose sole IP is APIPA (169.254.x.x) has NOT successfully
reconnected to the network. Persisting those addresses into
site_appliances.ip_addresses makes the admin console show a stale
"current IP" that nothing on the real LAN can reach, and trips
subnet-drift heuristics as a site-relocation event.

These tests pin the helper against every failure mode we've seen in
the field + the obvious adversarial input.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
for p in (backend_dir, mcp_server_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_helpers():
    """Import the helpers directly to avoid dragging in FastAPI/DB."""
    import importlib
    import types

    # Stub just enough of the module's imports to get the helpers to load.
    # The helpers themselves are pure functions of (raw_ips) -> list,
    # so we can exercise them without touching the handler.
    spec = importlib.util.find_spec("dashboard_api.sites")
    if spec is None:
        pytest.skip("sites module not importable in this env")
    # Actually importing sites is heavy; instead we re-implement via
    # the public function via exec of the snippet isolated to helpers.
    # Cleaner: import the real module.
    try:
        from dashboard_api import sites as _sites
    except Exception:
        import sites as _sites  # type: ignore
    return _sites


class TestIsRoutableIP:
    def test_rfc1918_is_routable(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("192.168.88.227") is True
        assert sites._is_routable_ip("10.0.0.5") is True
        assert sites._is_routable_ip("172.16.4.1") is True

    def test_public_is_routable(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("178.156.162.116") is True

    def test_wireguard_is_routable(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("10.100.0.2") is True

    def test_apipa_rejected_except_anycast(self):
        """169.254.88.1 is the engineered mesh anycast — must be kept.
        All other 169.254/16 addresses are DHCP-failure fallback APIPA
        and must be stripped."""
        sites = _load_helpers()
        assert sites._is_routable_ip("169.254.88.1") is True, (
            "anycast sentinel must survive filtering — missing_anycast "
            "detector depends on it"
        )
        assert sites._is_routable_ip("169.254.0.1") is False
        assert sites._is_routable_ip("169.254.255.254") is False
        assert sites._is_routable_ip("169.254.88.2") is False  # neighbor ≠ sentinel
        # Defensive: allow_anycast=False strips everything link-local
        assert sites._is_routable_ip("169.254.88.1", allow_anycast=False) is False

    def test_ipv6_link_local_rejected(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("fe80::1") is False
        assert sites._is_routable_ip("fe80::1234:5678:9abc:def0") is False

    def test_loopback_rejected(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("127.0.0.1") is False
        assert sites._is_routable_ip("::1") is False

    def test_unparseable_rejected(self):
        sites = _load_helpers()
        assert sites._is_routable_ip("") is False
        assert sites._is_routable_ip("not an ip") is False
        assert sites._is_routable_ip(None) is False  # type: ignore
        assert sites._is_routable_ip(42) is False  # type: ignore

    def test_whitespace_trimmed(self):
        sites = _load_helpers()
        assert sites._is_routable_ip(" 192.168.1.1 ") is True
        assert sites._is_routable_ip("\t10.0.0.1\n") is True


class TestFilterRoutableIPs:
    def test_drops_unintentional_apipa_keeps_rfc1918_and_anycast(self):
        sites = _load_helpers()
        # Healthy-online case: LAN IP + engineered anycast. Both stay.
        got = sites.filter_routable_ips(["192.168.88.227", "169.254.88.1"])
        assert got == ["192.168.88.227", "169.254.88.1"]

        # Real-failure case: DHCP outage left a fallback APIPA on the
        # interface. Sentinel anycast is there, real LAN IP is there,
        # the fallback 169.254.x.x garbage must get dropped.
        got = sites.filter_routable_ips(
            ["192.168.88.227", "169.254.88.1", "169.254.42.99"]
        )
        assert got == ["192.168.88.227", "169.254.88.1"]

    def test_deduplicates(self):
        sites = _load_helpers()
        got = sites.filter_routable_ips(["10.0.0.1", "10.0.0.1", "10.0.0.2"])
        assert got == ["10.0.0.1", "10.0.0.2"]

    def test_preserves_order(self):
        sites = _load_helpers()
        got = sites.filter_routable_ips(
            ["192.168.1.1", "10.0.0.5", "172.16.1.1"]
        )
        assert got == ["192.168.1.1", "10.0.0.5", "172.16.1.1"]

    def test_empty_list_returns_empty(self):
        sites = _load_helpers()
        assert sites.filter_routable_ips([]) == []
        assert sites.filter_routable_ips(None) == []

    def test_only_fallback_apipa_returns_empty(self):
        """Appliance whose only addresses are fallback-APIPA (not the
        engineered anycast) — there is no routable IP to persist. Empty
        list is the honest answer. Downstream consumers (admin console,
        drift heuristic, mesh target assigner) can act on the absence
        rather than being deceived by a ghost."""
        sites = _load_helpers()
        assert sites.filter_routable_ips(["169.254.42.99", "fe80::1"]) == []

    def test_only_anycast_retained(self):
        """Appliance reporting ONLY the anycast — this is unusual but
        valid during interface bring-up before DHCP completes. The
        anycast survives; downstream code sees there's no LAN IP and
        can decide what to do (probably show 'coming online')."""
        sites = _load_helpers()
        assert sites.filter_routable_ips(["169.254.88.1"]) == ["169.254.88.1"]

    def test_json_string_parsed(self):
        sites = _load_helpers()
        # parse_ip_addresses accepts a JSON-encoded string from older
        # DB rows; the filter inherits that handling.
        got = sites.filter_routable_ips('["192.168.1.1", "169.254.1.1"]')
        assert got == ["192.168.1.1"]


class TestAnycastInvariant:
    """Lock down the invariant that the anycast sentinel matches what
    _compute_network_anomaly expects. If someone changes ANYCAST_LINK_LOCAL
    in one place and not the helper, half the fleet will suddenly look
    broken to the anomaly detector. This test ties the two together."""

    def test_anycast_constant_and_filter_agree(self):
        sites = _load_helpers()
        assert sites.ANYCAST_LINK_LOCAL == "169.254.88.1"
        assert sites._is_routable_ip(sites.ANYCAST_LINK_LOCAL) is True
        # The filter path must preserve it verbatim (same-string check,
        # not just "some 169.254 address")
        kept = sites.filter_routable_ips([sites.ANYCAST_LINK_LOCAL])
        assert kept == [sites.ANYCAST_LINK_LOCAL]
