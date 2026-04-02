# Session 192 — Production Audit: 11 Issues Fixed

**Date:** 2026-04-01 to 2026-04-02
**Commits:** 5 (3a482f1, fad1a7a, cd42a13, 1a26104)
**Daemon:** v0.3.66 built + fleet order 224b8619

## Round 1: Initial Dashboard Audit (4 bugs)

1. **Incident resolution churn** — Resolved incidents reopened every 5min scan cycle. Added 30-min grace period. Migration 112 adds reopen_count.
2. **go_agents column mapping** — os_version stored in os_name, query read wrong column. Fixed INSERT + query + backfilled.
3. **Stale incident auto-resolve** — health_monitor now cleans >7d stuck incidents.
4. **security-events/archive 401** — Go daemon missing site_id in payload.

## Round 2: Linux Healing + Performance (4 bugs)

5. **isSelfHost() didn't match IPs** — Root cause of ALL Linux healing 0%. Appliance tried SSH to itself (192.168.88.236) instead of local exec. Added net.InterfaceAddrs() check.
6. **6 wrong keyword fallback runbook IDs** — RB-BACKUP-001→RB-WIN-BACKUP-001, etc. Three types hitting "unknown runbook" errors.
7. **linux_encryption not in MONITORING_ONLY** — Was burning L2 LLM calls (63 failures/day) for un-automatable LUKS check.
8. **device/sync 52-65s** — N+1 queries + missing indexes. Migration 113 adds 3 composite indexes.

## Round 3: Flywheel Promotion Audit (3 bugs)

9. **716 dead CVE watch rules** — All disabled, 0 matches. Deleted (864→128 rules).
10. **Platform promotion threshold unreachable** — Required distinct_orgs >= 5 with 2-3 sites. Lowered to >= 1.
11. **42/53 promoted rules never matched** — Duplicated builtin rules (builtins fire first). Added dedup check against enabled builtin/synced rules before promoting.

## Architectural Findings

- `learning_promotion_reports` table: 0 rows. Designed for appliance-pushed reports but Go daemon doesn't call the endpoint. Dead architecture.
- Duplicate flywheel code in `main.py` AND `background_tasks.py`. Dead code in background_tasks.py (not imported).
- Dashboard 0% compliance on north-valley-branch-2: DB actually has 22.22%. Frontend cache or timing issue.

## Test Results
- 292 Python tests pass, 18 Go packages pass
- All changes deployed to VPS
- v0.3.66 daemon fleet order active

## Files Changed
- `mcp-server/main.py` — grace period, MONITORING_ONLY, keyword map, flywheel thresholds+dedup
- `mcp-server/central-command/backend/sites.py` — go_agents INSERT fix
- `mcp-server/central-command/backend/routes.py` — agent-health COALESCE query
- `mcp-server/central-command/backend/health_monitor.py` — stale incident cleanup
- `mcp-server/central-command/backend/agent_api.py` — MONITORING_ONLY sync
- `mcp-server/central-command/backend/tests/test_incident_pipeline.py` — updated for new runbook IDs
- `mcp-server/central-command/backend/migrations/112_incident_reopen_count.sql`
- `mcp-server/central-command/backend/migrations/113_device_sync_indexes.sql`
- `appliance/internal/daemon/healing_executor.go` — isSelfHost + imports
- `appliance/internal/daemon/devicelogs.go` — archive site_id
