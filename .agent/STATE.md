# Current State

**Updated:** 2026-02-26T07:40:00Z
**Session:** 135
**Branch:** main

## System Health

| Component | Status |
|-----------|--------|
| VPS API | healthy |
| Dashboard | healthy |
| Physical Appliance | go_daemon_v0.2.5, fleet rebuild pending (3bf579c6) |
| VM Appliance | go_daemon_v0.2.5, fleet rebuild pending (3bf579c6) |
| L2 Planner | enabled (L2Enabled=true default, confidence >= 0.6 auto-exec) |
| Learning Flywheel | active, aggregation bridge deployed, 2 patterns promoted |
| Evidence Pipeline | 216k+ bundles, Ed25519 signed, real drift data |
| Compliance Packets | 56% compliance, 15 HIPAA controls |
| OTS Proofs | 2705 anchored, 251 pending |
| Fleet Updates | fleet_orders live, auto-detect deployed version |

## Latest Commit

```
19a0177 feat: resilience hardening — sd_notify, state persistence, subscription gating, crash-loop protection
```

## Active Fleet Order

- **ID:** 3bf579c6-9d75-4e6e-924b-031f59c7bf4a
- **Type:** nixos_rebuild
- **Status:** pending (expires 24h)
- **Includes:** sd_notify watchdog, crash-loop protection, WatchdogSec=120s

## Deployed Binaries (VPS)

- `checkin-receiver` — subscription_status in CheckinResponse
- `appliance-daemon` — sd_notify, state persistence, subscription gating, connectivity classification

## Current Blockers

- **WinRM 401 on DC** — needs home network access to diagnose
- **HIPAA compliance at 56%** — needs more check coverage
- Fleet rebuild pending — appliances must pick up order 3bf579c6
