"""Tests for discovery methods."""

import pytest
from datetime import datetime, timezone, timedelta

from network_scanner.discovery import (
    DiscoveredDevice,
    DiscoveryMethod,
    ADDiscovery,
    ADDiscoveryConfig,
    GoAgentRegistry,
    AgentInfo,
)
from network_scanner.discovery.arp_discovery import ARPDiscovery
from network_scanner._types import DiscoverySource


class TestDiscoveredDevice:
    """Tests for DiscoveredDevice dataclass."""

    def test_basic_device(self):
        """Should create device with IP address."""
        device = DiscoveredDevice(ip_address="192.168.1.100")
        assert device.ip_address == "192.168.1.100"
        assert device.hostname is None
        assert device.discovery_source == DiscoverySource.NMAP

    def test_device_with_all_fields(self):
        """Should create device with all fields populated."""
        device = DiscoveredDevice(
            ip_address="192.168.1.100",
            hostname="test-pc",
            mac_address="aa:bb:cc:dd:ee:ff",
            os_name="Windows 11",
            os_version="22H2",
            manufacturer="Dell",
            open_ports=[22, 443, 3389],
            discovery_source=DiscoverySource.AD,
            is_domain_joined=True,
        )

        assert device.ip_address == "192.168.1.100"
        assert device.hostname == "test-pc"
        assert device.mac_address == "aa:bb:cc:dd:ee:ff"
        assert device.os_name == "Windows 11"
        assert device.is_domain_joined is True
        assert 3389 in device.open_ports


class TestADDiscovery:
    """Tests for AD discovery."""

    def test_extract_domain_from_dn(self):
        """Should extract domain from DN."""
        discovery = ADDiscovery(
            server="dc.example.com",
            base_dn="DC=example,DC=com",
        )

        dn = "CN=COMPUTER,OU=Computers,DC=example,DC=com"
        domain = discovery._extract_domain_from_dn(dn)

        assert domain == "example.com"

    def test_extract_domain_nested_dn(self):
        """Should handle nested OUs."""
        discovery = ADDiscovery(
            server="dc.northvalley.local",
            base_dn="DC=northvalley,DC=local",
        )

        dn = "CN=NVDC01,OU=Domain Controllers,OU=Servers,DC=northvalley,DC=local"
        domain = discovery._extract_domain_from_dn(dn)

        assert domain == "northvalley.local"


class TestADDiscoveryConfig:
    """Tests for AD discovery configuration helper."""

    def test_from_domain(self):
        """Should create discovery from domain name."""
        discovery = ADDiscoveryConfig.from_domain(
            domain="example.com",
            username="scanner@example.com",
            password="secret",
        )

        assert discovery.base_dn == "DC=example,DC=local" or discovery.base_dn == "DC=example,DC=com"
        assert discovery.bind_dn == "scanner@example.com"
        assert discovery.bind_password == "secret"


class TestARPDiscovery:
    """Tests for ARP discovery."""

    def test_parse_linux_arp_line(self):
        """Should parse Linux arp output."""
        discovery = ARPDiscovery()

        # Linux format
        line = "? (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0"
        device = discovery._parse_arp_line(line)

        assert device is not None
        assert device.ip_address == "192.168.1.1"
        assert device.mac_address == "aa:bb:cc:dd:ee:ff"
        assert device.hostname is None

    def test_parse_macos_arp_line(self):
        """Should parse macOS arp output."""
        discovery = ARPDiscovery()

        # macOS format
        line = "gateway (192.168.88.1) at 0:50:56:c0:0:8 on en0 ifscope [ethernet]"
        device = discovery._parse_arp_line(line)

        assert device is not None
        assert device.ip_address == "192.168.88.1"
        assert device.hostname == "gateway"

    def test_skip_incomplete_entries(self):
        """Should skip incomplete ARP entries."""
        discovery = ARPDiscovery()

        line = "? (192.168.1.100) at (incomplete) on eth0"
        device = discovery._parse_arp_line(line)

        assert device is None

    def test_oui_lookup_vmware(self):
        """Should identify VMware MAC addresses."""
        discovery = ARPDiscovery()

        # VMware MAC prefix
        manufacturer = discovery._lookup_oui("00:50:56:aa:bb:cc")
        assert manufacturer == "VMware"

    def test_oui_lookup_unknown(self):
        """Should return None for unknown OUI."""
        discovery = ARPDiscovery()

        manufacturer = discovery._lookup_oui("12:34:56:78:90:ab")
        assert manufacturer is None


class TestGoAgentRegistry:
    """Tests for Go agent registry."""

    @pytest.fixture
    def registry(self):
        return GoAgentRegistry(stale_timeout_seconds=60)

    @pytest.mark.asyncio
    async def test_register_agent(self, registry):
        """Should register an agent."""
        agent = AgentInfo(
            host_id="test-001",
            hostname="test-pc",
            ip_address="192.168.1.100",
            os_name="Windows 11",
            os_version="22H2",
            agent_version="1.0.0",
        )

        await registry.register(agent)
        all_agents = await registry.get_all()

        assert len(all_agents) == 1
        assert all_agents[0].hostname == "test-pc"

    @pytest.mark.asyncio
    async def test_update_agent_checkin(self, registry):
        """Should update agent on re-checkin."""
        agent = AgentInfo(
            host_id="test-001",
            hostname="test-pc",
            ip_address="192.168.1.100",
            os_name="Windows 11",
            os_version="22H2",
            agent_version="1.0.0",
        )

        await registry.register(agent)

        # Update with new version
        agent.agent_version = "1.0.1"
        await registry.register(agent)

        all_agents = await registry.get_all()
        assert len(all_agents) == 1
        assert all_agents[0].agent_version == "1.0.1"

    @pytest.mark.asyncio
    async def test_unregister_agent(self, registry):
        """Should unregister an agent."""
        agent = AgentInfo(
            host_id="test-001",
            hostname="test-pc",
            ip_address="192.168.1.100",
            os_name="Windows",
            os_version="",
            agent_version="1.0.0",
        )

        await registry.register(agent)
        await registry.unregister("test-001")

        all_agents = await registry.get_all()
        assert len(all_agents) == 0

    @pytest.mark.asyncio
    async def test_get_active_agents(self, registry):
        """Should return only active agents."""
        # Register active agent
        active_agent = AgentInfo(
            host_id="active-001",
            hostname="active-pc",
            ip_address="192.168.1.100",
            os_name="Windows",
            os_version="",
            agent_version="1.0.0",
            last_checkin=datetime.now(timezone.utc),
        )
        await registry.register(active_agent)

        # Register stale agent (old checkin time)
        stale_agent = AgentInfo(
            host_id="stale-001",
            hostname="stale-pc",
            ip_address="192.168.1.101",
            os_name="Windows",
            os_version="",
            agent_version="1.0.0",
            last_checkin=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        await registry.register(stale_agent)

        active = await registry.get_active()
        assert len(active) == 1
        assert active[0].host_id == "active-001"

    @pytest.mark.asyncio
    async def test_cleanup_stale(self, registry):
        """Should cleanup stale agents."""
        # Register stale agent
        stale_agent = AgentInfo(
            host_id="stale-001",
            hostname="stale-pc",
            ip_address="192.168.1.100",
            os_name="Windows",
            os_version="",
            agent_version="1.0.0",
            last_checkin=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        await registry.register(stale_agent)

        removed = await registry.cleanup_stale()

        assert removed == 1
        all_agents = await registry.get_all()
        assert len(all_agents) == 0


class TestDiscoveryMethod:
    """Tests for discovery method interface."""

    def test_name_property(self):
        """Discovery methods should have a name."""
        discovery = ARPDiscovery()
        assert discovery.name == "arp"

    @pytest.mark.asyncio
    async def test_is_available(self):
        """Discovery methods should report availability."""
        discovery = ARPDiscovery()
        # This will depend on the system, just check it returns a bool
        result = await discovery.is_available()
        assert isinstance(result, bool)
