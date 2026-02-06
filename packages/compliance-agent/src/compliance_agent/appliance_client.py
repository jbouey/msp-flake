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
import ssl
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from .appliance_config import ApplianceConfig
from .phi_scrubber import PHIScrubber

logger = logging.getLogger(__name__)


def create_secure_ssl_context() -> ssl.SSLContext:
    """
    Create a hardened SSL context for Central Command connections.

    Security settings:
    - TLS 1.2 minimum (TLS 1.0/1.1 disabled)
    - Certificate verification enabled
    - Hostname checking enabled
    """
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx

VERSION = "1.0.16"


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
        # PHI scrubber for outbound data - excludes IP addresses since those are
        # infrastructure data intentionally shared with the partner dashboard
        self._outbound_scrubber = PHIScrubber(
            hash_redacted=True,
            exclude_categories={'ip_address'}
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session with TLS 1.2+ enforcement."""
        if self._session is None or self._session.closed:
            # Create hardened SSL connector
            ssl_context = create_secure_ssl_context()
            connector = aiohttp.TCPConnector(ssl=ssl_context)

            self._session = aiohttp.ClientSession(
                connector=connector,
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

    def _scrub_outbound(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Scrub PHI from outbound payload before transmission.

        HIPAA §164.312(e)(1) Transmission Security: ensure no PHI
        leaves the clinic in API payloads. Infrastructure data (IPs,
        hostnames) is preserved since it's intentionally shared.
        """
        try:
            scrubbed, result = self._outbound_scrubber.scrub_dict(data)
            if result.phi_scrubbed:
                logger.warning(
                    f"PHI scrubbed from outbound data: "
                    f"{result.patterns_matched} patterns ({result.patterns_by_type})"
                )
            return scrubbed
        except Exception as e:
            logger.error(f"PHI scrubbing failed (sending unscrubbed): {e}")
            return data

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
        # Scrub PHI from outbound payloads at the transport boundary
        if json_data is not None:
            json_data = self._scrub_outbound(json_data)

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
        compliance_summary: Optional[Dict] = None,
        has_local_credentials: bool = False
    ) -> Optional[Dict]:
        """
        Send phone-home checkin to Central Command.

        Args:
            has_local_credentials: If True, tells server appliance has fresh
                local credentials and doesn't need them in the response.

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
            "has_local_credentials": has_local_credentials,
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
    # Domain Discovery (Zero-Friction Deployment)
    # =========================================================================

    async def report_discovered_domain(
        self,
        appliance_id: str,
        discovered_domain: Dict[str, Any],
        awaiting_credentials: bool = True
    ) -> Optional[Dict]:
        """
        Report discovered AD domain to Central Command.
        
        Args:
            appliance_id: Appliance identifier
            discovered_domain: DiscoveredDomain.to_dict() output
            awaiting_credentials: Whether credentials are needed
            
        Returns:
            Response dict if successful, None otherwise
        """
        payload = {
            "site_id": self.config.site_id,
            "appliance_id": appliance_id,
            "discovered_domain": discovered_domain,
            "awaiting_credentials": awaiting_credentials,
        }
        
        status, response = await self._request(
            'POST',
            '/api/appliances/domain-discovered',
            json_data=payload
        )
        
        if status == 200:
            logger.info(f"Domain discovery reported: {discovered_domain.get('domain_name')}")
            return response if isinstance(response, dict) else {}
        else:
            logger.warning(f"Failed to report domain discovery: {status} - {response}")
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
        agent_signature: Optional[str] = None,
        signer: Optional[Any] = None  # Ed25519Signer instance
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
        checks = [
            {
                "check": check_type,
                "status": check_result,
                "host": host,
                "details": evidence_data,
                "hipaa_control": hipaa_control
            }
        ]
        summary = {
            "total_checks": 1,
            "compliant": 1 if check_result == "compliant" else 0,
            "non_compliant": 1 if check_result == "non_compliant" else 0,
            "errors": 1 if check_result == "error" else 0,
            "local_hash": bundle_hash  # For client-side verification
        }

        payload = {
            "site_id": self.config.site_id,
            "checked_at": timestamp.isoformat(),
            "checks": checks,
            "summary": summary
        }

        # Sign the payload if signer is provided (matches server-side verification)
        signed_data_str = None
        if signer and not agent_signature:
            try:
                # Build the exact structure the server expects for verification
                signed_data_str = json.dumps({
                    "site_id": self.config.site_id,
                    "checked_at": timestamp.isoformat(),
                    "checks": checks,
                    "summary": summary
                }, sort_keys=True)
                logger.debug(f"SIGNING DATA: {signed_data_str[:200]}...")
                signature_bytes = signer.sign(signed_data_str)
                agent_signature = signature_bytes.hex()
                logger.debug(f"SIGNATURE: {agent_signature[:40]}...")
            except Exception as e:
                logger.warning(f"Failed to sign evidence bundle: {e}")

        # Include agent signature and signed_data if available
        if agent_signature:
            payload["agent_signature"] = agent_signature
            # Include the exact signed data so server can verify against it
            if signed_data_str:
                payload["signed_data"] = signed_data_str

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

        Rules returned depend on site's healing_tier setting:
        - standard: 7 core rules
        - full_coverage: All 21 L1 rules

        Returns:
            List of rule dictionaries, or None on error
        """
        # Pass site_id to get tier-specific rules
        endpoint = f'/agent/sync?site_id={self.config.site_id}'
        status, response = await self._request(
            'GET',
            endpoint
        )

        if status == 200:
            rules = response.get('rules', [])
            healing_tier = response.get('healing_tier', 'standard')
            logger.info(f"Synced {len(rules)} L1 rules (tier: {healing_tier})")
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
        execution_time_ms: int,
        runbook_id: Optional[str] = None,
    ) -> bool:
        """
        Report successful resolution pattern to learning loop.

        This helps promote L2 patterns to L1 rules.

        Args:
            check_type: The type of check that was healed (e.g., "firewall")
            issue_signature: Unique identifier for this issue pattern
            resolution_steps: List of actions taken to resolve
            success: Whether the healing was successful
            execution_time_ms: Time taken to resolve
            runbook_id: Optional runbook ID that was used

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
            "runbook_id": runbook_id,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }

        status, response = await self._request(
            'POST',
            '/agent/patterns',
            json_data=payload
        )

        if status in (200, 201):
            logger.debug(f"Pattern reported: {issue_signature}")
            return True
        else:
            logger.debug(f"Pattern report failed: {status} - {response}")
            return False

    # =========================================================================
    # Incident Reporting
    # =========================================================================

    async def report_incident(
        self,
        incident_type: str,
        severity: str,
        check_type: str,
        details: Dict[str, Any],
        pre_state: Optional[Dict[str, Any]] = None,
        hipaa_controls: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Report an incident to Central Command.

        This creates an incident record in the dashboard and may trigger
        a remediation order if a matching runbook is found.

        Args:
            incident_type: Type of incident (e.g., "ntp_sync", "firewall")
            severity: Severity level ("low", "medium", "high", "critical")
            check_type: The check that detected the issue
            details: Additional details about the incident
            pre_state: State before the incident
            hipaa_controls: List of HIPAA controls affected

        Returns:
            Response dict with incident_id, resolution_level, etc.
            or None if the request failed.
        """
        payload = {
            "site_id": self.config.site_id,
            "host_id": get_hostname(),
            "incident_type": incident_type,
            "severity": severity,
            "check_type": check_type,
            "details": details,
            "pre_state": pre_state or {},
            "hipaa_controls": hipaa_controls or ["164.308(a)(1)(i)"],
        }

        status, response = await self._request(
            'POST',
            '/incidents',
            json_data=payload
        )

        if status == 200 and response:
            logger.info(f"Incident reported: {response.get('incident_id', 'N/A')}")
            return response
        elif status == 429:
            logger.warning("Incident reporting rate limited")
            return None
        else:
            logger.warning(f"Failed to report incident: {status}")
            return None

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
        import shutil
        # Find ip command - may not be in PATH for systemd services
        ip_cmd = shutil.which("ip") or "/run/current-system/sw/bin/ip" or "/sbin/ip"
        result = subprocess.run(
            [ip_cmd, "-4", "-o", "addr", "show"],
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
