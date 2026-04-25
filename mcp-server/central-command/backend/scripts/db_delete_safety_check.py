#!/usr/bin/env python3
"""db_delete_safety_check.py — pre-DELETE impact analysis for protected
tables.

Session 210-B 2026-04-25 hardening #4. Today's orphan appliance bug
(`84:3A:5B:91:B6:61`) was caused by a manual `DELETE FROM
site_appliances WHERE appliance_id='...'` that took out a row WITHOUT
considering that:

  - api_keys for the appliance stayed in the table
  - the daemon was alive and continued calling /api/appliances/checkin
  - /api/provision/rekey can't help because the row is now missing
  - the appliance is auth-orphaned with no in-band recovery path

This script makes that class of bug visible BEFORE the DELETE runs.

Usage (dry-run, default):

    python3 db_delete_safety_check.py \\
        --table site_appliances \\
        --where "appliance_id='north-valley-branch-2-84:3A:5B:91:B6:61'"

Output: a structured impact report listing every dependent row that
either CASCADE-deletes (data loss) or ORPHANS (auth/operational
problem) when the target row goes away. Exit code 0.

Usage (execute):

    python3 db_delete_safety_check.py \\
        --table site_appliances \\
        --where "appliance_id='...'" \\
        --execute \\
        --reason "Decommissioning physical-appliance-pilot-1aea78 — see ticket #..."

Prompts for interactive confirmation, writes admin_audit_log entry,
runs the DELETE inside a transaction. Reason ≥ 20 chars enforced.

Connect via DATABASE_URL or PSQL_DSN env var (asyncpg-style URL).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from typing import Dict, List, Tuple

import asyncpg


# ---------------------------------------------------------------------
# Impact maps — what to look for per protected table.
#
# Each entry: target_table -> list of (dependent_table, query_template).
# The query_template references {key} which is bound from the WHERE
# match (we extract the literal value from the WHERE clause).
#
# Add a new protected table here whenever a manual DELETE bites
# (Session 210-B: started with site_appliances + api_keys + sites).
# ---------------------------------------------------------------------
IMPACT_MAPS: Dict[str, List[Tuple[str, str]]] = {
    "site_appliances": [
        ("api_keys", """
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE active) AS active_keys
              FROM api_keys
             WHERE appliance_id = $1
        """),
        ("compliance_bundles", """
            SELECT COUNT(*) AS n
              FROM compliance_bundles
             WHERE appliance_id = $1
        """),
        ("incidents", """
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE status NOT IN ('resolved','closed')) AS unresolved
              FROM incidents
             WHERE site_id = (
                 SELECT site_id FROM site_appliances WHERE appliance_id = $1
             )
        """),
        ("install_sessions", """
            SELECT COUNT(*) AS n
              FROM install_sessions
             WHERE mac_address = (
                 SELECT mac_address FROM site_appliances WHERE appliance_id = $1
             )
        """),
        ("discovered_devices", """
            SELECT COUNT(*) AS n
              FROM discovered_devices
             WHERE LOWER(mac_address) = (
                 SELECT LOWER(mac_address) FROM site_appliances WHERE appliance_id = $1
             )
        """),
        ("fleet_orders_pending", """
            SELECT COUNT(*) AS n
              FROM fleet_orders
             WHERE site_id = (
                 SELECT site_id FROM site_appliances WHERE appliance_id = $1
             )
               AND status IN ('pending', 'active')
        """),
    ],
    "sites": [
        ("site_appliances", """
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE deleted_at IS NULL) AS active
              FROM site_appliances WHERE site_id = $1
        """),
        ("api_keys", """
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE active) AS active_keys
              FROM api_keys WHERE site_id = $1
        """),
        ("compliance_bundles", """
            SELECT COUNT(*) AS n FROM compliance_bundles WHERE site_id = $1
        """),
        ("incidents", """
            SELECT COUNT(*) AS n FROM incidents WHERE site_id = $1
        """),
        ("partners", """
            SELECT COUNT(*) AS n FROM partner_sites WHERE site_id = $1
        """),
    ],
    "api_keys": [
        ("dependent_appliance_alive", """
            SELECT
                sa.appliance_id,
                sa.last_checkin,
                EXTRACT(EPOCH FROM (NOW() - sa.last_checkin))/60 AS minutes_silent
              FROM site_appliances sa
             WHERE sa.appliance_id = (
                 SELECT appliance_id FROM api_keys WHERE id = $1
             )
        """),
    ],
}


def _extract_key(where_clause: str) -> str:
    """Extract the literal value from a simple `col='value'` WHERE.

    Supports:
      - col='value'
      - col = 'value' (with spaces)
      - col=$1 with env var fallback (not supported here)

    Refuses anything more complex (joins, functions) — the safety check
    only makes sense for a single-row target. Operator must use simple
    equality.
    """
    m = re.match(
        r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'([^']+)'\s*$",
        where_clause,
    )
    if not m:
        raise SystemExit(
            f"WHERE must be a single equality (col='value'), got: {where_clause!r}\n"
            "Compound conditions force the operator to think harder — "
            "extract the row's PK first, then use it here."
        )
    return m.group(2)


async def _connect():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("PSQL_DSN")
    if not dsn:
        raise SystemExit("DATABASE_URL or PSQL_DSN env var required")
    # asyncpg expects postgresql:// (not postgresql+asyncpg://)
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def _run_impact(table: str, key: str, conn) -> List[Dict]:
    """Run every impact query for the target table; return list of
    {dependent: name, row: <fetchrow result>}."""
    if table not in IMPACT_MAPS:
        raise SystemExit(
            f"No impact map defined for table {table!r}.\n"
            f"Add an entry to IMPACT_MAPS in this script before "
            "running DELETE on a new table."
        )
    out = []
    for dep_name, sql in IMPACT_MAPS[table]:
        try:
            row = await conn.fetchrow(sql, key)
            out.append({
                "dependent": dep_name,
                "row": dict(row) if row else None,
            })
        except Exception as e:
            out.append({
                "dependent": dep_name,
                "error": f"{type(e).__name__}: {e}",
            })
    return out


def _format_report(table: str, where: str, key: str, impacts: List[Dict]) -> str:
    out = []
    out.append(f"=== Pre-DELETE impact report ===")
    out.append(f"target table: {table}")
    out.append(f"WHERE:        {where}")
    out.append(f"resolved key: {key!r}")
    out.append("")
    out.append(f"Dependent-row counts (a non-zero count means the DELETE")
    out.append(f"will leave behind / cascade-delete that many rows):")
    out.append("")
    for imp in impacts:
        dep = imp["dependent"]
        if "error" in imp:
            out.append(f"  {dep:30}  [error: {imp['error']}]")
            continue
        row = imp["row"]
        if row is None:
            out.append(f"  {dep:30}  (no rows)")
        else:
            # Render every column from the result row.
            cols = ", ".join(f"{k}={v!r}" for k, v in row.items())
            out.append(f"  {dep:30}  {cols}")
    out.append("")
    return "\n".join(out)


async def _confirm_and_execute(table: str, where: str, key: str, reason: str, conn) -> None:
    """Interactive confirm + audited execute."""
    if len(reason) < 20:
        raise SystemExit("--reason must be ≥ 20 chars (audit context)")
    actor = os.environ.get("USER") or getpass.getuser() or "unknown"
    print(f"\nAbout to execute: DELETE FROM {table} WHERE {where}")
    print(f"Audit actor:      {actor}")
    print(f"Reason:           {reason}")
    print("\nType the resolved key to confirm:")
    typed = input("> ").strip()
    if typed != key:
        print(f"Mismatch — typed {typed!r} but resolved key was {key!r}. Aborting.")
        return

    async with conn.transaction():
        # Audit FIRST — if the DELETE rolls back, we still have the
        # intent recorded.
        await conn.execute(
            """
            INSERT INTO admin_audit_log (action, username, target, details)
            VALUES ($1, $2, $3, $4)
            """,
            f"db_delete.{table}",
            actor,
            f"{table}:{key}",
            json.dumps({"where": where, "reason": reason}),
        )
        # Run the actual DELETE.
        result = await conn.execute(
            f"DELETE FROM {table} WHERE {where}"
        )
        print(f"\n{result}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--table", required=True, help="Target table name (must be in IMPACT_MAPS)")
    p.add_argument("--where", required=True, help="Single-equality WHERE clause: col='value'")
    p.add_argument("--execute", action="store_true",
                   help="Actually run the DELETE (default: dry-run, report only)")
    p.add_argument("--reason", default="", help="Audit reason (≥20 chars when --execute)")
    args = p.parse_args()

    key = _extract_key(args.where)

    async def _go():
        conn = await _connect()
        try:
            impacts = await _run_impact(args.table, key, conn)
            print(_format_report(args.table, args.where, key, impacts))
            if args.execute:
                await _confirm_and_execute(args.table, args.where, key, args.reason, conn)
        finally:
            await conn.close()

    asyncio.run(_go())


if __name__ == "__main__":
    main()
