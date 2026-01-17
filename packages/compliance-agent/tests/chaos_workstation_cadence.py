#!/usr/bin/env python3
"""
Chaos Lab: Workstation Cadence Verification

Verifies the appliance agent runs workstation discovery and compliance
scans at the correct intervals:
- Discovery: Every 3600s (1 hour) from AD
- Compliance scans: Every 600s (10 minutes) on discovered workstations

This script monitors the appliance and validates timing behavior.
Run this as part of the chaos lab daily cycle.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

# Configuration
APPLIANCE_HOST = os.environ.get("APPLIANCE_HOST", "192.168.88.246")
APPLIANCE_USER = os.environ.get("APPLIANCE_USER", "root")
EXPECTED_SCAN_INTERVAL = 600  # 10 minutes
EXPECTED_DISCOVERY_INTERVAL = 3600  # 1 hour
TOLERANCE_SECONDS = 60  # Allow 1 minute variance

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def ssh_command(cmd: str, host: str = APPLIANCE_HOST, user: str = APPLIANCE_USER) -> Tuple[bool, str]:
    """Execute SSH command and return (success, output)."""
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", cmd],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "SSH command timed out"
    except Exception as e:
        return False, str(e)


def get_appliance_logs(lines: int = 100) -> str:
    """Fetch recent appliance agent logs."""
    success, output = ssh_command(f"journalctl -u compliance-agent -n {lines} --no-pager")
    return output if success else ""


def parse_workstation_events(logs: str) -> Dict[str, List[datetime]]:
    """Parse logs for workstation discovery and scan events."""
    events = {
        "discovery": [],
        "scan": [],
    }

    for line in logs.split("\n"):
        try:
            # Look for discovery events
            if "Starting workstation discovery from AD" in line or "enumerate_from_ad" in line:
                timestamp = extract_timestamp(line)
                if timestamp:
                    events["discovery"].append(timestamp)

            # Look for scan events
            if "Starting workstation compliance scan" in line or "run_all_checks" in line:
                timestamp = extract_timestamp(line)
                if timestamp:
                    events["scan"].append(timestamp)
        except Exception:
            continue

    return events


def extract_timestamp(log_line: str) -> Optional[datetime]:
    """Extract timestamp from log line."""
    try:
        # Format: "Jan 15 21:35:00 hostname service[pid]:"
        parts = log_line.split()
        if len(parts) >= 3:
            month_day_time = " ".join(parts[:3])
            year = datetime.now().year
            dt = datetime.strptime(f"{year} {month_day_time}", "%Y %b %d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    try:
        # Format: "2026-01-15T21:35:00+00:00"
        for part in log_line.split():
            if "T" in part and "-" in part:
                return datetime.fromisoformat(part.replace("Z", "+00:00"))
    except Exception:
        pass

    return None


def calculate_intervals(timestamps: List[datetime]) -> List[float]:
    """Calculate intervals between consecutive timestamps."""
    if len(timestamps) < 2:
        return []

    sorted_ts = sorted(timestamps)
    intervals = []
    for i in range(1, len(sorted_ts)):
        delta = (sorted_ts[i] - sorted_ts[i-1]).total_seconds()
        intervals.append(delta)

    return intervals


def verify_scan_cadence(intervals: List[float]) -> Tuple[bool, str]:
    """Verify scan intervals are within expected range."""
    if not intervals:
        return False, "No scan intervals found"

    failed = []
    for i, interval in enumerate(intervals):
        min_expected = EXPECTED_SCAN_INTERVAL - TOLERANCE_SECONDS
        max_expected = EXPECTED_SCAN_INTERVAL + TOLERANCE_SECONDS

        if not (min_expected <= interval <= max_expected):
            failed.append(f"Interval {i+1}: {interval:.0f}s (expected {min_expected}-{max_expected}s)")

    if failed:
        return False, f"Scan cadence violations: {'; '.join(failed)}"

    avg_interval = sum(intervals) / len(intervals)
    return True, f"Scan cadence OK: avg={avg_interval:.0f}s, count={len(intervals)}"


def verify_discovery_cadence(intervals: List[float]) -> Tuple[bool, str]:
    """Verify discovery intervals are within expected range."""
    if not intervals:
        return False, "No discovery intervals found"

    failed = []
    for i, interval in enumerate(intervals):
        min_expected = EXPECTED_DISCOVERY_INTERVAL - TOLERANCE_SECONDS
        max_expected = EXPECTED_DISCOVERY_INTERVAL + TOLERANCE_SECONDS

        if not (min_expected <= interval <= max_expected):
            failed.append(f"Interval {i+1}: {interval:.0f}s (expected {min_expected}-{max_expected}s)")

    if failed:
        return False, f"Discovery cadence violations: {'; '.join(failed)}"

    avg_interval = sum(intervals) / len(intervals)
    return True, f"Discovery cadence OK: avg={avg_interval:.0f}s, count={len(intervals)}"


def check_agent_running() -> Tuple[bool, str]:
    """Check if compliance agent is running."""
    success, output = ssh_command("systemctl is-active compliance-agent")
    if success and "active" in output:
        return True, "Agent is running"
    return False, f"Agent not running: {output}"


def check_workstation_config() -> Tuple[bool, str]:
    """Check if workstation scanning is configured."""
    success, output = ssh_command("cat /etc/osiriscare/agent.yaml 2>/dev/null | grep -E 'workstation|domain_controller'")
    if success and ("workstation_enabled" in output or "domain_controller" in output):
        return True, f"Workstation config found"
    return False, "Workstation scanning not configured"


def run_quick_check() -> Dict[str, any]:
    """Run a quick cadence verification check."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "passed": True,
    }

    # Check agent running
    success, msg = check_agent_running()
    results["checks"].append({"name": "agent_running", "passed": success, "message": msg})
    if not success:
        results["passed"] = False
        return results

    # Check workstation config
    success, msg = check_workstation_config()
    results["checks"].append({"name": "workstation_config", "passed": success, "message": msg})

    # Get and parse logs
    logs = get_appliance_logs(500)
    events = parse_workstation_events(logs)

    # Verify scan cadence
    scan_intervals = calculate_intervals(events["scan"])
    success, msg = verify_scan_cadence(scan_intervals)
    results["checks"].append({"name": "scan_cadence", "passed": success, "message": msg})
    if not success:
        results["passed"] = False

    # Verify discovery cadence
    discovery_intervals = calculate_intervals(events["discovery"])
    success, msg = verify_discovery_cadence(discovery_intervals)
    results["checks"].append({"name": "discovery_cadence", "passed": success, "message": msg})
    if not success:
        results["passed"] = False

    return results


def run_long_check(duration_minutes: int = 30) -> Dict[str, any]:
    """Run a long-duration cadence verification."""
    logger.info(f"Starting {duration_minutes} minute cadence verification...")

    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(minutes=duration_minutes)

    all_events = {"discovery": [], "scan": []}
    check_count = 0

    while datetime.now(timezone.utc) < end_time:
        check_count += 1
        logs = get_appliance_logs(100)
        events = parse_workstation_events(logs)

        # Merge new events
        for event_type in ["discovery", "scan"]:
            for ts in events[event_type]:
                if ts not in all_events[event_type]:
                    all_events[event_type].append(ts)
                    logger.info(f"Detected {event_type} event at {ts}")

        remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
        logger.info(f"Check {check_count}: {len(all_events['scan'])} scans, {len(all_events['discovery'])} discoveries, {remaining:.0f}s remaining")

        time.sleep(60)  # Check every minute

    # Final analysis
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_minutes": duration_minutes,
        "total_scans": len(all_events["scan"]),
        "total_discoveries": len(all_events["discovery"]),
        "checks": [],
        "passed": True,
    }

    # Expected counts
    expected_scans = duration_minutes // 10  # Every 10 min
    expected_discoveries = duration_minutes // 60  # Every hour

    # Verify scan count
    if len(all_events["scan"]) >= expected_scans:
        results["checks"].append({
            "name": "scan_count",
            "passed": True,
            "message": f"Got {len(all_events['scan'])} scans, expected >= {expected_scans}"
        })
    else:
        results["checks"].append({
            "name": "scan_count",
            "passed": False,
            "message": f"Got {len(all_events['scan'])} scans, expected >= {expected_scans}"
        })
        results["passed"] = False

    # Verify cadence intervals
    scan_intervals = calculate_intervals(all_events["scan"])
    success, msg = verify_scan_cadence(scan_intervals)
    results["checks"].append({"name": "scan_cadence", "passed": success, "message": msg})
    if not success:
        results["passed"] = False

    return results


def main():
    parser = argparse.ArgumentParser(description="Chaos Lab: Workstation Cadence Verification")
    parser.add_argument("--mode", choices=["quick", "long"], default="quick",
                       help="quick: Check recent logs, long: Monitor for 30+ minutes")
    parser.add_argument("--duration", type=int, default=30,
                       help="Duration in minutes for long mode")
    parser.add_argument("--json", action="store_true",
                       help="Output results as JSON")
    parser.add_argument("--host", default=APPLIANCE_HOST,
                       help="Appliance host")

    args = parser.parse_args()

    global APPLIANCE_HOST
    APPLIANCE_HOST = args.host

    if args.mode == "quick":
        results = run_quick_check()
    else:
        results = run_long_check(args.duration)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Workstation Cadence Verification Results")
        print(f"{'='*60}")
        print(f"Timestamp: {results['timestamp']}")
        print(f"Overall: {'PASSED' if results['passed'] else 'FAILED'}")
        print(f"\nChecks:")
        for check in results["checks"]:
            status = "PASS" if check["passed"] else "FAIL"
            print(f"  [{status}] {check['name']}: {check['message']}")
        print(f"{'='*60}\n")

    sys.exit(0 if results["passed"] else 1)


if __name__ == "__main__":
    main()
