#!/usr/bin/env python3
"""
Chaos Probe - Test L1 → L2 → L3 Escalation Flow

Injects incidents that don't match L1 rules to verify escalation works.

Usage:
    # Test L1→L2 escalation (no matching L1 rule)
    python scripts/chaos_probe.py --type l2

    # Test L1→L2→L3 escalation (high risk action)
    python scripts/chaos_probe.py --type l3

    # Test L1 match (should resolve at L1)
    python scripts/chaos_probe.py --type l1

    # Custom incident
    python scripts/chaos_probe.py --check-type "database_corruption" --severity critical
"""

import asyncio
import argparse
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Add the src directory to path
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

from compliance_agent.incident_db import IncidentDatabase, Incident, ResolutionLevel
from compliance_agent.auto_healer import AutoHealer, AutoHealerConfig
from compliance_agent.level1_deterministic import DeterministicEngine
from compliance_agent.level2_llm import Level2Planner, LLMConfig, LLMMode


# Predefined chaos scenarios
CHAOS_SCENARIOS = {
    # Should match L1 rule and resolve quickly
    "l1": {
        "check_type": "ntp_sync",
        "severity": "medium",
        "message": "[CHAOS] NTP sync failed - should match L1-NIX-NTP-001",
        "context": {
            "ntp_synchronized": False,
            "chaos_probe": True,
        }
    },
    # Should NOT match L1, escalate to L2
    "l2": {
        "check_type": "certificate_expiry",
        "severity": "high",
        "message": "[CHAOS] TLS certificate expires in 3 days - no L1 rule, should escalate to L2",
        "context": {
            "domain": "api.example.com",
            "expiry_date": "2026-01-08",
            "days_remaining": 3,
            "chaos_probe": True,
        }
    },
    # Should escalate all the way to L3 (critical severity, no safe action)
    "l3": {
        "check_type": "database_corruption",
        "severity": "critical",
        "message": "[CHAOS] Database integrity check failed - should escalate to L3",
        "context": {
            "database": "patient_records",
            "corruption_detected": True,
            "affected_tables": ["patients", "encounters"],
            "chaos_probe": True,
        }
    },
    # Memory pressure - no L1 rule
    "memory": {
        "check_type": "memory_pressure",
        "severity": "high",
        "message": "[CHAOS] Memory usage at 95% - no L1 rule",
        "context": {
            "memory_percent": 95,
            "available_mb": 512,
            "chaos_probe": True,
        }
    },
    # Windows service crash - should match L1
    "service": {
        "check_type": "windows_defender",
        "severity": "high",
        "message": "[CHAOS] Windows Defender service stopped",
        "context": {
            "service": "WinDefend",
            "status": "stopped",
            "chaos_probe": True,
        }
    },
}


async def run_chaos_probe(
    check_type: str,
    severity: str,
    message: str,
    context: dict,
    db_path: str = "/tmp/chaos_incidents.db",
    rules_dir: str = None,
    dry_run: bool = True,
    submit_to_central: bool = True,
    site_id: str = "chaos-probe-test"
):
    """Inject a chaos incident and observe the healing flow."""

    print("=" * 60)
    print("CHAOS PROBE - Testing Three-Tier Healing")
    print("=" * 60)
    print(f"\nInjecting incident:")
    print(f"  Check Type: {check_type}")
    print(f"  Severity:   {severity}")
    print(f"  Message:    {message}")
    print(f"  Dry Run:    {dry_run}")
    print(f"  Submit to Central Command: {submit_to_central}")
    print()

    # Initialize components
    db = IncidentDatabase(db_path)

    # Find rules directory
    if rules_dir is None:
        rules_dir = Path(__file__).parent.parent / "src" / "compliance_agent" / "rules"

    # Create incident (using dataclass fields)
    # IMPORTANT: L1 rules look for fields in raw_data like:
    #   - check_type (e.g., "ntp_sync", "windows_defender")
    #   - status (e.g., "fail", "pass", "warning")
    import uuid
    incident = Incident(
        id=str(uuid.uuid4()),
        site_id=site_id,
        host_id="localhost",
        incident_type=check_type,
        severity=severity,
        raw_data={
            "check_type": check_type,  # L1 rules match on this!
            "status": "fail",          # L1 rules match on this!
            "message": message,
            **context
        },
        pattern_signature=f"{check_type}:{severity}",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    print(f"Created incident: {incident.id}")
    print()

    # Submit to Central Command for stats tracking
    central_response = None
    if submit_to_central:
        api_url = os.environ.get("CENTRAL_COMMAND_URL", "https://api.osiriscare.net")
        print("-" * 40)
        print("Submitting to Central Command...")
        print("-" * 40)
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                # Use the /incidents endpoint format
                incident_data = {
                    "site_id": site_id,
                    "host_id": "chaos-probe",
                    "incident_type": check_type,
                    "severity": severity,
                    "check_type": check_type,
                    "details": {
                        "message": message,
                        "chaos_probe": True,
                        **context
                    },
                    "pre_state": {},
                    "hipaa_controls": ["164.308(a)(1)(i)"]  # General HIPAA admin safeguard
                }
                async with session.post(
                    f"{api_url}/incidents",
                    json=incident_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        central_response = await resp.json()
                        print(f"✅ Incident submitted to Central Command")
                        print(f"   Incident ID: {central_response.get('incident_id', 'N/A')}")
                        print(f"   Resolution Level: {central_response.get('resolution_level', 'N/A')}")
                        print(f"   Runbook: {central_response.get('runbook_id', 'N/A')}")
                        if central_response.get('order_id'):
                            print(f"   Order ID: {central_response.get('order_id')}")
                    elif resp.status == 429:
                        print(f"⚠️  Rate limited by Central Command")
                    elif resp.status == 404:
                        print(f"⚠️  Site not found - appliance must be registered first")
                        print(f"   Use --site-id with a registered site (e.g., physical-appliance-pilot-1aea78)")
                    else:
                        text = await resp.text()
                        print(f"⚠️  Central Command returned {resp.status}: {text[:200]}")
        except Exception as e:
            print(f"⚠️  Could not reach Central Command: {e}")
        print()

    # Check L1 rules
    print("-" * 40)
    print("LEVEL 1: Checking deterministic rules...")
    print("-" * 40)

    l1_engine = DeterministicEngine(rules_dir=rules_dir)
    # Rules are loaded automatically in __init__

    print(f"Loaded {len(l1_engine.rules)} L1 rules")

    l1_match = l1_engine.match(
        incident_id=incident.id,
        incident_type=incident.incident_type,
        severity=incident.severity,
        data=incident.raw_data
    )

    if l1_match:
        print(f"\n✅ L1 MATCH FOUND: {l1_match.rule.id}")
        print(f"   Rule: {l1_match.rule.name}")
        print(f"   Action: {l1_match.rule.action}")
        print(f"   Parameters: {l1_match.rule.action_params}")
        print(f"\n   Resolution: L1 would handle this (no escalation needed)")
        return {
            "resolution_level": "L1",
            "rule_id": l1_match.rule.id,
            "action": l1_match.rule.action,
        }
    else:
        print("\n❌ No L1 rule matched - escalating to L2...")

    # L2 planning
    print()
    print("-" * 40)
    print("LEVEL 2: LLM Planner...")
    print("-" * 40)

    # Check if we have an LLM API key
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if not api_key:
        print("\n⚠️  No LLM API key found (ANTHROPIC_API_KEY or OPENAI_API_KEY)")
        print("   L2 would normally generate a remediation plan here.")
        print("   Set an API key to test actual L2 planning.")
        print("\n   Simulating L2 failure → escalating to L3...")

        return {
            "resolution_level": "L3",
            "reason": "No L1 match and no LLM API key configured",
            "escalation": True,
        }

    # Try L2 with actual LLM
    # Detect provider from API key format
    api_provider = "anthropic" if api_key.startswith("sk-ant") else "openai"
    api_model = "claude-3-haiku-20240307" if api_provider == "anthropic" else "gpt-4o-mini"

    llm_config = LLMConfig(
        mode=LLMMode.API,
        api_provider=api_provider,
        api_model=api_model,
        api_key=api_key,
        api_timeout=30,
    )

    l2_planner = Level2Planner(incident_db=db, config=llm_config)

    print(f"Using LLM: {api_provider} / {api_model}")
    print("Generating remediation plan...")

    try:
        decision = await l2_planner.plan(incident)

        if decision and not decision.escalate_to_l3:
            print(f"\n✅ L2 generated plan:")
            print(f"   Confidence: {decision.confidence:.1%}")
            print(f"   Action: {decision.recommended_action}")
            print(f"   Parameters: {decision.action_params}")
            print(f"   Reasoning: {decision.reasoning}")
            if decision.runbook_id:
                print(f"   Runbook: {decision.runbook_id}")

            if dry_run:
                print("\n   [DRY RUN] Would execute this action")
            else:
                print("\n   Executing action...")
                # Execute would happen here

            return {
                "resolution_level": "L2",
                "confidence": decision.confidence,
                "action": decision.recommended_action,
                "params": decision.action_params,
                "reasoning": decision.reasoning,
            }
        else:
            reason = decision.reasoning if decision else "No decision returned"
            print(f"\n❌ L2 recommends escalation: {reason}")
            print("   Escalating to L3...")

    except Exception as e:
        print(f"\n❌ L2 error: {e}")
        print("   Escalating to L3...")

    # L3 escalation - Generate actual email
    print()
    print("-" * 40)
    print("LEVEL 3: Human Escalation")
    print("-" * 40)

    # Generate the escalation email
    l2_reasoning = locals().get('decision')
    l2_reason = l2_reasoning.reasoning if l2_reasoning else "L2 not attempted or failed"

    email_subject = f"[{severity.upper()}] [{check_type}] Compliance Incident Requires Attention"
    email_body = f"""
================================================================================
                    🚨 COMPLIANCE INCIDENT ESCALATION 🚨
================================================================================

INCIDENT DETAILS
----------------
  ID:           {incident.id}
  Type:         {check_type}
  Severity:     {severity.upper()}
  Site:         {incident.site_id}
  Host:         {incident.host_id}
  Time:         {incident.created_at}

DESCRIPTION
-----------
{message}

CONTEXT DATA
------------
{json.dumps(context, indent=2)}

ESCALATION REASON
-----------------
  L1 (Deterministic): No matching rule found
  L2 (LLM Analysis):  {l2_reason}

RECOMMENDED ACTIONS
-------------------
  1. Review the incident details above
  2. Check system logs on the affected host
  3. Determine if manual intervention is required
  4. Document resolution in the incident tracking system

HIPAA CONSIDERATIONS
--------------------
  This incident may affect HIPAA compliance. Please ensure:
  - All actions are documented
  - PHI exposure is assessed
  - Breach notification procedures are followed if applicable

================================================================================
  Incident ID: {incident.id}
  Generated by: OsirisCare Compliance Agent v1.0.19
  Escalation Level: L3 (Human Required)
================================================================================
"""

    print("\n📧 ESCALATION EMAIL:")
    print("=" * 60)
    print(f"To:      compliance-alerts@osiriscare.net")
    print(f"Subject: {email_subject}")
    print("=" * 60)
    print(email_body)

    # Try to send via Central Command API if available
    api_url = os.environ.get("CENTRAL_COMMAND_URL", "https://api.osiriscare.net")
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            alert_data = {
                "site_id": incident.site_id,
                "alert_type": "escalation",
                "severity": severity,
                "subject": email_subject,
                "body": email_body,
                "incident_id": incident.id,
            }
            async with session.post(
                f"{api_url}/api/alerts/email",
                json=alert_data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    print("\n✅ Email sent via Central Command API")
                else:
                    print(f"\n⚠️  Could not send email (API returned {resp.status})")
    except Exception as e:
        print(f"\n⚠️  Email not sent (Central Command unavailable): {e}")

    return {
        "resolution_level": "L3",
        "escalation": True,
        "reason": "L1 no match, L2 unable to resolve",
        "email_subject": email_subject,
        "email_generated": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Chaos Probe - Test three-tier healing escalation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Predefined scenarios:
  l1       - NTP sync failure (should match L1 rule)
  l2       - Certificate expiry (no L1 rule, goes to L2)
  l3       - Database corruption (critical, goes to L3)
  memory   - Memory pressure (no L1 rule)
  service  - Windows Defender stopped (matches L1)

Examples:
  python scripts/chaos_probe.py --type l2
  python scripts/chaos_probe.py --check-type "custom_issue" --severity high
        """
    )

    parser.add_argument(
        "--type", "-t",
        choices=list(CHAOS_SCENARIOS.keys()),
        help="Predefined chaos scenario"
    )
    parser.add_argument(
        "--check-type", "-c",
        help="Custom check type"
    )
    parser.add_argument(
        "--severity", "-s",
        choices=["low", "medium", "high", "critical"],
        default="high",
        help="Incident severity"
    )
    parser.add_argument(
        "--message", "-m",
        help="Custom incident message"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute remediation (default: dry run)"
    )
    parser.add_argument(
        "--db", "-d",
        default="/tmp/chaos_incidents.db",
        help="Incident database path"
    )
    parser.add_argument(
        "--no-central",
        action="store_true",
        help="Skip submitting to Central Command (local test only)"
    )
    parser.add_argument(
        "--site-id",
        default="chaos-probe-test",
        help="Site ID to use for the incident"
    )

    args = parser.parse_args()

    # Determine scenario
    if args.type:
        scenario = CHAOS_SCENARIOS[args.type]
        check_type = scenario["check_type"]
        severity = scenario["severity"]
        message = scenario["message"]
        context = scenario["context"]
    elif args.check_type:
        check_type = args.check_type
        severity = args.severity
        message = args.message or f"[CHAOS] Custom probe: {check_type}"
        context = {"chaos_probe": True, "custom": True}
    else:
        parser.print_help()
        print("\nError: Specify --type or --check-type")
        sys.exit(1)

    # Run the probe
    result = asyncio.run(run_chaos_probe(
        check_type=check_type,
        severity=severity,
        message=message,
        context=context,
        db_path=args.db,
        dry_run=not args.execute,
        submit_to_central=not args.no_central,
        site_id=args.site_id,
    ))

    print()
    print("=" * 60)
    print(f"RESULT: Resolved at {result['resolution_level']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
