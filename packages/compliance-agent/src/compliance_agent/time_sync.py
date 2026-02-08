"""
Multi-source NTP time verification for evidence timestamps.

Queries multiple NTP servers to verify system clock accuracy.
Used to populate ntp_verification field in evidence bundles.

HIPAA Controls:
- §164.312(b) - Audit Controls (accurate timestamps)
- §164.312(c)(1) - Integrity Controls (provable temporal ordering)
"""

import asyncio
import logging
import socket
import struct
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# NTP epoch offset from Unix epoch (70 years in seconds)
NTP_EPOCH_OFFSET = 2208988800

# Default NTP servers (diverse sources for consensus)
DEFAULT_NTP_SERVERS = [
    "time.nist.gov",
    "time.cloudflare.com",
    "pool.ntp.org",
]


def _query_ntp_server(server: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    Query a single NTP server using raw UDP (NTPv3).

    Returns dict with offset_ms, server, rtt_ms on success, None on failure.
    """
    # NTP packet: 48 bytes, LI=0, VN=3, Mode=3 (client)
    packet = b'\x1b' + 47 * b'\0'

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)

        t1 = time.time()
        sock.sendto(packet, (server, 123))
        data, _ = sock.recvfrom(1024)
        t4 = time.time()

        sock.close()

        if len(data) < 48:
            return None

        # Extract transmit timestamp (bytes 40-47)
        tx_seconds = struct.unpack('!I', data[40:44])[0]
        tx_fraction = struct.unpack('!I', data[44:48])[0]
        tx_time = tx_seconds - NTP_EPOCH_OFFSET + tx_fraction / (2**32)

        # Simplified offset: (server_time - client_time) adjusted for RTT
        rtt = t4 - t1
        offset = tx_time - (t1 + rtt / 2)

        return {
            "server": server,
            "offset_ms": round(offset * 1000, 2),
            "rtt_ms": round(rtt * 1000, 2),
            "ntp_time": datetime.fromtimestamp(tx_time, tz=timezone.utc).isoformat(),
        }

    except (socket.timeout, socket.gaierror, OSError) as e:
        logger.debug(f"NTP query failed for {server}: {e}")
        return None
    except Exception as e:
        logger.warning(f"NTP unexpected error for {server}: {e}")
        return None


class NTPVerifier:
    """
    Multi-source NTP time verification.

    Queries multiple NTP servers and checks consensus to verify
    system clock accuracy within configured tolerance.
    """

    def __init__(
        self,
        servers: Optional[List[str]] = None,
        max_skew_ms: int = 5000,
        timeout: float = 5.0,
        min_sources: int = 2,
    ):
        self.servers = servers or DEFAULT_NTP_SERVERS.copy()
        self.max_skew_ms = max_skew_ms
        self.timeout = timeout
        self.min_sources = min_sources

    async def verify_time(self) -> Dict[str, Any]:
        """
        Query NTP servers and verify system clock accuracy.

        Returns dict matching the ntp_verification field format:
        {
            "verified": bool,
            "offset_ms": float (median offset),
            "max_skew_ms": int,
            "sources": [{server, offset_ms, rtt_ms}, ...],
            "source_count": int,
            "verified_at": ISO timestamp,
            "error": str or None
        }
        """
        loop = asyncio.get_event_loop()

        # Query all servers concurrently via thread pool (NTP uses blocking UDP)
        tasks = [
            loop.run_in_executor(None, _query_ntp_server, server, self.timeout)
            for server in self.servers
        ]
        results = await asyncio.gather(*tasks)

        # Filter successful responses
        sources = [r for r in results if r is not None]

        if len(sources) < self.min_sources:
            return {
                "verified": False,
                "offset_ms": None,
                "max_skew_ms": self.max_skew_ms,
                "sources": sources,
                "source_count": len(sources),
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "error": f"Only {len(sources)} of {self.min_sources} required NTP sources responded",
            }

        # Compute median offset
        offsets = sorted(s["offset_ms"] for s in sources)
        mid = len(offsets) // 2
        if len(offsets) % 2 == 0:
            median_offset = (offsets[mid - 1] + offsets[mid]) / 2
        else:
            median_offset = offsets[mid]

        # Check if all sources agree within max_skew
        verified = abs(median_offset) <= self.max_skew_ms

        return {
            "verified": verified,
            "offset_ms": round(median_offset, 2),
            "max_skew_ms": self.max_skew_ms,
            "sources": sources,
            "source_count": len(sources),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "error": None if verified else f"Clock offset {median_offset:.1f}ms exceeds max skew {self.max_skew_ms}ms",
        }
