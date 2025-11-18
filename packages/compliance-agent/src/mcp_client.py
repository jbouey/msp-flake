"""
MCP Client - Pull-Only Communication with mTLS

This client implements the pull-only communication pattern:
- NO listening sockets (agent never accepts inbound connections)
- Outbound HTTPS only with mutual TLS (mTLS)
- Polling-based (agent initiates all communication)
- Graceful degradation when MCP unavailable

Security:
- mTLS with client certificates for authentication
- Certificate pinning via CA certificate
- Hostname verification enforced
- Timeout handling to prevent hangs

API Endpoints:
- GET /api/v1/orders - Poll for orders
- POST /api/v1/evidence - Push evidence bundles
- GET /api/v1/health - Health check
"""

import aiohttp
import ssl
import json
import logging
from typing import Optional, List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)


class MCPClient:
    """
    MCP client with mTLS support

    Pull-only architecture - agent initiates all connections.
    """

    def __init__(
        self,
        base_url: str,
        cert_file: str,
        key_file: str,
        ca_file: str,
        timeout: int = 30
    ):
        """
        Initialize MCP client

        Args:
            base_url: MCP server base URL (e.g., https://mcp.example.com)
            cert_file: Path to client certificate (PEM format)
            key_file: Path to client private key (PEM format)
            ca_file: Path to CA certificate for server verification
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

        logger.info(f"Initializing MCP client for {self.base_url}")

        # Verify certificate files exist
        for path_str, name in [
            (cert_file, "client certificate"),
            (key_file, "client key"),
            (ca_file, "CA certificate")
        ]:
            path = Path(path_str)
            if not path.exists():
                raise FileNotFoundError(f"{name} not found: {path_str}")

        # Configure mTLS
        self.ssl_context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=ca_file
        )

        # Load client certificate and key
        self.ssl_context.load_cert_chain(
            certfile=cert_file,
            keyfile=key_file
        )

        # Security settings
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

        # Enforce TLS 1.2+ only (no TLS 1.0/1.1)
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        logger.info("✓ mTLS configured")

        # Session (created on first use)
        self.session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self):
        """Create session if needed"""
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=10,  # Max 10 concurrent connections
                ttl_dns_cache=300  # Cache DNS for 5 minutes
            )

            # Create session with timeout
            timeout = aiohttp.ClientTimeout(total=self.timeout)

            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'MSP-Compliance-Agent/1.0',
                    'Content-Type': 'application/json'
                }
            )

    async def poll_orders(self, site_id: str) -> List[Dict]:
        """
        Poll MCP for new orders

        Args:
            site_id: Site identifier for filtering orders

        Returns:
            List of orders for this site

        Raises:
            aiohttp.ClientError: If MCP unreachable or returns error
        """
        await self._ensure_session()

        url = f"{self.base_url}/api/v1/orders"
        params = {'site_id': site_id}

        logger.debug(f"Polling orders from {url}")

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    orders = await resp.json()
                    logger.debug(f"Received {len(orders)} orders")
                    return orders

                elif resp.status == 404:
                    # No orders available
                    logger.debug("No orders available (404)")
                    return []

                else:
                    # Unexpected status
                    error_text = await resp.text()
                    logger.warning(f"MCP returned {resp.status}: {error_text}")
                    raise aiohttp.ClientError(f"MCP error: {resp.status}")

        except aiohttp.ClientError as e:
            logger.error(f"Failed to poll orders: {e}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error polling orders: {e}")
            raise aiohttp.ClientError(str(e))

    async def push_evidence(self, evidence: Dict) -> bool:
        """
        Push evidence bundle to MCP

        Args:
            evidence: Evidence bundle dictionary

        Returns:
            True if evidence accepted, False otherwise

        Raises:
            aiohttp.ClientError: If MCP unreachable
        """
        await self._ensure_session()

        url = f"{self.base_url}/api/v1/evidence"

        logger.debug(f"Pushing evidence bundle {evidence.get('id', 'unknown')}")

        try:
            async with self.session.post(url, json=evidence) as resp:
                if resp.status == 200:
                    logger.info(f"Evidence {evidence.get('id')} accepted by MCP")
                    return True

                elif resp.status == 202:
                    # Accepted but not yet processed
                    logger.info(f"Evidence {evidence.get('id')} queued by MCP")
                    return True

                else:
                    error_text = await resp.text()
                    logger.warning(f"MCP rejected evidence: {resp.status} - {error_text}")
                    return False

        except aiohttp.ClientError as e:
            logger.error(f"Failed to push evidence: {e}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error pushing evidence: {e}")
            raise aiohttp.ClientError(str(e))

    async def health_check(self) -> bool:
        """
        Check if MCP server is reachable

        Returns:
            True if MCP is healthy, False otherwise
        """
        await self._ensure_session()

        url = f"{self.base_url}/api/v1/health"

        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    health_data = await resp.json()
                    logger.debug(f"MCP health: {health_data.get('status', 'unknown')}")
                    return health_data.get('status') == 'healthy'
                else:
                    logger.warning(f"MCP health check failed: {resp.status}")
                    return False

        except aiohttp.ClientError:
            logger.warning("MCP health check failed (network error)")
            return False

        except Exception as e:
            logger.warning(f"MCP health check failed: {e}")
            return False

    async def get_config(self, site_id: str) -> Optional[Dict]:
        """
        Fetch configuration from MCP

        Optional feature - allows MCP to push config updates.

        Args:
            site_id: Site identifier

        Returns:
            Configuration dictionary or None if unavailable
        """
        await self._ensure_session()

        url = f"{self.base_url}/api/v1/config/{site_id}"

        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    config = await resp.json()
                    logger.info("Fetched updated configuration from MCP")
                    return config
                else:
                    logger.debug(f"No config update available: {resp.status}")
                    return None

        except aiohttp.ClientError as e:
            logger.warning(f"Failed to fetch config: {e}")
            return None

    async def close(self):
        """
        Close client session

        Should be called during agent shutdown.
        """
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("MCP client closed")

    def __repr__(self) -> str:
        return f"MCPClient(base_url='{self.base_url}')"
