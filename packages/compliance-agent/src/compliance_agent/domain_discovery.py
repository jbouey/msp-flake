"""
Automatic Active Directory domain discovery.

This module detects the AD domain the appliance is connected to
by querying DNS for SRV records and attempting LDAP connection tests.

Zero configuration required - runs automatically on boot.
"""

import asyncio
import socket
import logging
import subprocess
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDomain:
    """Represents a discovered AD domain."""
    domain_name: str                    # e.g., "northvalley.local"
    netbios_name: Optional[str]         # e.g., "NORTHVALLEY"
    domain_controllers: List[str]       # List of DC hostnames/IPs
    dns_servers: List[str]              # DNS servers for this domain
    discovered_at: datetime
    discovery_method: str               # "dns_srv", "dhcp_option", "resolv_conf"
    
    def to_dict(self) -> Dict:
        return {
            "domain_name": self.domain_name,
            "netbios_name": self.netbios_name,
            "domain_controllers": self.domain_controllers,
            "dns_servers": self.dns_servers,
            "discovered_at": self.discovered_at.isoformat(),
            "discovery_method": self.discovery_method,
        }


class DomainDiscovery:
    """
    Discovers AD domain without any configuration.
    
    Discovery methods (in order of preference):
    1. DNS SRV records (_ldap._tcp.dc._msdcs.DOMAIN)
    2. DHCP option 15 (domain name)
    3. Reverse DNS on gateway
    4. resolv.conf search domain
    """
    
    def __init__(self):
        self.discovered_domain: Optional[DiscoveredDomain] = None
        self._discovery_complete = asyncio.Event()
    
    async def discover(self, timeout: float = 30.0) -> Optional[DiscoveredDomain]:
        """
        Attempt to discover AD domain using multiple methods.
        
        Returns DiscoveredDomain if found, None otherwise.
        """
        logger.info("Starting AD domain auto-discovery...")
        
        # Method 1: DNS SRV records
        domain = await self._discover_via_dns_srv()
        if domain:
            self.discovered_domain = domain
            logger.info(f"Discovered domain via DNS SRV: {domain.domain_name}")
            return domain
        
        # Method 2: DHCP domain suffix
        domain = await self._discover_via_dhcp()
        if domain:
            self.discovered_domain = domain
            logger.info(f"Discovered domain via DHCP: {domain.domain_name}")
            return domain
        
        # Method 3: Check resolv.conf search domain
        domain = await self._discover_via_resolv_conf()
        if domain:
            self.discovered_domain = domain
            logger.info(f"Discovered domain via resolv.conf: {domain.domain_name}")
            return domain
        
        logger.warning("AD domain auto-discovery failed - manual configuration required")
        return None
    
    async def _discover_via_dns_srv(self) -> Optional[DiscoveredDomain]:
        """
        Query DNS for AD-specific SRV records.
        
        AD creates these records automatically:
        - _ldap._tcp.dc._msdcs.DOMAIN
        - _kerberos._tcp.dc._msdcs.DOMAIN
        """
        # First, get our DNS search domains
        search_domains = self._get_dns_search_domains()
        
        for search_domain in search_domains:
            try:
                # Query for domain controller SRV record using dig
                srv_query = f"_ldap._tcp.dc._msdcs.{search_domain}"
                
                # Use dig to query SRV record
                proc = await asyncio.create_subprocess_exec(
                    "dig", "+short", "SRV", srv_query,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                
                if proc.returncode == 0 and stdout:
                    # Parse SRV records: priority weight port target
                    # e.g., "0 100 389 dc1.northvalley.local."
                    lines = stdout.decode().strip().split('\n')
                    domain_controllers = []
                    
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 4:
                            # Target is the last part, remove trailing dot
                            dc_host = parts[3].rstrip('.')
                            if dc_host and dc_host not in domain_controllers:
                                domain_controllers.append(dc_host)
                    
                    if domain_controllers:
                        # Verify this is actually AD by checking LDAP port
                        if await self._verify_ldap_available(domain_controllers[0]):
                            return DiscoveredDomain(
                                domain_name=search_domain,
                                netbios_name=self._extract_netbios(search_domain),
                                domain_controllers=domain_controllers,
                                dns_servers=self._get_dns_servers(),
                                discovered_at=datetime.now(timezone.utc),
                                discovery_method="dns_srv",
                            )
            except asyncio.TimeoutError:
                logger.debug(f"DNS SRV query timed out for {search_domain}")
                continue
            except Exception as e:
                logger.debug(f"DNS SRV query failed for {search_domain}: {e}")
                continue
        
        return None
    
    async def _discover_via_dhcp(self) -> Optional[DiscoveredDomain]:
        """Check DHCP-provided domain name."""
        # On NixOS, DHCP domain is in /etc/resolv.conf or networkd
        try:
            # Check networkd lease files
            import glob
            lease_files = glob.glob("/run/systemd/netif/leases/*")
            
            for lease_file in lease_files:
                try:
                    with open(lease_file) as f:
                        content = f.read()
                        for line in content.split('\n'):
                            if line.startswith('DOMAINNAME='):
                                domain = line.split('=', 1)[1].strip().strip('"')
                                if domain:
                                    # Verify it's an AD domain
                                    dcs = await self._find_domain_controllers(domain)
                                    if dcs:
                                        return DiscoveredDomain(
                                            domain_name=domain,
                                            netbios_name=self._extract_netbios(domain),
                                            domain_controllers=dcs,
                                            dns_servers=self._get_dns_servers(),
                                            discovered_at=datetime.now(timezone.utc),
                                            discovery_method="dhcp_option",
                                        )
                except Exception as e:
                    logger.debug(f"Failed to read lease file {lease_file}: {e}")
                    continue
        except Exception as e:
            logger.debug(f"DHCP discovery failed: {e}")
        
        return None
    
    async def _discover_via_resolv_conf(self) -> Optional[DiscoveredDomain]:
        """Check resolv.conf for search domain."""
        try:
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.startswith('search ') or line.startswith('domain '):
                        domains = line.split()[1:]
                        for domain in domains:
                            dcs = await self._find_domain_controllers(domain)
                            if dcs:
                                return DiscoveredDomain(
                                    domain_name=domain,
                                    netbios_name=self._extract_netbios(domain),
                                    domain_controllers=dcs,
                                    dns_servers=self._get_dns_servers(),
                                    discovered_at=datetime.now(timezone.utc),
                                    discovery_method="resolv_conf",
                                )
        except Exception as e:
            logger.debug(f"resolv.conf discovery failed: {e}")
        
        return None
    
    async def _find_domain_controllers(self, domain: str) -> List[str]:
        """Find domain controllers for a given domain."""
        try:
            srv_query = f"_ldap._tcp.dc._msdcs.{domain}"
            
            proc = await asyncio.create_subprocess_exec(
                "dig", "+short", "SRV", srv_query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            
            if proc.returncode == 0 and stdout:
                lines = stdout.decode().strip().split('\n')
                dcs = []
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 4:
                        dc_host = parts[3].rstrip('.')
                        if dc_host and dc_host not in dcs:
                            dcs.append(dc_host)
                return dcs
        except Exception as e:
            logger.debug(f"Failed to find DCs for {domain}: {e}")
        
        return []
    
    async def _verify_ldap_available(self, host: str, port: int = 389) -> bool:
        """Verify LDAP port is accessible on host."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False
    
    def _get_dns_search_domains(self) -> List[str]:
        """Get DNS search domains from system config."""
        domains = []
        try:
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.startswith('search '):
                        domains.extend(line.split()[1:])
                    elif line.startswith('domain '):
                        domains.append(line.split()[1])
        except Exception:
            pass
        return domains
    
    def _get_dns_servers(self) -> List[str]:
        """Get DNS servers from system config."""
        servers = []
        try:
            with open('/etc/resolv.conf') as f:
                for line in f:
                    if line.startswith('nameserver '):
                        servers.append(line.split()[1])
        except Exception:
            pass
        return servers
    
    def _extract_netbios(self, domain: str) -> Optional[str]:
        """Extract likely NetBIOS name from domain."""
        # northvalley.local -> NORTHVALLEY
        parts = domain.split('.')
        if parts:
            return parts[0].upper()
        return None
