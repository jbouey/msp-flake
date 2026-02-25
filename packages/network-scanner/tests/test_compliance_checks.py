"""
Unit tests for HIPAA network compliance checks.

Tests all 7 checks: pass/fail/warn scenarios, applicability filtering,
medical device exclusion.
"""

import pytest
from datetime import datetime, timezone

from network_scanner._types import (
    Device,
    DevicePort,
    DeviceType,
    ScanPolicy,
    DeviceStatus,
    ComplianceStatus,
)
from network_scanner.compliance.base import ComplianceResult
from network_scanner.compliance.network_checks import (
    ALL_NETWORK_CHECKS,
    ProhibitedPortsCheck,
    EncryptedServicesCheck,
    TLSWebServicesCheck,
    DatabaseExposureCheck,
    SNMPSecurityCheck,
    RDPExposureCheck,
    DeviceInventoryCheck,
)


def _make_device(
    device_type: DeviceType = DeviceType.WORKSTATION,
    ports: list[int] | None = None,
    medical: bool = False,
) -> Device:
    """Create a test device with optional ports."""
    device = Device(
        id="test-device-1",
        ip_address="192.168.88.100",
        hostname="test-ws",
        device_type=device_type,
        status=DeviceStatus.MONITORED,
    )
    if medical:
        device.mark_as_medical()
    if ports:
        device.open_ports = [
            DevicePort(device_id=device.id, port=p) for p in ports
        ]
    return device


# ===========================================================================
# ProhibitedPortsCheck
# ===========================================================================

class TestProhibitedPortsCheck:
    check = ProhibitedPortsCheck()

    @pytest.mark.asyncio
    async def test_no_ports_passes(self):
        device = _make_device(ports=[])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_safe_ports_passes(self):
        device = _make_device(ports=[80, 443, 22, 3389])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_ftp_fails(self):
        device = _make_device(ports=[21, 443])
        result = await self.check.run(device)
        assert result.status == "fail"
        assert 21 in result.details["prohibited_ports"]

    @pytest.mark.asyncio
    async def test_telnet_fails(self):
        device = _make_device(ports=[23])
        result = await self.check.run(device)
        assert result.status == "fail"
        assert 23 in result.details["prohibited_ports"]

    @pytest.mark.asyncio
    async def test_rsh_family_fails(self):
        device = _make_device(ports=[512, 513, 514])
        result = await self.check.run(device)
        assert result.status == "fail"
        assert len(result.details["prohibited_ports"]) == 3

    @pytest.mark.asyncio
    async def test_tftp_fails(self):
        device = _make_device(ports=[69])
        result = await self.check.run(device)
        assert result.status == "fail"

    def test_applicable_to_all_types(self):
        for dt in [DeviceType.WORKSTATION, DeviceType.SERVER, DeviceType.NETWORK, DeviceType.PRINTER]:
            device = _make_device(device_type=dt)
            assert self.check.is_applicable(device)

    def test_not_applicable_to_medical(self):
        device = _make_device(device_type=DeviceType.MEDICAL)
        assert not self.check.is_applicable(device)


# ===========================================================================
# EncryptedServicesCheck
# ===========================================================================

class TestEncryptedServicesCheck:
    check = EncryptedServicesCheck()

    @pytest.mark.asyncio
    async def test_no_http_passes(self):
        device = _make_device(ports=[22, 3389])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_https_only_passes(self):
        device = _make_device(ports=[443])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_http_without_https_fails(self):
        device = _make_device(ports=[80])
        result = await self.check.run(device)
        assert result.status == "fail"

    @pytest.mark.asyncio
    async def test_both_http_and_https_warns(self):
        device = _make_device(ports=[80, 443])
        result = await self.check.run(device)
        assert result.status == "warn"

    def test_applicable_to_workstations_servers(self):
        assert self.check.is_applicable(_make_device(device_type=DeviceType.WORKSTATION))
        assert self.check.is_applicable(_make_device(device_type=DeviceType.SERVER))

    def test_not_applicable_to_network_printer(self):
        assert not self.check.is_applicable(_make_device(device_type=DeviceType.NETWORK))
        assert not self.check.is_applicable(_make_device(device_type=DeviceType.PRINTER))


# ===========================================================================
# TLSWebServicesCheck
# ===========================================================================

class TestTLSWebServicesCheck:
    check = TLSWebServicesCheck()

    @pytest.mark.asyncio
    async def test_no_alt_ports_passes(self):
        device = _make_device(device_type=DeviceType.SERVER, ports=[80, 443])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_8080_without_8443_warns(self):
        device = _make_device(device_type=DeviceType.SERVER, ports=[8080])
        result = await self.check.run(device)
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_8080_with_8443_passes(self):
        device = _make_device(device_type=DeviceType.SERVER, ports=[8080, 8443])
        result = await self.check.run(device)
        assert result.status == "pass"

    def test_only_applicable_to_servers(self):
        assert self.check.is_applicable(_make_device(device_type=DeviceType.SERVER))
        assert not self.check.is_applicable(_make_device(device_type=DeviceType.WORKSTATION))


# ===========================================================================
# DatabaseExposureCheck
# ===========================================================================

class TestDatabaseExposureCheck:
    check = DatabaseExposureCheck()

    @pytest.mark.asyncio
    async def test_no_db_ports_passes(self):
        device = _make_device(ports=[80, 443, 22])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_mysql_on_workstation_fails(self):
        device = _make_device(device_type=DeviceType.WORKSTATION, ports=[3306])
        result = await self.check.run(device)
        assert result.status == "fail"
        assert 3306 in result.details["exposed_databases"]

    @pytest.mark.asyncio
    async def test_postgres_on_printer_fails(self):
        device = _make_device(device_type=DeviceType.PRINTER, ports=[5432])
        result = await self.check.run(device)
        assert result.status == "fail"

    @pytest.mark.asyncio
    async def test_redis_on_network_fails(self):
        device = _make_device(device_type=DeviceType.NETWORK, ports=[6379])
        result = await self.check.run(device)
        assert result.status == "fail"

    def test_not_applicable_to_servers(self):
        """Servers are expected to run databases."""
        assert not self.check.is_applicable(_make_device(device_type=DeviceType.SERVER))

    def test_applicable_to_workstations(self):
        assert self.check.is_applicable(_make_device(device_type=DeviceType.WORKSTATION))


# ===========================================================================
# SNMPSecurityCheck
# ===========================================================================

class TestSNMPSecurityCheck:
    check = SNMPSecurityCheck()

    @pytest.mark.asyncio
    async def test_no_snmp_passes(self):
        device = _make_device(ports=[80, 443])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_snmp_161_warns(self):
        device = _make_device(ports=[161])
        result = await self.check.run(device)
        assert result.status == "warn"
        assert 161 in result.details["snmp_ports"]

    @pytest.mark.asyncio
    async def test_snmp_162_warns(self):
        device = _make_device(ports=[162])
        result = await self.check.run(device)
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_both_snmp_ports_warns(self):
        device = _make_device(ports=[161, 162])
        result = await self.check.run(device)
        assert result.status == "warn"
        assert sorted(result.details["snmp_ports"]) == [161, 162]


# ===========================================================================
# RDPExposureCheck
# ===========================================================================

class TestRDPExposureCheck:
    check = RDPExposureCheck()

    @pytest.mark.asyncio
    async def test_no_rdp_passes(self):
        device = _make_device(device_type=DeviceType.SERVER, ports=[22, 443])
        result = await self.check.run(device)
        assert result.status == "pass"

    @pytest.mark.asyncio
    async def test_rdp_on_server_warns(self):
        device = _make_device(device_type=DeviceType.SERVER, ports=[3389])
        result = await self.check.run(device)
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_rdp_on_printer_warns(self):
        device = _make_device(device_type=DeviceType.PRINTER, ports=[3389])
        result = await self.check.run(device)
        assert result.status == "warn"

    def test_not_applicable_to_workstations(self):
        """RDP is expected on workstations for remote support."""
        assert not self.check.is_applicable(_make_device(device_type=DeviceType.WORKSTATION))

    def test_applicable_to_servers_network(self):
        assert self.check.is_applicable(_make_device(device_type=DeviceType.SERVER))
        assert self.check.is_applicable(_make_device(device_type=DeviceType.NETWORK))


# ===========================================================================
# DeviceInventoryCheck
# ===========================================================================

class TestDeviceInventoryCheck:
    check = DeviceInventoryCheck()

    @pytest.mark.asyncio
    async def test_no_ports_warns(self):
        device = _make_device(ports=[])
        result = await self.check.run(device)
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_has_ports_passes(self):
        device = _make_device(ports=[22, 80])
        result = await self.check.run(device)
        assert result.status == "pass"
        assert result.details["ports_found"] == 2

    @pytest.mark.asyncio
    async def test_single_port_passes(self):
        device = _make_device(ports=[443])
        result = await self.check.run(device)
        assert result.status == "pass"


# ===========================================================================
# ALL_NETWORK_CHECKS integration
# ===========================================================================

class TestAllChecks:
    def test_seven_checks_registered(self):
        assert len(ALL_NETWORK_CHECKS) == 7

    def test_unique_check_types(self):
        types = [c.check_type for c in ALL_NETWORK_CHECKS]
        assert len(types) == len(set(types))

    def test_all_have_hipaa_control(self):
        for check in ALL_NETWORK_CHECKS:
            assert check.hipaa_control is not None
            assert check.hipaa_control.startswith("§")

    @pytest.mark.asyncio
    async def test_medical_device_excluded(self):
        """Medical devices should not be applicable to any check."""
        device = _make_device(medical=True)
        for check in ALL_NETWORK_CHECKS:
            assert not check.is_applicable(device)

    @pytest.mark.asyncio
    async def test_result_format(self):
        device = _make_device(ports=[22, 443])
        for check in ALL_NETWORK_CHECKS:
            if check.is_applicable(device):
                result = await check.run(device)
                assert isinstance(result, ComplianceResult)
                assert result.status in ("pass", "warn", "fail")
                assert result.check_type == check.check_type
                assert isinstance(result.details, dict)
