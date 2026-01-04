#!/usr/bin/env python3
"""
Appliance provisioning module for QR code / provision code based setup.

Handles first-boot appliance configuration via partner-issued provision codes.
Works alongside MAC-based provisioning as an alternative provisioning method.

Usage:
    # Check if provisioning needed
    if needs_provisioning():
        run_provisioning_cli()

    # Or with explicit code (automated)
    run_provisioning_auto("ABCD1234EFGH5678")
"""

import os
import re
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
import urllib.request
import urllib.error
import json
import ssl

# Configuration paths
CONFIG_DIR = Path("/var/lib/msp")
CONFIG_PATH = CONFIG_DIR / "config.yaml"
DEFAULT_API_ENDPOINT = "https://api.osiriscare.net"


def needs_provisioning() -> bool:
    """Check if appliance needs provisioning (no config.yaml exists)."""
    return not CONFIG_PATH.exists()


def get_mac_address() -> str:
    """
    Get the primary MAC address of the appliance.

    Prefers physical ethernet interfaces over wireless.
    Returns formatted MAC like "84:3A:5B:91:B6:61".
    """
    # Try to get MAC from network interfaces
    net_path = Path("/sys/class/net")

    # Priority order: eth*, en*, then anything else
    interfaces = []
    if net_path.exists():
        for iface in net_path.iterdir():
            name = iface.name
            if name == "lo":
                continue

            address_file = iface / "address"
            if address_file.exists():
                mac = address_file.read_text().strip().upper()
                if mac and mac != "00:00:00:00:00:00":
                    # Prioritize by interface name
                    if name.startswith("eth"):
                        interfaces.insert(0, mac)
                    elif name.startswith("en"):
                        if not interfaces or not interfaces[0].startswith("eth"):
                            interfaces.insert(0, mac)
                        else:
                            interfaces.append(mac)
                    else:
                        interfaces.append(mac)

    if interfaces:
        return interfaces[0]

    # Fallback: generate pseudo-MAC from hostname
    hostname = get_hostname()
    return f"02:{hostname[:10].encode().hex()[:10].upper()}"


def get_hostname() -> str:
    """Get the appliance hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "osiriscare-appliance"


def generate_api_key(length: int = 32) -> str:
    """Generate a cryptographically secure API key."""
    return secrets.token_urlsafe(length)


def claim_provision_code(
    code: str,
    api_endpoint: str = DEFAULT_API_ENDPOINT
) -> Tuple[bool, Dict[str, Any]]:
    """
    Claim a provision code from Central Command.

    Args:
        code: 16-character provision code (e.g., "ABCD1234EFGH5678")
        api_endpoint: API base URL

    Returns:
        (success, data) where data contains either:
        - On success: site_id, appliance_id, partner info
        - On failure: error message
    """
    # Validate code format
    code = code.upper().replace("-", "").replace(" ", "")
    if not re.match(r'^[A-Z0-9]{16}$', code):
        return False, {"error": "Invalid provision code format. Expected 16 alphanumeric characters."}

    mac_address = get_mac_address()
    hostname = get_hostname()

    url = f"{api_endpoint}/api/partners/claim"

    payload = {
        "provision_code": code,
        "mac_address": mac_address,
        "hostname": hostname
    }

    try:
        # Create SSL context (allow self-signed in dev)
        ctx = ssl.create_default_context()

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OsirisCare-Appliance/1.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=45, context=ctx) as response:
            data = json.loads(response.read().decode('utf-8'))

            if data.get("status") == "claimed":
                return True, data
            else:
                return False, {"error": data.get("message", "Unknown error")}

    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            error_data = json.loads(body)
            return False, {"error": error_data.get("detail", f"HTTP {e.code}")}
        except json.JSONDecodeError:
            return False, {"error": f"HTTP {e.code}: {body[:100]}"}

    except urllib.error.URLError as e:
        if "timed out" in str(e.reason):
            return False, {"error": "Connection timed out"}
        return False, {"error": f"Failed to connect to server: {e.reason}"}

    except Exception as e:
        return False, {"error": f"Unexpected error: {str(e)}"}


def create_config(
    site_id: str,
    appliance_id: str,
    partner_info: Optional[Dict[str, Any]] = None,
    api_endpoint: str = DEFAULT_API_ENDPOINT
) -> Path:
    """
    Create the appliance configuration file.

    Args:
        site_id: Site identifier
        appliance_id: Unique appliance identifier
        partner_info: Partner branding info (slug, brand_name, primary_color)
        api_endpoint: API base URL

    Returns:
        Path to created config file
    """
    # Ensure directory exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    api_key = generate_api_key()
    mac_address = get_mac_address()
    hostname = get_hostname()
    now = datetime.now(timezone.utc).isoformat()

    config_content = f"""# OsirisCare Appliance Configuration
# Generated: {now}
# Do not edit manually

site_id: {site_id}
appliance_id: {appliance_id}
api_key: {api_key}
api_endpoint: {api_endpoint}
poll_interval: 60
enable_drift_detection: true
enable_evidence_upload: true
enable_l1_sync: true
healing_enabled: true
healing_dry_run: false
state_dir: /var/lib/msp
log_level: INFO
"""

    # Add partner section if present
    if partner_info:
        config_content += "\n# Partner branding\npartner:\n"
        for key, value in partner_info.items():
            if isinstance(value, str) and ('#' in value or ':' in value):
                config_content += f'  {key}: "{value}"\n'
            else:
                config_content += f"  {key}: {value}\n"

    # Add metadata
    config_content += f"""
# Provisioning metadata
provisioned_at: {now}
mac_address: {mac_address}
hostname: {hostname}
"""

    # Write config with secure permissions
    CONFIG_PATH.write_text(config_content)
    os.chmod(CONFIG_PATH, 0o600)

    return CONFIG_PATH


def run_provisioning_cli(api_endpoint: str = DEFAULT_API_ENDPOINT) -> bool:
    """
    Run interactive CLI provisioning.

    Prompts user for provision code and claims it.

    Returns:
        True if provisioning succeeded, False otherwise
    """
    mac_address = get_mac_address()
    hostname = get_hostname()

    print()
    print("=" * 60)
    print("  OsirisCare Appliance Provisioning")
    print("=" * 60)
    print()
    print(f"MAC Address: {mac_address}")
    print(f"Hostname:    {hostname}")
    print()
    print("Enter your provision code (from partner dashboard):")
    print("Format: XXXXXXXXXXXXXXXX (16 characters)")
    print()

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            code = input("Provision Code: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nProvisioning cancelled.")
            return False

        if not code:
            print("No code entered. Try again.")
            continue

        print()
        print("Provisioning...")

        success, data = claim_provision_code(code, api_endpoint)

        if success:
            # Create config file
            config_path = create_config(
                site_id=data["site_id"],
                appliance_id=data["appliance_id"],
                partner_info=data.get("partner"),
                api_endpoint=data.get("api_endpoint", api_endpoint)
            )

            print()
            print("=" * 60)
            print("  Provisioning Complete!")
            print("=" * 60)
            print()
            print(f"Site ID:     {data['site_id']}")
            if data.get("partner", {}).get("brand_name"):
                print(f"Partner:     {data['partner']['brand_name']}")
            print(f"Config:      {config_path}")
            print()
            print("The agent will now restart in normal operation mode.")
            print()
            return True
        else:
            error = data.get("error", "Unknown error")
            print(f"Error: {error}")

            if attempt < max_attempts:
                print(f"Attempts remaining: {max_attempts - attempt}")
                print()
            else:
                print()
                print("Maximum attempts reached.")
                print("Please verify your provision code and try again.")
                return False

    return False


def run_provisioning_auto(
    code: str,
    api_endpoint: str = DEFAULT_API_ENDPOINT
) -> bool:
    """
    Run non-interactive provisioning with provided code.

    Args:
        code: 16-character provision code
        api_endpoint: API base URL

    Returns:
        True if provisioning succeeded, False otherwise
    """
    print(f"Auto-provisioning with code: {code[:4]}...{code[-4:]}")

    success, data = claim_provision_code(code, api_endpoint)

    if success:
        config_path = create_config(
            site_id=data["site_id"],
            appliance_id=data["appliance_id"],
            partner_info=data.get("partner"),
            api_endpoint=data.get("api_endpoint", api_endpoint)
        )
        print(f"Provisioning complete. Config: {config_path}")
        return True
    else:
        print(f"Provisioning failed: {data.get('error', 'Unknown error')}")
        return False


# Entry point for CLI
def main():
    """CLI entry point for manual provisioning."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OsirisCare Appliance Provisioning"
    )
    parser.add_argument(
        '--code',
        metavar='CODE',
        help='Provision code (non-interactive mode)'
    )
    parser.add_argument(
        '--api-endpoint',
        default=DEFAULT_API_ENDPOINT,
        help=f'API endpoint (default: {DEFAULT_API_ENDPOINT})'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Check if provisioning is needed'
    )
    parser.add_argument(
        '--mac',
        action='store_true',
        help='Print MAC address and exit'
    )

    args = parser.parse_args()

    if args.check:
        if needs_provisioning():
            print("Provisioning required")
            return 1
        else:
            print("Already provisioned")
            return 0

    if args.mac:
        print(get_mac_address())
        return 0

    if args.code:
        success = run_provisioning_auto(args.code, args.api_endpoint)
        return 0 if success else 1

    # Interactive mode
    success = run_provisioning_cli(args.api_endpoint)
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
