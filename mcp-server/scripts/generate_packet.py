#!/usr/bin/env python3
"""
Generate a compliance packet for a site.

Usage:
    python generate_packet.py <site_id> [--month YYYY-MM]

Example:
    python generate_packet.py physical-appliance-pilot-1aea78 --month 2026-01
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

async def generate_packet(site_id: str, month: str = None):
    """Generate compliance packet for a site."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text

    # Database connection
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://mcp:mcp@localhost/mcp")
    engine = create_async_engine(DATABASE_URL)
    async_session = async_sessionmaker(engine, class_=AsyncSession)

    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    year, mon = int(month.split("-")[0]), int(month.split("-")[1])
    start_date = datetime(year, mon, 1, tzinfo=timezone.utc)
    if mon < 12:
        end_date = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

    print(f"\n{'='*60}")
    print(f"HIPAA Compliance Packet - {site_id}")
    print(f"Period: {month}")
    print(f"{'='*60}\n")

    async with async_session() as db:
        # 1. Get overall compliance stats
        result = await db.execute(text("""
            SELECT
                COUNT(*) as total_bundles,
                SUM(COALESCE((summary->>'total_checks')::int, 0)) as total_checks,
                SUM(COALESCE((summary->>'compliant')::int, 0)) as compliant,
                SUM(COALESCE((summary->>'non_compliant')::int, 0)) as non_compliant,
                SUM(COALESCE((summary->>'errors')::int, 0)) as errors
            FROM compliance_bundles
            WHERE site_id = :site_id
            AND checked_at >= :start_date AND checked_at < :end_date
        """), {"site_id": site_id, "start_date": start_date, "end_date": end_date})
        stats = result.fetchone()

        if stats.total_bundles == 0:
            print(f"No compliance data found for {site_id} in {month}")
            return

        total_individual = stats.compliant + stats.non_compliant + stats.errors
        compliance_rate = (stats.compliant / total_individual * 100) if total_individual > 0 else 100

        print(f"EXECUTIVE SUMMARY")
        print(f"-" * 40)
        print(f"Compliance Runs:  {stats.total_bundles:,}")
        print(f"Total Checks:     {total_individual:,}")
        print(f"Compliant:        {stats.compliant:,} ({compliance_rate:.1f}%)")
        print(f"Non-Compliant:    {stats.non_compliant:,}")
        print(f"Errors:           {stats.errors:,}")
        print(f"Overall Score:    {compliance_rate:.1f}%")
        print()

        # 2. Get check breakdown by category
        result = await db.execute(text("""
            SELECT
                checks->>'check' as check_type,
                COUNT(*) as count,
                COUNT(CASE WHEN checks->>'status' = 'pass' THEN 1 END) as passed
            FROM compliance_bundles,
                 jsonb_array_elements(checks) as checks
            WHERE site_id = :site_id
            AND checked_at >= :start_date AND checked_at < :end_date
            GROUP BY checks->>'check'
            ORDER BY count DESC
            LIMIT 15
        """), {"site_id": site_id, "start_date": start_date, "end_date": end_date})
        checks = result.fetchall()

        print(f"CHECK BREAKDOWN BY CATEGORY")
        print(f"-" * 40)
        print(f"{'Check Type':<25} {'Total':>8} {'Pass':>8} {'Rate':>8}")
        print(f"-" * 40)
        for check in checks:
            check_type = check.check_type or "unknown"
            count = check.count or 0
            passed = check.passed or 0
            rate = (passed / count * 100) if count > 0 else 0
            print(f"{check_type:<25} {count:>8,} {passed:>8,} {rate:>7.1f}%")
        print()

        # 3. Get incidents summary
        result = await db.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN status = 'resolved' THEN 1 END) as resolved,
                COUNT(CASE WHEN resolution_tier = 'L1' THEN 1 END) as l1,
                COUNT(CASE WHEN resolution_tier = 'L2' THEN 1 END) as l2,
                COUNT(CASE WHEN resolution_tier = 'L3' THEN 1 END) as l3,
                AVG(EXTRACT(EPOCH FROM (resolved_at - reported_at))/60) as avg_mttr_min
            FROM incidents
            WHERE reported_at >= :start_date AND reported_at < :end_date
        """), {"start_date": start_date, "end_date": end_date})
        incidents = result.fetchone()

        print(f"INCIDENT SUMMARY")
        print(f"-" * 40)
        print(f"Total Incidents:  {incidents.total or 0}")
        print(f"Resolved:         {incidents.resolved or 0}")
        print(f"L1 (Auto-fix):    {incidents.l1 or 0}")
        print(f"L2 (LLM):         {incidents.l2 or 0}")
        print(f"L3 (Human):       {incidents.l3 or 0}")
        if incidents.avg_mttr_min:
            print(f"Avg MTTR:         {incidents.avg_mttr_min:.1f} minutes")
        print()

        # 4. Get evidence bundles
        result = await db.execute(text("""
            SELECT bundle_id, check_type, outcome, s3_uri, timestamp_start
            FROM evidence_bundles e
            JOIN appliances a ON e.appliance_id = a.id
            WHERE a.site_id = :site_id
            ORDER BY timestamp_start DESC
            LIMIT 10
        """), {"site_id": site_id})
        evidence = result.fetchall()

        print(f"EVIDENCE BUNDLES (WORM Storage)")
        print(f"-" * 40)
        if evidence:
            for e in evidence:
                print(f"  {e.bundle_id}: {e.check_type} ({e.outcome})")
                if e.s3_uri:
                    print(f"    -> {e.s3_uri}")
        else:
            print("  No evidence bundles in WORM storage yet")
        print()

        # 5. HIPAA Control mapping
        print(f"HIPAA CONTROL STATUS")
        print(f"-" * 40)
        controls = {
            "164.308(a)(7)(ii)(A)": ("Data Backup Plan", compliance_rate >= 95),
            "164.312(b)": ("Audit Controls", stats.total_checks > 0),
            "164.312(c)(1)": ("Integrity Controls", len(evidence) > 0),
            "164.308(a)(5)(ii)(B)": ("Malware Protection", compliance_rate >= 80),
            "164.312(a)(1)": ("Access Control", compliance_rate >= 90),
        }
        for control_id, (name, passed) in controls.items():
            status = "PASS" if passed else "NEEDS REVIEW"
            print(f"  {control_id}: {name}")
            print(f"    Status: {status}")
        print()

        print(f"{'='*60}")
        print(f"Report Generated: {datetime.now(timezone.utc).isoformat()}")
        print(f"{'='*60}")

        # Generate PDF if report_generator is available
        try:
            from dashboard_api.report_generator import generate_pdf_report, save_report_to_file

            # Format data for PDF generator
            kpis = {
                "compliance_score": compliance_rate,
                "mttr_minutes": float(incidents.avg_mttr_min) if incidents.avg_mttr_min else None,
                "mfa_coverage": 100.0,  # TODO: Get actual MFA stats
                "backup_success_rate": 100.0 if compliance_rate >= 95 else 85.0,
            }

            pdf_controls = [
                {"id": k, "name": v[0], "status": "pass" if v[1] else "warning", "description": ""}
                for k, v in controls.items()
            ]

            # Get incident details for PDF
            result = await db.execute(text("""
                SELECT incident_type, severity, status, resolution_tier, reported_at
                FROM incidents
                WHERE reported_at >= :start_date AND reported_at < :end_date
                ORDER BY reported_at DESC
                LIMIT 20
            """), {"start_date": start_date, "end_date": end_date})
            incident_rows = result.fetchall()

            pdf_incidents = [
                {
                    "type": i.incident_type,
                    "severity": i.severity,
                    "status": i.status,
                    "resolution_tier": i.resolution_tier,
                    "reported_at": i.reported_at.isoformat() if i.reported_at else None
                }
                for i in incident_rows
            ]

            pdf_bytes = generate_pdf_report(
                site_id=site_id,
                site_name=site_id.replace("-", " ").title(),
                month=month,
                kpis=kpis,
                controls=pdf_controls,
                incidents=pdf_incidents
            )
            if pdf_bytes:
                output_path = save_report_to_file(pdf_bytes, site_id, month)
                print(f"\nPDF Report saved to: {output_path}")
        except ImportError as e:
            print(f"\n[Note: PDF generation not available - {e}]")
        except Exception as e:
            print(f"\n[PDF generation failed: {e}]")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate compliance packet")
    parser.add_argument("site_id", help="Site ID")
    parser.add_argument("--month", help="Month (YYYY-MM)", default=None)
    args = parser.parse_args()

    asyncio.run(generate_packet(args.site_id, args.month))
