"""Diagnostic probes (Phase 12.2 — Session 205).

Fixed whitelist of bounded diagnostic commands that ride the existing
signed `diagnostic` fleet-order channel. The daemon already trusts the
order type (signed Ed25519 envelope), so we're not introducing a new
network surface or a new auth path — just publishing a stable catalog
of approved probe commands and wiring the result back to admin UI.

Explicit non-goals per round-table synthesis:
  - NOT arbitrary shell execution
  - NOT a replacement for WireGuard emergency access
  - NOT capable of writing to the appliance (all probes are read-only)
  - NOT a fix for crash-looped appliances (they can't execute orders)

Usage from admin API:
    result = await run_diagnostic_probe(
        db, site_id='north-valley-branch-2',
        probe='wg_status', actor='admin-user',
        wait_seconds=90,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Probe catalog ────────────────────────────────────────────────────
# Each probe is a fixed command string. The daemon's existing
# `diagnostic` order handler runs it. Commands are chosen for:
#   - READ-ONLY operation (no disk writes, no service state change)
#   - Bounded execution time (all use `timeout <Ns>` or are one-shot)
#   - PHI-safe output (filtered at the daemon via phiscrub before return)
#   - Relevance to the classes of failure we've actually hit in production
#
# ADDING a probe requires:
#   1. PR review by Security (not just SWE) to confirm read-only + PHI-safe
#   2. A matching RUNBOOKS.md entry explaining the use case
#   3. An admin_audit_log entry per invocation (handled by run_diagnostic_probe)
PROBE_CATALOG: Dict[str, Dict[str, Any]] = {
    "wg_status": {
        "description": "WireGuard tunnel state + systemd timer status",
        "command": (
            "timeout 5s wg show 2>&1; echo '---'; "
            "timeout 5s systemctl status wireguard-emergency.service --no-pager -l 2>&1 | tail -20; "
            "echo '---'; "
            "timeout 5s systemctl list-timers wireguard-*.timer --no-pager 2>&1"
        ),
        "category": "network",
    },
    "daemon_logs_1h": {
        "description": "Last 1h of appliance-daemon journalctl, WinRM/auth focus",
        "command": (
            "timeout 10s journalctl -u appliance-daemon --since '1 hour ago' --no-pager 2>&1 "
            "| grep -iE 'winrm|401|localadmin|LookupWinTarget|credentials|error' | tail -40"
        ),
        "category": "diagnostics",
    },
    "windows_targets_cached": {
        "description": "Cached Windows targets from last checkin (PHI-scrubbed)",
        "command": (
            "timeout 3s cat /var/lib/msp/windows_targets.json 2>/dev/null "
            "| head -80"
        ),
        "category": "credentials",
    },
    "winrm_reach": {
        "description": "TCP + HTTPS probe to WinRM on windows targets",
        "command": (
            "for tgt in 192.168.88.250 192.168.88.251; do "
            "  echo \"=== $tgt 5985 ===\"; "
            "  timeout 3s nc -zv $tgt 5985 2>&1; "
            "  echo \"=== $tgt 5986 ===\"; "
            "  timeout 3s nc -zv $tgt 5986 2>&1; "
            "done"
        ),
        "category": "network",
    },
    "dns_ad_resolution": {
        "description": "DNS resolution for AD DC + workstation hostnames",
        "command": (
            "for host in NVDC01 NVWS01; do "
            "  echo \"=== $host ===\"; "
            "  timeout 3s nslookup $host 2>&1 | tail -5; "
            "done"
        ),
        "category": "network",
    },
    "net_state": {
        "description": "Appliance network state: addresses + routes + resolvers",
        "command": (
            "echo '=== ADDR ==='; timeout 2s ip -4 addr show 2>&1; "
            "echo '=== ROUTE ==='; timeout 2s ip route 2>&1; "
            "echo '=== RESOLV ==='; timeout 2s cat /etc/resolv.conf 2>&1"
        ),
        "category": "network",
    },
    "disk_smart": {
        "description": "SMART status on installed-system storage (root + data partitions)",
        "command": (
            "for dev in /dev/nvme0n1 /dev/sda /dev/mmcblk0; do "
            "  [ -b $dev ] && echo \"=== $dev ===\" && "
            "    timeout 5s smartctl -H $dev 2>&1 | tail -8; "
            "done"
        ),
        "category": "hardware",
    },
    "boot_history": {
        "description": "Last boot reasons + reboot loop detection",
        "command": (
            "echo '=== uptime ==='; timeout 2s uptime; "
            "echo '=== last 5 boots ==='; timeout 3s last -x --time-format iso reboot 2>&1 | head -6; "
            "echo '=== reboot_source ==='; "
            "timeout 2s cat /var/lib/msp/reboot_source 2>/dev/null || echo '(none)'"
        ),
        "category": "hardware",
    },
}

# Explicit allowlist for substring protections. Even though the daemon
# has its own command handler, we belt-and-suspenders reject anything
# that smells like a shell escape when validated at the backend.
#
# Allow: 2>&1 stderr redirect, 2>/dev/null, | head|tail|grep pipes.
# Block: file-destination redirects ('> /tmp/...', '>> file'), write
# ops (rm/mv/cp/dd), curl uploads, shell-bomb patterns.
_DANGEROUS_SUBSTR_RE = re.compile(
    # Redirection to a real file destination (but allow 2>&1 / 2>/dev/null)
    r"(?<!&)>\s*[^&/d]|"            # '> <not-device>'
    r">>|"                            # append redirect
    r"\brm\s|\bmv\s|\bcp\s|\bdd\s|"   # disk-modifying commands
    r"/dev/sd[a-z]\s*=|"              # explicit block-device assignment
    r"\bcurl\s+[^|;]*--upload-file|"  # uploads
    r":\s*\(\)\s*\{",                 # fork-bomb signature
)


def list_probes() -> List[Dict[str, Any]]:
    """Return the probe catalog (public — safe to expose via API)."""
    return [
        {"name": name, "description": p["description"], "category": p["category"]}
        for name, p in sorted(PROBE_CATALOG.items())
    ]


async def run_diagnostic_probe(
    db: AsyncSession,
    site_id: str,
    probe: str,
    actor: str,
    wait_seconds: int = 60,
) -> Dict[str, Any]:
    """Issue a signed `diagnostic` fleet order with the probe's command,
    wait up to `wait_seconds` for completion, return the output.

    Security:
      * probe name MUST be in PROBE_CATALOG (whitelist)
      * command substring sanity check (belt + suspenders)
      * every call writes an admin_audit_log entry
      * fleet order signed with existing Ed25519 mechanism

    Returns:
      {
        "probe": str,
        "status": "completed" | "failed" | "pending",
        "fleet_order_id": uuid,
        "output": dict | None,
        "error_message": str | None,
        "duration_ms": int | None,
      }
    """
    if probe not in PROBE_CATALOG:
        raise ValueError(f"Unknown probe: {probe!r}. See list_probes().")

    spec = PROBE_CATALOG[probe]
    cmd = spec["command"]
    if _DANGEROUS_SUBSTR_RE.search(cmd):
        # Should never fire (catalog is code-reviewed), but we validate
        # defensively in case a future commit slips a bad entry in.
        raise ValueError(
            f"Probe {probe!r} command failed safety check; catalog is broken."
        )

    # Issue the signed diagnostic fleet order via the existing helper.
    # (create_fleet_order_for_site lives in fleet_updates.py; uses the
    # site's appliance + Ed25519 signing infrastructure.)
    from .fleet_updates import create_fleet_order_for_site
    from .fleet import get_pool
    from .tenant_middleware import admin_connection

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        fleet_order_id = await create_fleet_order_for_site(
            conn,
            site_id=site_id,
            order_type="diagnostic",
            parameters={
                "command": cmd,
                "probe": probe,
                "issued_by": actor,
            },
            expires_hours=1,
        )
        if not fleet_order_id:
            raise RuntimeError("Failed to create diagnostic fleet order")

        # Audit log — every diagnostic invocation is recorded
        await conn.execute("""
            INSERT INTO admin_audit_log (username, action, target, details, created_at)
            VALUES ($1, 'DIAGNOSTIC_PROBE_ISSUED', $2, $3::jsonb, NOW())
        """,
            actor,
            f"site:{site_id}:probe:{probe}",
            json.dumps({
                "fleet_order_id": fleet_order_id,
                "probe": probe,
                "category": spec["category"],
            }),
        )

    # Poll fleet_order_completions for the result.
    deadline = datetime.now(timezone.utc) + timedelta(seconds=max(10, wait_seconds))
    while datetime.now(timezone.utc) < deadline:
        row = (await db.execute(text("""
            SELECT status, output, error_message, duration_ms
            FROM fleet_order_completions
            WHERE fleet_order_id = :fid
            LIMIT 1
        """), {"fid": fleet_order_id})).fetchone()
        if row:
            return {
                "probe": probe,
                "status": row.status,
                "fleet_order_id": fleet_order_id,
                "output": row.output,
                "error_message": row.error_message,
                "duration_ms": row.duration_ms,
            }
        await asyncio.sleep(3)

    # Timed out waiting — the daemon didn't pick up or the appliance is
    # down. Return pending; caller can poll later.
    return {
        "probe": probe,
        "status": "pending",
        "fleet_order_id": fleet_order_id,
        "output": None,
        "error_message": f"No completion received within {wait_seconds}s",
        "duration_ms": None,
    }
