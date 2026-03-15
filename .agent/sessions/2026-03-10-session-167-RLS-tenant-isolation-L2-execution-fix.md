# Session 167 — RLS Tenant Isolation + L2 Execution Fix

**Date:** 2026-03-10
**Commits:** `6e1887f`, `7c149b5`
**Previous Session:** 166

---

## Goals

- [x] Complete post-migration verification runbook for RLS (migrations 078+079)
- [x] Fix RLS enforcement (superuser bypass)
- [x] Investigate and fix L2 decisions not executing

---

## Progress

### RLS Tenant Isolation — Verified and Enforced

- **Root cause:** `mcp` role is superuser → bypasses all RLS
- **Fix:** Created `mcp_app` role (NOSUPERUSER, NOBYPASSRLS), updated docker-compose
- 22 tables, 44 policies, cross-tenant isolation verified
- WORM + audit triggers confirmed working within RLS scope
- Auto-populate triggers added for incidents.site_id and l2_decisions.site_id
- All tests pass (1037 pytest, 90 vitest, tsc clean)

### L2 Planner Execution Fix

- **Root cause 1:** Backend `runbook_action_map` missing 33 Windows runbooks → always `escalate_to_l3=True`
- **Root cause 2:** Daemon `executeL2Action` ran action strings as raw PowerShell → always failed
- **Fix:** Backend returns `escalate=false` for valid runbooks; daemon routes L2 through `executeHealingOrder`
- Daemon v0.3.21 built + fleet order active

---

## Files Changed

| File | Change |
|------|--------|
| `migrations/078_rls_tenant_isolation.sql` | Added auto-populate triggers |
| `migrations/079_app_role_rls_enforcement.sql` | NEW — mcp_app role |
| `mcp-server/main.py` | Fixed L2 runbook_action_map |
| `appliance/internal/daemon/daemon.go` | L2 routes through executeHealingOrder |
| VPS docker-compose.yml | DATABASE_URL → mcp_app |

---

## Next Session

1. Verify daemon v0.3.21 deployed + L2 executions succeeding
2. Phase 4 P2: PgBouncer deployment, wire tenant_connection(), add site_id to remaining tables
3. Flip app.is_admin default to 'false' after endpoints migrated
