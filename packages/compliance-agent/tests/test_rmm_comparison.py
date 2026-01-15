"""
Tests for RMM Comparison Engine.

Tests device matching, gap analysis, and deduplication recommendations.
"""

import pytest
from datetime import datetime, timezone, timedelta

from compliance_agent.rmm_comparison import (
    RMMComparisonEngine,
    RMMDevice,
    RMMProvider,
    MatchConfidence,
    GapType,
    DeviceMatch,
    CoverageGap,
    ComparisonReport,
    compare_with_rmm,
)


class TestRMMDevice:
    """Tests for RMMDevice dataclass."""

    def test_normalize_hostname(self):
        """Test hostname normalization."""
        device = RMMDevice(hostname="workstation01.domain.local")
        assert device.normalize_hostname() == "WORKSTATION01"

        device2 = RMMDevice(hostname="WS-002")
        assert device2.normalize_hostname() == "WS-002"

        device3 = RMMDevice(hostname="")
        assert device3.normalize_hostname() == ""

    def test_normalize_mac(self):
        """Test MAC address normalization."""
        device = RMMDevice(hostname="test", mac_address="00:1A:2B:3C:4D:5E")
        assert device.normalize_mac() == "001A2B3C4D5E"

        device2 = RMMDevice(hostname="test", mac_address="00-1A-2B-3C-4D-5E")
        assert device2.normalize_mac() == "001A2B3C4D5E"

        device3 = RMMDevice(hostname="test", mac_address=None)
        assert device3.normalize_mac() is None

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now(timezone.utc)
        device = RMMDevice(
            hostname="WS01",
            device_id="12345",
            ip_address="192.168.1.100",
            mac_address="00:1A:2B:3C:4D:5E",
            os_name="Windows 10 Enterprise",
            rmm_provider=RMMProvider.CONNECTWISE,
            rmm_agent_id="CW-12345",
            rmm_last_seen=now,
            rmm_online=True,
        )

        result = device.to_dict()
        assert result["hostname"] == "WS01"
        assert result["ip_address"] == "192.168.1.100"
        assert result["rmm_provider"] == "connectwise"
        assert result["rmm_online"] is True


class TestRMMComparisonEngine:
    """Tests for RMMComparisonEngine."""

    @pytest.fixture
    def engine(self):
        """Create comparison engine instance."""
        return RMMComparisonEngine()

    @pytest.fixture
    def sample_rmm_devices(self):
        """Sample RMM device data."""
        return [
            RMMDevice(
                hostname="WORKSTATION01",
                ip_address="192.168.1.101",
                mac_address="00:1A:2B:3C:4D:01",
                os_name="Windows 10 Enterprise",
                rmm_provider=RMMProvider.CONNECTWISE,
                rmm_agent_id="CW-001",
                rmm_online=True,
            ),
            RMMDevice(
                hostname="WORKSTATION02",
                ip_address="192.168.1.102",
                mac_address="00:1A:2B:3C:4D:02",
                os_name="Windows 11 Pro",
                rmm_provider=RMMProvider.CONNECTWISE,
                rmm_agent_id="CW-002",
                rmm_online=True,
            ),
            RMMDevice(
                hostname="WORKSTATION03",
                ip_address="192.168.1.103",
                mac_address="00:1A:2B:3C:4D:03",
                os_name="Windows 10 Pro",
                rmm_provider=RMMProvider.CONNECTWISE,
                rmm_agent_id="CW-003",
                rmm_online=False,
            ),
        ]

    @pytest.fixture
    def sample_workstations(self):
        """Sample AD workstation data."""
        return [
            {
                "hostname": "WORKSTATION01",
                "ip_address": "192.168.1.101",
                "mac_address": "00:1A:2B:3C:4D:01",
                "os_name": "Windows 10 Enterprise",
                "online": True,
            },
            {
                "hostname": "WORKSTATION02",
                "ip_address": "192.168.1.102",
                "mac_address": "00:1A:2B:3C:4D:02",
                "os_name": "Windows 11 Pro",
                "online": True,
            },
            {
                "hostname": "WORKSTATION04",
                "ip_address": "192.168.1.104",
                "mac_address": "00:1A:2B:3C:4D:04",
                "os_name": "Windows 10 Pro",
                "online": True,
            },
        ]

    def test_load_rmm_data(self, engine, sample_rmm_devices):
        """Test loading RMM device data."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)

        assert len(engine._rmm_devices) == 3
        assert engine._rmm_provider == RMMProvider.CONNECTWISE
        assert "WORKSTATION01" in engine._index_by_hostname
        assert "192.168.1.101" in engine._index_by_ip

    def test_load_from_csv(self, engine):
        """Test loading RMM data from CSV."""
        csv_content = """hostname,ip_address,mac_address,os_name
WS01,192.168.1.101,00:1A:2B:3C:4D:01,Windows 10
WS02,192.168.1.102,00:1A:2B:3C:4D:02,Windows 11
WS03,192.168.1.103,00:1A:2B:3C:4D:03,Windows 10"""

        count = engine.load_from_csv(csv_content, RMMProvider.MANUAL)

        assert count == 3
        assert len(engine._rmm_devices) == 3
        assert engine._rmm_devices[0].hostname == "WS01"

    def test_compare_exact_match(self, engine, sample_rmm_devices, sample_workstations):
        """Test comparison with exact matches."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations(sample_workstations)

        assert report.our_device_count == 3
        assert report.rmm_device_count == 3
        assert report.matched_count == 2  # WS01 and WS02 match, WS04 has no match
        assert report.exact_match_count == 2  # Both matches should be exact

        # Find WORKSTATION01 match
        ws01_match = next(m for m in report.matches if m.our_hostname == "WORKSTATION01")
        assert ws01_match.confidence == MatchConfidence.EXACT
        assert ws01_match.rmm_device.hostname == "WORKSTATION01"

        # WORKSTATION04 should NOT match (different from any RMM device)
        ws04_match = next(m for m in report.matches if m.our_hostname == "WORKSTATION04")
        assert ws04_match.confidence == MatchConfidence.NO_MATCH

    def test_compare_no_rmm_data(self, engine, sample_workstations):
        """Test comparison with no RMM data loaded."""
        report = engine.compare_workstations(sample_workstations)

        assert report.rmm_device_count == 0
        assert report.matched_count == 0
        assert report.coverage_rate == 0.0
        assert len(report.gaps) == 3
        assert all(g.gap_type == GapType.MISSING_FROM_RMM for g in report.gaps)

    def test_gap_detection_missing_from_rmm(self, engine, sample_rmm_devices, sample_workstations):
        """Test detection of devices missing from RMM."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations(sample_workstations)

        missing_gaps = [g for g in report.gaps if g.gap_type == GapType.MISSING_FROM_RMM]
        assert len(missing_gaps) == 1
        assert missing_gaps[0].device["hostname"] == "WORKSTATION04"

    def test_gap_detection_missing_from_ad(self, engine, sample_rmm_devices, sample_workstations):
        """Test detection of RMM devices not in AD."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations(sample_workstations)

        # WORKSTATION03 is in RMM but not in our workstations
        missing_ad_gaps = [g for g in report.gaps if g.gap_type == GapType.MISSING_FROM_AD]
        assert len(missing_ad_gaps) == 1
        assert missing_ad_gaps[0].device["hostname"] == "WORKSTATION03"

    def test_stale_device_detection(self, engine):
        """Test detection of stale RMM devices."""
        old_time = datetime.now(timezone.utc) - timedelta(days=45)
        rmm_devices = [
            RMMDevice(
                hostname="OLD_DEVICE",
                rmm_provider=RMMProvider.CONNECTWISE,
                rmm_agent_id="OLD-001",
                rmm_last_seen=old_time,
                rmm_online=False,
            ),
        ]

        engine.load_rmm_data(rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations([])

        stale_gaps = [g for g in report.gaps if g.gap_type == GapType.STALE_RMM]
        assert len(stale_gaps) == 1
        assert "stale" in stale_gaps[0].recommendation.lower()

    def test_fuzzy_hostname_match(self, engine):
        """Test fuzzy hostname matching with minor differences."""
        rmm_devices = [
            RMMDevice(
                hostname="WS-01",  # With hyphen
                ip_address="192.168.1.100",
                rmm_provider=RMMProvider.MANUAL,
                rmm_agent_id="1",
            ),
        ]

        workstations = [
            {
                "hostname": "WS01",  # Without hyphen - small diff
                "ip_address": "192.168.1.200",  # Different IP
            },
        ]

        engine.load_rmm_data(rmm_devices, RMMProvider.MANUAL)
        report = engine.compare_workstations(workstations)

        # Should match via fuzzy hostname (WS01 contained in WS-01, diff is 1 char)
        assert report.matched_count == 1
        match = report.matches[0]
        assert match.confidence in (MatchConfidence.LOW, MatchConfidence.MEDIUM)

    def test_mac_address_match(self, engine):
        """Test matching by MAC address."""
        rmm_devices = [
            RMMDevice(
                hostname="DIFFERENT_NAME",
                mac_address="00:1A:2B:3C:4D:5E",
                rmm_provider=RMMProvider.MANUAL,
                rmm_agent_id="1",
            ),
        ]

        workstations = [
            {
                "hostname": "MY_WORKSTATION",
                "mac_address": "00-1A-2B-3C-4D-5E",  # Same MAC, different format
            },
        ]

        engine.load_rmm_data(rmm_devices, RMMProvider.MANUAL)
        report = engine.compare_workstations(workstations)

        assert report.matched_count == 1
        assert "mac_address" in report.matches[0].matching_fields

    def test_discrepancy_detection(self, engine):
        """Test detection of data discrepancies."""
        rmm_devices = [
            RMMDevice(
                hostname="WORKSTATION01",
                ip_address="192.168.1.100",
                os_name="Windows 10",
                rmm_provider=RMMProvider.MANUAL,
                rmm_agent_id="1",
                rmm_online=True,
            ),
        ]

        workstations = [
            {
                "hostname": "WORKSTATION01",
                "ip_address": "192.168.1.200",  # Different IP
                "os_name": "Windows 10",
                "online": False,  # Different online status
            },
        ]

        engine.load_rmm_data(rmm_devices, RMMProvider.MANUAL)
        report = engine.compare_workstations(workstations)

        assert report.matched_count == 1
        match = report.matches[0]
        assert len(match.discrepancies) >= 1
        assert any("IP" in d for d in match.discrepancies)

    def test_coverage_rate_calculation(self, engine, sample_rmm_devices, sample_workstations):
        """Test coverage rate calculation."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations(sample_workstations)

        # 2 of 3 workstations matched
        assert report.coverage_rate == pytest.approx(66.7, rel=0.1)

    def test_deduplication_recommendations(self, engine, sample_rmm_devices, sample_workstations):
        """Test deduplication recommendations generation."""
        engine.load_rmm_data(sample_rmm_devices, RMMProvider.CONNECTWISE)
        report = engine.compare_workstations(sample_workstations)

        recommendations = engine.get_deduplication_recommendations(report)

        # Should have recommendations for missing devices
        assert len(recommendations) >= 1

        # Check recommendation structure
        for rec in recommendations:
            assert "priority" in rec
            assert "category" in rec
            assert "action" in rec
            assert rec["priority"] in ("high", "medium", "low")


class TestRMMProviderLoaders:
    """Tests for RMM-specific data loaders."""

    @pytest.fixture
    def engine(self):
        return RMMComparisonEngine()

    def test_load_from_connectwise(self, engine):
        """Test loading ConnectWise Automate data."""
        cw_data = [
            {
                "Id": 12345,
                "ComputerName": "WS01",
                "LocalIPAddress": "192.168.1.101",
                "MACAddress": "00:1A:2B:3C:4D:01",
                "OS": "Windows 10 Enterprise",
                "Status": 1,
                "LastContact": "2025-01-15T10:30:00Z",
            },
            {
                "Id": 12346,
                "ComputerName": "WS02",
                "LocalIPAddress": "192.168.1.102",
                "Status": 0,
            },
        ]

        count = engine.load_from_connectwise(cw_data)

        assert count == 2
        assert engine._rmm_provider == RMMProvider.CONNECTWISE
        assert engine._rmm_devices[0].hostname == "WS01"
        assert engine._rmm_devices[0].rmm_online is True
        assert engine._rmm_devices[1].rmm_online is False

    def test_load_from_datto(self, engine):
        """Test loading Datto RMM data."""
        datto_data = [
            {
                "uid": "ABC123",
                "hostname": "WS01",
                "intIpAddress": "192.168.1.101",
                "online": True,
                "operatingSystem": "Windows 10",
            },
        ]

        count = engine.load_from_datto(datto_data)

        assert count == 1
        assert engine._rmm_provider == RMMProvider.DATTO
        assert engine._rmm_devices[0].rmm_agent_id == "ABC123"

    def test_load_from_ninja(self, engine):
        """Test loading NinjaRMM data."""
        ninja_data = [
            {
                "id": 789,
                "systemName": "WS01",
                "ipAddress": "192.168.1.101",
                "offline": False,
                "system": {
                    "name": "Windows 10 Enterprise",
                    "manufacturer": "Dell",
                    "model": "OptiPlex 7080",
                },
            },
        ]

        count = engine.load_from_ninja(ninja_data)

        assert count == 1
        assert engine._rmm_provider == RMMProvider.NINJA
        assert engine._rmm_devices[0].manufacturer == "Dell"
        assert engine._rmm_devices[0].rmm_online is True


class TestCompareWithRMMFunction:
    """Tests for the convenience function."""

    @pytest.mark.asyncio
    async def test_compare_with_rmm(self):
        """Test the async convenience function."""
        workstations = [
            {"hostname": "WS01", "ip_address": "192.168.1.101"},
            {"hostname": "WS02", "ip_address": "192.168.1.102"},
        ]

        rmm_data = [
            {"hostname": "WS01", "ip_address": "192.168.1.101"},
            {"hostname": "WS03", "ip_address": "192.168.1.103"},
        ]

        result = await compare_with_rmm(workstations, rmm_data, "manual")

        assert "summary" in result
        assert result["summary"]["our_device_count"] == 2
        assert result["summary"]["rmm_device_count"] == 2
        # WS01 matches (hostname + IP), WS02 has no match
        assert result["summary"]["matched_count"] == 1


class TestComparisonReport:
    """Tests for ComparisonReport dataclass."""

    def test_report_to_dict(self):
        """Test report conversion to dictionary."""
        report = ComparisonReport(
            our_device_count=10,
            rmm_device_count=12,
            matched_count=8,
            exact_match_count=6,
            matches=[],
            gaps=[],
            coverage_rate=80.0,
            accuracy_rate=75.0,
            rmm_provider=RMMProvider.CONNECTWISE,
            comparison_timestamp=datetime.now(timezone.utc),
        )

        result = report.to_dict()

        assert result["summary"]["our_device_count"] == 10
        assert result["summary"]["coverage_rate"] == 80.0
        assert result["metadata"]["rmm_provider"] == "connectwise"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def engine(self):
        return RMMComparisonEngine()

    def test_empty_workstations(self, engine):
        """Test with empty workstation list."""
        rmm_devices = [
            RMMDevice(hostname="WS01", rmm_provider=RMMProvider.MANUAL, rmm_agent_id="1"),
        ]
        engine.load_rmm_data(rmm_devices)

        report = engine.compare_workstations([])

        assert report.our_device_count == 0
        assert report.coverage_rate == 0.0
        # All RMM devices should be reported as missing from AD
        assert len(report.gaps) == 1

    def test_workstations_missing_fields(self, engine):
        """Test with workstations missing optional fields."""
        rmm_devices = [
            RMMDevice(hostname="WS01", rmm_provider=RMMProvider.MANUAL, rmm_agent_id="1"),
        ]
        engine.load_rmm_data(rmm_devices)

        workstations = [
            {"hostname": "WS01"},  # Only hostname, no IP, MAC, OS
        ]

        report = engine.compare_workstations(workstations)

        assert report.matched_count == 1

    def test_special_characters_in_hostname(self, engine):
        """Test with special characters in hostnames."""
        rmm_devices = [
            RMMDevice(
                hostname="WS-01_TEST",
                ip_address="192.168.1.100",
                rmm_provider=RMMProvider.MANUAL,
                rmm_agent_id="1",
            ),
        ]
        engine.load_rmm_data(rmm_devices)

        workstations = [
            {"hostname": "WS-01_TEST", "ip_address": "192.168.1.100"},
        ]

        report = engine.compare_workstations(workstations)

        assert report.matched_count == 1
        # hostname_exact (0.35) + ip_address (0.30) = 0.65 -> HIGH confidence
        assert report.matches[0].confidence == MatchConfidence.HIGH

    def test_unicode_in_data(self, engine):
        """Test with unicode characters in data."""
        rmm_devices = [
            RMMDevice(
                hostname="WS01",
                os_name="Windows 10 — Enterprise",  # em dash
                rmm_provider=RMMProvider.MANUAL,
                rmm_agent_id="1",
            ),
        ]
        engine.load_rmm_data(rmm_devices)

        workstations = [
            {"hostname": "WS01", "os_name": "Windows 10 Enterprise"},
        ]

        report = engine.compare_workstations(workstations)
        assert report.matched_count == 1
