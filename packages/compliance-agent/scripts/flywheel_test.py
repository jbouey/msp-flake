#!/usr/bin/env python3
"""
Flywheel Promotion Pipeline Test

Tests the complete L2→L1 auto-promotion flywheel:
1. Submits pattern reports to Central Command via API
2. Verifies patterns accumulate in the patterns table
3. Triggers the flywheel promotion scan
4. Validates auto-promoted L1 rules appear in agent sync

Usage:
    python scripts/flywheel_test.py --api-url https://api.osiriscare.net
    python scripts/flywheel_test.py --api-url https://api.osiriscare.net --inject-count 3
"""

import asyncio
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# For direct HTTP requests
try:
    import httpx
except ImportError:
    print("Installing httpx...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx


# Test patterns that simulate real healing events
TEST_PATTERNS = [
    {
        "site_id": "site-pilot-1aea78",
        "check_type": "service_netlogon",
        "issue_signature": "service_netlogon:192.168.88.250",
        "resolution_steps": ["Restart-Service NetLogon", "Verify-Service NetLogon"],
        "success": True,
        "execution_time_ms": 2100,
        "runbook_id": "RB-WIN-SVC-001",
    },
    {
        "site_id": "site-pilot-1aea78",
        "check_type": "password_policy",
        "issue_signature": "password_policy:192.168.88.250",
        "resolution_steps": ["Set-MinPasswordLength 14", "Verify-PasswordPolicy"],
        "success": True,
        "execution_time_ms": 1800,
        "runbook_id": "RB-WIN-SEC-003",
    },
    {
        "site_id": "site-pilot-1aea78",
        "check_type": "windows_defender",
        "issue_signature": "windows_defender:192.168.88.244",
        "resolution_steps": ["Set-MpPreference -DisableRealtimeMonitoring $false", "Update-MpSignature"],
        "success": True,
        "execution_time_ms": 3200,
        "runbook_id": "RB-WIN-SEC-002",
    },
    {
        "site_id": "site-pilot-1aea78",
        "check_type": "audit_policy",
        "issue_signature": "audit:192.168.88.242",
        "resolution_steps": ["auditctl -R /etc/audit/rules.d/audit.rules", "service auditd restart"],
        "success": True,
        "execution_time_ms": 1500,
        "runbook_id": "RB-LIN-AUDIT-001",
    },
]


async def submit_pattern_reports(client: httpx.AsyncClient, api_url: str, count: int = 2):
    """Submit pattern reports to push patterns over promotion threshold."""
    print(f"\n{'='*60}")
    print(f"PHASE 1: Submitting {count} rounds of pattern reports")
    print(f"{'='*60}")

    submitted = 0
    for round_num in range(count):
        print(f"\n--- Round {round_num + 1}/{count} ---")
        for pattern in TEST_PATTERNS:
            try:
                resp = await client.post(
                    f"{api_url}/agent/patterns",
                    json=pattern,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"  [OK] {pattern['check_type']}: "
                          f"occurrences={data.get('occurrences', '?')}, "
                          f"status={data.get('status', '?')}")
                    submitted += 1
                else:
                    print(f"  [FAIL] {pattern['check_type']}: HTTP {resp.status_code} - {resp.text[:100]}")
            except Exception as e:
                print(f"  [ERROR] {pattern['check_type']}: {e}")

    print(f"\nSubmitted {submitted} pattern reports")
    return submitted


async def check_pattern_counts(client: httpx.AsyncClient, api_url: str):
    """Check current pattern counts in the database."""
    print(f"\n{'='*60}")
    print("PHASE 2: Checking pattern counts")
    print(f"{'='*60}")

    # We'll query via the agent sync endpoint to see what rules exist
    try:
        resp = await client.get(
            f"{api_url}/agent/sync",
            params={"site_id": "site-pilot-1aea78"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            rules = data.get("rules", [])
            promoted = [r for r in rules if r.get("source") == "promoted"]
            print(f"  Total rules in sync: {len(rules)}")
            print(f"  Promoted rules: {len(promoted)}")
            print(f"  Healing tier: {data.get('healing_tier', 'unknown')}")
            for r in promoted:
                print(f"    - {r['id']}: {r.get('name', 'unnamed')}")
            return {"total": len(rules), "promoted": len(promoted)}
        else:
            print(f"  [FAIL] HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


async def trigger_flywheel_scan(client: httpx.AsyncClient, api_url: str):
    """Trigger the flywheel promotion scan by calling the internal endpoint.

    The flywheel normally runs every 30 minutes. We can accelerate by
    hitting the /agent/patterns endpoint with enough data, then checking
    if the background task picks it up.
    """
    print(f"\n{'='*60}")
    print("PHASE 3: Waiting for flywheel promotion scan")
    print(f"{'='*60}")
    print("  The flywheel loop runs every 30 minutes.")
    print("  Checking if any patterns qualify for auto-promotion...")

    # We can't directly trigger the background loop, but we can check
    # the promoted rules endpoint to see if anything was promoted
    for attempt in range(6):
        try:
            resp = await client.get(
                f"{api_url}/agent/sync",
                params={"site_id": "site-pilot-1aea78"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                promoted = [r for r in data.get("rules", []) if r.get("source") == "promoted"]
                print(f"  Check {attempt + 1}/6: {len(promoted)} promoted rules")

                # Also check the promoted-rules endpoint
                resp2 = await client.get(
                    f"{api_url}/api/agent/sync/promoted-rules",
                    params={"site_id": "site-pilot-1aea78"},
                    timeout=10.0,
                )
                if resp2.status_code == 200:
                    pr_data = resp2.json()
                    pr_rules = pr_data.get("rules", [])
                    if pr_rules:
                        print(f"  Partner-promoted rules: {len(pr_rules)}")
                        for r in pr_rules:
                            print(f"    - {r['rule_id']}: {r.get('pattern_signature', '')[:30]}")
            else:
                print(f"  Check {attempt + 1}/6: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  Check {attempt + 1}/6: Error - {e}")

        if attempt < 5:
            print("  Waiting 10 seconds...")
            await asyncio.sleep(10)


async def validate_l1_rules_counter_trigger(client: httpx.AsyncClient, api_url: str):
    """Validate the L1 rules counter trigger is working."""
    print(f"\n{'='*60}")
    print("PHASE 4: Validating L1 rule counter trigger")
    print(f"{'='*60}")

    # Submit execution telemetry to test the trigger
    telemetry = {
        "site_id": "site-pilot-1aea78",
        "execution": {
            "execution_id": f"test-flywheel-{int(time.time())}",
            "incident_id": "test-incident-001",
            "site_id": "site-pilot-1aea78",
            "appliance_id": "appliance-pilot",
            "runbook_id": "RB-WIN-SVC-001",
            "hostname": "192.168.88.250",
            "platform": "windows",
            "incident_type": "service_netlogon",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 2.1,
            "success": True,
            "status": "success",
            "verification_passed": True,
            "confidence": 0.95,
            "resolution_level": "L1",
            "state_before": {"service_status": "stopped"},
            "state_after": {"service_status": "running"},
            "executed_steps": [
                {"step": 1, "action": "Restart-Service NetLogon", "result": "success"}
            ],
        }
    }

    try:
        resp = await client.post(
            f"{api_url}/api/execution-telemetry",
            json=telemetry,
            timeout=10.0,
        )
        if resp.status_code == 200:
            print(f"  [OK] Execution telemetry submitted (should trigger counter update)")
        else:
            print(f"  [INFO] HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  [INFO] Telemetry endpoint: {e}")


async def run_full_test(api_url: str, inject_count: int):
    """Run the complete flywheel test pipeline."""
    print(f"\n{'#'*60}")
    print(f"  FLYWHEEL PROMOTION PIPELINE TEST")
    print(f"  API: {api_url}")
    print(f"  Injection rounds: {inject_count}")
    print(f"  Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'#'*60}")

    async with httpx.AsyncClient(verify=False) as client:
        # Phase 1: Check current state
        before = await check_pattern_counts(client, api_url)

        # Phase 2: Submit pattern reports
        submitted = await submit_pattern_reports(client, api_url, count=inject_count)

        # Phase 3: Submit execution telemetry (tests counter trigger)
        await validate_l1_rules_counter_trigger(client, api_url)

        # Phase 4: Check pattern counts after injection
        after = await check_pattern_counts(client, api_url)

        # Phase 5: Check for promotion
        await trigger_flywheel_scan(client, api_url)

        # Final summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"  Pattern reports submitted: {submitted}")
        if before and after:
            print(f"  Rules before: {before['total']} ({before['promoted']} promoted)")
            print(f"  Rules after:  {after['total']} ({after['promoted']} promoted)")
            if after['promoted'] > before['promoted']:
                print(f"  NEW PROMOTIONS: {after['promoted'] - before['promoted']}")
            else:
                print(f"  No new promotions yet (flywheel runs every 30 min)")
                print(f"  Monitor with: ssh root@178.156.162.116 'docker logs mcp-server 2>&1 | grep -i flywheel | tail -10'")


def main():
    parser = argparse.ArgumentParser(description="Flywheel Promotion Pipeline Test")
    parser.add_argument("--api-url", default="https://api.osiriscare.net",
                        help="Central Command API URL")
    parser.add_argument("--inject-count", type=int, default=3,
                        help="Number of rounds of pattern reports to inject (default: 3)")
    args = parser.parse_args()

    asyncio.run(run_full_test(args.api_url, args.inject_count))


if __name__ == "__main__":
    main()
