"""
Multi-source NTP time verification.

Queries multiple NTP servers to verify timestamp integrity before evidence signing.
Ensures HIPAA-compliant audit trail timestamps are accurate and verifiable.

HIPAA Controls:
- §164.312(b) - Audit Controls (accurate timestamps)
- §164.312(c)(1) - Integrity Controls (tamper-evident records)

Usage:
    verifier = NTPVerifier(config)
    result = await verifier.verify()

    if result.passed:
        # Proceed with evidence signing
        bundle.ntp_verification = result.to_dict()
    else:
        # Alert on time sync failure
        await send_time_sync_alert(result)
"""

import asyncio
import socket
import struct
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from statistics import median, stdev

logger = logging.getLogger(__name__)


# Default NTP servers (NIST, Google, Cloudflare, pool.ntp.org)
DEFAULT_NTP_SERVERS = [
    "time.nist.gov",
    "time.google.com",
    "time.cloudflare.com",
    "pool.ntp.org",
    "time.apple.com",
]

# NTP packet format (48 bytes)
# LI=0, VN=3, Mode=3 (client), Stratum=0, Poll=0, Precision=0
NTP_PACKET = b'\x1b' + 47 * b'\0'

# NTP epoch is 1900-01-01, Unix epoch is 1970-01-01
NTP_DELTA = 2208988800


@dataclass
class NTPServerResult:
    """Result from a single NTP server query."""
    server: str
    offset_ms: Optional[float] = None
    round_trip_ms: Optional[float] = None
    stratum: Optional[int] = None
    success: bool = False
    error: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class NTPVerificationResult:
    """Result of multi-source NTP verification."""
    passed: bool = False
    local_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    servers_queried: int = 0
    servers_responded: int = 0
    median_offset_ms: Optional[float] = None
    max_skew_ms: Optional[float] = None
    min_stratum: Optional[int] = None
    server_results: List[NTPServerResult] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage in evidence bundles."""
        return {
            "passed": self.passed,
            "local_time": self.local_time.isoformat(),
            "servers_queried": self.servers_queried,
            "servers_responded": self.servers_responded,
            "median_offset_ms": self.median_offset_ms,
            "max_skew_ms": self.max_skew_ms,
            "min_stratum": self.min_stratum,
            "error": self.error,
            "sources": [
                {
                    "server": r.server,
                    "offset_ms": r.offset_ms,
                    "round_trip_ms": r.round_trip_ms,
                    "stratum": r.stratum,
                    "success": r.success,
                    "error": r.error,
                }
                for r in self.server_results
            ]
        }


class NTPVerifier:
    """
    Multi-source NTP time verifier.

    Queries multiple NTP servers and verifies:
    1. At least 3 servers respond successfully
    2. Median offset from local clock is within threshold
    3. Skew between sources is within 5 seconds
    """

    def __init__(
        self,
        servers: Optional[List[str]] = None,
        min_servers: int = 3,
        max_offset_ms: int = 5000,
        max_skew_ms: int = 5000,
        timeout_seconds: float = 5.0
    ):
        """
        Initialize NTP verifier.

        Args:
            servers: List of NTP server hostnames (default: NIST, Google, Cloudflare, etc.)
            min_servers: Minimum servers that must respond (default: 3)
            max_offset_ms: Maximum acceptable offset from NTP time (default: 5000ms)
            max_skew_ms: Maximum acceptable skew between NTP sources (default: 5000ms)
            timeout_seconds: Timeout per NTP query (default: 5s)
        """
        self.servers = servers if servers is not None else DEFAULT_NTP_SERVERS.copy()
        self.min_servers = min_servers
        self.max_offset_ms = max_offset_ms
        self.max_skew_ms = max_skew_ms
        self.timeout = timeout_seconds

    async def verify(self) -> NTPVerificationResult:
        """
        Perform multi-source NTP verification.

        Returns:
            NTPVerificationResult with pass/fail status and details
        """
        result = NTPVerificationResult(
            local_time=datetime.now(timezone.utc),
            servers_queried=len(self.servers)
        )

        # Query all servers concurrently
        tasks = [self._query_server(server) for server in self.servers]
        server_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        successful_results = []
        for i, sr in enumerate(server_results):
            if isinstance(sr, Exception):
                result.server_results.append(NTPServerResult(
                    server=self.servers[i],
                    success=False,
                    error=str(sr)
                ))
            else:
                result.server_results.append(sr)
                if sr.success:
                    successful_results.append(sr)

        result.servers_responded = len(successful_results)

        # Check if enough servers responded
        if result.servers_responded < self.min_servers:
            result.passed = False
            result.error = f"Only {result.servers_responded} of {self.min_servers} required servers responded"
            logger.warning(f"NTP verification failed: {result.error}")
            return result

        # Calculate offsets
        offsets = [r.offset_ms for r in successful_results if r.offset_ms is not None]

        if not offsets:
            result.passed = False
            result.error = "No valid offsets received from NTP servers"
            logger.warning(f"NTP verification failed: {result.error}")
            return result

        # Calculate statistics
        result.median_offset_ms = median(offsets)
        result.max_skew_ms = max(offsets) - min(offsets)

        # Find minimum stratum (most authoritative source)
        strata = [r.stratum for r in successful_results if r.stratum is not None]
        if strata:
            result.min_stratum = min(strata)

        # Validate offset within threshold
        if abs(result.median_offset_ms) > self.max_offset_ms:
            result.passed = False
            result.error = f"Local clock offset {result.median_offset_ms:.1f}ms exceeds threshold {self.max_offset_ms}ms"
            logger.warning(f"NTP verification failed: {result.error}")
            return result

        # Validate skew between sources
        if result.max_skew_ms > self.max_skew_ms:
            result.passed = False
            result.error = f"NTP source skew {result.max_skew_ms:.1f}ms exceeds threshold {self.max_skew_ms}ms"
            logger.warning(f"NTP verification failed: {result.error}")
            return result

        # All checks passed
        result.passed = True
        logger.info(
            f"NTP verification passed: {result.servers_responded} servers, "
            f"median offset {result.median_offset_ms:.1f}ms, "
            f"max skew {result.max_skew_ms:.1f}ms"
        )

        return result

    async def _query_server(self, server: str) -> NTPServerResult:
        """
        Query a single NTP server.

        Uses raw socket NTP protocol (RFC 5905) for portability.

        Args:
            server: NTP server hostname

        Returns:
            NTPServerResult with offset or error
        """
        result = NTPServerResult(server=server)

        try:
            # Resolve hostname
            loop = asyncio.get_event_loop()
            addr_info = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(server, 123, socket.AF_INET, socket.SOCK_DGRAM)
            )
            if not addr_info:
                result.error = "DNS resolution failed"
                return result

            addr = addr_info[0][4]

            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout)
            sock.setblocking(False)

            try:
                # Record send time
                t1 = time.time()

                # Send NTP request
                await loop.sock_sendto(sock, NTP_PACKET, addr)

                # Receive response
                data, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 48),
                    timeout=self.timeout
                )

                # Record receive time
                t4 = time.time()

                if len(data) < 48:
                    result.error = "Incomplete NTP response"
                    return result

                # Parse NTP response
                # Byte 0: LI (2 bits) | VN (3 bits) | Mode (3 bits)
                # Byte 1: Stratum
                # Bytes 40-47: Transmit Timestamp
                result.stratum = data[1]

                # Extract transmit timestamp (bytes 40-47)
                # NTP timestamps are 64-bit: 32-bit seconds + 32-bit fraction
                ntp_time = struct.unpack('!II', data[40:48])
                t3 = ntp_time[0] + ntp_time[1] / (2**32) - NTP_DELTA

                # Calculate offset using simple formula
                # offset = ((t2 - t1) + (t3 - t4)) / 2
                # Simplified: server_time - client_time
                result.round_trip_ms = (t4 - t1) * 1000
                result.offset_ms = (t3 - t4) * 1000  # Positive = local clock is behind
                result.timestamp = datetime.fromtimestamp(t3, tz=timezone.utc)
                result.success = True

                logger.debug(
                    f"NTP {server}: offset={result.offset_ms:.1f}ms, "
                    f"rtt={result.round_trip_ms:.1f}ms, stratum={result.stratum}"
                )

            finally:
                sock.close()

        except asyncio.TimeoutError:
            result.error = "Timeout"
            logger.debug(f"NTP {server}: timeout after {self.timeout}s")
        except socket.gaierror as e:
            result.error = f"DNS error: {e}"
            logger.debug(f"NTP {server}: DNS error: {e}")
        except Exception as e:
            result.error = str(e)
            logger.debug(f"NTP {server}: error: {e}")

        return result


async def verify_time_for_evidence(
    max_offset_ms: int = 5000,
    max_skew_ms: int = 5000,
    min_servers: int = 3
) -> NTPVerificationResult:
    """
    Convenience function to verify time before evidence signing.

    Args:
        max_offset_ms: Maximum acceptable offset (default: 5000ms = 5s)
        max_skew_ms: Maximum acceptable skew between sources (default: 5000ms = 5s)
        min_servers: Minimum servers that must respond (default: 3)

    Returns:
        NTPVerificationResult
    """
    verifier = NTPVerifier(
        max_offset_ms=max_offset_ms,
        max_skew_ms=max_skew_ms,
        min_servers=min_servers
    )
    return await verifier.verify()


async def get_verified_timestamp() -> tuple[datetime, Optional[Dict[str, Any]]]:
    """
    Get current timestamp with NTP verification.

    Returns:
        Tuple of (timestamp, ntp_verification_dict or None if failed)
    """
    result = await verify_time_for_evidence()
    timestamp = datetime.now(timezone.utc)

    if result.passed:
        return timestamp, result.to_dict()
    else:
        logger.warning(f"Time verification failed: {result.error}")
        return timestamp, None
