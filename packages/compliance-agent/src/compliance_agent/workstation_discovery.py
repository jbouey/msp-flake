"""
Workstation Discovery via Active Directory.

Discovers Windows workstations from AD domain controllers.
Filters by OS type, enumerates online status.
Reuses WinRM infrastructure from runbooks/windows/executor.py.

HIPAA Relevance:
- §164.308(a)(1) - Risk Analysis (inventory all workstations)
- §164.310(d)(1) - Device/Media Controls (track all endpoints)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class WorkstationOS(str, Enum):
    """Windows workstation OS types to discover."""
    WINDOWS_10 = "Windows 10"
    WINDOWS_11 = "Windows 11"
    WINDOWS_10_ENT = "Windows 10 Enterprise"
    WINDOWS_11_ENT = "Windows 11 Enterprise"


@dataclass
class Workstation:
    """Discovered workstation from Active Directory."""

    hostname: str
    distinguished_name: str
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    last_logon: Optional[datetime] = None

    # Runtime status
    online: bool = False
    last_seen: Optional[datetime] = None
    last_check_error: Optional[str] = None

    # Compliance tracking
    compliance_status: str = "unknown"  # compliant, drifted, unknown
    last_compliance_check: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for evidence/API."""
        return {
            "hostname": self.hostname,
            "distinguished_name": self.distinguished_name,
            "ip_address": self.ip_address,
            "mac_address": self.mac_address,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "last_logon": self.last_logon.isoformat() if self.last_logon else None,
            "online": self.online,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "compliance_status": self.compliance_status,
        }


class WorkstationDiscovery:
    """
    Discover Windows workstations from Active Directory.

    Uses LDAP queries via PowerShell (Get-ADComputer) on domain controller.
    Filters for workstation OS types (Windows 10/11).
    Reuses existing WinRM infrastructure.
    """

    # PowerShell script to query AD for workstations
    AD_QUERY_SCRIPT = '''
$workstations = Get-ADComputer -Filter {
    OperatingSystem -like "*Windows 10*" -or
    OperatingSystem -like "*Windows 11*"
} -Properties Name, DNSHostName, IPv4Address, OperatingSystem, OperatingSystemVersion, LastLogonDate, Enabled |
Where-Object { $_.Enabled -eq $true } |
Select-Object Name, DNSHostName, IPv4Address, OperatingSystem, OperatingSystemVersion, LastLogonDate, DistinguishedName

$result = @()
foreach ($ws in $workstations) {
    $result += @{
        hostname = $ws.Name
        dns_hostname = $ws.DNSHostName
        ip_address = $ws.IPv4Address
        os_name = $ws.OperatingSystem
        os_version = $ws.OperatingSystemVersion
        last_logon = if ($ws.LastLogonDate) { $ws.LastLogonDate.ToString("o") } else { $null }
        distinguished_name = $ws.DistinguishedName
    }
}
$result | ConvertTo-Json -Depth 3
'''

    # PowerShell script to check if workstation is online
    PING_CHECK_SCRIPT = '''
param([string]$Hostname)
$result = Test-Connection -ComputerName $Hostname -Count 1 -Quiet -ErrorAction SilentlyContinue
@{ online = $result } | ConvertTo-Json
'''

    # PowerShell script for WMI connectivity check
    WMI_CHECK_SCRIPT = '''
param([string]$Hostname)
try {
    $wmi = Get-WmiObject -Class Win32_ComputerSystem -ComputerName $Hostname -ErrorAction Stop
    @{
        online = $true
        model = $wmi.Model
        manufacturer = $wmi.Manufacturer
    } | ConvertTo-Json
} catch {
    @{ online = $false; error = $_.Exception.Message } | ConvertTo-Json
}
'''

    def __init__(
        self,
        executor,  # WindowsExecutor instance
        domain_controller: str,
        credentials: Dict[str, str],
        cache_ttl_seconds: int = 3600,  # 1 hour cache
    ):
        """
        Initialize workstation discovery.

        Args:
            executor: WindowsExecutor for WinRM commands
            domain_controller: DC hostname/IP for AD queries
            credentials: Dict with username/password for DC
            cache_ttl_seconds: How long to cache discovery results
        """
        self.executor = executor
        self.domain_controller = domain_controller
        self.credentials = credentials
        self.cache_ttl_seconds = cache_ttl_seconds

        # Cache
        self._workstation_cache: List[Workstation] = []
        self._cache_timestamp: Optional[datetime] = None

    async def enumerate_from_ad(self) -> List[Workstation]:
        """
        Query Active Directory for all workstation computer objects.

        Returns:
            List of Workstation objects discovered from AD

        Raises:
            Exception if AD query fails
        """
        logger.info(f"Querying AD for workstations via {self.domain_controller}")

        try:
            # Execute AD query on domain controller
            result = await self.executor.run_script(
                target=self.domain_controller,
                script=self.AD_QUERY_SCRIPT,
                credentials=self.credentials,
                timeout_seconds=60,
            )

            if not result.success:
                logger.error(f"AD query failed: {result.error}")
                raise Exception(f"AD query failed: {result.error}")

            # Parse JSON response
            import json
            workstations_data = json.loads(result.output.get("stdout", "[]"))

            # Handle single result (comes as dict, not list)
            if isinstance(workstations_data, dict):
                workstations_data = [workstations_data]

            workstations = []
            for ws_data in workstations_data:
                ws = Workstation(
                    hostname=ws_data.get("hostname", ""),
                    distinguished_name=ws_data.get("distinguished_name", ""),
                    ip_address=ws_data.get("ip_address"),
                    os_name=ws_data.get("os_name"),
                    os_version=ws_data.get("os_version"),
                    last_logon=self._parse_datetime(ws_data.get("last_logon")),
                )
                workstations.append(ws)

            logger.info(f"Discovered {len(workstations)} workstations from AD")

            # Update cache
            self._workstation_cache = workstations
            self._cache_timestamp = datetime.now(timezone.utc)

            return workstations

        except Exception as e:
            logger.error(f"Failed to enumerate workstations from AD: {e}")
            raise

    async def check_online_status(
        self,
        workstations: List[Workstation],
        method: str = "ping",  # ping or wmi
        concurrency: int = 10,
    ) -> List[Workstation]:
        """
        Check online status for a list of workstations.

        Args:
            workstations: List of workstations to check
            method: 'ping' for ICMP, 'wmi' for WMI query (more reliable)
            concurrency: Max concurrent checks

        Returns:
            Same list with online status updated
        """
        logger.info(f"Checking online status for {len(workstations)} workstations")

        semaphore = asyncio.Semaphore(concurrency)

        async def check_one(ws: Workstation) -> Workstation:
            async with semaphore:
                try:
                    target = ws.ip_address or ws.hostname
                    if not target:
                        ws.online = False
                        ws.last_check_error = "No IP or hostname"
                        return ws

                    if method == "ping":
                        result = await self.executor.run_script(
                            target=self.domain_controller,
                            script=self.PING_CHECK_SCRIPT,
                            script_params={"Hostname": target},
                            credentials=self.credentials,
                            timeout_seconds=10,
                        )
                    else:  # wmi
                        result = await self.executor.run_script(
                            target=self.domain_controller,
                            script=self.WMI_CHECK_SCRIPT,
                            script_params={"Hostname": target},
                            credentials=self.credentials,
                            timeout_seconds=15,
                        )

                    if result.success:
                        import json
                        status = json.loads(result.output.get("stdout", "{}"))
                        ws.online = status.get("online", False)
                        ws.last_seen = datetime.now(timezone.utc) if ws.online else ws.last_seen
                        ws.last_check_error = status.get("error")
                    else:
                        ws.online = False
                        ws.last_check_error = result.error

                except Exception as e:
                    ws.online = False
                    ws.last_check_error = str(e)

                return ws

        # Run checks concurrently
        tasks = [check_one(ws) for ws in workstations]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                workstations[i].online = False
                workstations[i].last_check_error = str(result)

        online_count = sum(1 for ws in workstations if ws.online)
        logger.info(f"Online status: {online_count}/{len(workstations)} workstations reachable")

        return workstations

    async def discover_and_check(
        self,
        use_cache: bool = True,
    ) -> List[Workstation]:
        """
        Full discovery: enumerate from AD + check online status.

        Args:
            use_cache: Use cached discovery if within TTL

        Returns:
            List of workstations with online status
        """
        # Check cache
        if use_cache and self._workstation_cache and self._cache_timestamp:
            age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds()
            if age < self.cache_ttl_seconds:
                logger.info(f"Using cached workstation list ({len(self._workstation_cache)} devices)")
                # Still refresh online status
                return await self.check_online_status(self._workstation_cache)

        # Fresh discovery
        workstations = await self.enumerate_from_ad()
        return await self.check_online_status(workstations)

    def get_online_workstations(self) -> List[Workstation]:
        """Get only online workstations from cache."""
        return [ws for ws in self._workstation_cache if ws.online]

    def get_offline_workstations(self) -> List[Workstation]:
        """Get only offline workstations from cache."""
        return [ws for ws in self._workstation_cache if not ws.online]

    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None


# Convenience function for appliance agent
async def discover_workstations(
    executor,
    domain_controller: str,
    credentials: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Convenience function for appliance agent integration.

    Returns list of workstation dicts ready for API/evidence.
    """
    discovery = WorkstationDiscovery(
        executor=executor,
        domain_controller=domain_controller,
        credentials=credentials,
    )

    workstations = await discovery.discover_and_check()
    return [ws.to_dict() for ws in workstations]
