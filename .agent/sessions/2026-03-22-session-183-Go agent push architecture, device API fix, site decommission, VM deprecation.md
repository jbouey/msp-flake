# Session 183 - Go Agent Push Architecture, Device API Fix, Site Decommission, VM Deprecation

**Date:** 2026-03-22
**Started:** 12:10
**Previous Session:** 182
**Go Daemon:** v0.3.26
**Go Agents:** 3 deployed (NVDC01, NVWS01, iMac)

---

## Goals

- [x] Fix Device API 500 errors (IPv4Address cast)
- [x] Push-first scan architecture (daemon skips WinRM for Go agent hosts)
- [x] Deploy Go agents to all lab machines
- [x] Site decommission feature
- [x] VM appliance deprecation
- [x] Go agent compliance data across all portals
- [x] Stale data cleanup

---

## Progress

### Completed

1. **Device API 500 fix** — cast asyncpg IPv4Address to str in device_sync.py (4 locations). Deployed via CI/CD.
2. **Push-first scan priority** — driftscan.go skips WinRM for hosts with active Go agents, uses agent data instead.
3. **Post-deploy gRPC verification** — 60s timeout + WinRM diagnostic on failure.
4. **update_agent fleet order** — was a stub, now actually downloads binary.
5. **NETLOGON UNC fallback** — when HTTP download blocked, copies via \\DC\NETLOGON share.
6. **WinRM base64 chunked transfer** — 20KB chunks for large binaries.
7. **configure_workstation_agent fleet order** — full agent lifecycle (download, install, configure, verify).
8. **VM appliance deprecated** — test-appliance-lab-b3c40c set to inactive. Source of stale .247 data.
9. **Go agent deployed to NVWS01** — push architecture proven on Windows 10.
10. **Go agent deployed to NVDC01** — immediate heal commands flowing.
11. **Go agent deployed to iMac** — launchd daemon on macOS 11.7 (Go 1.22 compat).
12. **Agent check result sync** — checks_passed/checks_total from agents to go_agents table.
13. **Site decommission feature** — GET /sites/{id}/export + POST /sites/{id}/decommission + DecommissionModal frontend.
14. **macOS build compat** — Makefile uses Go 1.22 for darwin (Big Sur support).
15. **Agent deployment docs** — agent/DEPLOY.md, deploy.sh script, systemd/launchd units.
16. **L2 guardrails fix** — execute_runbook + configure_screen_lock added to allowlist.
17. **Fleet sidebar** — hides inactive/decommissioned sites.
18. **Stale workstation cleanup** — dedup IP-only entries, expire 7d/30d, Go agent sync.
19. **All check results sent** — agents send pass+fail for accurate compliance percentage.
20. **Go agent compliance across portals** — partner sites/orgs, client dashboard/sites, admin fleet/health.
21. **Compliance scoring** — blends Go agent data with bundle data (weighted average).
22. **Dynamic drift checks** — dashboard shows count from DB instead of hardcoded 6.
23. **GlobalStats** — active_drift_checks + total_go_agents fields.
24. **Stale incidents resolved** — for deprecated VM site.
25. **OpenClaw** — DNS fixed (Cloudflare proxied → DNS only), gateway restarted after crash.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| mcp-server/app/dashboard_api/device_sync.py | IPv4Address → str cast (4 locations) |
| appliance/appliance-daemon/internal/driftscan/driftscan.go | Push-first scan priority |
| appliance/appliance-daemon/internal/orders/orders.go | configure_workstation_agent, update_agent |
| appliance/appliance-daemon/internal/winrm/ | NETLOGON fallback, base64 chunks |
| mcp-server/app/main.py | Site decommission endpoints, Go agent sync |
| mcp-server/app/dashboard_api/_routes_impl.py | Compliance scoring blend, dynamic drift checks |
| mcp-server/central-command/frontend/src/ | DecommissionModal, fleet sidebar filter, GlobalStats |
| agent/ | Deploy scripts, DEPLOY.md, Makefile Go 1.22 compat |
| docs/ARCHITECTURE.md | Updated to Session 183 state |
| docs/ROADMAP.md | Updated status to production push-first |

---

## Next Session

1. First pilot client onboarding preparation
2. Compliance packet PDF improvements
3. Partner white-label customization
4. Multi-site fleet scaling validation
