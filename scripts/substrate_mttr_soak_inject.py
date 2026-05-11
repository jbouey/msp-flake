#!/usr/bin/env python3
"""Substrate-MTTR soak injector (Phase 4, Session 219+).

Injects synthetic incidents into the `synthetic-mttr-soak` site at
production-like cadence, then resolves them after a fixed window to
measure substrate-engine detect→alert→resolve latency.

Usage:
    # 1h smoke test (24 + 5 + 1 = 26 incidents total)
    ./substrate_mttr_soak_inject.py --duration-hours 1

    # 24h full soak
    ./substrate_mttr_soak_inject.py --duration-hours 24

    # dry-run (insert + immediately delete one of each severity to test path)
    ./substrate_mttr_soak_inject.py --dry-run

    # custom profile
    ./substrate_mttr_soak_inject.py --duration-hours 24 \\
        --sev1-per-hour 2 --sev2-per-hour 10 --sev3-per-hour 30

ENV:
    DATABASE_URL — postgres DSN (must hit production DB to be useful)

Design doc: .agent/plans/24-substrate-mttr-soak-2026-05-11.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg  # type: ignore


SOAK_SITE_ID = "synthetic-mttr-soak"
SOAK_APPLIANCE_ID = "synthetic-mttr-soak-appliance"

# Per-severity resolution window. After this many seconds, the injector
# programmatically resolves the incident. Isolates substrate-engine
# detect-latency from the (non-existent for synthetic) healing tier.
RESOLUTION_WINDOW_SECONDS = {
    "low":      4 * 3600,   # sev3
    "medium":   30 * 60,    # sev2
    "high":     30 * 60,    # sev2 (severity-text variant)
    "critical": 10 * 60,    # sev1
}

# Incident-type profile per severity. Real-looking but tagged synthetic.
INCIDENT_TYPES = {
    "critical": ("ransomware_indicator", "soak/security"),
    "high":     ("backup_not_configured", "soak/backup"),
    "medium":   ("patching_drift",        "soak/patching"),
    "low":      ("informational_audit",   "soak/audit"),
}


@dataclass
class SoakConfig:
    duration_hours: float
    sev1_per_hour: int
    sev2_per_hour: int
    sev3_per_hour: int
    dry_run: bool
    resume_run_id: Optional[str]


async def _ensure_synthetic_appliance(conn: asyncpg.Connection) -> None:
    """Idempotently create the synthetic appliance row so incidents
    can reference it via FK. We use a synthetic UUID derived from the
    appliance_id string for deterministic re-runs."""
    appliance_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, SOAK_APPLIANCE_ID)
    await conn.execute(
        """
        INSERT INTO site_appliances
            (appliance_id, site_id, hostname, status, mac_address, ip_addresses)
        VALUES ($1, $2, 'synthetic-soak', 'offline', '00:00:00:00:00:00', '[]'::jsonb)
        ON CONFLICT (appliance_id) DO NOTHING
        """,
        SOAK_APPLIANCE_ID, SOAK_SITE_ID,
    )
    # appliances table is the cryptographic-identity table; soak doesn't
    # need a row there since no real signatures.
    _ = appliance_uuid  # reserved for FK if needed


async def _new_soak_run(conn: asyncpg.Connection, cfg: SoakConfig) -> str:
    """Insert a soak_runs row, return its UUID."""
    row = await conn.fetchrow(
        """
        INSERT INTO substrate_mttr_soak_runs (config, status)
        VALUES ($1::jsonb, 'running')
        RETURNING soak_run_id
        """,
        json.dumps({
            "duration_hours": cfg.duration_hours,
            "rates": {
                "sev1": cfg.sev1_per_hour,
                "sev2": cfg.sev2_per_hour,
                "sev3": cfg.sev3_per_hour,
            },
            "started_at": datetime.now(timezone.utc).isoformat(),
        }),
    )
    return str(row["soak_run_id"])


async def _inject_one(
    conn: asyncpg.Connection,
    severity: str,
    soak_run_id: str,
) -> str:
    """Insert one synthetic incident. Returns the incident UUID."""
    incident_type, check_type = INCIDENT_TYPES[severity]
    incident_id = str(uuid.uuid4())
    # appliance_id is a UUID column on incidents — derive from
    # SOAK_APPLIANCE_ID deterministically.
    appliance_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, SOAK_APPLIANCE_ID))

    details = {
        "soak_test": "true",
        "soak_run_id": soak_run_id,
        "soak_severity": severity,
        "injected_at": datetime.now(timezone.utc).isoformat(),
        "synthetic": "Phase-4 substrate-MTTR soak",
    }
    await conn.execute(
        """
        INSERT INTO incidents
            (id, appliance_id, incident_type, severity, check_type,
             details, pre_state, status, reported_at, created_at, site_id)
        VALUES
            ($1::uuid, $2::uuid, $3, $4, $5,
             $6::jsonb, '{}'::jsonb, 'open', NOW(), NOW(), $7)
        """,
        incident_id, appliance_uuid,
        incident_type, severity, check_type,
        json.dumps(details), SOAK_SITE_ID,
    )
    return incident_id


async def _resolve_expired_incidents(
    conn: asyncpg.Connection, soak_run_id: str,
) -> int:
    """Resolve any soak incident past its severity-specific window.
    Returns count resolved."""
    cutoffs = []
    now = datetime.now(timezone.utc)
    for sev, win_sec in RESOLUTION_WINDOW_SECONDS.items():
        cutoff = now - timedelta(seconds=win_sec)
        cutoffs.append((sev, cutoff))

    total = 0
    for sev, cutoff in cutoffs:
        result = await conn.execute(
            """
            UPDATE incidents
               SET status      = 'resolved',
                   resolved_at = NOW(),
                   resolution_tier = 'monitoring'
             WHERE site_id = $1
               AND severity = $2
               AND status   = 'open'
               AND reported_at < $3
               AND details->>'soak_run_id' = $4
            """,
            SOAK_SITE_ID, sev, cutoff, soak_run_id,
        )
        # asyncpg returns 'UPDATE N'
        try:
            total += int(result.split(" ")[-1])
        except Exception:
            pass
    return total


async def _close_soak_run(
    conn: asyncpg.Connection, soak_run_id: str,
    status: str = "completed",
) -> None:
    await conn.execute(
        """
        UPDATE substrate_mttr_soak_runs
           SET ended_at = NOW(),
               status   = $2
         WHERE soak_run_id = $1::uuid
        """,
        soak_run_id, status,
    )


async def run_soak(cfg: SoakConfig) -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL env required")

    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_synthetic_appliance(conn)

        if cfg.resume_run_id:
            soak_run_id = cfg.resume_run_id
            print(f"[soak] resuming run {soak_run_id}", flush=True)
        else:
            soak_run_id = await _new_soak_run(conn, cfg)
            print(f"[soak] started run {soak_run_id}", flush=True)

        if cfg.dry_run:
            for sev in ("critical", "medium", "low"):
                iid = await _inject_one(conn, sev, soak_run_id)
                await conn.execute(
                    "DELETE FROM incidents WHERE id = $1::uuid", iid,
                )
                print(f"[soak] dry-run injected+deleted {sev}: {iid}",
                      flush=True)
            await _close_soak_run(conn, soak_run_id, "completed")
            return soak_run_id

        # Tick every minute.
        end = datetime.now(timezone.utc) + timedelta(hours=cfg.duration_hours)
        tick = 0
        injected_by_sev = {"critical": 0, "medium": 0, "low": 0}
        # Distribute rates evenly across the 60 minutes of each hour.
        # Each tick (60s) injects:
        #   sev1: 1/60 × per_hour  → typically 0–1
        #   sev2: 5/60 × per_hour  → ~1 every ~12 min
        #   sev3: 20/60 × per_hour → ~1 every ~3 min
        while datetime.now(timezone.utc) < end:
            tick += 1
            # Per-tick injection count = floor(rate × tick / 60) minus
            # already-injected. Smooths out integer-rate timing.
            target_sev1 = int(cfg.sev1_per_hour * tick / 60)
            target_sev2 = int(cfg.sev2_per_hour * tick / 60)
            target_sev3 = int(cfg.sev3_per_hour * tick / 60)
            target_sev1 = target_sev1 - (tick // 60) * cfg.sev1_per_hour
            # ^ wrong, simpler: use a running counter
            pass  # see deferred-cadence note below

            # Simpler cadence: minute % (60//rate) == 0 fires the
            # severity. Avoids drift + integer issues.
            for sev_label, sev_value, rate in (
                ("critical", "critical", cfg.sev1_per_hour),
                ("medium",   "medium",   cfg.sev2_per_hour),
                ("low",      "low",      cfg.sev3_per_hour),
            ):
                if rate <= 0:
                    continue
                interval_minutes = max(1, 60 // rate)
                if tick % interval_minutes == 0:
                    iid = await _inject_one(conn, sev_value, soak_run_id)
                    injected_by_sev[sev_value] += 1
                    print(
                        f"[soak] tick={tick} inj {sev_value}: {iid[:8]} "
                        f"(total {injected_by_sev})",
                        flush=True,
                    )

            # Resolve expired ones every tick.
            resolved = await _resolve_expired_incidents(conn, soak_run_id)
            if resolved:
                print(
                    f"[soak] tick={tick} resolved {resolved} expired",
                    flush=True,
                )
            await asyncio.sleep(60)

        await _close_soak_run(conn, soak_run_id, "completed")
        print(f"[soak] run {soak_run_id} complete: {injected_by_sev}",
              flush=True)
        return soak_run_id
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Substrate-MTTR soak injector")
    ap.add_argument("--duration-hours", type=float, default=1.0)
    ap.add_argument("--sev1-per-hour", type=int, default=1)
    ap.add_argument("--sev2-per-hour", type=int, default=5)
    ap.add_argument("--sev3-per-hour", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume-run-id", default=None)
    args = ap.parse_args()

    cfg = SoakConfig(
        duration_hours=args.duration_hours,
        sev1_per_hour=args.sev1_per_hour,
        sev2_per_hour=args.sev2_per_hour,
        sev3_per_hour=args.sev3_per_hour,
        dry_run=args.dry_run,
        resume_run_id=args.resume_run_id,
    )
    run_id = asyncio.run(run_soak(cfg))
    print(f"\n[soak] soak_run_id = {run_id}")
    print("       analyze with: substrate_mttr_soak_report.py --soak-run-id "
          f"{run_id}")


if __name__ == "__main__":
    main()
