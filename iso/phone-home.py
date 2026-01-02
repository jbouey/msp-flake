#!/usr/bin/env python3
"""
Minimal phone-home agent for OsirisCare Compliance Appliance.
Quick-fix version - will be replaced with proper Nix package.
"""

import json
import socket
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# Configuration
MCP_URL = "https://api.osiriscare.net"
CHECKIN_ENDPOINT = "/api/appliances/checkin"
CONFIG_PATH = Path("/var/lib/msp/config.yaml")
POLL_INTERVAL = 60  # seconds
VERSION = "0.1.1-quickfix"


def get_hostname() -> str:
    """Get system hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def get_mac_address() -> str:
    """Get primary MAC address."""
    try:
        # Try to get from /sys/class/net
        for iface in Path("/sys/class/net").iterdir():
            if iface.name in ("lo", "docker0", "virbr0"):
                continue
            addr_file = iface / "address"
            if addr_file.exists():
                mac = addr_file.read_text().strip()
                if mac and mac != "00:00:00:00:00:00":
                    return mac
    except Exception:
        pass
    return "00:00:00:00:00:00"


def get_ip_addresses() -> list:
    """Get all non-loopback IP addresses."""
    ips = []
    try:
        result = subprocess.run(
            ["/run/current-system/sw/bin/ip", "-4", "-o", "addr", "show"],
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


def get_site_id() -> str:
    """Get site ID from config file or default."""
    try:
        if CONFIG_PATH.exists():
            # Simple YAML parsing (no dependencies)
            content = CONFIG_PATH.read_text()
            for line in content.splitlines():
                if line.strip().startswith("site_id:"):
                    return line.split(":", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return "unconfigured"


def get_api_key() -> str:
    """Get API key from config file."""
    try:
        if CONFIG_PATH.exists():
            content = CONFIG_PATH.read_text()
            for line in content.splitlines():
                if line.strip().startswith("api_key:"):
                    return line.split(":", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return ""


def get_nixos_version() -> str:
    """Get NixOS version."""
    try:
        version_file = Path("/etc/os-release")
        if version_file.exists():
            content = version_file.read_text()
            for line in content.splitlines():
                if line.startswith("VERSION_ID="):
                    return line.split("=")[1].strip('"')
    except Exception:
        pass
    return "unknown"


def phone_home():
    """Send checkin to Central Command."""
    payload = {
        "site_id": get_site_id(),
        "mac_address": get_mac_address(),
        "hostname": get_hostname(),
        "ip_addresses": get_ip_addresses(),
        "agent_version": VERSION,
        "nixos_version": get_nixos_version(),
        "uptime_seconds": get_uptime_seconds(),
    }

    url = f"{MCP_URL}{CHECKIN_ENDPOINT}"
    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"osiriscare-appliance/{VERSION}",
    }

    # Add API key if configured
    api_key = get_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")
            timestamp = datetime.now(timezone.utc).isoformat()
            print(f"[{timestamp}] Checkin OK: {status} - Site: {payload['site_id']}")
            return True
    except urllib.error.HTTPError as e:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[{timestamp}] Checkin HTTP error: {e.code} {e.reason}")
        return False
    except urllib.error.URLError as e:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[{timestamp}] Checkin connection error: {e.reason}")
        return False
    except Exception as e:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[{timestamp}] Checkin error: {e}")
        return False


def main():
    """Main loop - phone home every POLL_INTERVAL seconds."""
    print(f"OsirisCare Compliance Agent v{VERSION}")
    print(f"Site ID: {get_site_id()}")
    print(f"Hostname: {get_hostname()}")
    print(f"MAC: {get_mac_address()}")
    print(f"IPs: {get_ip_addresses()}")
    print(f"MCP URL: {MCP_URL}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print("-" * 50)

    # Initial delay to let network settle
    time.sleep(5)

    while True:
        phone_home()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
