"""
Tests for NTP time synchronization verification.
"""

import pytest
from unittest.mock import patch, MagicMock
from compliance_agent.time_sync import NTPVerifier, _query_ntp_server


def _make_ntp_result(server: str, offset_ms: float, rtt_ms: float = 10.0):
    """Helper to create a mock NTP result."""
    return {
        "server": server,
        "offset_ms": offset_ms,
        "rtt_ms": rtt_ms,
        "ntp_time": "2026-02-06T12:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_verify_time_all_sources_agree():
    """Test verification passes when all sources agree."""
    verifier = NTPVerifier(max_skew_ms=5000)

    mock_results = [
        _make_ntp_result("time.nist.gov", 2.5),
        _make_ntp_result("time.cloudflare.com", 3.1),
        _make_ntp_result("pool.ntp.org", 1.8),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    assert result["verified"] is True
    assert result["source_count"] == 3
    assert result["error"] is None
    assert abs(result["offset_ms"] - 2.5) < 0.1  # median of [1.8, 2.5, 3.1]


@pytest.mark.asyncio
async def test_verify_time_clock_drift():
    """Test verification fails when clock is drifted."""
    verifier = NTPVerifier(max_skew_ms=100)

    mock_results = [
        _make_ntp_result("time.nist.gov", 5200.0),
        _make_ntp_result("time.cloudflare.com", 5150.0),
        _make_ntp_result("pool.ntp.org", 5300.0),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    assert result["verified"] is False
    assert "exceeds max skew" in result["error"]


@pytest.mark.asyncio
async def test_verify_time_insufficient_sources():
    """Test verification fails with too few sources."""
    verifier = NTPVerifier(min_sources=2)

    mock_results = [
        _make_ntp_result("time.nist.gov", 2.0),
        None,  # Failed
        None,  # Failed
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    assert result["verified"] is False
    assert result["source_count"] == 1
    assert "Only 1 of 2" in result["error"]


@pytest.mark.asyncio
async def test_verify_time_all_sources_fail():
    """Test verification fails when all sources fail."""
    verifier = NTPVerifier()

    with patch("compliance_agent.time_sync._query_ntp_server", return_value=None):
        result = await verifier.verify_time()

    assert result["verified"] is False
    assert result["source_count"] == 0


@pytest.mark.asyncio
async def test_verify_time_negative_offset():
    """Test verification handles negative offsets (clock ahead)."""
    verifier = NTPVerifier(max_skew_ms=5000)

    mock_results = [
        _make_ntp_result("time.nist.gov", -50.0),
        _make_ntp_result("time.cloudflare.com", -48.0),
        _make_ntp_result("pool.ntp.org", -52.0),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    assert result["verified"] is True
    assert result["offset_ms"] < 0


@pytest.mark.asyncio
async def test_verify_time_result_format():
    """Test result dict has all required fields for ntp_verification."""
    verifier = NTPVerifier(max_skew_ms=5000)

    mock_results = [
        _make_ntp_result("time.nist.gov", 1.0),
        _make_ntp_result("time.cloudflare.com", 2.0),
        _make_ntp_result("pool.ntp.org", 3.0),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    # All required fields present
    assert "verified" in result
    assert "offset_ms" in result
    assert "max_skew_ms" in result
    assert "sources" in result
    assert "source_count" in result
    assert "verified_at" in result
    assert "error" in result

    # Sources have correct structure
    for source in result["sources"]:
        assert "server" in source
        assert "offset_ms" in source
        assert "rtt_ms" in source


@pytest.mark.asyncio
async def test_verify_time_custom_servers():
    """Test custom NTP server list."""
    custom_servers = ["ntp1.example.com", "ntp2.example.com"]
    verifier = NTPVerifier(servers=custom_servers, min_sources=2)

    mock_results = [
        _make_ntp_result("ntp1.example.com", 1.0),
        _make_ntp_result("ntp2.example.com", 2.0),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    assert result["verified"] is True
    assert result["source_count"] == 2


@pytest.mark.asyncio
async def test_verify_time_even_source_count():
    """Test median calculation with even number of sources."""
    verifier = NTPVerifier(
        servers=["a", "b", "c", "d"],
        min_sources=2,
        max_skew_ms=5000,
    )

    mock_results = [
        _make_ntp_result("a", 10.0),
        _make_ntp_result("b", 20.0),
        _make_ntp_result("c", 30.0),
        _make_ntp_result("d", 40.0),
    ]

    with patch("compliance_agent.time_sync._query_ntp_server", side_effect=mock_results):
        result = await verifier.verify_time()

    # Median of [10, 20, 30, 40] = (20+30)/2 = 25
    assert result["offset_ms"] == 25.0


def test_query_ntp_server_timeout():
    """Test NTP query handles timeout gracefully."""
    with patch("socket.socket") as mock_socket:
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = TimeoutError()
        mock_socket.return_value = mock_sock

        result = _query_ntp_server("invalid.server", timeout=0.1)

    assert result is None
