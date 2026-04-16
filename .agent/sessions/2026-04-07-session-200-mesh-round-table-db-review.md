# Session 200 — Mesh Round Table + DB Scaling + UX Fixes

**Date:** 2026-04-07 / 2026-04-08
**Commits:** `b2f3d84`, `a47de9b`, `b4ccac8`, `550ccdf`, `002c952`, `cf2fe0e`, `402a48d` (9 total)
**Status:** All deployed to production

## Phase 2 (2026-04-08) — DB Scaling + UX

### DB Scaling (all 4 round table items executed):
1. Pool consolidation — asyncpg min=2/max=25, dual-pool documented in fleet.py
2. Checkin savepoints — 5 bare steps wrapped (3.5, 3.6, 4, 4.5, 6b-2)
3. Migration 137 — `incident_remediation_steps` relational table (replaces JSONB array)
4. Migration 138 — `compliance_bundles` partitioned (25 monthly, 232K rows), `portal_access_log` (31 monthly)

### Bug fix: assigned_target_count hardcoded to 0
- Root cause: `sites.py` `get_site()` at `/api/sites/{site_id}` had `'assigned_target_count': 0` hardcoded
- The query didn't SELECT `assigned_targets` at all
- `routes.py` at `/api/dashboard/fleet/{site_id}` had the correct query but frontend doesn't call that endpoint
- Fix: added `assigned_targets` to SELECT, use actual value

### UX: Clickable Pass/Warn/Fail badges
- ComplianceHealthInfographic badges are now buttons with `onStatusClick` prop
- Clicking "4 Fail" navigates to `/incidents?site_id=X&status=fail`
- Wired up in SiteDetail.tsx

### OpenClaw fix
- v2026.4.1 → v2026.4.5 auto-updated, `streamMode` config key deprecated
- `openclaw doctor --fix` migrated config, restart resolved

## Trigger

Dashboard showed "Scan Coordination: 0 targets" for all 3 appliances despite hash ring fix being deployed. User asked for round table evaluation.

## Investigation

1. **DB confirmed targets ARE assigned** (2-1-1 across 3 nodes) — round-robin working correctly
2. Dashboard "0 targets" was stale frontend cache — backend data was correct
3. Found 3 code bugs during investigation:
   - `appliance_db_id` referenced 3 times but **never defined** — NameError caught by `except Exception: pass`, silently disabling discovery ownership filter
   - `canonical_aid` freshly reconstructed instead of using DB-stored `canonical_id` — fragile
   - Two competing `normalize_mac` functions (sites.py = colons, hash_ring.py = stripped) — confusing

## Round Table — Mesh Architecture

**Participants:** CCIE, Principal SWE, Product Manager, DB Engineer

| Persona | Verdict |
|---------|---------|
| CCIE | Production-functional, not enterprise-ready. Cross-subnet reachability unaware. Credential scoping bug = HIPAA finding. |
| Principal SWE | Architecture sound, implementation has 3 bugs a linter would catch. Same-day fixes. |
| PM | Stop polishing mesh. Kill "Scan Coordination" from UI. Go sell single-appliance. |
| DB Engineer | JSONB assigned_targets correct. Nested savepoints concerning at scale. |

## Round Table — Database Review

**Full inventory:** 136 tables, 175+ RLS policies, 384 indexes, 96 JSONB columns

**Top 5 recommendations (Lead DB Engineer):**
1. Consolidate dual connection pools (SQLAlchemy 50 + asyncpg 50 → 25 PgBouncer slots)
2. Add missing incident/order indexes — **DONE (Migration 136)**
3. Partition portal_access_log + compliance_bundles
4. Move remediation_history from JSONB array to relational table
5. Split checkin into micro-transactions (eliminate nested savepoints)

**PM cost assessment:** Single Postgres holds to 50 orgs ($80-120/mo). Don't go managed until 20-30 orgs. 6-year HIPAA retention = ~1TB at 50 orgs, archive to S3.

## Fixes Applied

| Fix | File(s) | Impact |
|-----|---------|--------|
| `appliance_db_id` → `canonical_id` | sites.py:3043,3053,3070 | Discovery ownership filter now functional |
| `canonical_aid` → `canonical_id` | sites.py:3262-3267 | Target persistence uses DB-stored ID |
| `normalize_mac` → `normalize_mac_for_ring` | hash_ring.py, sites.py, 2 test files | Disambiguated MAC formats |
| "Scan Coordination" → "Devices Monitored" | SiteDetail.tsx | Customer-friendly label |
| Remove target badge from client portal | ClientDetail.tsx | No internal detail leaking to clients |
| Migration 136: 4 performance indexes | 136_performance_indexes.sql | incidents, orders, portal_access_log |

## Tests

- 29/29 hash ring + target assignment tests passing
- TypeScript clean (tsc + eslint 0 errors)
- Frontend build clean (51s)

## Deployment

1. Git push (2 commits) → CI
2. Manual rsync to `/opt/mcp-server/app/dashboard_api/` + `/dashboard_api_mount/` + `/current/dashboard_api/`
3. `docker compose up -d --build --force-recreate mcp-server`
4. 4 indexes created via `CREATE INDEX CONCURRENTLY` (ran individually — CONCURRENTLY can't be in a transaction)
5. Container healthy, health check OK, code verified deployed

## Architecture Notes for Future Sessions

- **Dual pool consolidation** needed before 10 orgs (SQLAlchemy + asyncpg fighting for PgBouncer)
- **Checkin micro-transactions** needed before 10 orgs (150 savepoints/min = pg_subtrans cliff)
- **remediation_history → relational table** before 20 orgs (TOAST bloat)
- **Table partitioning** before 50 orgs (compliance_bundles, portal_access_log)
- **Deploy path:** rsync to 3 directories (`app/dashboard_api/`, `dashboard_api_mount/`, `current/dashboard_api/`) + rebuild container
