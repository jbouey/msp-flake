"""
Tests for multi-source NTP time verification.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import socket

from compliance_agent.ntp_verify import (
    NTPVerifier,
    NTPVerificationResult,
    NTPServerResult,
    verify_time_for_evidence,
    get_verified_timestamp,
    DEFAULT_NTP_SERVERS,
    NTP_DELTA,
)


# =============================================================================
# NTPServerResult Tests
# =============================================================================


class TestNTPServerResult:
    """Tests for NTPServerResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = NTPServerResult(server="time.example.com")
        assert result.server == "time.example.com"
        assert result.offset_ms is None
        assert result.round_trip_ms is None
        assert result.stratum is None
        assert result.success is False
        assert result.error is None
        assert result.timestamp is None

    def test_successful_result(self):
        """Test successful NTP query result."""
        result = NTPServerResult(
            server="time.google.com",
            offset_ms=15.5,
            round_trip_ms=45.2,
            stratum=1,
            success=True,
            timestamp=datetime.now(timezone.utc)
        )
        assert result.success is True
        assert result.offset_ms == 15.5
        assert result.stratum == 1

    def test_failed_result(self):
        """Test failed NTP query result."""
        result = NTPServerResult(
            server="time.example.com",
            success=False,
            error="Timeout"
        )
        assert result.success is False
        assert result.error == "Timeout"


# =============================================================================
# NTPVerificationResult Tests
# =============================================================================


class TestNTPVerificationResult:
    """Tests for NTPVerificationResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = NTPVerificationResult()
        assert result.passed is False
        assert result.servers_queried == 0
        assert result.servers_responded == 0
        assert result.median_offset_ms is None
        assert result.max_skew_ms is None
        assert result.server_results == []

    def test_to_dict(self):
        """Test serialization to dictionary."""
        server_result = NTPServerResult(
            server="time.google.com",
            offset_ms=10.5,
            round_trip_ms=25.0,
            stratum=1,
            success=True
        )
        result = NTPVerificationResult(
            passed=True,
            servers_queried=5,
            servers_responded=4,
            median_offset_ms=12.5,
            max_skew_ms=50.0,
            min_stratum=1,
            server_results=[server_result]
        )

        d = result.to_dict()
        assert d["passed"] is True
        assert d["servers_queried"] == 5
        assert d["servers_responded"] == 4
        assert d["median_offset_ms"] == 12.5
        assert d["max_skew_ms"] == 50.0
        assert d["min_stratum"] == 1
        assert len(d["sources"]) == 1
        assert d["sources"][0]["server"] == "time.google.com"
        assert d["sources"][0]["offset_ms"] == 10.5
        assert "local_time" in d

    def test_to_dict_with_error(self):
        """Test serialization with error."""
        result = NTPVerificationResult(
            passed=False,
            error="Only 2 of 3 required servers responded"
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert d["error"] == "Only 2 of 3 required servers responded"


# =============================================================================
# NTPVerifier Tests
# =============================================================================


class TestNTPVerifier:
    """Tests for NTPVerifier class."""

    def test_default_servers(self):
        """Test default NTP servers are used."""
        verifier = NTPVerifier()
        assert len(verifier.servers) == len(DEFAULT_NTP_SERVERS)
        assert "time.google.com" in verifier.servers

    def test_custom_servers(self):
        """Test custom NTP servers."""
        servers = ["ntp1.example.com", "ntp2.example.com", "ntp3.example.com"]
        verifier = NTPVerifier(servers=servers)
        assert verifier.servers == servers

    def test_default_thresholds(self):
        """Test default threshold values."""
        verifier = NTPVerifier()
        assert verifier.min_servers == 3
        assert verifier.max_offset_ms == 5000
        assert verifier.max_skew_ms == 5000
        assert verifier.timeout == 5.0

    def test_custom_thresholds(self):
        """Test custom threshold values."""
        verifier = NTPVerifier(
            min_servers=2,
            max_offset_ms=1000,
            max_skew_ms=2000,
            timeout_seconds=3.0
        )
        assert verifier.min_servers == 2
        assert verifier.max_offset_ms == 1000
        assert verifier.max_skew_ms == 2000
        assert verifier.timeout == 3.0


class TestNTPVerifierVerify:
    """Tests for NTPVerifier.verify() method."""

    @pytest.fixture
    def mock_query_server(self):
        """Fixture to mock _query_server."""
        with patch.object(NTPVerifier, '_query_server', new_callable=AsyncMock) as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_verify_all_success(self, mock_query_server):
        """Test verification with all servers responding successfully."""
        # Mock successful responses with similar offsets
        mock_query_server.side_effect = [
            NTPServerResult(server="s1", offset_ms=10, stratum=1, success=True),
            NTPServerResult(server="s2", offset_ms=15, stratum=2, success=True),
            NTPServerResult(server="s3", offset_ms=12, stratum=1, success=True),
        ]

        verifier = NTPVerifier(
            servers=["s1", "s2", "s3"],
            min_servers=3,
            max_offset_ms=5000,
            max_skew_ms=5000
        )
        result = await verifier.verify()

        assert result.passed is True
        assert result.servers_queried == 3
        assert result.servers_responded == 3
        assert result.median_offset_ms == 12  # median of [10, 12, 15]
        assert result.max_skew_ms == 5  # 15 - 10
        assert result.min_stratum == 1

    @pytest.mark.asyncio
    async def test_verify_not_enough_servers(self, mock_query_server):
        """Test verification fails when not enough servers respond."""
        mock_query_server.side_effect = [
            NTPServerResult(server="s1", offset_ms=10, stratum=1, success=True),
            NTPServerResult(server="s2", success=False, error="Timeout"),
            NTPServerResult(server="s3", success=False, error="DNS error"),
        ]

        verifier = NTPVerifier(
            servers=["s1", "s2", "s3"],
            min_servers=3
        )
        result = await verifier.verify()

        assert result.passed is False
        assert result.servers_responded == 1
        assert "1 of 3 required servers responded" in result.error

    @pytest.mark.asyncio
    async def test_verify_offset_too_large(self, mock_query_server):
        """Test verification fails when offset exceeds threshold."""
        mock_query_server.side_effect = [
            NTPServerResult(server="s1", offset_ms=6000, stratum=1, success=True),
            NTPServerResult(server="s2", offset_ms=6100, stratum=1, success=True),
            NTPServerResult(server="s3", offset_ms=6050, stratum=1, success=True),
        ]

        verifier = NTPVerifier(
            servers=["s1", "s2", "s3"],
            min_servers=3,
            max_offset_ms=5000
        )
        result = await verifier.verify()

        assert result.passed is False
        assert "exceeds threshold" in result.error
        assert "6050" in result.error  # median offset

    @pytest.mark.asyncio
    async def test_verify_skew_too_large(self, mock_query_server):
        """Test verification fails when source skew exceeds threshold."""
        mock_query_server.side_effect = [
            NTPServerResult(server="s1", offset_ms=100, stratum=1, success=True),
            NTPServerResult(server="s2", offset_ms=6000, stratum=1, success=True),  # Way off
            NTPServerResult(server="s3", offset_ms=150, stratum=1, success=True),
        ]

        verifier = NTPVerifier(
            servers=["s1", "s2", "s3"],
            min_servers=3,
            max_offset_ms=10000,
            max_skew_ms=5000  # 6000 - 100 = 5900 > 5000
        )
        result = await verifier.verify()

        assert result.passed is False
        assert "skew" in result.error.lower()

    @pytest.mark.asyncio
    async def test_verify_partial_success(self, mock_query_server):
        """Test verification passes with partial server responses."""
        mock_query_server.side_effect = [
            NTPServerResult(server="s1", offset_ms=10, stratum=1, success=True),
            NTPServerResult(server="s2", success=False, error="Timeout"),
            NTPServerResult(server="s3", offset_ms=15, stratum=2, success=True),
            NTPServerResult(server="s4", offset_ms=12, stratum=1, success=True),
        ]

        verifier = NTPVerifier(
            servers=["s1", "s2", "s3", "s4"],
            min_servers=3,
            max_offset_ms=5000,
            max_skew_ms=5000
        )
        result = await verifier.verify()

        assert result.passed is True
        assert result.servers_queried == 4
        assert result.servers_responded == 3


class TestNTPVerifierQueryServer:
    """Tests for NTPVerifier._query_server() method."""

    @pytest.mark.asyncio
    async def test_query_server_timeout(self):
        """Test server query handles timeout gracefully."""
        verifier = NTPVerifier(
            servers=["nonexistent.invalid"],
            timeout_seconds=0.1
        )
        result = await verifier._query_server("nonexistent.invalid")

        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_query_server_dns_failure(self):
        """Test server query handles DNS failure."""
        verifier = NTPVerifier(timeout_seconds=0.5)
        result = await verifier._query_server("definitely-not-a-real-domain-12345.invalid")

        assert result.success is False
        assert result.error is not None


# =============================================================================
# Integration Tests (Live NTP - skip if no network)
# =============================================================================


class TestNTPVerifierLive:
    """Live integration tests for NTP verification."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not socket.gethostname(),
        reason="No network connectivity"
    )
    async def test_live_ntp_query(self):
        """Test live NTP query to time.google.com."""
        verifier = NTPVerifier(timeout_seconds=5.0)

        # Query a known reliable server
        result = await verifier._query_server("time.google.com")

        # May fail if no network, but should return valid structure
        if result.success:
            assert result.offset_ms is not None
            assert result.round_trip_ms is not None
            assert result.stratum is not None
            assert result.stratum <= 15  # Valid stratum range
            assert abs(result.offset_ms) < 60000  # Within 60 seconds

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not socket.gethostname(),
        reason="No network connectivity"
    )
    async def test_live_verification(self):
        """Test live multi-source verification."""
        # Use only reliable servers for live test
        verifier = NTPVerifier(
            servers=["time.google.com", "time.cloudflare.com", "time.apple.com"],
            min_servers=2,  # Lower threshold for test reliability
            timeout_seconds=5.0
        )

        result = await verifier.verify()

        # Should get at least 2 responses from these reliable servers
        if result.servers_responded >= 2:
            assert result.median_offset_ms is not None
            # If clock is reasonably synced, should pass
            # Don't assert passed=True since clock may be off


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @pytest.mark.asyncio
    async def test_verify_time_for_evidence(self):
        """Test verify_time_for_evidence function."""
        with patch.object(NTPVerifier, 'verify', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = NTPVerificationResult(
                passed=True,
                servers_queried=5,
                servers_responded=4,
                median_offset_ms=10.0
            )

            result = await verify_time_for_evidence(
                max_offset_ms=5000,
                max_skew_ms=5000,
                min_servers=3
            )

            assert result.passed is True
            mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_verified_timestamp_success(self):
        """Test get_verified_timestamp with successful verification."""
        with patch.object(NTPVerifier, 'verify', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = NTPVerificationResult(
                passed=True,
                servers_responded=4,
                median_offset_ms=10.0
            )

            timestamp, verification = await get_verified_timestamp()

            assert timestamp is not None
            assert verification is not None
            assert verification["passed"] is True

    @pytest.mark.asyncio
    async def test_get_verified_timestamp_failure(self):
        """Test get_verified_timestamp with failed verification."""
        with patch.object(NTPVerifier, 'verify', new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = NTPVerificationResult(
                passed=False,
                error="Not enough servers"
            )

            timestamp, verification = await get_verified_timestamp()

            assert timestamp is not None
            assert verification is None  # Returns None on failure


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_server_list(self):
        """Test with empty server list."""
        verifier = NTPVerifier(servers=[], min_servers=1)
        result = await verifier.verify()

        assert result.passed is False
        assert result.servers_queried == 0
        assert result.servers_responded == 0

    @pytest.mark.asyncio
    async def test_negative_offset(self):
        """Test handling of negative offsets (local clock ahead)."""
        with patch.object(NTPVerifier, '_query_server', new_callable=AsyncMock) as mock:
            mock.side_effect = [
                NTPServerResult(server="s1", offset_ms=-100, stratum=1, success=True),
                NTPServerResult(server="s2", offset_ms=-150, stratum=1, success=True),
                NTPServerResult(server="s3", offset_ms=-120, stratum=1, success=True),
            ]

            verifier = NTPVerifier(servers=["s1", "s2", "s3"], min_servers=3)
            result = await verifier.verify()

            assert result.passed is True
            assert result.median_offset_ms == -120
            assert result.max_skew_ms == 50  # -100 - (-150) = 50

    @pytest.mark.asyncio
    async def test_exception_in_query(self):
        """Test handling of exceptions during server query."""
        with patch.object(NTPVerifier, '_query_server', new_callable=AsyncMock) as mock:
            mock.side_effect = [
                Exception("Unexpected error"),
                NTPServerResult(server="s2", offset_ms=10, stratum=1, success=True),
                NTPServerResult(server="s3", offset_ms=15, stratum=1, success=True),
                NTPServerResult(server="s4", offset_ms=12, stratum=1, success=True),
            ]

            verifier = NTPVerifier(servers=["s1", "s2", "s3", "s4"], min_servers=3)
            result = await verifier.verify()

            # Should still pass with 3 successful responses
            assert result.passed is True
            assert result.servers_responded == 3
            # First server should show as failed
            assert result.server_results[0].success is False
            assert "Unexpected error" in result.server_results[0].error
