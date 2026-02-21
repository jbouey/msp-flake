#!/usr/bin/env python3
"""
Generate site-specific configuration for MSP Compliance Appliance.

This script generates all the configuration files needed to provision
an appliance for a specific clinic site.

Usage:
    python generate-config.py --site-id clinic-001 --site-name "Smith Family Practice"
    python generate-config.py --site-id clinic-001 --site-name "Smith" --timezone "America/New_York"

Output:
    ./appliance-config/<site-id>/
    ├── config.yaml           # Main config (copy to /var/lib/msp/)
    ├── certs/                 # mTLS certificates (copy to /etc/msp/certs/)
    │   ├── client.crt
    │   ├── client.key
    │   └── ca.crt
    └── registration.yaml     # Register this in Central Command

After running:
    1. Register site in Central Command using registration.yaml
    2. Copy config.yaml to USB or bake into image
    3. Copy certs/ to USB or bake into image
    4. Ship appliance to clinic
"""

import argparse
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml")
    sys.exit(1)


def generate_api_key() -> str:
    """Generate secure API key for site authentication."""
    return f"sk-site-{secrets.token_urlsafe(32)}"


def generate_portal_token() -> str:
    """Generate portal access token for client dashboard."""
    return secrets.token_urlsafe(48)


def generate_appliance_id() -> str:
    """Generate unique appliance identifier."""
    return f"app-{secrets.token_hex(8)}"


def run_openssl(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run openssl command."""
    return subprocess.run(
        ["openssl"] + args,
        capture_output=True,
        text=True,
        check=check
    )


def generate_mtls_certs(site_id: str, output_dir: Path) -> Path:
    """
    Generate mTLS client certificates for the appliance.

    If a CA exists at /etc/msp/ca/, uses it to sign.
    Otherwise, generates a self-signed certificate for testing.
    """
    certs_dir = output_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing CA
    ca_cert = Path("/etc/msp/ca/ca.crt")
    ca_key = Path("/etc/msp/ca/ca.key")
    use_ca = ca_cert.exists() and ca_key.exists()

    print(f"  Generating client key...")
    run_openssl([
        "genrsa",
        "-out", str(certs_dir / "client.key"),
        "4096"
    ])
    os.chmod(certs_dir / "client.key", 0o600)

    print(f"  Generating CSR...")
    run_openssl([
        "req",
        "-new",
        "-key", str(certs_dir / "client.key"),
        "-out", str(certs_dir / "client.csr"),
        "-subj", f"/CN={site_id}/O=OsirisCare/OU=ComplianceAppliance"
    ])

    if use_ca:
        print(f"  Signing with CA certificate...")
        run_openssl([
            "x509",
            "-req",
            "-in", str(certs_dir / "client.csr"),
            "-CA", str(ca_cert),
            "-CAkey", str(ca_key),
            "-CAcreateserial",
            "-out", str(certs_dir / "client.crt"),
            "-days", "365",
            "-sha256"
        ])
        # Copy CA cert
        subprocess.run(["cp", str(ca_cert), str(certs_dir / "ca.crt")], check=True)
        print(f"  Certificates signed with production CA")
    else:
        print(f"  WARNING: No CA found at /etc/msp/ca/")
        print(f"  Generating self-signed certificate for testing")
        run_openssl([
            "x509",
            "-req",
            "-in", str(certs_dir / "client.csr"),
            "-signkey", str(certs_dir / "client.key"),
            "-out", str(certs_dir / "client.crt"),
            "-days", "365",
            "-sha256"
        ])
        # Create placeholder CA cert
        subprocess.run([
            "cp",
            str(certs_dir / "client.crt"),
            str(certs_dir / "ca.crt")
        ], check=True)

    # Cleanup CSR
    (certs_dir / "client.csr").unlink()

    # Set permissions
    os.chmod(certs_dir / "client.crt", 0o644)
    os.chmod(certs_dir / "ca.crt", 0o644)

    return certs_dir


def generate_config(
    site_id: str,
    site_name: str,
    api_endpoint: str,
    timezone_str: str,
    api_key: str,
    portal_token: str,
    appliance_id: str,
) -> dict:
    """Generate the main configuration dictionary."""
    return {
        "site_id": site_id,
        "site_name": site_name,
        "appliance_id": appliance_id,
        "api_endpoint": api_endpoint,
        "api_key": api_key,
        "portal_token": portal_token,
        "tls": {
            "client_cert": "/etc/msp/certs/client.crt",
            "client_key": "/etc/msp/certs/client.key",
            "ca_cert": "/etc/msp/certs/ca.crt",
        },
        "agent": {
            "poll_interval": 60,
            "evidence_retention_days": 7,
            "offline_queue_max_mb": 100,
            "log_level": "INFO",
        },
        "local_status": {
            "enabled": True,
            "port": 80,
        },
        "maintenance_window": {
            "enabled": True,
            "start": "02:00",
            "end": "05:00",
            "timezone": timezone_str,
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
        "windows_targets": [],
        "discovery": {
            "enabled": True,
            "subnets": [],
            "exclude_ips": [],
        },
        "compliance": {
            "patch_critical_max_age_hours": 72,
            "patch_high_max_age_hours": 168,
            "backup_max_age_hours": 24,
            "backup_restore_test_max_age_days": 30,
            "privileged_access_review_days": 90,
            "sign_evidence": True,
        },
        "_generated": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "generator": "generate-config.py v1.0",
            "appliance_version": "1.0.0",
        },
    }


def generate_registration(
    site_id: str,
    site_name: str,
    api_key: str,
    portal_token: str,
    appliance_id: str,
) -> dict:
    """Generate registration info for Central Command."""
    return {
        "site_id": site_id,
        "site_name": site_name,
        "appliance_id": appliance_id,
        "api_key": api_key,
        "portal_token": portal_token,
        "portal_url": f"https://portal.osiriscare.net/site/{site_id}?token={portal_token}",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "instructions": {
            "step_1": "Add site to Central Command database",
            "step_2": "Register API key for authentication",
            "step_3": "Generate portal magic link for client",
            "step_4": "Copy config.yaml and certs/ to appliance",
            "step_5": "Ship appliance to clinic",
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate MSP Compliance Appliance configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--site-id",
        required=True,
        help="Unique site identifier (e.g., clinic-001)"
    )
    parser.add_argument(
        "--site-name",
        required=True,
        help="Human-readable site name (e.g., 'Smith Family Practice')"
    )
    parser.add_argument(
        "--output-dir",
        default="./appliance-config",
        help="Output directory (default: ./appliance-config)"
    )
    parser.add_argument(
        "--api-endpoint",
        default="https://api.osiriscare.net",
        help="Central Command API endpoint"
    )
    parser.add_argument(
        "--timezone",
        default="America/New_York",
        help="Timezone for maintenance window (default: America/New_York)"
    )
    parser.add_argument(
        "--skip-certs",
        action="store_true",
        help="Skip certificate generation (use existing)"
    )

    args = parser.parse_args()

    # Validate site_id format
    if not args.site_id.replace("-", "").replace("_", "").isalnum():
        print("ERROR: site-id must be alphanumeric with hyphens/underscores only")
        sys.exit(1)

    output_dir = Path(args.output_dir) / args.site_id
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"MSP Compliance Appliance Configuration Generator")
    print(f"{'='*60}")
    print(f"Site ID:   {args.site_id}")
    print(f"Site Name: {args.site_name}")
    print(f"Output:    {output_dir}")
    print(f"{'='*60}\n")

    # Generate secrets
    print("Generating secrets...")
    api_key = generate_api_key()
    portal_token = generate_portal_token()
    appliance_id = generate_appliance_id()
    print(f"  API Key:      {api_key[:20]}...")
    print(f"  Portal Token: {portal_token[:20]}...")
    print(f"  Appliance ID: {appliance_id}")

    # Generate certificates
    if not args.skip_certs:
        print("\nGenerating mTLS certificates...")
        certs_dir = generate_mtls_certs(args.site_id, output_dir)
        print(f"  Certificates saved to: {certs_dir}")
    else:
        print("\nSkipping certificate generation (--skip-certs)")

    # Generate main config
    print("\nGenerating configuration...")
    config = generate_config(
        site_id=args.site_id,
        site_name=args.site_name,
        api_endpoint=args.api_endpoint,
        timezone_str=args.timezone,
        api_key=api_key,
        portal_token=portal_token,
        appliance_id=appliance_id,
    )

    config_file = output_dir / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"  Config saved to: {config_file}")

    # Generate registration info
    print("\nGenerating registration info...")
    registration = generate_registration(
        site_id=args.site_id,
        site_name=args.site_name,
        api_key=api_key,
        portal_token=portal_token,
        appliance_id=appliance_id,
    )

    registration_file = output_dir / "registration.yaml"
    with open(registration_file, "w") as f:
        yaml.dump(registration, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"  Registration saved to: {registration_file}")

    # Print summary
    print(f"\n{'='*60}")
    print("Configuration generated successfully!")
    print(f"{'='*60}")
    print(f"""
Output directory: {output_dir}

Files created:
  - config.yaml       Copy to /var/lib/msp/ on appliance
  - certs/            Copy to /etc/msp/certs/ on appliance
  - registration.yaml Register this site in Central Command

Portal URL for clinic:
  https://portal.osiriscare.net/site/{args.site_id}?token={portal_token}

Next steps:
  1. Register site in Central Command:
     curl -X POST https://api.osiriscare.net/api/sites \\
       -H "Authorization: Bearer $ADMIN_TOKEN" \\
       -d @{registration_file}

  2. Copy config to USB drive:
     mkdir -p /mnt/usb/msp
     cp {config_file} /mnt/usb/msp/
     cp -r {output_dir}/certs /mnt/usb/msp/

  3. On appliance, install config:
     cp /mnt/usb/msp/config.yaml /var/lib/msp/
     cp -r /mnt/usb/msp/certs /etc/msp/
     systemctl restart appliance-daemon

  4. Verify phone-home:
     journalctl -u appliance-daemon -f
""")


if __name__ == "__main__":
    main()
