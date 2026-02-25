"""
Network scanner configuration.

Configuration is SEPARATE from the compliance-agent configuration
to maintain credential isolation (blast radius containment).

Scanner credentials are stored in /var/lib/msp/scanner_creds.yaml
(separate from healer credentials).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


def _detect_local_subnets() -> list[str]:
    """Auto-detect scannable subnets from local network interfaces.

    Returns CIDR ranges for all non-loopback IPv4 interfaces.
    Works on Linux (NixOS appliance) via netifaces or ip command fallback.
    """
    subnets = []

    # Try netifaces first (available on appliance)
    try:
        import netifaces
        for iface in netifaces.interfaces():
            if iface == "lo":
                continue
            addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
            for addr in addrs:
                ip = addr.get("addr", "")
                netmask = addr.get("netmask", "")
                if ip and netmask and not ip.startswith("127."):
                    import ipaddress
                    try:
                        network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                        subnets.append(str(network))
                    except ValueError:
                        pass
        if subnets:
            logger.info(f"Auto-detected network ranges: {subnets}")
            return subnets
    except ImportError:
        pass

    # Fallback: parse `ip -4 addr` output (Linux)
    try:
        import subprocess
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            import ipaddress
            for line in result.stdout.splitlines():
                parts = line.split()
                # Format: "2: eth0 inet 192.168.88.241/24 ..."
                iface = parts[1] if len(parts) > 1 else ""
                if iface == "lo":
                    continue
                for part in parts:
                    if "/" in part:
                        try:
                            network = ipaddress.IPv4Network(part, strict=False)
                            if not network.is_loopback:
                                subnets.append(str(network))
                            break
                        except ValueError:
                            continue
            if subnets:
                logger.info(f"Auto-detected network ranges (ip cmd): {subnets}")
                return subnets
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.warning("Could not auto-detect network ranges")
    return []


@dataclass
class ScannerConfig:
    """
    Network scanner configuration.

    IMPORTANT: Scanner credentials are separate from compliance-agent
    credentials to limit blast radius if compromised.
    """

    # Network ranges to scan
    network_ranges: list[str] = field(default_factory=list)

    # Discovery methods
    enable_ad_discovery: bool = True
    enable_arp_discovery: bool = True
    enable_nmap_discovery: bool = True
    enable_go_agent_checkins: bool = True

    # AD/LDAP settings (scanner-specific credentials)
    ad_server: Optional[str] = None
    ad_base_dn: Optional[str] = None
    ad_bind_dn: Optional[str] = None
    ad_bind_password: Optional[str] = None

    # SNMP community string (for network devices)
    snmp_community: str = "public"

    # Scanning behavior
    nmap_scan_type: str = "syn"  # syn, connect
    nmap_arguments: str = "-sS -sV --top-ports 1000"
    max_concurrent_scans: int = 10
    scan_timeout_seconds: int = 300
    host_timeout_seconds: int = 60

    # Medical device handling - ALWAYS EXCLUDED BY DEFAULT
    exclude_medical_by_default: bool = True  # This should never be False
    medical_detection_ports: list[int] = field(
        default_factory=lambda: [104, 2575, 2761, 2762, 11112, 4242, 8042]
    )

    # Schedule
    daily_scan_hour: int = 2  # 2 AM
    daily_scan_minute: int = 0

    # API server (for on-demand scans)
    api_host: str = "127.0.0.1"
    api_port: int = 8082

    # Database paths
    db_path: Path = field(default_factory=lambda: Path("/var/lib/msp/devices.db"))
    credentials_path: Path = field(default_factory=lambda: Path("/var/lib/msp/scanner_creds.yaml"))

    # Central Command sync
    central_command_url: Optional[str] = None
    site_id: Optional[str] = None
    api_key: Optional[str] = None
    sync_interval_seconds: int = 300  # 5 minutes

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "ScannerConfig":
        """Load configuration from environment variables."""
        config = cls()

        # Network ranges (comma-separated, or "auto" for auto-detection)
        ranges = os.getenv("NETWORK_RANGES", "")
        if ranges.strip().lower() == "auto":
            config.network_ranges = _detect_local_subnets()
        elif ranges:
            config.network_ranges = [r.strip() for r in ranges.split(",")]

        # Discovery methods
        config.enable_ad_discovery = os.getenv("ENABLE_AD", "true").lower() == "true"
        config.enable_arp_discovery = os.getenv("ENABLE_ARP", "true").lower() == "true"
        config.enable_nmap_discovery = os.getenv("ENABLE_NMAP", "true").lower() == "true"
        config.enable_go_agent_checkins = os.getenv("ENABLE_GO_AGENT", "true").lower() == "true"

        # AD settings
        config.ad_server = os.getenv("AD_SERVER")
        config.ad_base_dn = os.getenv("AD_BASE_DN")
        config.ad_bind_dn = os.getenv("AD_BIND_DN")
        config.ad_bind_password = os.getenv("AD_BIND_PASSWORD")

        # Schedule
        config.daily_scan_hour = int(os.getenv("DAILY_SCAN_HOUR", "2"))
        config.daily_scan_minute = int(os.getenv("DAILY_SCAN_MINUTE", "0"))

        # API server
        config.api_host = os.getenv("API_HOST", "127.0.0.1")
        config.api_port = int(os.getenv("API_PORT", "8082"))

        # Paths
        if db_path := os.getenv("DB_PATH"):
            config.db_path = Path(db_path)
        if creds_path := os.getenv("CREDENTIALS_PATH"):
            config.credentials_path = Path(creds_path)

        # Central Command
        config.central_command_url = os.getenv("CENTRAL_COMMAND_URL")
        config.site_id = os.getenv("SITE_ID")
        config.api_key = os.getenv("API_KEY")

        # Logging
        config.log_level = os.getenv("LOG_LEVEL", "INFO")

        return config

    @classmethod
    def from_yaml(cls, path: Path) -> "ScannerConfig":
        """Load configuration from YAML file."""
        if not path.exists():
            logger.warning(f"Config file not found: {path}, using defaults")
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        # Map YAML keys to config attributes
        if "network_ranges" in data:
            config.network_ranges = data["network_ranges"]

        if "discovery" in data:
            d = data["discovery"]
            config.enable_ad_discovery = d.get("ad", True)
            config.enable_arp_discovery = d.get("arp", True)
            config.enable_nmap_discovery = d.get("nmap", True)
            config.enable_go_agent_checkins = d.get("go_agent", True)

        if "ad" in data:
            a = data["ad"]
            config.ad_server = a.get("server")
            config.ad_base_dn = a.get("base_dn")
            config.ad_bind_dn = a.get("bind_dn")
            config.ad_bind_password = a.get("bind_password")

        if "schedule" in data:
            s = data["schedule"]
            config.daily_scan_hour = s.get("hour", 2)
            config.daily_scan_minute = s.get("minute", 0)

        if "api" in data:
            a = data["api"]
            config.api_host = a.get("host", "127.0.0.1")
            config.api_port = a.get("port", 8082)

        if "central_command" in data:
            c = data["central_command"]
            config.central_command_url = c.get("url")
            config.site_id = c.get("site_id")
            config.api_key = c.get("api_key")

        if "paths" in data:
            p = data["paths"]
            if "db" in p:
                config.db_path = Path(p["db"])
            if "credentials" in p:
                config.credentials_path = Path(p["credentials"])

        config.log_level = data.get("log_level", "INFO")

        return config

    def load_credentials(self) -> bool:
        """
        Load scanner credentials from separate credentials file.

        This keeps scanner credentials isolated from healer credentials.
        """
        if not self.credentials_path.exists():
            logger.warning(f"Credentials file not found: {self.credentials_path}")
            return False

        try:
            with open(self.credentials_path) as f:
                creds = yaml.safe_load(f) or {}

            if "ad" in creds:
                self.ad_bind_dn = creds["ad"].get("bind_dn", self.ad_bind_dn)
                self.ad_bind_password = creds["ad"].get("bind_password", self.ad_bind_password)

            if "snmp" in creds:
                self.snmp_community = creds["snmp"].get("community", self.snmp_community)

            logger.info("Scanner credentials loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return False

    def validate(self) -> list[str]:
        """Validate configuration, returning list of errors."""
        errors = []

        if not self.network_ranges:
            errors.append("No network ranges configured")

        if self.enable_ad_discovery and not self.ad_server:
            errors.append("AD discovery enabled but no AD server configured")

        if self.daily_scan_hour < 0 or self.daily_scan_hour > 23:
            errors.append(f"Invalid scan hour: {self.daily_scan_hour}")

        # CRITICAL: Medical devices must be excluded by default
        if not self.exclude_medical_by_default:
            errors.append("CRITICAL: Medical devices must be excluded by default")
            self.exclude_medical_by_default = True  # Force correct setting

        return errors


# Example scanner_creds.yaml:
"""
# /var/lib/msp/scanner_creds.yaml
# SEPARATE from healer credentials for blast radius containment

ad:
  bind_dn: "CN=scanner,OU=Service Accounts,DC=example,DC=com"
  bind_password: "scanner-password-here"

snmp:
  community: "public"
"""

# Example scanner_config.yaml:
"""
# /var/lib/msp/scanner_config.yaml

network_ranges:
  - "192.168.88.0/24"
  - "10.0.0.0/24"

discovery:
  ad: true
  arp: true
  nmap: true
  go_agent: true

ad:
  server: "dc1.northvalley.local"
  base_dn: "DC=northvalley,DC=local"

schedule:
  hour: 2
  minute: 0

api:
  host: "127.0.0.1"
  port: 8082

central_command:
  url: "https://api.osiriscare.net"
  site_id: "north-valley-dental"
  api_key: "sk-..."

paths:
  db: "/var/lib/msp/devices.db"
  credentials: "/var/lib/msp/scanner_creds.yaml"

log_level: "INFO"
"""
