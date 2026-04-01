#!/usr/bin/env python3
"""
Daily Healing Report — replaces chaos lab's WinRM verification with telemetry-based scoring.

Queries Central Command's execution_telemetry for real production healing rate.
Generates a report comparable to the chaos lab format. Can send email via the
existing alert infrastructure.

Run from any machine with SSH access to VPS (no iMac needed).

Usage:
    python3 daily_healing_report.py                    # Last 24h report
    python3 daily_healing_report.py --hours 6          # Last 6 hours
    python3 daily_healing_report.py --email             # Send via email
    python3 daily_healing_report.py --json              # JSON output
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


VPS_HOST = "root@178.156.162.116"
DB_CMD = 'docker exec mcp-postgres psql -U mcp -d mcp -t -A -c'


def query_db(sql: str) -> list[dict]:
    """Execute SQL on VPS and parse pipe-delimited output."""
    result = subprocess.run(
        ["ssh", VPS_HOST, f'{DB_CMD} "{sql}"'],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        print(f"DB query failed: {result.stderr}", file=sys.stderr)
        return []
    rows = []
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            rows.append(line.split("|"))
    return rows


def get_healing_summary(hours: int) -> dict:
    """Get overall healing rate and per-type breakdown."""
    # Overall
    overall = query_db(f"""
        SELECT count(*), count(*) FILTER (WHERE success),
               round(100.0 * count(*) FILTER (WHERE success) / NULLIF(count(*), 0), 1)
        FROM execution_telemetry
        WHERE created_at > now() - interval '{hours} hours'
    """)

    total = int(overall[0][0]) if overall else 0
    healed = int(overall[0][1]) if overall else 0
    rate = float(overall[0][2]) if overall and overall[0][2] else 0.0

    # Per category (map incident_type to chaos lab categories)
    category_map = {
        "firewall_status": "firewall", "firewall_dangerous_rules": "firewall",
        "windows_defender": "defender", "defender_exclusions": "defender",
        "defender_cloud_protection": "defender",
        "windows_update": "services", "service_status": "services",
        "screen_lock": "screenlock", "screen_lock_policy": "screenlock",
        "bitlocker_status": "screenlock", "bitlocker": "screenlock",
        "audit_policy": "audit", "security_audit": "audit",
        "password_policy": "credential_policy",
        "guest_account": "local_accounts",
        "registry_run_persistence": "registry_persistence",
        "rogue_scheduled_tasks": "scheduled_tasks",
        "smb_signing": "smb_security", "smb1_protocol": "smb_security",
        "wmi_event_persistence": "wmi_persistence",
    }

    breakdown = query_db(f"""
        SELECT incident_type, count(*), count(*) FILTER (WHERE success),
               round(100.0 * count(*) FILTER (WHERE success) / NULLIF(count(*), 0), 1)
        FROM execution_telemetry
        WHERE created_at > now() - interval '{hours} hours'
        GROUP BY incident_type ORDER BY count(*) DESC
    """)

    categories = {}
    by_type = []
    for row in breakdown:
        itype, attempts, successes, type_rate = row[0], int(row[1]), int(row[2]), float(row[3] or 0)
        by_type.append({
            "incident_type": itype,
            "attempts": attempts,
            "healed": successes,
            "rate": type_rate,
        })
        cat = category_map.get(itype, "other")
        if cat not in categories:
            categories[cat] = {"attempts": 0, "healed": 0}
        categories[cat]["attempts"] += attempts
        categories[cat]["healed"] += successes

    for cat in categories:
        c = categories[cat]
        c["rate"] = round(100.0 * c["healed"] / c["attempts"], 1) if c["attempts"] > 0 else 0.0

    return {
        "total": total,
        "healed": healed,
        "failed": total - healed,
        "rate": rate,
        "categories": categories,
        "by_type": by_type,
        "hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def format_text_report(data: dict) -> str:
    """Format report in chaos lab style."""
    rate = data["rate"]
    if rate >= 80:
        emoji, status = "\U0001f7e2", "HEALTHY"
    elif rate >= 50:
        emoji, status = "\U0001f7e1", "DEGRADED"
    else:
        emoji, status = "\U0001f534", "CRITICAL"

    lines = [
        f"PRODUCTION HEALING REPORT — Last {data['hours']}h",
        "=" * 50,
        "",
        f"{emoji} HEALING RATE: {rate}% ({status})",
        f"  Total: {data['total']} | Healed: {data['healed']} | Failed: {data['failed']}",
        "",
        "CATEGORY BREAKDOWN:",
    ]

    for cat, stats in sorted(data["categories"].items(), key=lambda x: -x[1]["attempts"]):
        mark = "\u2713" if stats["rate"] >= 80 else "\u2717"
        lines.append(f" {mark} {cat:<25} {stats['healed']}/{stats['attempts']} ({stats['rate']}%)")

    lines.append("")
    lines.append("PER-CHECK DETAIL:")
    for t in data["by_type"]:
        mark = "\u2713" if t["rate"] >= 80 else "\u2717"
        lines.append(f"   {mark} {t['incident_type']:<30} {t['healed']}/{t['attempts']} ({t['rate']}%)")

    lines.append("")
    lines.append(f"Generated: {data['generated_at']}")
    lines.append("Source: execution_telemetry (production)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Production Healing Report")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window (default 24h)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--email", action="store_true", help="Send email report")
    args = parser.parse_args()

    data = get_healing_summary(args.hours)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        report = format_text_report(data)
        print(report)

    if args.email:
        # Use VPS to send via the existing email infrastructure
        subject = f"[Healing Report] {data['rate']}% — last {args.hours}h"
        print(f"\nEmail subject: {subject}")
        print("(Email sending via Central Command API — TODO: wire to /api/alerts/email)")


if __name__ == "__main__":
    main()
