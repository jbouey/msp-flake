"""
Active Directory discovery using LDAP.

Queries AD for computer objects to discover domain-joined devices.
Uses the ldap3 library for LDAP operations.
"""

from __future__ import annotations

import logging
from typing import Optional

from ldap3 import Server, Connection, ALL, SUBTREE
from ldap3.core.exceptions import LDAPException

from .._types import DiscoverySource
from .base import DiscoveredDevice, DiscoveryMethod

logger = logging.getLogger(__name__)


class ADDiscovery(DiscoveryMethod):
    """
    Discover devices via Active Directory LDAP queries.

    Queries AD for computer objects, extracting hostname, OS info,
    and distinguished name for each computer found.
    """

    def __init__(
        self,
        server: str,
        base_dn: str,
        bind_dn: Optional[str] = None,
        bind_password: Optional[str] = None,
        use_ssl: bool = False,
        port: Optional[int] = None,
    ):
        """
        Initialize AD discovery.

        Args:
            server: AD domain controller hostname or IP
            base_dn: Base DN for search (e.g., "DC=example,DC=com")
            bind_dn: DN for authentication (e.g., "CN=scanner,OU=Service,DC=example,DC=com")
            bind_password: Password for bind_dn
            use_ssl: Use LDAPS (port 636) instead of LDAP (port 389)
            port: Override default port
        """
        self.server_address = server
        self.base_dn = base_dn
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.use_ssl = use_ssl
        self.port = port or (636 if use_ssl else 389)

    @property
    def name(self) -> str:
        return "ad"

    async def is_available(self) -> bool:
        """Check if AD server is reachable."""
        try:
            server = Server(
                self.server_address,
                port=self.port,
                use_ssl=self.use_ssl,
                get_info=ALL,
                connect_timeout=5,
            )
            conn = Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                raise_exceptions=True,
            )
            conn.unbind()
            return True
        except LDAPException as e:
            logger.warning(f"AD server not available: {e}")
            return False

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Discover computers from Active Directory.

        Returns list of discovered devices with hostnames and OS info.
        """
        devices = []

        try:
            server = Server(
                self.server_address,
                port=self.port,
                use_ssl=self.use_ssl,
                get_info=ALL,
                connect_timeout=10,
            )

            conn = Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                raise_exceptions=True,
            )

            # Search for computer objects
            search_filter = "(objectClass=computer)"
            attributes = [
                "cn",                      # Common name (hostname)
                "dNSHostName",             # FQDN
                "operatingSystem",         # OS name
                "operatingSystemVersion",  # OS version
                "distinguishedName",       # Full DN
                "lastLogonTimestamp",      # Last login
                "description",             # Description field
            ]

            conn.search(
                search_base=self.base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
            )

            for entry in conn.entries:
                try:
                    hostname = str(entry.cn) if entry.cn else None
                    dns_hostname = str(entry.dNSHostName) if hasattr(entry, 'dNSHostName') and entry.dNSHostName else None
                    os_name = str(entry.operatingSystem) if hasattr(entry, 'operatingSystem') and entry.operatingSystem else None
                    os_version = str(entry.operatingSystemVersion) if hasattr(entry, 'operatingSystemVersion') and entry.operatingSystemVersion else None
                    dn = str(entry.distinguishedName) if entry.distinguishedName else None

                    # Extract domain from DN
                    domain = self._extract_domain_from_dn(dn) if dn else None

                    # We don't have IP from AD - will need to resolve
                    # For now, use hostname as placeholder
                    device = DiscoveredDevice(
                        ip_address=dns_hostname or hostname or "unknown",
                        hostname=hostname,
                        os_name=os_name,
                        os_version=os_version,
                        discovery_source=DiscoverySource.AD,
                        distinguished_name=dn,
                        domain=domain,
                        is_domain_joined=True,
                    )
                    devices.append(device)

                    logger.debug(f"AD: Found computer {hostname} ({os_name})")

                except Exception as e:
                    logger.warning(f"Error processing AD entry: {e}")
                    continue

            conn.unbind()
            logger.info(f"AD discovery found {len(devices)} computers")

        except LDAPException as e:
            logger.error(f"LDAP error during AD discovery: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during AD discovery: {e}")

        return devices

    def _extract_domain_from_dn(self, dn: str) -> str:
        """Extract domain name from distinguished name."""
        # DN like "CN=COMPUTER,OU=Computers,DC=example,DC=com"
        # Extract DC components -> example.com
        parts = []
        for component in dn.split(","):
            if component.upper().startswith("DC="):
                parts.append(component[3:])
        return ".".join(parts) if parts else ""


class ADDiscoveryConfig:
    """Configuration helper for AD discovery."""

    @staticmethod
    def from_domain(
        domain: str,
        username: str,
        password: str,
        use_ssl: bool = False,
    ) -> ADDiscovery:
        """
        Create AD discovery from domain name.

        Args:
            domain: Domain name (e.g., "example.com")
            username: Username in UPN format (e.g., "scanner@example.com")
            password: Password
            use_ssl: Use LDAPS
        """
        # Convert domain to base DN
        base_dn = ",".join(f"DC={part}" for part in domain.split("."))

        # Use domain controller (typically the domain name resolves to DC)
        server = domain

        return ADDiscovery(
            server=server,
            base_dn=base_dn,
            bind_dn=username,
            bind_password=password,
            use_ssl=use_ssl,
        )
