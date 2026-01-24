"""Tests for the scanner service."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from network_scanner.scanner_service import NetworkScannerService
from network_scanner.config import ScannerConfig
from network_scanner.discovery import DiscoveredDevice
from network_scanner._types import DeviceType, ScanPolicy, DeviceStatus


@pytest.fixture
def temp_db():
    """Create temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def scanner_config(temp_db):
    """Create scanner config with temp database."""
    config = ScannerConfig()
    config.db_path = temp_db
    config.network_ranges = ["192.168.1.0/24"]
    config.enable_ad_discovery = False
    config.enable_arp_discovery = False
    config.enable_nmap_discovery = False
    config.enable_go_agent_checkins = False
    return config


@pytest.fixture
def scanner_service(scanner_config):
    """Create scanner service."""
    return NetworkScannerService(scanner_config)


class TestScannerServiceInit:
    """Tests for scanner service initialization."""

    def test_init_with_config(self, scanner_config):
        """Should initialize with config."""
        service = NetworkScannerService(scanner_config)

        assert service.config == scanner_config
        assert service.db is not None
        assert service._running is False

    def test_init_creates_database(self, scanner_config):
        """Should create database on init."""
        service = NetworkScannerService(scanner_config)

        # Database should exist and be queryable
        counts = service.db.get_device_counts()
        assert counts["total"] == 0


class TestDeduplication:
    """Tests for device deduplication."""

    def test_dedupe_by_ip(self, scanner_service):
        """Should deduplicate devices by IP."""
        devices = [
            DiscoveredDevice(ip_address="192.168.1.1", hostname=None),
            DiscoveredDevice(ip_address="192.168.1.1", hostname="router"),
            DiscoveredDevice(ip_address="192.168.1.2", hostname="pc1"),
        ]

        result = scanner_service._dedupe_by_ip(devices)

        assert len(result) == 2
        # Should have merged hostname
        router = next(d for d in result if d.ip_address == "192.168.1.1")
        assert router.hostname == "router"

    def test_dedupe_merges_ports(self, scanner_service):
        """Should merge ports from multiple discoveries."""
        devices = [
            DiscoveredDevice(ip_address="192.168.1.1", open_ports=[22, 80]),
            DiscoveredDevice(ip_address="192.168.1.1", open_ports=[443, 3389]),
        ]

        result = scanner_service._dedupe_by_ip(devices)

        assert len(result) == 1
        assert set(result[0].open_ports) == {22, 80, 443, 3389}

    def test_dedupe_merges_port_services(self, scanner_service):
        """Should merge port services."""
        devices = [
            DiscoveredDevice(
                ip_address="192.168.1.1",
                open_ports=[22],
                port_services={22: "ssh"},
            ),
            DiscoveredDevice(
                ip_address="192.168.1.1",
                open_ports=[443],
                port_services={443: "https"},
            ),
        ]

        result = scanner_service._dedupe_by_ip(devices)

        assert len(result) == 1
        assert result[0].port_services == {22: "ssh", 443: "https"}


class TestRunScan:
    """Tests for scan execution."""

    @pytest.mark.asyncio
    async def test_run_scan_creates_record(self, scanner_service):
        """Should create scan record."""
        result = await scanner_service.run_scan(triggered_by="test")

        assert "scan_id" in result
        assert result["status"] == "completed"

        # Check database
        history = scanner_service.db.get_scan_history(limit=1)
        assert len(history) == 1
        assert history[0].triggered_by == "test"

    @pytest.mark.asyncio
    async def test_run_scan_with_mock_discovery(self, scanner_service):
        """Should process discovered devices."""
        # Create mock discovery method
        mock_method = MagicMock()
        mock_method.name = "test"
        mock_method.is_available = AsyncMock(return_value=True)
        mock_method.discover = AsyncMock(return_value=[
            DiscoveredDevice(
                ip_address="192.168.1.100",
                hostname="test-pc",
                open_ports=[3389],
            ),
        ])

        scanner_service._discovery_methods = [mock_method]

        result = await scanner_service.run_scan(triggered_by="test")

        assert result["status"] == "completed"
        assert result["devices_found"] == 1
        assert result["new_devices"] == 1

        # Check device was stored
        devices = scanner_service.db.get_devices()
        assert len(devices) == 1
        assert devices[0].ip_address == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_run_scan_excludes_medical_devices(self, scanner_service):
        """Should exclude medical devices."""
        mock_method = MagicMock()
        mock_method.name = "test"
        mock_method.is_available = AsyncMock(return_value=True)
        mock_method.discover = AsyncMock(return_value=[
            DiscoveredDevice(
                ip_address="192.168.1.50",
                hostname="dicom-server",
                open_ports=[104, 11112],  # DICOM ports
            ),
        ])

        scanner_service._discovery_methods = [mock_method]

        result = await scanner_service.run_scan(triggered_by="test")

        assert result["medical_devices_excluded"] == 1

        # Device should be in DB but excluded
        devices = scanner_service.db.get_devices()
        assert len(devices) == 1
        assert devices[0].medical_device is True
        assert devices[0].scan_policy == ScanPolicy.EXCLUDED

    @pytest.mark.asyncio
    async def test_run_scan_handles_errors(self, scanner_service):
        """Should handle discovery errors gracefully."""
        mock_method = MagicMock()
        mock_method.name = "failing"
        mock_method.is_available = AsyncMock(return_value=True)
        mock_method.discover = AsyncMock(side_effect=Exception("Test error"))

        scanner_service._discovery_methods = [mock_method]

        # Should not raise
        result = await scanner_service.run_scan(triggered_by="test")
        assert result["status"] == "completed"


class TestScannerConfig:
    """Tests for scanner configuration."""

    def test_from_env_defaults(self):
        """Should have sensible defaults."""
        config = ScannerConfig()

        assert config.daily_scan_hour == 2
        assert config.exclude_medical_by_default is True
        assert config.api_port == 8082

    def test_validate_requires_network_ranges(self):
        """Should require network ranges."""
        config = ScannerConfig()
        config.network_ranges = []

        errors = config.validate()
        assert any("network ranges" in e.lower() for e in errors)

    def test_validate_forces_medical_exclusion(self):
        """Should force medical exclusion on."""
        config = ScannerConfig()
        config.exclude_medical_by_default = False
        config.network_ranges = ["192.168.1.0/24"]

        errors = config.validate()

        # Should force it back on
        assert config.exclude_medical_by_default is True
        assert any("medical" in e.lower() for e in errors)

    def test_credentials_separate_from_config(self):
        """Scanner credentials should be in separate file."""
        config = ScannerConfig()

        # Credentials path should be different from main config
        assert "scanner_creds" in str(config.credentials_path)
