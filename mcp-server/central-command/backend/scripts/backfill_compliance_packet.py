#!/usr/bin/env python3
"""One-off backfill for missed monthly HIPAA compliance packets.

Background (2026-05-02): substrate `compliance_packets_stalled` sev1 fired
for `physical-appliance-pilot-1aea78` April 2026. Auto-gen loop in
main.py::_compliance_packet_loop kept timing out on
`QueryCanceledError: statement timeout` because the orphan site has 962
April bundles and the CompliancePacket query plan exceeds the connection
pool's command_timeout. This script bypasses the timeout by setting
statement_timeout=0 on the session before invoking generate_packet().

Idempotent: uses the same INSERT ... ON CONFLICT (site_id, month, year,
framework) DO UPDATE pattern as the loop.

Usage:
    docker exec mcp-server python3 \\
      /app/dashboard_api/scripts/backfill_compliance_packet.py \\
      --site-id physical-appliance-pilot-1aea78 \\
      --year 2026 --month 4 --apply

DO NOT use this to backfill a packet for a period when the site emitted
no compliance_bundles — that creates a phantom attestation and breaks
HIPAA §164.316(b)(2)(i) chain of custody. The script verifies bundle
presence before generation; refuses if zero bundles in the period.

Per CLAUDE.md "no silent write failures" — all errors raise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone


def _get_db_url() -> str:
    """Prefer MIGRATION_DATABASE_URL (direct `mcp` superuser, bypasses
    PgBouncer + RLS). Falls back to DATABASE_URL but warns — `mcp_app`
    via PgBouncer respects RLS, which silently filters compliance_bundles
    and would cause this script to refuse the backfill with 0 bundles."""
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if url:
        return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("MIGRATION_DATABASE_URL or DATABASE_URL must be set")
    print("WARNING — using DATABASE_URL (mcp_app via PgBouncer); RLS may "
          "hide compliance_bundles. Set MIGRATION_DATABASE_URL instead.",
          file=sys.stderr)
    return url


async def _verify_bundles_exist(asyncpg_conn, site_id: str, year: int, month: int) -> int:
    """Refuse backfill if the site emitted no bundles in the target month.
    HIPAA §164.316(b)(2)(i) requires the attestation reflect REAL evidence,
    not a placeholder."""
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    row = await asyncpg_conn.fetchrow(
        """
        SELECT COUNT(*) AS n FROM compliance_bundles
        WHERE site_id = $1
          AND created_at >= make_timestamptz($2, $3, 1, 0, 0, 0, 'UTC')
          AND created_at <  make_timestamptz($4, $5, 1, 0, 0, 0, 'UTC')
        """,
        site_id, year, month, next_year, next_month,
    )
    return int(row["n"])


async def _packet_already_exists(asyncpg_conn, site_id, year, month, framework) -> bool:
    row = await asyncpg_conn.fetchval(
        """
        SELECT 1 FROM compliance_packets
        WHERE site_id = $1 AND month = $2 AND year = $3 AND framework = $4
        """,
        site_id, month, year, framework,
    )
    return bool(row)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--framework", default="hipaa")
    parser.add_argument("--apply", action="store_true",
                        help="Without this flag, runs in DRY-RUN mode.")
    args = parser.parse_args()

    if args.month < 1 or args.month > 12:
        sys.exit("--month must be 1..12")
    if args.year < 2020 or args.year > 2100:
        sys.exit("--year out of range")

    import asyncpg  # noqa: WPS433
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: WPS433

    raw_url = _get_db_url()
    sa_url = raw_url
    if sa_url.startswith("postgres://"):
        sa_url = sa_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif sa_url.startswith("postgresql://"):
        sa_url = sa_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    asyncpg_url = raw_url
    if asyncpg_url.startswith("postgresql+asyncpg://"):
        asyncpg_url = asyncpg_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    asyncpg_conn = await asyncpg.connect(asyncpg_url)
    # No timeout on the recovery session — these queries can be slow on
    # legacy sites with thousands of bundles. Cluster default is 0;
    # forcing 0 here makes the script independent of pool defaults.
    await asyncpg_conn.execute("SET statement_timeout = 0")

    bundle_count = await _verify_bundles_exist(
        asyncpg_conn, args.site_id, args.year, args.month
    )
    if bundle_count == 0:
        await asyncpg_conn.close()
        sys.exit(
            f"REFUSING: site_id={args.site_id} emitted 0 bundles in "
            f"{args.year}-{args.month:02d}. A backfill packet would be a "
            f"phantom attestation. HIPAA §164.316(b)(2)(i) chain-of-custody "
            f"violation."
        )

    already_exists = await _packet_already_exists(
        asyncpg_conn, args.site_id, args.year, args.month, args.framework
    )
    if already_exists:
        await asyncpg_conn.close()
        print(f"OK — packet already exists for "
              f"{args.site_id} / {args.year}-{args.month:02d} / "
              f"{args.framework}; nothing to do.")
        return 0

    print(f"Backfill plan:")
    print(f"  site_id:        {args.site_id}")
    print(f"  period:         {args.year}-{args.month:02d}")
    print(f"  framework:      {args.framework}")
    print(f"  bundles in period: {bundle_count}")
    print(f"  apply:          {args.apply}")
    print()

    if not args.apply:
        await asyncpg_conn.close()
        print("DRY-RUN — re-run with --apply to actually generate.")
        return 0

    # Build SA session with statement_timeout=0 too (CompliancePacket
    # uses an AsyncSession internally).
    engine = create_async_engine(
        sa_url, echo=False,
        connect_args={"server_settings": {"statement_timeout": "0"}},
    )
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession)

    sys.path.insert(0, "/app")
    # Import as package member so the inline `from .framework_mapper`
    # relative import inside CompliancePacket resolves correctly. The
    # auto-gen loop in main.py uses this same fully-qualified path.
    from dashboard_api.compliance_packet import CompliancePacket  # noqa: WPS433

    async with SessionLocal() as session:
        pkt = CompliancePacket(args.site_id, args.month, args.year, session,
                               framework=args.framework)
        result = await pkt.generate_packet()
    data = result.get("data", {}) or {}

    markdown = None
    if result.get("markdown_path"):
        try:
            with open(result["markdown_path"]) as fh:
                markdown = fh.read()
        except Exception as exc:
            print(f"WARNING — could not read markdown file "
                  f"{result.get('markdown_path')}: {exc}", file=sys.stderr)

    insert_result = await asyncpg_conn.execute("""
        INSERT INTO compliance_packets (
            site_id, month, year, packet_id,
            compliance_score, critical_issues, auto_fixes,
            mttr_hours, framework, controls_summary,
            markdown_content, generated_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, 'backfill-script-2026-05-02')
        ON CONFLICT (site_id, month, year, framework) DO UPDATE SET
            compliance_score = EXCLUDED.compliance_score,
            critical_issues = EXCLUDED.critical_issues,
            markdown_content = EXCLUDED.markdown_content,
            generated_at = NOW()
        """,
        args.site_id, args.month, args.year, result["packet_id"],
        data.get("compliance_pct"),
        data.get("critical_issue_count", 0),
        data.get("auto_fixed_count", 0),
        data.get("mttr_hours"),
        args.framework,
        json.dumps(data.get("controls", {})),
        markdown,
    )
    print(f"INSERT result: {insert_result}")
    print(f"packet_id:     {result.get('packet_id')}")
    print(f"compliance_pct: {data.get('compliance_pct')}")

    await asyncpg_conn.close()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
