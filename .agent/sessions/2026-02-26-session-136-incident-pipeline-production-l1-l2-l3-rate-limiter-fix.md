# Session 136 — Incident Pipeline Production + Rate Limiter Fix

**Date:** 2026-02-26
**Started:** 08:02
**Previous Session:** 135

---

## Goals

- [x] Fix both sites showing Offline on dashboard
- [x] Fix incident pipeline to use L1 DB rules → L2 LLM → L3 escalation
- [x] Populate Linux runbook steps for production fleet orders
- [x] Consolidate duplicate L1 rules
- [x] Audit site detail features (Portal Link, Devices, Workstations, Go Agents, Frameworks, Cloud Integrations)
- [x] Wire network incidents through L2 analysis instead of straight-to-L3

---

## Progress

### Completed

1. **Rate limiter fix** — separate agent bucket (600/min) prevents scan telemetry from starving checkins
2. **Caddy route fix** — removed broken `checkin-receiver:8001` route, all API traffic to `mcp-server:8000`
3. **Incident pipeline rewrite** — L1 queries `l1_rules` table, L2 LLM fallback, L3 only as last resort
4. **Notification dedup** — increased from 1h to 4h for L1/L2 incidents (reduces linux_firewall spam)
5. **41 runbooks with steps** — 13 core Linux + LIN-* series populated with real remediation commands
6. **L1 rule consolidation** — 1 active rule per incident type, disabled 12+ duplicates
7. **DB cleanup** — expired 20 orders, resolved 14 stale incidents, fixed framework 404
8. **Network L2 analysis** — 4 network incident types now flow through L2 for vendor-specific recommendations
9. **Frontend audit** — all 6 site detail tabs verified working

### Blocked

- WinRM 401 still open (no workstation enrollment)
- Fleet rebuild pending (order 3bf579c6)

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/rate_limiter.py` | Separate agent rate limiter (600/min, 20k/hr) |
| `mcp-server/main.py` | L1 DB rules → L2 LLM → L3 pipeline; 4h notification dedup |
| `mcp-server/central-command/backend/migrations/063_linux_runbook_steps_l1_cleanup.sql` | Runbook steps + L1 consolidation |
| `/opt/mcp-server/Caddyfile` (VPS) | Removed broken checkin-receiver route |

## Commits

| Hash | Description |
|------|-------------|
| `2a4829e` | feat: separate agent rate limiter bucket |
| `4863013` | feat: production incident pipeline — L1 DB rules → L2 LLM → L3 |
| `c213c51` | feat: populate Linux runbook steps + consolidate L1 rules |

---

## Next Session

1. Fleet rebuild — deploy resilience hardening (sd_notify, state persistence, crash-loop protection)
2. WinRM 401 fix — unblocks AD enrollment and workstation compliance
3. HIPAA compliance push from 56% — need workstation data flowing
4. Flywheel promotion cycle — `wmi_event_persistence` eligible, verify auto-promote
5. Network device credential UI — client portal feature for network compliance remediation
6. Cloud Integrations — complete Microsoft Security OAuth to start syncing resources
