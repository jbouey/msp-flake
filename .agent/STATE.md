# Current State

**Updated:** 2026-03-14T21:45:00Z
**Session:** 179
**Branch:** main

## System Health

| Component | Status |
|-----------|--------|
| VPS API | healthy |
| Dashboard | healthy |
| Physical Appliance | go_daemon_v0.3.20, fleet order v0.3.21 re-issued (7d expiry) |
| VM Appliance | go_daemon_v0.3.21 |
| L2 Planner | enabled (L2Enabled=true, confidence >= 0.6 auto-exec) |
| Learning Flywheel | active, 37 eligible, 2 promoted |
| Evidence Pipeline | 203k+ bundles, Ed25519 signed |
| Compliance Packets | 56% compliance, 15 HIPAA controls |
| OTS Proofs | 2705 anchored, 251 pending |
| Fleet Updates | v0.3.21 order 48e4e7f6 active (expires Mar 22) |
| Chaos Lab | 43.8% healing rate → improvements shipped |

## Session 179 — Chaos Lab Healing Gaps (5 fixes)

### HIGH (3)
1. **Audit policy healing** — `RB-WIN-SEC-026` expanded from 6→12 subcategories (Object Access, Detailed Tracking), added `gpupdate /force` before `auditpol /set`. Scanner updated to match.
2. **Registry persistence** — `RB-WIN-SEC-019` now covers Winlogon key path + safe entry whitelist
3. **Rogue admin healing** — `L1-WIN-ROGUE-ADMIN-001` changed from `escalate` to `run_windows_runbook` → new `RB-WIN-SEC-027` (remove from Admins group + disable account)

### MEDIUM (2)
4. **Firewall inbound rules** — New end-to-end: scanner check #24 (`firewall_dangerous_rules`) + `L1-WIN-FW-RULES-001` + `RB-WIN-SEC-028` (remove/disable dangerous inbound allow rules)
5. **DNS service recovery** — `RB-WIN-SVC-001` improved with dependency-aware restart + disabled StartType fix

### Fleet Order
- Cancelled expired order `359717f5` (0/2 delivered, 2 days old)
- Created new order `48e4e7f6` — nixos_rebuild, skip v0.3.21, 7-day expiry (Mar 22)
- VM appliance already at v0.3.21 (marked skipped)

### Files Changed
- `appliance/internal/daemon/driftscan.go` — 12 audit subcategories, new firewall inbound rules scanner check #24
- `appliance/internal/healing/builtin_rules.go` — rogue admin→heal, new L1-WIN-FW-RULES-001
- `appliance/internal/daemon/runbooks.json` — improved 3 runbooks + 2 new (122 total)
- `mcp-server/central-command/frontend/src/types/index.ts` — `firewall_dangerous_rules` label

## Current Blockers

- **Fleet order delivery** — physical appliance (v0.3.20) not picking up fleet orders despite active checkins. iMac unreachable from Mac (SSH timeout). Need to check appliance logs when iMac accessible.
- **Apollo API** — 403 on People Search (trial plan limitation)
- **External credential rotation** — Anthropic, AWS, SMTP, OAuth still pending provider console access
