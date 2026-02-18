# Session 118: Go Daemon NixOS Deploy via VPS

**Date:** 2026-02-17
**Duration:** ~20 minutes (continuation from session 113)
**Context:** Deploying Go daemon NixOS packaging committed in session 113

## Summary

Attempted to deploy the Go daemon NixOS configuration to the physical appliance. Direct SSH to the appliance timed out (lab network unreachable from this machine). Successfully connected to VPS and issued a `nixos_rebuild` order via direct DB insert.

## What Was Done

### Deploy Attempt via Direct SSH
- SSH to 192.168.88.241 (physical appliance) — timed out
- Lab network not reachable from this machine

### Deploy via VPS Central Command
1. Connected to VPS at 178.156.162.116
2. Found Docker containers: `mcp-postgres`, `central-command`, `mcp-server`, etc.
3. Queried database: found physical appliance `physical-appliance-pilot-1aea78-84:3A:5B:91:B6:61` in site `physical-appliance-pilot-1aea78`
4. Discovered `nixos_rebuild` is not in backend's `OrderType` enum (only: update_agent, run_runbook, restart_service, run_command, collect_logs, reboot, force_checkin, run_drift, sync_rules)
5. Inserted order directly into `admin_orders` table:
   - `order_id`: ORD-REBUILD-20260217
   - `order_type`: nixos_rebuild
   - `parameters`: `{"flake_ref": "github:jbouey/msp-flake#osiriscare-appliance-disk"}`
   - `priority`: 10
   - `expires_at`: NOW() + 2 hours
6. Order status: **pending** — appliance last checked in at 23:47, will pick up on next checkin

### API Endpoint Discovery
- Order creation endpoint: `POST /api/sites/{site_id}/appliances/{appliance_id}/orders`
- Broadcast endpoint: `POST /api/sites/{site_id}/orders/broadcast`
- Backend `OrderType` enum missing `nixos_rebuild` — should be added for dashboard usage

## Files Changed
- None (DB-only change on VPS)

## Remaining / Next Priorities
1. **Verify rebuild completes** — Check order status transitions to `completed` after appliance checks in
2. **Add `nixos_rebuild` to backend OrderType** — So dashboard can trigger rebuilds without direct DB access
3. **Enable Go daemon** — After rebuild succeeds: `touch /var/lib/msp/.use-go-daemon` then reboot
4. **Wire L2 action execution** — Connect WinRM/SSH executors to L2 decision results
5. **Order completion POST** — Implement actual HTTP POST to `/api/appliances/orders/<id>/complete`
6. **72-hour soak test** — Run Go daemon on physical HP T640 with monitoring
7. **Python cleanup** — Remove replaced Python components after successful soak
