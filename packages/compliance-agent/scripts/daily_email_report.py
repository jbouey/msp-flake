#!/usr/bin/env python3
"""Daily healing report — queries VPS DB directly and sends email."""
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.text import MIMEText

CHAOS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    config = {}
    config_path = os.path.join(CHAOS_DIR, "config.env")
    if os.path.exists(config_path):
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def query_vps(sql):
    """Query VPS DB via SSH."""
    cmd = [
        "ssh", "-o", "ConnectTimeout=10", "root@178.156.162.116",
        f'docker exec mcp-postgres psql -U mcp -d mcp -t -A -c "{sql}"',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def build_report():
    lines = []
    lines.append(f"OsirisCare Daily Healing Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    # Healing rate
    healing = query_vps(
        "SELECT count(*) FILTER (WHERE success), count(*) "
        "FROM execution_telemetry WHERE created_at > NOW() - interval '24h'"
    )
    if healing and "|" in healing:
        s, t = healing.split("|")
        rate = round(int(s) / int(t) * 100, 1) if int(t) > 0 else 0
        lines.append(f"Healing Rate (24h): {rate}% ({s}/{t})")
    else:
        lines.append(f"Healing Rate: {healing}")

    # Incidents
    incidents = query_vps(
        "SELECT status, count(*) FROM incidents GROUP BY status ORDER BY count DESC"
    )
    lines.append("\nIncidents:")
    for line in incidents.split("\n"):
        if "|" in line:
            s, c = line.split("|")
            lines.append(f"  {s}: {c}")

    # Evidence
    evidence = query_vps(
        "SELECT site_id, count(*), count(*) FILTER (WHERE signature_valid) "
        "FROM compliance_bundles WHERE checked_at > NOW() - interval '24h' GROUP BY site_id"
    )
    lines.append("\nEvidence (24h):")
    for line in evidence.split("\n"):
        if "|" in line:
            parts = line.split("|")
            lines.append(f"  {parts[0]}: {parts[1]} bundles, {parts[2]} verified")

    # Witnesses
    witnesses = query_vps(
        "SELECT count(*) FROM witness_attestations WHERE created_at > NOW() - interval '24h'"
    )
    lines.append(f"\nWitness Attestations (24h): {witnesses}")

    # Appliances
    appliances = query_vps(
        "SELECT site_id, agent_version, NOW() - last_checkin as age "
        "FROM site_appliances ORDER BY site_id"
    )
    lines.append("\nAppliances:")
    for line in appliances.split("\n"):
        if "|" in line:
            parts = line.split("|")
            lines.append(f"  {parts[0]}: v{parts[1]} (age: {parts[2].strip()[:10]})")

    return "\n".join(lines)


def send_email(subject, body):
    config = load_config()
    smtp_user = config.get("SMTP_USER", "")
    smtp_pass = config.get("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        print("SMTP not configured — printing report only", file=sys.stderr)
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.get("SMTP_FROM", "alerts@osiriscare.net")
    msg["To"] = config.get("EMAIL_TO", "administrator@osiriscare.net")

    with smtplib.SMTP(config.get("SMTP_HOST", "mail.privateemail.com"), int(config.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(smtp_user, smtp_pass)
        s.sendmail(msg["From"], [msg["To"]], msg.as_string())
    print(f"Email sent: {subject}")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send_email(
        f"[OsirisCare] Daily Healing Report - {datetime.now().strftime('%Y-%m-%d')}",
        report,
    )
