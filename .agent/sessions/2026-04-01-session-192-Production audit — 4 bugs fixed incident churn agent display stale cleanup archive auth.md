# Session 192 — Production Audit: 4 Bugs Fixed

**Date:** 2026-04-01
**Duration:** ~45 min
**Commits:** 2 (3a482f1, fad1a7a)

## Trigger

User reviewed Pipeline Health and System Health dashboards, noticed:
- All 3 Go agents showing "offline" with "--" for OS/version despite active healing
- Only "1 Resolved 24h" despite 439 successful healings
- 23 permanently stuck incidents
- DB connections display question (not a bug)

## Root Causes Found

### 1. Incident Resolution Churn (HIGH)
**main.py:1916** — Resolved incidents immediately reopened by next scan cycle.
- Daemon heals drift → POST /incidents/resolve (success)
- Next scan (5 min) → drift still present → POST /incidents → reopens with resolved_at=NULL
- Dashboard shows perpetual "1 Resolved 24h" despite hundreds of healings
- **Fix:** 30-min grace period after resolve. Tracks reopen_count (Migration 112).

### 2. go_agents Column Mapping (MEDIUM)
**sites.py:2549** — `agent.os_version` stored in `os_name` column ($6), but agent-health query reads `os_version` (always empty).
- **Fix:** Populate both columns in INSERT. COALESCE in query. Backfilled 3 rows.

### 3. Stale Incident Accumulation (MEDIUM)
23 incidents stuck in open/escalated/resolving from Mar 22-31 — types that can't be healed automatically.
- **Fix:** `_resolve_stale_incidents()` in health_monitor.py — auto-resolves >7d incidents with no recent healing attempts.

### 4. security-events/archive 401 (LOW)
**devicelogs.go** — Archive payload missing `site_id`. `require_appliance_auth()` needs it.
- **Fix:** Added `site_id: cfg.SiteID` to payload. Needs Go daemon rebuild to deploy.

### 5. resolution_tier CHECK Constraint (QUICK FIX)
Stale cleanup used `'auto_stale'` which violated CHECK constraint (only L1/L2/L3/monitoring allowed).
- **Fix:** Changed to `'monitoring'`.

## Verification

| Fix | Verified |
|-----|----------|
| Grace period | rogue_scheduled_tasks resolved at 20:29 and STAYING resolved |
| go_agents OS | os_version column populated for all 3 agents |
| Stale cleanup | Deployed, awaiting first health_monitor cycle |
| Archive auth | Code committed, needs Go daemon deploy |
| Tests | 292 Python pass, 18 Go packages pass |

## Not A Bug
- DB connections "1/18" = 1 active / 18 total pg_stat_activity. Postgres has 21/100 used. Normal.

## Files Changed
- `mcp-server/main.py` — incident grace period logic
- `mcp-server/central-command/backend/sites.py` — go_agents INSERT fix
- `mcp-server/central-command/backend/routes.py` — agent-health query COALESCE
- `mcp-server/central-command/backend/health_monitor.py` — stale incident cleanup
- `mcp-server/central-command/backend/migrations/112_incident_reopen_count.sql`
- `appliance/internal/daemon/devicelogs.go` — archive site_id
