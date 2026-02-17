"""
DNS SRV record registration for Go agent auto-discovery.

Creates _osiris-grpc._tcp.domain SRV record pointing to appliance IP
so Go agents can discover the appliance without manual configuration.

Uses PowerShell Add-DnsServerResourceRecord on DC via WinRM.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SRV_SERVICE = "_osiris-grpc._tcp"
APPLIANCE_A_RECORD = "osiris-appliance"


class DNSRegistrar:
    """Registers DNS SRV records for appliance discovery."""

    def __init__(
        self,
        domain: str,
        appliance_ip: str,
        grpc_port: int = 50051,
    ):
        self.domain = domain
        self.appliance_ip = appliance_ip
        self.grpc_port = grpc_port

    async def register_srv_record(
        self, executor, dc_host: str, credentials: dict
    ) -> bool:
        """
        Register _osiris-grpc._tcp.domain SRV record via PowerShell on DC.

        Args:
            executor: WindowsExecutor instance
            dc_host: DC hostname or IP for WinRM
            credentials: {"username": "DOMAIN\\user", "password": "..."}

        Returns:
            True if SRV record was created/verified successfully.
        """
        ps_script = f'''
        $zone = "{self.domain}"
        $srvName = "{SRV_SERVICE}"
        $aName = "{APPLIANCE_A_RECORD}"

        # Create A record for appliance if not present
        $aRecord = Get-DnsServerResourceRecord -ZoneName $zone -Name $aName -RRType A -ErrorAction SilentlyContinue
        if (-not $aRecord) {{
            Add-DnsServerResourceRecord -ZoneName $zone -Name $aName -A -IPv4Address "{self.appliance_ip}"
            Write-Output "A_CREATED"
        }} else {{
            Write-Output "A_EXISTS"
        }}

        # Remove existing SRV record if present (to update)
        $existing = Get-DnsServerResourceRecord -ZoneName $zone -Name $srvName -RRType SRV -ErrorAction SilentlyContinue
        if ($existing) {{
            Remove-DnsServerResourceRecord -ZoneName $zone -Name $srvName -RRType SRV -Force
        }}

        # Create SRV record: _osiris-grpc._tcp.domain -> osiris-appliance.domain:50051
        Add-DnsServerResourceRecord -ZoneName $zone -Name $srvName -Srv `
            -Priority 0 -Weight 100 -Port {self.grpc_port} `
            -DomainName "$aName.$zone"

        # Verify
        $verify = Get-DnsServerResourceRecord -ZoneName $zone -Name $srvName -RRType SRV -ErrorAction SilentlyContinue
        if ($verify) {{
            Write-Output "SRV_OK"
        }} else {{
            Write-Output "SRV_FAIL"
        }}
        '''

        loop = asyncio.get_event_loop()

        def _exec():
            result = executor.run_script(
                target=dc_host,
                script=ps_script,
                credentials=credentials,
                timeout_seconds=30,
            )
            if result.success:
                return result.output.get("stdout", "")
            logger.error("DNS registration failed: %s", result.error)
            return None

        output = await loop.run_in_executor(None, _exec)

        if output and "SRV_OK" in output:
            logger.info(
                "DNS SRV record registered: %s.%s -> %s:%d",
                SRV_SERVICE,
                self.domain,
                self.appliance_ip,
                self.grpc_port,
            )
            return True

        logger.error("DNS SRV registration failed (output: %s)", output)
        return False

    async def verify_srv_record(self) -> Optional[str]:
        """Verify the SRV record resolves correctly (from appliance perspective)."""
        import socket

        try:
            loop = asyncio.get_event_loop()
            answers = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(
                    f"{SRV_SERVICE}.{self.domain}",
                    self.grpc_port,
                    type=socket.SOCK_STREAM,
                ),
            )
            if answers:
                return f"{answers[0][4][0]}:{answers[0][4][1]}"
        except (socket.gaierror, OSError):
            pass

        return None
