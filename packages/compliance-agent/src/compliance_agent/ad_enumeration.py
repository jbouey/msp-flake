"""
Active Directory enumeration module.

Discovers all Windows servers and workstations from AD
using a single domain admin credential.
"""

import asyncio
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)


@dataclass
class ADComputer:
    """Represents a computer object from AD."""
    hostname: str
    fqdn: str
    ip_address: Optional[str]
    os_name: str
    os_version: str
    is_server: bool
    is_workstation: bool
    is_domain_controller: bool
    ou_path: str
    last_logon: Optional[datetime]
    enabled: bool
    
    def to_dict(self) -> Dict:
        return {
            "hostname": self.hostname,
            "fqdn": self.fqdn,
            "ip_address": self.ip_address,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "is_server": self.is_server,
            "is_workstation": self.is_workstation,
            "is_domain_controller": self.is_domain_controller,
            "ou_path": self.ou_path,
            "last_logon": self.last_logon.isoformat() if self.last_logon else None,
            "enabled": self.enabled,
        }


class ADEnumerator:
    """
    Enumerates all computers from Active Directory.
    
    Uses PowerShell via WinRM to query AD, so requires:
    - Domain admin credentials (or delegated read access)
    - WinRM enabled on at least one domain controller
    """
    
    def __init__(self, domain_controller: str, username: str, password: str, domain: str, executor):
        """
        Initialize AD enumerator.
        
        Args:
            domain_controller: DC hostname/IP
            username: Domain admin username
            password: Domain admin password
            domain: Domain name (e.g., "northvalley.local")
            executor: WindowsExecutor instance for WinRM
        """
        self.dc = domain_controller
        self.username = username
        self.password = password
        self.domain = domain
        self.executor = executor
    
    async def enumerate_all(self) -> Tuple[List[ADComputer], List[ADComputer]]:
        """
        Enumerate all computers from AD.
        
        Returns:
            Tuple of (servers, workstations)
        """
        logger.info(f"Starting AD enumeration against {self.dc}")
        
        # PowerShell script to enumerate all computers
        ps_script = '''
        Import-Module ActiveDirectory -ErrorAction SilentlyContinue
        
        $computers = Get-ADComputer -Filter * -Properties `
            Name, DNSHostName, IPv4Address, OperatingSystem, OperatingSystemVersion, `
            DistinguishedName, LastLogonDate, Enabled, PrimaryGroupID
        
        $result = @()
        foreach ($comp in $computers) {
            $result += @{
                Name = $comp.Name
                DNSHostName = $comp.DNSHostName
                IPv4Address = $comp.IPv4Address
                OperatingSystem = $comp.OperatingSystem
                OperatingSystemVersion = $comp.OperatingSystemVersion
                DistinguishedName = $comp.DistinguishedName
                LastLogonDate = if ($comp.LastLogonDate) { $comp.LastLogonDate.ToString("o") } else { $null }
                Enabled = $comp.Enabled
                PrimaryGroupID = $comp.PrimaryGroupID
            }
        }
        
        $result | ConvertTo-Json -Depth 3
        '''
        
        try:
            # Format credentials properly
            credentials = {
                "username": f"{self.domain}\\{self.username}" if '\\' not in self.username and '@' not in self.username else self.username,
                "password": self.password,
            }
            
            # Run PowerShell script on domain controller
            # skip_phi_scrub: AD computer data contains IPs that are
            # network infrastructure, not patient data
            result = await self.executor.run_script(
                target=self.dc,
                script=ps_script,
                credentials=credentials,
                timeout_seconds=120,
                skip_phi_scrub=True,
            )
            
            if not result.success:
                logger.error(f"AD enumeration failed: {result.error}")
                return [], []
            
            # Parse JSON response
            output = result.output.get("stdout", "").strip()
            if not output:
                logger.warning("AD enumeration returned empty result")
                return [], []
            
            # Handle single result (not array)
            try:
                data = json.loads(output)
                if isinstance(data, dict):
                    data = [data]
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AD enumeration JSON: {e}")
                logger.debug(f"Raw output: {output[:500]}")
                return [], []
            
            computers = []
            for item in data:
                computer = self._parse_computer(item)
                if computer:
                    computers.append(computer)
            
            # Separate servers and workstations
            servers = [c for c in computers if c.is_server]
            workstations = [c for c in computers if c.is_workstation]
            
            logger.info(f"Enumerated {len(servers)} servers, {len(workstations)} workstations")
            return servers, workstations
            
        except Exception as e:
            logger.error(f"AD enumeration error: {e}")
            return [], []
    
    def _parse_computer(self, item: Dict) -> Optional[ADComputer]:
        """Parse AD computer object into ADComputer dataclass."""
        try:
            os_name = item.get('OperatingSystem') or ''
            
            # Determine if server or workstation based on OS
            is_server = 'Server' in os_name
            is_workstation = not is_server and ('Windows 10' in os_name or 'Windows 11' in os_name)
            
            # Check if domain controller (PrimaryGroupID 516 = DC)
            is_dc = item.get('PrimaryGroupID') == 516
            
            # Parse last logon date
            last_logon = None
            if item.get('LastLogonDate'):
                try:
                    last_logon_str = item['LastLogonDate']
                    # Handle ISO format with or without timezone
                    if 'Z' in last_logon_str:
                        last_logon = datetime.fromisoformat(last_logon_str.replace('Z', '+00:00'))
                    else:
                        last_logon = datetime.fromisoformat(last_logon_str)
                except Exception as e:
                    logger.debug(f"Failed to parse LastLogonDate: {e}")
            
            fqdn = item.get('DNSHostName') or item.get('Name', '')
            
            return ADComputer(
                hostname=item.get('Name', ''),
                fqdn=fqdn,
                ip_address=item.get('IPv4Address'),
                os_name=os_name,
                os_version=item.get('OperatingSystemVersion') or '',
                is_server=is_server,
                is_workstation=is_workstation,
                is_domain_controller=is_dc,
                ou_path=item.get('DistinguishedName', ''),
                last_logon=last_logon,
                enabled=item.get('Enabled', True),
            )
        except Exception as e:
            logger.warning(f"Failed to parse computer: {e}")
            return None
    
    async def resolve_missing_ips(self, computers: List[ADComputer]) -> None:
        """
        Resolve FQDNs to IP addresses for computers missing IPv4Address.

        Runs a single bulk DNS lookup on the DC via PowerShell so the
        appliance (which may not resolve .local domains) can connect by IP.
        """
        need_resolution = [c for c in computers if not c.ip_address and c.fqdn]
        if not need_resolution:
            return

        # Build PowerShell to resolve all FQDNs in one call
        fqdns = [c.fqdn for c in need_resolution]
        # Escape for PowerShell array
        fqdn_list = ",".join(f"'{f}'" for f in fqdns)
        ps_script = f'''
        $results = @{{}}
        foreach ($name in @({fqdn_list})) {{
            try {{
                $ip = [System.Net.Dns]::GetHostAddresses($name) | Where-Object {{ $_.AddressFamily -eq 'InterNetwork' }} | Select-Object -First 1
                if ($ip) {{ $results[$name] = $ip.IPAddressToString }}
            }} catch {{}}
        }}
        $results | ConvertTo-Json
        '''

        credentials = {
            "username": f"{self.domain}\\{self.username}" if '\\' not in self.username and '@' not in self.username else self.username,
            "password": self.password,
        }

        try:
            result = await self.executor.run_script(
                target=self.dc,
                script=ps_script,
                credentials=credentials,
                timeout_seconds=30,
                skip_phi_scrub=True,
            )

            if result.success:
                output = result.output.get("stdout", "").strip()
                if output:
                    resolved = json.loads(output)
                    if isinstance(resolved, dict):
                        for computer in need_resolution:
                            ip = resolved.get(computer.fqdn)
                            if ip:
                                computer.ip_address = ip
                                logger.debug(f"Resolved {computer.fqdn} → {ip}")
                        logger.info(f"Resolved {len(resolved)}/{len(need_resolution)} FQDNs to IPs")
        except Exception as e:
            logger.warning(f"Bulk DNS resolution failed: {e}")

    async def test_connectivity(self, target: ADComputer, port: int = 5985) -> bool:
        """
        Test if a computer is reachable via WinRM from the appliance.

        Uses a direct TCP connection test (faster and more reliable than
        running PowerShell Test-NetConnection on the DC).
        """
        hostname = target.ip_address or target.fqdn or target.hostname
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(hostname, port),
                timeout=5.0,
            )
            writer.close()
            await writer.wait_closed()
            logger.debug(f"Connectivity OK: {hostname}:{port}")
            return True
        except Exception as e:
            logger.debug(f"Connectivity failed for {hostname}:{port}: {e}")
            return False


class EnumerationResult:
    """Results of AD enumeration with connectivity status."""
    
    def __init__(self):
        self.servers: List[ADComputer] = []
        self.workstations: List[ADComputer] = []
        self.reachable_servers: List[ADComputer] = []
        self.reachable_workstations: List[ADComputer] = []
        self.unreachable: List[ADComputer] = []
        self.enumeration_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "total_servers": len(self.servers),
            "total_workstations": len(self.workstations),
            "reachable_servers": len(self.reachable_servers),
            "reachable_workstations": len(self.reachable_workstations),
            "unreachable": len(self.unreachable),
            "enumeration_time": self.enumeration_time.isoformat() if self.enumeration_time else None,
            "servers": [s.to_dict() for s in self.reachable_servers],
            "workstations": [w.to_dict() for w in self.reachable_workstations],
        }
