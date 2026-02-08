"""
Tests for auto-enrollment of new domain devices.

Tests the device DB sync, AD enumeration workstation enrollment,
periodic re-enumeration, and auto DC detection features.
"""

import pytest
import sqlite3
import tempfile
import os
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from datetime import datetime, timezone, timedelta
from pathlib import Path


class TestDeviceDBSync:
    """Tests for _maybe_sync_device_db() — network-scanner → compliance-agent bridge."""

    def _make_agent(self, device_db_path=None):
        """Create a minimal ApplianceAgent for testing."""
        from compliance_agent.appliance_agent import ApplianceAgent, DEFAULT_DEVICE_DB_SYNC_INTERVAL

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent._last_device_db_sync = datetime.min.replace(tzinfo=timezone.utc)
        agent._device_db_sync_interval = DEFAULT_DEVICE_DB_SYNC_INTERVAL
        agent._device_db_path = Path(device_db_path) if device_db_path else Path("/nonexistent/devices.db")
        agent.windows_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent._domain_controller = "192.168.88.250"
        agent.logger = MagicMock()
        return agent

    def _create_device_db(self, db_path, devices):
        """Create a test devices.db with the given device rows."""
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY,
                hostname TEXT,
                ip_address TEXT,
                device_type TEXT,
                os_name TEXT,
                medical_device INTEGER DEFAULT 0,
                manually_opted_in INTEGER DEFAULT 0,
                scan_policy TEXT DEFAULT 'standard',
                status TEXT DEFAULT 'discovered'
            )
        """)
        for d in devices:
            conn.execute(
                "INSERT INTO devices (hostname, ip_address, device_type, os_name, medical_device, manually_opted_in, scan_policy, status) VALUES (?,?,?,?,?,?,?,?)",
                (
                    d.get("hostname", ""),
                    d.get("ip_address", ""),
                    d.get("device_type", "workstation"),
                    d.get("os_name", "Windows 10"),
                    d.get("medical_device", 0),
                    d.get("manually_opted_in", 0),
                    d.get("scan_policy", "standard"),
                    d.get("status", "monitored"),
                ),
            )
        conn.commit()
        conn.close()

    @pytest.mark.asyncio
    async def test_sync_adds_new_devices(self):
        """Device DB sync should add new monitored devices as WindowsTarget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "NVWS01", "ip_address": "192.168.88.251", "device_type": "workstation", "status": "monitored"},
                {"hostname": "NVSRV01", "ip_address": "192.168.88.244", "device_type": "server", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 2
            hostnames = {t.hostname for t in agent.windows_targets}
            assert "NVWS01" in hostnames
            assert "NVSRV01" in hostnames

    @pytest.mark.asyncio
    async def test_sync_skips_existing_targets(self):
        """Device DB sync should not duplicate existing targets."""
        from compliance_agent.runbooks.windows.executor import WindowsTarget

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "NVWS01", "ip_address": "192.168.88.251", "device_type": "workstation", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            # Pre-add this target
            agent.windows_targets = [
                WindowsTarget(hostname="NVWS01", ip_address="192.168.88.251", username="u", password="p"),
            ]
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            # Should still be just 1
            assert len(agent.windows_targets) == 1

    @pytest.mark.asyncio
    async def test_sync_skips_existing_by_ip(self):
        """Device DB sync should skip targets already present by IP match."""
        from compliance_agent.runbooks.windows.executor import WindowsTarget

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "NVWS01.northvalley.local", "ip_address": "192.168.88.251", "device_type": "workstation", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            # Pre-add with different hostname but same IP
            agent.windows_targets = [
                WindowsTarget(hostname="NVWS01", ip_address="192.168.88.251", username="u", password="p"),
            ]
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 1

    @pytest.mark.asyncio
    async def test_sync_excludes_medical_devices(self):
        """Medical devices should NOT be auto-enrolled unless manually opted in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "XRAY-01", "ip_address": "192.168.88.200", "device_type": "workstation", "medical_device": 1, "manually_opted_in": 0, "status": "monitored"},
                {"hostname": "SAFE-WS", "ip_address": "192.168.88.201", "device_type": "workstation", "medical_device": 0, "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 1
            assert agent.windows_targets[0].hostname == "SAFE-WS"

    @pytest.mark.asyncio
    async def test_sync_includes_opted_in_medical(self):
        """Medical devices manually opted in should be enrolled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "XRAY-01", "ip_address": "192.168.88.200", "device_type": "workstation", "medical_device": 1, "manually_opted_in": 1, "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 1
            assert agent.windows_targets[0].hostname == "XRAY-01"

    @pytest.mark.asyncio
    async def test_sync_skips_excluded_devices(self):
        """Devices with scan_policy='excluded' should not be enrolled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "EXCLUDED-WS", "ip_address": "192.168.88.210", "device_type": "workstation", "scan_policy": "excluded", "status": "monitored"},
                {"hostname": "NORMAL-WS", "ip_address": "192.168.88.211", "device_type": "workstation", "scan_policy": "standard", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 1
            assert agent.windows_targets[0].hostname == "NORMAL-WS"

    @pytest.mark.asyncio
    async def test_sync_respects_interval(self):
        """Sync should only run after the configured interval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "WS01", "ip_address": "192.168.88.251", "device_type": "workstation", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value={
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPass123!",
            })

            # First sync should run
            await agent._maybe_sync_device_db()
            assert len(agent.windows_targets) == 1

            # Second sync immediately after should be skipped
            agent.windows_targets = []  # Reset to detect if it runs
            await agent._maybe_sync_device_db()
            assert len(agent.windows_targets) == 0  # Skipped, still empty

    @pytest.mark.asyncio
    async def test_sync_no_creds_skips(self):
        """Sync should skip when no credentials are available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "devices.db")
            self._create_device_db(db_path, [
                {"hostname": "WS01", "ip_address": "192.168.88.251", "device_type": "workstation", "status": "monitored"},
            ])

            agent = self._make_agent(db_path)
            agent._get_dc_credentials = MagicMock(return_value=None)

            await agent._maybe_sync_device_db()

            assert len(agent.windows_targets) == 0

    @pytest.mark.asyncio
    async def test_sync_missing_db_skips(self):
        """Sync should skip when devices.db doesn't exist."""
        agent = self._make_agent("/nonexistent/devices.db")

        await agent._maybe_sync_device_db()

        assert len(agent.windows_targets) == 0


class TestADEnumerationWorkstations:
    """Tests for workstation enrollment in _enumerate_ad_targets()."""

    @pytest.mark.asyncio
    async def test_enumerate_adds_workstations_to_windows_targets(self):
        """AD enumeration should add reachable workstations to windows_targets."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.ad_enumeration import ADComputer, EnumerationResult
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.windows_targets = []
        agent.workstation_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent.windows_executor = MagicMock()
        agent._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()
        agent.client = MagicMock()

        # Set up discovered domain
        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        # Mock credentials
        agent._get_domain_credentials = AsyncMock(return_value={
            "username": "NORTHVALLEY\\Administrator",
            "password": "TestPass123!",
        })

        # Mock report
        agent._report_enumeration_results = AsyncMock()

        # Create mock computers
        mock_ws = ADComputer(
            hostname="NVWS01",
            fqdn="NVWS01.northvalley.local",
            ip_address="192.168.88.251",
            os_name="Windows 10 Enterprise",
            os_version="10.0.19045",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="OU=Workstations,DC=northvalley,DC=local",
            last_logon=now_utc(),
            enabled=True,
        )

        mock_srv = ADComputer(
            hostname="NVSRV01",
            fqdn="NVSRV01.northvalley.local",
            ip_address="192.168.88.244",
            os_name="Windows Server 2022",
            os_version="10.0.20348",
            is_server=True,
            is_workstation=False,
            is_domain_controller=False,
            ou_path="OU=Servers,DC=northvalley,DC=local",
            last_logon=now_utc(),
            enabled=True,
        )

        # Mock ADEnumerator
        with patch('compliance_agent.appliance_agent.ADEnumerator') as MockEnum:
            mock_enum = MockEnum.return_value
            mock_enum.enumerate_all = AsyncMock(return_value=([mock_srv], [mock_ws]))
            mock_enum.test_connectivity = AsyncMock(return_value=True)
            mock_enum.resolve_missing_ips = AsyncMock()

            await agent._enumerate_ad_targets()

        # Both server and workstation should be in windows_targets
        # Hostnames should be IPs (preferred over FQDNs for appliance DNS compatibility)
        assert len(agent.windows_targets) == 2
        hostnames = {t.hostname for t in agent.windows_targets}
        assert "192.168.88.244" in hostnames
        assert "192.168.88.251" in hostnames

    @pytest.mark.asyncio
    async def test_enumerate_skips_unreachable_workstations(self):
        """Unreachable workstations should NOT be added to windows_targets."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.ad_enumeration import ADComputer
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.windows_targets = []
        agent.workstation_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent.windows_executor = MagicMock()
        agent._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()
        agent.client = MagicMock()

        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        agent._get_domain_credentials = AsyncMock(return_value={
            "username": "NORTHVALLEY\\Administrator",
            "password": "TestPass123!",
        })
        agent._report_enumeration_results = AsyncMock()

        mock_ws = ADComputer(
            hostname="OFFLINEWS",
            fqdn="OFFLINEWS.northvalley.local",
            ip_address="192.168.88.199",
            os_name="Windows 10",
            os_version="10.0.19045",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="OU=Workstations,DC=northvalley,DC=local",
            last_logon=now_utc(),
            enabled=True,
        )

        with patch('compliance_agent.appliance_agent.ADEnumerator') as MockEnum:
            mock_enum = MockEnum.return_value
            mock_enum.enumerate_all = AsyncMock(return_value=([], [mock_ws]))
            mock_enum.test_connectivity = AsyncMock(return_value=False)
            mock_enum.resolve_missing_ips = AsyncMock()

            await agent._enumerate_ad_targets()

        assert len(agent.windows_targets) == 0

    @pytest.mark.asyncio
    async def test_enumerate_doesnt_duplicate_existing(self):
        """AD enumeration should not add targets that already exist."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.ad_enumeration import ADComputer
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent.runbooks.windows.executor import WindowsTarget
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.workstation_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent.windows_executor = MagicMock()
        agent._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()
        agent.client = MagicMock()

        # Pre-existing target
        agent.windows_targets = [
            WindowsTarget(
                hostname="NVWS01.northvalley.local",
                ip_address="192.168.88.251",
                username="u",
                password="p",
            ),
        ]

        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        agent._get_domain_credentials = AsyncMock(return_value={
            "username": "NORTHVALLEY\\Administrator",
            "password": "TestPass123!",
        })
        agent._report_enumeration_results = AsyncMock()

        mock_ws = ADComputer(
            hostname="NVWS01",
            fqdn="NVWS01.northvalley.local",
            ip_address="192.168.88.251",
            os_name="Windows 10",
            os_version="10.0.19045",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="OU=Workstations,DC=northvalley,DC=local",
            last_logon=now_utc(),
            enabled=True,
        )

        with patch('compliance_agent.appliance_agent.ADEnumerator') as MockEnum:
            mock_enum = MockEnum.return_value
            mock_enum.enumerate_all = AsyncMock(return_value=([], [mock_ws]))
            mock_enum.test_connectivity = AsyncMock(return_value=True)
            mock_enum.resolve_missing_ips = AsyncMock()

            await agent._enumerate_ad_targets()

        # Should still be just 1 (not duplicated)
        assert len(agent.windows_targets) == 1


class TestFQDNToIPResolution:
    """Tests for resolve_missing_ips() — FQDN→IP resolution via DC."""

    @pytest.mark.asyncio
    async def test_resolve_fills_missing_ips(self):
        """Computers without ip_address should get IPs resolved via DC."""
        from compliance_agent.ad_enumeration import ADEnumerator, ADComputer
        from compliance_agent._types import now_utc

        executor = MagicMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.output = {"stdout": '{"WS01.northvalley.local": "192.168.88.251"}'}
        executor.run_script = AsyncMock(return_value=result_mock)

        enumerator = ADEnumerator(
            domain_controller="192.168.88.250",
            username="Administrator",
            password="TestPass123!",
            domain="northvalley.local",
            executor=executor,
        )

        computer = ADComputer(
            hostname="WS01",
            fqdn="WS01.northvalley.local",
            ip_address=None,  # Missing
            os_name="Windows 10",
            os_version="10.0",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="",
            last_logon=now_utc(),
            enabled=True,
        )

        await enumerator.resolve_missing_ips([computer])

        assert computer.ip_address == "192.168.88.251"

    @pytest.mark.asyncio
    async def test_resolve_skips_computers_with_ips(self):
        """Computers that already have ip_address should not be re-resolved."""
        from compliance_agent.ad_enumeration import ADEnumerator, ADComputer
        from compliance_agent._types import now_utc

        executor = MagicMock()
        executor.run_script = AsyncMock()

        enumerator = ADEnumerator(
            domain_controller="192.168.88.250",
            username="Administrator",
            password="TestPass123!",
            domain="northvalley.local",
            executor=executor,
        )

        computer = ADComputer(
            hostname="WS01",
            fqdn="WS01.northvalley.local",
            ip_address="192.168.88.251",  # Already set
            os_name="Windows 10",
            os_version="10.0",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="",
            last_logon=now_utc(),
            enabled=True,
        )

        await enumerator.resolve_missing_ips([computer])

        # Should NOT have called executor — nothing to resolve
        executor.run_script.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_handles_failure_gracefully(self):
        """DNS resolution failure should not crash — computers just keep no IP."""
        from compliance_agent.ad_enumeration import ADEnumerator, ADComputer
        from compliance_agent._types import now_utc

        executor = MagicMock()
        executor.run_script = AsyncMock(side_effect=Exception("WinRM timeout"))

        enumerator = ADEnumerator(
            domain_controller="192.168.88.250",
            username="Administrator",
            password="TestPass123!",
            domain="northvalley.local",
            executor=executor,
        )

        computer = ADComputer(
            hostname="WS01",
            fqdn="WS01.northvalley.local",
            ip_address=None,
            os_name="Windows 10",
            os_version="10.0",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="",
            last_logon=now_utc(),
            enabled=True,
        )

        # Should not raise
        await enumerator.resolve_missing_ips([computer])
        assert computer.ip_address is None

    @pytest.mark.asyncio
    async def test_ip_preferred_over_fqdn_in_windows_targets(self):
        """WindowsTarget hostname should use IP when available, not FQDN."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.ad_enumeration import ADComputer
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.windows_targets = []
        agent.workstation_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent.windows_executor = MagicMock()
        agent._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()
        agent.client = MagicMock()

        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        agent._get_domain_credentials = AsyncMock(return_value={
            "username": "NORTHVALLEY\\Administrator",
            "password": "TestPass123!",
        })
        agent._report_enumeration_results = AsyncMock()

        # Computer with both IP and FQDN
        mock_ws = ADComputer(
            hostname="WS01",
            fqdn="WS01.northvalley.local",
            ip_address="192.168.88.251",
            os_name="Windows 10",
            os_version="10.0",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="",
            last_logon=now_utc(),
            enabled=True,
        )

        with patch('compliance_agent.appliance_agent.ADEnumerator') as MockEnum:
            mock_enum = MockEnum.return_value
            mock_enum.enumerate_all = AsyncMock(return_value=([], [mock_ws]))
            mock_enum.test_connectivity = AsyncMock(return_value=True)
            mock_enum.resolve_missing_ips = AsyncMock()

            await agent._enumerate_ad_targets()

        assert len(agent.windows_targets) == 1
        # Must use IP, not FQDN
        assert agent.windows_targets[0].hostname == "192.168.88.251"
        assert agent.windows_targets[0].ip_address == "192.168.88.251"

    @pytest.mark.asyncio
    async def test_fqdn_fallback_when_no_ip(self):
        """When no IP is available, FQDN should be used as hostname fallback."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.ad_enumeration import ADComputer
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.windows_targets = []
        agent.workstation_targets = []
        agent.config = MagicMock()
        agent.config.site_id = "test-site"
        agent.windows_executor = MagicMock()
        agent._last_enumeration = datetime.min.replace(tzinfo=timezone.utc)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()
        agent.client = MagicMock()

        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        agent._get_domain_credentials = AsyncMock(return_value={
            "username": "NORTHVALLEY\\Administrator",
            "password": "TestPass123!",
        })
        agent._report_enumeration_results = AsyncMock()

        # Computer with FQDN but no IP (resolution failed)
        mock_ws = ADComputer(
            hostname="WS02",
            fqdn="WS02.northvalley.local",
            ip_address=None,
            os_name="Windows 10",
            os_version="10.0",
            is_server=False,
            is_workstation=True,
            is_domain_controller=False,
            ou_path="",
            last_logon=now_utc(),
            enabled=True,
        )

        with patch('compliance_agent.appliance_agent.ADEnumerator') as MockEnum:
            mock_enum = MockEnum.return_value
            mock_enum.enumerate_all = AsyncMock(return_value=([], [mock_ws]))
            mock_enum.test_connectivity = AsyncMock(return_value=True)
            mock_enum.resolve_missing_ips = AsyncMock()

            await agent._enumerate_ad_targets()

        assert len(agent.windows_targets) == 1
        # Falls back to FQDN when no IP
        assert agent.windows_targets[0].hostname == "WS02.northvalley.local"


class TestPeriodicReEnumeration:
    """Tests for _maybe_reenumerate_ad() periodic re-enumeration."""

    @pytest.mark.asyncio
    async def test_reenumerate_triggers_after_interval(self):
        """Re-enumeration should trigger when interval has elapsed."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )
        agent._last_enumeration = datetime.now(timezone.utc) - timedelta(hours=2)
        agent._enumeration_interval = 3600  # 1 hour
        agent.logger = MagicMock()

        agent._enumerate_ad_targets = AsyncMock()

        await agent._maybe_reenumerate_ad()

        agent._enumerate_ad_targets.assert_called_once()

    @pytest.mark.asyncio
    async def test_reenumerate_skips_before_interval(self):
        """Re-enumeration should skip when interval hasn't elapsed."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )
        agent._last_enumeration = datetime.now(timezone.utc) - timedelta(minutes=30)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()

        agent._enumerate_ad_targets = AsyncMock()

        await agent._maybe_reenumerate_ad()

        agent._enumerate_ad_targets.assert_not_called()

    @pytest.mark.asyncio
    async def test_reenumerate_skips_without_domain(self):
        """Re-enumeration should skip when no domain is discovered."""
        from compliance_agent.appliance_agent import ApplianceAgent

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.discovered_domain = None
        agent.logger = MagicMock()

        agent._enumerate_ad_targets = AsyncMock()

        await agent._maybe_reenumerate_ad()

        agent._enumerate_ad_targets.assert_not_called()

    @pytest.mark.asyncio
    async def test_reenumerate_handles_failure(self):
        """Re-enumeration should handle errors gracefully."""
        from compliance_agent.appliance_agent import ApplianceAgent
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        agent = ApplianceAgent.__new__(ApplianceAgent)
        agent.discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )
        agent._last_enumeration = datetime.now(timezone.utc) - timedelta(hours=2)
        agent._enumeration_interval = 3600
        agent.logger = MagicMock()

        agent._enumerate_ad_targets = AsyncMock(side_effect=Exception("WinRM timeout"))

        # Should not raise
        await agent._maybe_reenumerate_ad()


class TestAutoDCDetection:
    """Tests for auto-setting _domain_controller from discovered domain."""

    def test_dc_auto_set_on_domain_discovery(self):
        """_domain_controller should be set when domain is discovered with DCs."""
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        # Simulate the auto-set logic (from _discover_domain_on_boot)
        domain_controller = None
        discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        if not domain_controller and discovered_domain.domain_controllers:
            domain_controller = discovered_domain.domain_controllers[0]

        assert domain_controller == "192.168.88.250"

    def test_dc_not_overwritten_if_already_set(self):
        """If _domain_controller is already set, it should not be overwritten."""
        from compliance_agent.domain_discovery import DiscoveredDomain
        from compliance_agent._types import now_utc

        domain_controller = "10.0.0.1"  # Already configured
        discovered_domain = DiscoveredDomain(
            domain_name="northvalley.local",
            netbios_name="NORTHVALLEY",
            domain_controllers=["192.168.88.250"],
            dns_servers=["192.168.88.250"],
            discovered_at=now_utc(),
            discovery_method="dns_srv",
        )

        if not domain_controller and discovered_domain.domain_controllers:
            domain_controller = discovered_domain.domain_controllers[0]

        assert domain_controller == "10.0.0.1"  # Unchanged
