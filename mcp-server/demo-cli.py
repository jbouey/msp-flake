#!/usr/bin/env python3
"""
Demo CLI - Trigger Incidents for In-House Demo

Breaks things on command to demonstrate auto-remediation capabilities.
Simulates real-world incidents that the MCP system detects and fixes.

Usage:
    ./demo-cli.py break backup          # Simulate backup failure
    ./demo-cli.py break disk            # Fill disk to 95%
    ./demo-cli.py break service nginx   # Stop nginx service
    ./demo-cli.py break cert            # Expire SSL certificate (simulated)
    ./demo-cli.py break baseline        # Modify firewall rules
    ./demo-cli.py status                # Show current incident status
    ./demo-cli.py reset                 # Reset all incidents to normal
"""

import argparse
import subprocess
import sys
import json
import time
from pathlib import Path
from datetime import datetime
import requests

class DemoIncidentTrigger:
    """Trigger incidents for demo purposes"""

    def __init__(self, mcp_url: str = "http://localhost:8000"):
        self.mcp_url = mcp_url
        self.incident_log = Path("/tmp/msp-demo-incidents.json")
        self.state_dir = Path("/tmp/msp-demo-state")
        self.state_dir.mkdir(exist_ok=True)

    def trigger_backup_failure(self):
        """Simulate backup failure by creating corrupt backup state"""
        print("🔥 Triggering BACKUP FAILURE incident...")

        # Create fake backup directory
        backup_dir = self.state_dir / "backup"
        backup_dir.mkdir(exist_ok=True)

        # Create corrupt backup marker
        corrupt_file = backup_dir / "BACKUP_FAILED.txt"
        corrupt_file.write_text(f"Backup failed at {datetime.utcnow().isoformat()}\n")
        corrupt_file.write_text("Error: Disk full / Backup destination unreachable\n", mode='a')

        # Create log entry simulating backup failure
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": "ERROR",
            "service": "backup-service",
            "message": "Backup job failed: destination unreachable",
            "exit_code": 1,
            "incident_type": "backup_failure"
        }

        self._log_incident(log_entry)

        # Send incident to MCP server
        self._send_to_mcp({
            "client_id": "demo-clinic",
            "hostname": "demo-server",
            "incident_type": "backup_failure",
            "severity": "high",
            "details": {
                "backup_job": "daily-backup",
                "error": "Destination unreachable",
                "last_successful_backup": "2025-10-31T02:00:00Z"
            }
        })

        print("✅ Incident triggered! MCP should detect and remediate within 60s")
        print(f"   Log: {corrupt_file}")
        print(f"   Expected: MCP runs RB-BACKUP-001 to fix")

        return True

    def trigger_disk_full(self):
        """Simulate disk full by creating large temp file"""
        print("🔥 Triggering DISK FULL incident...")

        # Calculate available space
        result = subprocess.run(['df', '-h', '/tmp'], capture_output=True, text=True)
        print(f"Current /tmp status:\n{result.stdout}")

        # Create 1GB temp file to fill disk
        temp_file = self.state_dir / "disk-filler.bin"
        print(f"Creating 1GB temp file: {temp_file}")

        subprocess.run([
            'dd', 'if=/dev/zero', f'of={temp_file}',
            'bs=1M', 'count=1024'
        ], capture_output=True)

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": "CRITICAL",
            "service": "disk-monitor",
            "message": "Disk usage exceeded 95% threshold",
            "disk": "/tmp",
            "usage_percent": 96,
            "incident_type": "disk_full"
        }

        self._log_incident(log_entry)

        self._send_to_mcp({
            "client_id": "demo-clinic",
            "hostname": "demo-server",
            "incident_type": "disk_full",
            "severity": "critical",
            "details": {
                "disk": "/tmp",
                "usage_percent": 96,
                "threshold": 90,
                "available_mb": 512
            }
        })

        print("✅ Incident triggered! MCP should detect and remediate")
        print(f"   File: {temp_file} (1GB)")
        print(f"   Expected: MCP runs RB-DISK-001 to cleanup /tmp")

        return True

    def trigger_service_crash(self, service: str = "nginx"):
        """Simulate service crash by stopping service"""
        print(f"🔥 Triggering SERVICE CRASH incident ({service})...")

        # Check if service exists
        result = subprocess.run(
            ['systemctl', 'is-active', service],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print(f"   Stopping {service}...")
            subprocess.run(['systemctl', 'stop', service], capture_output=True)
        else:
            print(f"   {service} not running, creating simulated crash marker")
            crash_marker = self.state_dir / f"{service}.crashed"
            crash_marker.write_text(f"Service crashed at {datetime.utcnow().isoformat()}\n")

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": "ERROR",
            "service": service,
            "message": f"Service {service} unexpectedly stopped",
            "exit_code": 1,
            "incident_type": "service_crash"
        }

        self._log_incident(log_entry)

        self._send_to_mcp({
            "client_id": "demo-clinic",
            "hostname": "demo-server",
            "incident_type": "service_crash",
            "severity": "high",
            "details": {
                "service_name": service,
                "status": "stopped",
                "crash_time": datetime.utcnow().isoformat()
            }
        })

        print("✅ Incident triggered! MCP should detect and remediate")
        print(f"   Service: {service} (stopped)")
        print(f"   Expected: MCP runs RB-SERVICE-001 to restart")

        return True

    def trigger_cert_expiry(self):
        """Simulate certificate expiry"""
        print("🔥 Triggering CERT EXPIRY incident...")

        # Create fake cert with expiry marker
        cert_dir = self.state_dir / "certs"
        cert_dir.mkdir(exist_ok=True)

        cert_file = cert_dir / "demo.example.com.crt"
        cert_file.write_text(f"""-----BEGIN CERTIFICATE-----
[Simulated Certificate - Expires in 5 days]
Issued: 2024-01-01
Expires: 2025-11-06 (WARNING: < 7 days)
-----END CERTIFICATE-----
""")

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": "WARNING",
            "service": "cert-monitor",
            "message": "SSL certificate expires in 5 days",
            "cert_path": str(cert_file),
            "days_remaining": 5,
            "incident_type": "cert_expiry"
        }

        self._log_incident(log_entry)

        self._send_to_mcp({
            "client_id": "demo-clinic",
            "hostname": "demo-server",
            "incident_type": "cert_expiry",
            "severity": "medium",
            "details": {
                "cert_path": str(cert_file),
                "domain": "demo.example.com",
                "days_remaining": 5,
                "expiry_date": "2025-11-06"
            }
        })

        print("✅ Incident triggered! MCP should detect and remediate")
        print(f"   Cert: {cert_file}")
        print(f"   Expected: MCP runs RB-CERT-001 to renew")

        return True

    def trigger_baseline_drift(self):
        """Simulate baseline drift by modifying firewall rules"""
        print("🔥 Triggering BASELINE DRIFT incident...")

        drift_marker = self.state_dir / "baseline-drift.json"
        drift_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "drift_type": "firewall_rules",
            "expected": {
                "firewall_status": "active",
                "ssh_password_auth": "no",
                "ntp_synchronized": "yes"
            },
            "current": {
                "firewall_status": "inactive",  # DRIFT!
                "ssh_password_auth": "no",
                "ntp_synchronized": "yes"
            },
            "drift_detected": True
        }

        drift_marker.write_text(json.dumps(drift_data, indent=2))

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": "WARNING",
            "service": "baseline-enforcement",
            "message": "Baseline drift detected: firewall status",
            "drift_item": "firewall_status",
            "expected": "active",
            "current": "inactive",
            "incident_type": "baseline_drift"
        }

        self._log_incident(log_entry)

        self._send_to_mcp({
            "client_id": "demo-clinic",
            "hostname": "demo-server",
            "incident_type": "baseline_drift",
            "severity": "high",
            "details": drift_data
        })

        print("✅ Incident triggered! MCP should detect and remediate")
        print(f"   Drift: {drift_marker}")
        print(f"   Expected: MCP auto-remediates firewall status")

        return True

    def show_status(self):
        """Show current incident status"""
        print("\n📊 Current Incident Status\n")

        if not self.incident_log.exists():
            print("No incidents logged yet.")
            return

        with open(self.incident_log, 'r') as f:
            incidents = [json.loads(line) for line in f]

        print(f"Total incidents: {len(incidents)}\n")

        # Group by type
        by_type = {}
        for inc in incidents:
            itype = inc.get('incident_type', 'unknown')
            if itype not in by_type:
                by_type[itype] = []
            by_type[itype].append(inc)

        for itype, incs in by_type.items():
            print(f"  {itype}: {len(incs)} incidents")
            latest = incs[-1]
            print(f"    Latest: {latest['timestamp']}")
            print(f"    Message: {latest.get('message', 'N/A')}")
            print()

        # Check MCP server status
        print("🔍 Checking MCP server status...")
        try:
            response = requests.get(f"{self.mcp_url}/health", timeout=2)
            if response.status_code == 200:
                print(f"   ✅ MCP server is running at {self.mcp_url}")
            else:
                print(f"   ⚠️  MCP server returned status {response.status_code}")
        except Exception as e:
            print(f"   ❌ MCP server not reachable: {e}")

    def reset_all(self):
        """Reset all incidents to normal state"""
        print("\n🔄 Resetting all incidents to normal state...\n")

        # Remove disk filler
        temp_file = self.state_dir / "disk-filler.bin"
        if temp_file.exists():
            print(f"   Removing {temp_file}")
            temp_file.unlink()

        # Clear state directory
        for file in self.state_dir.iterdir():
            if file.is_file():
                print(f"   Removing {file}")
                file.unlink()

        # Archive incident log
        if self.incident_log.exists():
            archive = self.incident_log.with_suffix('.json.archive')
            print(f"   Archiving incident log to {archive}")
            self.incident_log.rename(archive)

        print("\n✅ All incidents reset!")

    def _log_incident(self, log_entry: dict):
        """Append incident to log file"""
        with open(self.incident_log, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def _send_to_mcp(self, incident_data: dict):
        """Send incident to MCP server"""
        try:
            response = requests.post(
                f"{self.mcp_url}/chat",
                json=incident_data,
                headers={"X-API-Key": "demo-key"},
                timeout=5
            )

            if response.status_code == 200:
                result = response.json()
                print(f"\n   📡 MCP Response:")
                print(f"      Runbook: {result.get('runbook_id', 'N/A')}")
                print(f"      Status: {result.get('status', 'N/A')}")
            else:
                print(f"\n   ⚠️  MCP returned status {response.status_code}")

        except Exception as e:
            print(f"\n   ⚠️  Could not reach MCP server: {e}")
            print(f"      (Incident logged locally, will be picked up by monitoring)")


def main():
    parser = argparse.ArgumentParser(
        description="Demo CLI - Trigger incidents for in-house demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./demo-cli.py break backup          # Trigger backup failure
  ./demo-cli.py break disk             # Fill disk to 95%
  ./demo-cli.py break service nginx    # Stop nginx
  ./demo-cli.py break cert             # Expire SSL cert
  ./demo-cli.py break baseline         # Drift from baseline
  ./demo-cli.py status                 # Show incident status
  ./demo-cli.py reset                  # Reset all incidents
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Break command
    break_parser = subparsers.add_parser('break', help='Trigger an incident')
    break_parser.add_argument(
        'incident_type',
        choices=['backup', 'disk', 'service', 'cert', 'baseline'],
        help='Type of incident to trigger'
    )
    break_parser.add_argument(
        'service_name',
        nargs='?',
        default='nginx',
        help='Service name (for service incident type)'
    )

    # Status command
    subparsers.add_parser('status', help='Show current incident status')

    # Reset command
    subparsers.add_parser('reset', help='Reset all incidents to normal')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    trigger = DemoIncidentTrigger()

    if args.command == 'break':
        if args.incident_type == 'backup':
            trigger.trigger_backup_failure()
        elif args.incident_type == 'disk':
            trigger.trigger_disk_full()
        elif args.incident_type == 'service':
            trigger.trigger_service_crash(args.service_name)
        elif args.incident_type == 'cert':
            trigger.trigger_cert_expiry()
        elif args.incident_type == 'baseline':
            trigger.trigger_baseline_drift()

    elif args.command == 'status':
        trigger.show_status()

    elif args.command == 'reset':
        trigger.reset_all()


if __name__ == "__main__":
    main()
