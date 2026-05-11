#!/usr/bin/env python3
"""Substrate-MTTR soak analyzer (Phase 4, Session 219+).

Reads a completed soak_run_id and produces a P50/P95/P99 MTTR report
per severity plus a contractual-SLA verdict.

Usage:
    ./substrate_mttr_soak_report.py --soak-run-id <uuid>
    ./substrate_mttr_soak_report.py --soak-run-id <uuid> --format markdown

ENV:
    DATABASE_URL — postgres DSN
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from typing import Dict, List

import asyncpg  # type: ignore


SLA_HOURS = {
    "critical": 4,    # sev1 ≤ 4h
    "high":     24,   # sev2 ≤ 24h
    "medium":   24,   # sev2 ≤ 24h
    "low":      30 * 24,  # sev3 ≤ 30 days
}


def _pctile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


async def _gather(conn: asyncpg.Connection, run_id: str) -> Dict:
    run_row = await conn.fetchrow(
        """
        SELECT soak_run_id, started_at, ended_at, status, config
          FROM substrate_mttr_soak_runs
         WHERE soak_run_id = $1::uuid
        """, run_id,
    )
    if not run_row:
        sys.exit(f"soak_run_id {run_id} not found")

    incident_rows = await conn.fetch(
        """
        SELECT id, severity, reported_at, resolved_at, status
          FROM incidents
         WHERE details->>'soak_run_id' = $1
        """, run_id,
    )

    # Per-incident: substrate detect latency = first violation row
    # referencing this incident, alert latency comes from substrate
    # tick cadence. We approximate `detected_at` by the first
    # substrate_violations.last_seen_at row for the synthetic site
    # AT-OR-AFTER the incident's reported_at.
    # For v1 we use simpler measurement: resolution_at - reported_at
    # = end-to-end injector latency (NOT substrate-only).
    # Future hardening: join substrate_violations for true detect-time.

    by_sev: Dict[str, List[float]] = {}
    open_count: Dict[str, int] = {}
    for r in incident_rows:
        sev = r["severity"]
        if r["resolved_at"] is None:
            open_count[sev] = open_count.get(sev, 0) + 1
            continue
        mttr = (r["resolved_at"] - r["reported_at"]).total_seconds() / 60.0
        by_sev.setdefault(sev, []).append(mttr)

    summary: Dict[str, Dict] = {}
    for sev in ("critical", "high", "medium", "low"):
        vals = by_sev.get(sev, [])
        sla_minutes = SLA_HOURS[sev] * 60
        p50 = _pctile(vals, 50)
        p95 = _pctile(vals, 95)
        p99 = _pctile(vals, 99)
        summary[sev] = {
            "count":         len(vals),
            "open":          open_count.get(sev, 0),
            "p50_minutes":   round(p50, 2),
            "p95_minutes":   round(p95, 2),
            "p99_minutes":   round(p99, 2),
            "sla_minutes":   sla_minutes,
            "sla_met_p99":   p99 <= sla_minutes if vals else None,
        }

    return {
        "soak_run_id":   str(run_row["soak_run_id"]),
        "started_at":    run_row["started_at"].isoformat(),
        "ended_at":      run_row["ended_at"].isoformat() if run_row["ended_at"] else None,
        "status":        run_row["status"],
        "config":        run_row["config"],
        "totals": {
            "incidents":   len(incident_rows),
            "resolved":    sum(len(v) for v in by_sev.values()),
            "still_open":  sum(open_count.values()),
        },
        "per_severity":  summary,
    }


def _markdown(report: Dict) -> str:
    lines = [
        "# Substrate-MTTR Soak Report",
        "",
        f"**soak_run_id:** `{report['soak_run_id']}`",
        f"**status:** {report['status']}",
        f"**started:** {report['started_at']}",
        f"**ended:** {report['ended_at']}",
        "",
        "## Totals",
        f"- Injected: **{report['totals']['incidents']}**",
        f"- Resolved: **{report['totals']['resolved']}**",
        f"- Still open: **{report['totals']['still_open']}**",
        "",
        "## Per-severity MTTR",
        "",
        "| Severity | Count | Open | P50 (min) | P95 (min) | P99 (min) | SLA (min) | P99 ≤ SLA |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for sev in ("critical", "high", "medium", "low"):
        s = report["per_severity"][sev]
        verdict = "✅" if s["sla_met_p99"] else ("⚠️" if s["sla_met_p99"] is None else "❌")
        lines.append(
            f"| {sev} | {s['count']} | {s['open']} | "
            f"{s['p50_minutes']} | {s['p95_minutes']} | {s['p99_minutes']} | "
            f"{s['sla_minutes']} | {verdict} |"
        )
    return "\n".join(lines)


async def main_async(run_id: str, fmt: str) -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL env required")
    conn = await asyncpg.connect(dsn)
    try:
        report = await _gather(conn, run_id)
        if fmt == "json":
            print(json.dumps(report, indent=2, default=str))
        else:
            print(_markdown(report))
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Substrate-MTTR soak analyzer")
    ap.add_argument("--soak-run-id", required=True)
    ap.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = ap.parse_args()
    asyncio.run(main_async(args.soak_run_id, args.format))


if __name__ == "__main__":
    main()
