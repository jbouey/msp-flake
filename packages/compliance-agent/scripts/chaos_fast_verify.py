#!/usr/bin/env python3
"""
Chaos Lab Fast Verifier — checks telemetry DB instead of WinRM re-scan.

Instead of waiting 20 minutes and re-running WinRM checks, this queries
Central Command's execution_telemetry table for healing events that match
the injected drift. Verifies healing in ~30 seconds instead of 20 minutes.

Usage:
    # Inject drift, wait 2 scan cycles (30s), then verify
    python3 chaos_fast_verify.py --incident-type defender_exclusions --host 192.168.88.251

    # Verify all recent heals
    python3 chaos_fast_verify.py --all --since 5m

    # Batch verify from chaos lab results file
    python3 chaos_fast_verify.py --results-file results/2026-04-01.json
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)


API_BASE = os.environ.get("CENTRAL_COMMAND_URL", "https://api.osiriscare.net")
SITE_ID = os.environ.get("SITE_ID", "physical-appliance-pilot-1aea78")


def parse_duration(s: str) -> timedelta:
    """Parse duration string like '5m', '1h', '30s'."""
    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))
    elif s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    elif s.endswith("s"):
        return timedelta(seconds=int(s[:-1]))
    return timedelta(minutes=int(s))


def check_telemetry(incident_type: str, host: str = None, since_minutes: int = 30) -> dict:
    """Query execution telemetry for healing events matching the incident type."""
    # Use the admin API to query telemetry
    params = {
        "incident_type": incident_type,
        "since_minutes": since_minutes,
        "site_id": SITE_ID,
    }
    if host:
        params["hostname"] = host

    try:
        resp = requests.get(
            f"{API_BASE}/api/dashboard/admin/telemetry",
            params=params,
            timeout=10,
            cookies={"session": os.environ.get("SESSION_COOKIE", "")},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass

    # Fallback: query DB directly via psql if we have VPS access
    return None


def check_healing_via_db(incident_type: str, host: str, since_minutes: int) -> dict:
    """Direct DB query for healing telemetry."""
    import subprocess

    since_interval = f"{since_minutes} minutes"
    query = f"""
        SELECT runbook_id, success, count(*), max(created_at) as latest
        FROM execution_telemetry
        WHERE incident_type = '{incident_type}'
          AND created_at > now() - interval '{since_interval}'
        GROUP BY runbook_id, success
        ORDER BY latest DESC
        LIMIT 5
    """

    try:
        result = subprocess.run(
            [
                "ssh", "root@178.156.162.116",
                f"docker exec mcp-postgres psql -U mcp -d mcp -t -A -c \"{query}\""
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            rows = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split("|")
                if len(parts) >= 4:
                    rows.append({
                        "runbook_id": parts[0],
                        "success": parts[1] == "t",
                        "count": int(parts[2]),
                        "latest": parts[3],
                    })
            return {"rows": rows, "total": sum(r["count"] for r in rows)}
    except Exception as e:
        print(f"DB query failed: {e}", file=sys.stderr)

    return None


def verify_incident(incident_type: str, host: str = None, since_minutes: int = 30) -> bool:
    """Check if an incident was healed recently."""
    result = check_healing_via_db(incident_type, host, since_minutes)
    if not result or not result.get("rows"):
        print(f"  {incident_type}: NO TELEMETRY (not detected or not attempted)")
        return False

    successes = sum(r["count"] for r in result["rows"] if r["success"])
    failures = sum(r["count"] for r in result["rows"] if not r["success"])
    total = successes + failures

    if successes > 0:
        latest = result["rows"][0]
        print(f"  {incident_type}: HEALED ({successes}/{total}, runbook={latest['runbook_id']}, latest={latest['latest']})")
        return True
    else:
        print(f"  {incident_type}: FAILED ({failures} attempts, runbook={result['rows'][0]['runbook_id']})")
        return False


def verify_all(since_minutes: int = 30):
    """Verify all recent healing attempts."""
    result = check_healing_via_db("", None, since_minutes)
    if not result:
        print("No telemetry data found")
        return

    healed = sum(1 for r in result["rows"] if r["success"])
    failed = sum(1 for r in result["rows"] if not r["success"])
    total = healed + failed
    rate = (healed / total * 100) if total > 0 else 0

    print(f"\nHealing Summary (last {since_minutes}m):")
    print(f"  Total attempts: {total}")
    print(f"  Healed: {healed}")
    print(f"  Failed: {failed}")
    print(f"  Rate: {rate:.1f}%")
    print()

    for r in result["rows"]:
        status = "HEALED" if r["success"] else "FAILED"
        print(f"  [{status}] {r['runbook_id']} × {r['count']} (latest: {r['latest']})")


def verify_results_file(path: str, since_minutes: int = 30):
    """Verify healing for all scenarios in a chaos lab results file."""
    with open(path) as f:
        data = json.load(f)

    scenarios = data.get("scenarios", [])
    if not scenarios:
        print("No scenarios in results file")
        return

    healed = 0
    failed = 0
    no_data = 0

    print(f"Verifying {len(scenarios)} scenarios from {path}:")
    print()

    for s in scenarios:
        incident_type = s.get("incident_type", s.get("check_type", s.get("name", "unknown")))
        host = s.get("hostname", s.get("host", None))
        if verify_incident(incident_type, host, since_minutes):
            healed += 1
        elif s.get("monitoring_only"):
            no_data += 1
        else:
            failed += 1

    total = healed + failed
    rate = (healed / total * 100) if total > 0 else 0
    print(f"\n{'='*50}")
    print(f"RESULT: {healed}/{total} healed ({rate:.1f}%)")
    if no_data:
        print(f"  ({no_data} monitoring-only scenarios excluded)")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Chaos Lab Fast Verifier")
    parser.add_argument("--incident-type", "-t", help="Check specific incident type")
    parser.add_argument("--host", help="Filter by hostname")
    parser.add_argument("--since", default="30m", help="Look back window (default: 30m)")
    parser.add_argument("--all", action="store_true", help="Show all recent telemetry")
    parser.add_argument("--results-file", help="Verify from chaos lab results JSON")

    args = parser.parse_args()
    since = int(parse_duration(args.since).total_seconds() / 60)

    if args.results_file:
        verify_results_file(args.results_file, since)
    elif args.all:
        verify_all(since)
    elif args.incident_type:
        verify_incident(args.incident_type, args.host, since)
    else:
        verify_all(since)


if __name__ == "__main__":
    main()
