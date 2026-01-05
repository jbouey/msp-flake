"""
Central Command API client for appliance mode.

Uses HTTPS + API key authentication instead of mTLS.
Provides endpoints for:
- Phone-home checkin
- Evidence bundle upload
- L1 rules sync
- Learning loop feedback
"""

import aiohttp
import asyncio
import json
import socket
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from .appliance_config import ApplianceConfig

logger = logging.getLogger(__name__)

VERSION = "1.0.0"


class CentralCommandClient:
    """
    HTTP client for Central Command API.

    Uses API key authentication via Bearer token.
    All connections are HTTPS.
    """

    def __init__(
        self,
        config: ApplianceConfig,
        max_retries: int = 3,
        timeout: int = 30
    ):
        """
        Initialize Central Command client.

        Args:
            config: Appliance configuration
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
        """
        self.config = config
        self.max_retries = max_retries
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    'User-Agent': f'osiriscare-appliance/{VERSION}',
                    'Authorization': f'Bearer {self.config.api_key}',
                    'Content-Type': 'application/json',
                    'X-Site-ID': self.config.site_id,
                }
            )
        return self._session

    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None,
        **kwargs
    ) -> tuple[int, Dict[str, Any]]:
        """
        Make HTTP request with retry logic.

        Returns:
            Tuple of (status_code, response_json)
        """
        url = f"{self.config.api_endpoint}{endpoint}"
        session = await self._get_session()
        last_error = None

        for attempt in range(self.max_retries):
            try:
                async with session.request(method, url, json=json_data, **kwargs) as response:
                    try:
                        data = await response.json()
                    except Exception:
                        data = {"error": await response.text()}

                    return response.status, data

            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(f"Request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        logger.error(f"All retries failed: {last_error}")
        return 0, {"error": str(last_error)}

    # =========================================================================
    # Phone-Home
    # =========================================================================

    async def checkin(
        self,
        hostname: str,
        mac_address: str,
        ip_addresses: List[str],
        uptime_seconds: int,
        agent_version: str = VERSION,
        nixos_version: str = "unknown",
        compliance_summary: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Send phone-home checkin to Central Command.

        Returns:
            Response dict with orders, windows_targets, etc. if successful.
            None if checkin failed.
        """
        # Use format expected by /api/appliances/checkin endpoint
        payload = {
            "site_id": self.config.site_id,
            "hostname": hostname,
            "mac_address": mac_address,
            "ip_addresses": ip_addresses,
            "uptime_seconds": uptime_seconds,
            "agent_version": agent_version,
            "nixos_version": nixos_version,
        }

        status, response = await self._request(
            'POST',
            '/api/appliances/checkin',
            json_data=payload
        )

        if status == 200:
            logger.debug(f"Checkin successful: {self.config.site_id}")
            return response if isinstance(response, dict) else {}
        else:
            logger.warning(f"Checkin failed: {status} - {response}")
            return None

    # =========================================================================
    # Evidence Upload
    # =========================================================================

    async def submit_evidence(
        self,
        bundle_hash: str,
        check_type: str,
        check_result: str,
        evidence_data: Dict[str, Any],
        timestamp: Optional[datetime] = None,
        host: Optional[str] = None,
        hipaa_control: Optional[str] = None,
        agent_signature: Optional[str] = None
    ) -> Optional[str]:
        """
        Submit evidence bundle to Central Command.

        The agent signs the bundle locally with Ed25519, then the server
        also signs it and adds to the hash chain.

        Args:
            bundle_hash: SHA256 hash of evidence data (for local verification)
            check_type: Type of check (e.g., "ntp_sync", "disk_usage")
            check_result: Result status ("compliant", "non_compliant", "error")
            evidence_data: Detailed check data
            timestamp: When check was performed (defaults to now)
            host: Hostname that was checked
            hipaa_control: HIPAA control reference (e.g., "164.312(b)")
            agent_signature: Hex-encoded Ed25519 signature from agent (optional)

        Returns:
            Bundle ID if successful, None otherwise
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        if host is None:
            host = socket.gethostname()

        # Build payload matching server's EvidenceBundleCreate model
        payload = {
            "site_id": self.config.site_id,
            "checked_at": timestamp.isoformat(),
            "checks": [
                {
                    "check": check_type,
                    "status": check_result,
                    "host": host,
                    "details": evidence_data,
                    "hipaa_control": hipaa_control
                }
            ],
            "summary": {
                "total_checks": 1,
                "compliant": 1 if check_result == "compliant" else 0,
                "non_compliant": 1 if check_result == "non_compliant" else 0,
                "errors": 1 if check_result == "error" else 0,
                "local_hash": bundle_hash  # For client-side verification
            }
        }

        # Include agent signature if provided
        if agent_signature:
            payload["agent_signature"] = agent_signature

        status, response = await self._request(
            'POST',
            f'/api/evidence/sites/{self.config.site_id}/submit',
            json_data=payload
        )

        if status in (200, 201):
            bundle_id = response.get('bundle_id')
            logger.info(f"Evidence submitted: {bundle_id}")
            return bundle_id
        else:
            logger.error(f"Evidence submission failed: {status} - {response}")
            return None

    # =========================================================================
    # L1 Rules Sync
    # =========================================================================

    async def sync_rules(self) -> Optional[List[Dict]]:
        """
        Fetch L1 rules from Central Command.

        Returns:
            List of rule dictionaries, or None on error
        """
        status, response = await self._request(
            'GET',
            '/agent/sync'
        )

        if status == 200:
            rules = response.get('rules', [])
            logger.info(f"Synced {len(rules)} L1 rules")
            return rules
        else:
            logger.warning(f"Rules sync failed: {status} - {response}")
            return None

    # =========================================================================
    # Learning Loop Feedback
    # =========================================================================

    async def report_pattern(
        self,
        check_type: str,
        issue_signature: str,
        resolution_steps: List[str],
        success: bool,
        execution_time_ms: int
    ) -> bool:
        """
        Report successful resolution pattern to learning loop.

        This helps promote L2 patterns to L1 rules.

        Returns:
            True if report accepted
        """
        payload = {
            "site_id": self.config.site_id,
            "check_type": check_type,
            "issue_signature": issue_signature,
            "resolution_steps": resolution_steps,
            "success": success,
            "execution_time_ms": execution_time_ms,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }

        status, response = await self._request(
            'POST',
            '/agent/checkin',
            json_data=payload
        )

        return status == 200

    # =========================================================================
    # Health Check
    # =========================================================================

    async def health_check(self) -> bool:
        """
        Check if Central Command is reachable.

        Returns:
            True if healthy
        """
        status, response = await self._request('GET', '/health')
        return status == 200

    # =========================================================================
    # Order Polling (for remote updates and commands)
    # =========================================================================

    async def fetch_pending_orders(self, appliance_id: str) -> List[Dict]:
        """
        Fetch pending orders for this appliance.

        Returns:
            List of pending order dictionaries
        """
        status, response = await self._request(
            'GET',
            f'/api/sites/{self.config.site_id}/appliances/{appliance_id}/orders/pending'
        )

        if status == 200:
            orders = response if isinstance(response, list) else response.get('orders', [])
            if orders:
                logger.info(f"Fetched {len(orders)} pending orders")
            return orders
        else:
            logger.debug(f"No pending orders or fetch failed: {status}")
            return []

    async def acknowledge_order(self, order_id: str) -> bool:
        """
        Acknowledge receipt of an order (mark as 'executing').

        Returns:
            True if acknowledged successfully
        """
        status, response = await self._request(
            'POST',
            f'/api/orders/{order_id}/acknowledge'
        )
        return status == 200

    async def complete_order(
        self,
        order_id: str,
        success: bool,
        result: Optional[Dict] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Mark an order as completed or failed.

        Returns:
            True if completion recorded successfully
        """
        payload = {
            "success": success,
            "result": result or {},
            "error_message": error_message,
        }

        status, response = await self._request(
            'POST',
            f'/api/orders/{order_id}/complete',
            json_data=payload
        )
        return status == 200

    async def download_file(self, url: str, dest_path: str) -> bool:
        """
        Download a file from a URL to local path.

        Used for downloading agent update packages.

        Returns:
            True if download successful
        """
        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status == 200:
                    from pathlib import Path
                    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(dest_path, 'wb') as f:
                        while chunk := await response.content.read(8192):
                            f.write(chunk)
                    logger.info(f"Downloaded {url} to {dest_path}")
                    return True
                else:
                    logger.error(f"Download failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False

    # =========================================================================
    # Context Manager
    # =========================================================================

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# =============================================================================
# Helper functions for system info (used by phone-home)
# =============================================================================

def get_hostname() -> str:
    """Get system hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def get_mac_address() -> str:
    """Get primary MAC address.

    Prioritizes:
    1. Ethernet interfaces (eth*, enp*, eno*) that are UP
    2. Any interface that is UP with a valid MAC
    3. Any interface with a valid MAC
    """
    try:
        candidates = []
        for iface in Path("/sys/class/net").iterdir():
            if iface.name in ("lo", "docker0", "virbr0"):
                continue

            # Check if interface is UP
            operstate_file = iface / "operstate"
            is_up = False
            if operstate_file.exists():
                state = operstate_file.read_text().strip().lower()
                is_up = state in ("up", "unknown")  # "unknown" is common for some ethernet

            addr_file = iface / "address"
            if addr_file.exists():
                mac = addr_file.read_text().strip().upper()
                if mac and mac != "00:00:00:00:00:00":
                    # Prioritize ethernet interfaces
                    is_ethernet = iface.name.startswith(("eth", "enp", "eno", "ens"))
                    priority = 0
                    if is_up and is_ethernet:
                        priority = 3  # Best: active ethernet
                    elif is_ethernet:
                        priority = 2  # Second: any ethernet
                    elif is_up:
                        priority = 1  # Third: active non-ethernet
                    # priority 0 = down non-ethernet (wireless)
                    candidates.append((priority, iface.name, mac))

        if candidates:
            # Sort by priority (descending), then by interface name
            candidates.sort(key=lambda x: (-x[0], x[1]))
            return candidates[0][2]
    except Exception:
        pass
    return "00:00:00:00:00:00"


def get_ip_addresses() -> List[str]:
    """Get all non-loopback IP addresses."""
    ips = []
    try:
        import subprocess
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                iface = parts[1]
                addr = parts[3].split("/")[0]
                if iface != "lo" and not addr.startswith("127."):
                    ips.append(addr)
    except Exception:
        pass
    return ips or ["0.0.0.0"]


def get_uptime_seconds() -> int:
    """Get system uptime in seconds."""
    try:
        uptime_str = Path("/proc/uptime").read_text().split()[0]
        return int(float(uptime_str))
    except Exception:
        return 0


def get_nixos_version() -> str:
    """Get NixOS version."""
    try:
        version_file = Path("/etc/os-release")
        if version_file.exists():
            for line in version_file.read_text().splitlines():
                if line.startswith("VERSION_ID="):
                    return line.split("=")[1].strip('"')
    except Exception:
        pass
    return "unknown"
