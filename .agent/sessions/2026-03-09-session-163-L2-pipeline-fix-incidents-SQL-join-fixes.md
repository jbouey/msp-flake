# Session 163 - L2 Pipeline Fix + Incidents SQL Join Fixes

**Date:** 2026-03-09
**Started:** 12:54
**Previous Session:** 162
**Status:** Complete

---

## Goals

- [x] Fix site detail page not loading (SQL errors)
- [x] Fix L2 bypass — failed L1 orders skipping L2
- [x] L2 planner dynamic runbook loading
- [x] Add missing L1 rules for common incident types
- [x] Kick off chaos lab execution
- [ ] Shut down ws01 VM (iMac unreachable)
- [ ] Verify L2 fallback end-to-end with real incident

---

## Progress

### Completed

1. **Fixed 5 SQL queries** in `routes.py` referencing `i.site_id` (column doesn't exist on `incidents` table). All now join through `appliances` table. Commit `e542c99`.
2. **Added L2 fallback** in `sites.py` order completion handler. Failed L1 healing orders now try L2 LLM planner before escalating to L3. Commit `5ded789`.
3. **Dynamic runbook loading** in `l2_planner.py`. DB has 88 runbooks but `AVAILABLE_RUNBOOKS` only had 10. Added `_load_dynamic_runbooks()` with 5-min TTL cache. Commit `dbdd23b`.
4. **Added 8 L1 rules** directly to VPS DB: bitlocker, bitlocker_status, screen_lock, security_audit, winrm, service_status, rdp_nla, password_policy.
5. **Cleaned up duplicate** workstation entry for 192.168.88.250.
6. All 3 commits deployed via CI/CD.

### Blocked

- iMac (192.168.88.50) unreachable all session — couldn't shut down ws01 VM or fully test chaos lab
- WinRM timeouts due to iMac RAM pressure

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/routes.py` | Fixed 5 SQL queries joining incidents→appliances for site_id |
| `mcp-server/central-command/backend/sites.py` | Added L2 fallback on failed L1 healing orders |
| `mcp-server/central-command/backend/l2_planner.py` | Dynamic runbook loading from DB with TTL cache |

---

## Next Session

1. Verify L2 fallback works end-to-end with real failed L1 healing order
2. Shut down ws01 VM once iMac is reachable
3. Monitor chaos lab execution and incident pipeline
4. Workstation compliance statuses still stale (18+ days) — need successful driftscan
