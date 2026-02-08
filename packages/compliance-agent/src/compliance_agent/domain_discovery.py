"""
Automatic Active Directory domain discovery.

This module detects the AD domain the appliance is connected to
by querying DNS for SRV records and attempting LDAP connection tests.

Zero configuration required - runs automatically on boot.
"""

import asyncio
import re
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
    
    def __init__(self, known_dns_candidates: Optional[List[str]] = None):
        self.discovered_domain: Optional[DiscoveredDomain] = None
        self._discovery_complete = asyncio.Event()
        self._known_dns_candidates = known_dns_candidates or []
    
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
        
        # Method 4: Try using known host IPs as DNS servers
        if self._known_dns_candidates:
            domain = await self._discover_via_known_hosts()
            if domain:
                self.discovered_domain = domain
                logger.info(f"Discovered domain via known host DNS: {domain.domain_name}")
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
    
    async def _discover_via_known_hosts(self) -> Optional[DiscoveredDomain]:
        """
        Discover domain by probing known Windows target IPs for LDAP/DNS.

        Strategy: connect to LDAP port 389, read rootDSE to get the domain,
        then query SRV records using the host as DNS server.
        """
        for host_ip in self._known_dns_candidates:
            try:
                # Step 1: Check if this host runs LDAP (it's a DC)
                if not await self._verify_ldap_available(host_ip):
                    continue

                logger.debug(f"LDAP available on {host_ip}, probing for domain...")

                # Step 2: Get domain from LDAP rootDSE via raw socket
                domain_dn = await self._query_ldap_root_dse(host_ip)
                if not domain_dn:
                    continue

                # Convert DN to domain name: DC=northvalley,DC=local → northvalley.local
                domain_name = self._dn_to_domain(domain_dn)
                if not domain_name:
                    continue

                logger.info(f"LDAP rootDSE on {host_ip} reports domain: {domain_name}")

                # This host serves LDAP for this domain — it's a DC
                return DiscoveredDomain(
                    domain_name=domain_name,
                    netbios_name=self._extract_netbios(domain_name),
                    domain_controllers=[host_ip],
                    dns_servers=[host_ip],
                    discovered_at=datetime.now(timezone.utc),
                    discovery_method="known_host_ldap",
                )

            except asyncio.TimeoutError:
                logger.warning(f"Known host probe timed out for {host_ip}")
            except Exception as e:
                logger.warning(f"Known host probe failed for {host_ip}: {e}")

        return None

    async def _query_ldap_root_dse(self, host: str, port: int = 389) -> Optional[str]:
        """
        Query LDAP rootDSE to get defaultNamingContext.

        Uses a raw LDAP SearchRequest (anonymous bind) — no external libs needed.
        Returns the defaultNamingContext DN (e.g. "DC=northvalley,DC=local").
        """
        import struct

        def _ber_length(length: int) -> bytes:
            if length < 0x80:
                return bytes([length])
            elif length < 0x100:
                return bytes([0x81, length])
            else:
                return bytes([0x82]) + struct.pack('>H', length)

        def _ber_sequence(data: bytes) -> bytes:
            return b'\x30' + _ber_length(len(data)) + data

        def _ber_integer(val: int) -> bytes:
            encoded = val.to_bytes((val.bit_length() + 8) // 8, 'big', signed=True) if val else b'\x00'
            return b'\x02' + _ber_length(len(encoded)) + encoded

        def _ber_octet_string(val: str) -> bytes:
            encoded = val.encode('utf-8')
            return b'\x04' + _ber_length(len(encoded)) + encoded

        def _ber_enum(val: int) -> bytes:
            return b'\x0a' + b'\x01' + bytes([val])

        def _ber_bool(val: bool) -> bytes:
            return b'\x01\x01' + (b'\xff' if val else b'\x00')

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5.0
            )

            # Build LDAP SearchRequest for rootDSE
            # MessageID = 1
            msg_id = _ber_integer(1)

            # SearchRequest: baseObject="", scope=baseObject(0), derefAliases=never(0)
            # sizeLimit=1, timeLimit=10, typesOnly=false
            # filter=(objectClass=*) → present filter for "objectClass"
            # attributes: ["defaultNamingContext"]
            base_dn = _ber_octet_string("")
            scope = _ber_enum(0)  # baseObject
            deref = _ber_enum(0)  # neverDerefAliases
            size_limit = _ber_integer(1)
            time_limit = _ber_integer(10)
            types_only = _ber_bool(False)

            # Present filter: (objectClass=*) → tag 0x87 + "objectClass"
            filter_val = b"objectClass"
            present_filter = b'\x87' + _ber_length(len(filter_val)) + filter_val

            # Attributes: sequence of "defaultNamingContext"
            attr = _ber_octet_string("defaultNamingContext")
            attrs = _ber_sequence(attr)

            search_body = base_dn + scope + deref + size_limit + time_limit + types_only + present_filter + attrs
            # SearchRequest application tag = 0x63
            search_req = b'\x63' + _ber_length(len(search_body)) + search_body

            # Full LDAP message
            message = _ber_sequence(msg_id + search_req)

            writer.write(message)
            await writer.drain()

            # Read response
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            writer.close()
            await writer.wait_closed()

            if not response:
                return None

            # Parse response — look for "defaultNamingContext" and its value
            marker = b"defaultNamingContext"
            idx = response.find(marker)
            if idx < 0:
                return None

            # After the attribute name, find the OCTET STRING (0x04) containing the value
            rest = response[idx + len(marker):]
            # Look for OCTET STRING tag (0x04) followed by length
            for i in range(min(10, len(rest))):
                if rest[i] == 0x04 and i + 1 < len(rest):
                    # Read BER length
                    length_byte = rest[i + 1]
                    if length_byte < 0x80:
                        value_len = length_byte
                        value_start = i + 2
                    elif length_byte == 0x81:
                        value_len = rest[i + 2]
                        value_start = i + 3
                    else:
                        continue
                    # Extract exactly value_len bytes
                    dn_bytes = rest[value_start:value_start + value_len]
                    return dn_bytes.decode('ascii', errors='ignore')

            # Fallback: regex extraction
            dc_start = rest.find(b"DC=")
            if dc_start >= 0:
                rest_str = rest[dc_start:dc_start + 200].decode('ascii', errors='ignore')
                match = re.match(r'(DC=[A-Za-z0-9_-]+(?:,DC=[A-Za-z0-9_-]+)*)', rest_str)
                if match:
                    return match.group(1)

        except Exception as e:
            logger.debug(f"LDAP rootDSE query failed on {host}: {e}")

        return None

    @staticmethod
    def _dn_to_domain(dn: str) -> Optional[str]:
        """Convert LDAP DN to domain name. DC=northvalley,DC=local → northvalley.local"""
        parts = []
        for component in dn.split(','):
            component = component.strip()
            if component.upper().startswith('DC='):
                parts.append(component[3:])
        return '.'.join(parts) if parts else None

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
