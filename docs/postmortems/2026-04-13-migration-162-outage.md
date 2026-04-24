# Postmortem — 60-min production outage from Migration 162

**Date:** 2026-04-13
**Duration:** ~60 minutes (04:04 UTC first-failure → 05:37 UTC healthy)
**Severity:** P1 — API down, all appliances unable to check in
**Session context:** Session 205, flywheel remediation Phase 1

## Summary

Session 205 Phase 1 included Migration 162 which backfilled historical
synthetic `L2-*` runbook_ids on `platform_pattern_stats`,
`aggregated_pattern_stats`, and `patterns`. The migration referenced a
column `aggregated_pattern_stats.runbook_id` that does not exist on the
live schema — the column is `recommended_action` there. Migration apply
failed; `main.py` lifespan's FAIL-CLOSED gate raised `SystemExit(2)`;
mcp-server entered a restart loop. Each restart attempt poisoned
PgBouncer's backend pool with orphaned prepared statements, which then
caused `DuplicatePreparedStatementError` on SQLAlchemy engine init even
after the migration was hotfixed. CI could not deploy the fix because
`docker exec mcp-server` required a running container.

## Timeline

| Time (UTC) | Event |
|---|---|
| 03:58 | Phase 1 committed (403ed70), CI begins |
| 04:02 | CI fails: "column runbook_id does not exist" on Migration 162 |
| 04:02–04:08 | Phases 2-4 merged onto main (44681c2, 4b430eb, f0e0be6); CI re-fails on each (container already in restart loop) |
| 04:08 | Hotfix commit (9c624c9) pushed; CI fails for same reason |
| 04:33 | First log entry showing migration 162 failure on restart loop |
| 05:37 | Manual recovery: scp fixed migration directly to dashboard_api_mount/, stop mcp-server, pg_terminate_backend, restart pgbouncer, start mcp-server |
| 05:37:48 | All 4 migrations (162–165) applied, mcp-server healthy |
| 05:39 | Auto-promote ran on OLD code (still cached in memory) — 2 rules promoted via the old direct-INSERT path |

## Root cause

Two independent faults chained:

1. **Migration 162 schema drift** — written against assumed schema
   (`aggregated_pattern_stats.runbook_id`), production schema uses
   `recommended_action`. The migration was not dry-run against a
   representative schema before push.

2. **PgBouncer prepared-statement poisoning** — asyncpg's per-connection
   statement cache interacts badly with PgBouncer transaction-pooling
   mode when client processes die while holding prepared statements.
   Dead-and-restarted mcp-server instances leave orphaned prepared
   statements on pooled Postgres backends; subsequent clients collide.
   Existing mitigation (`statement_cache_size=0` in engine connect_args)
   prevents the client from caching, but doesn't clear orphans from
   server-side backends.

## Impact

- API endpoints unreachable for ~60 min
- All appliance checkins failed during window
- No data loss (migrations rolled back cleanly on failure; PgBouncer
  poisoning was reversible)

## What worked

- **FAIL-CLOSED migration gate behaved correctly** — better to refuse
  startup than serve traffic with an inconsistent schema
- **Transactional migration bodies** — migration 162's failure rolled
  back atomically, leaving the DB at a known state (v161 applied)
- **Separate mount path** — `dashboard_api_mount/` let us scp the fixed
  migration directly, bypassing CI

## What didn't work

- **CI auto-recovery** — deploy workflow requires `docker exec` into
  mcp-server; container in restart loop breaks this. No path back
  without human intervention.
- **Schema assumption** — no pre-flight check against the live schema
  before migrations ship. Caught by production rather than CI.

## Action items

1. **Pre-flight schema check** — CI step that applies new migrations
   against a snapshot of the production schema in a scratch DB before
   production deploy. Catches drift at PR time.
2. **PgBouncer clear-on-restart** — when mcp-server exits non-zero,
   trigger `DISCARD ALL` on all pooled backends (or restart pgbouncer)
   before the container restarts. Can be added as a pre-start hook.
3. **Recovery runbook in RUNBOOKS.md** — codified procedure for
   "migration-induced restart loop" (done in this PR).
4. **CI-independent deploy path** — document + codify the manual
   scp-to-dashboard_api_mount + atomic restart procedure for
   situations where CI `docker exec` is unavailable.

## Non-action items (considered, not implemented)

- **Disable FAIL-CLOSED migration gate** — rejected. The gate is there
  because silent schema drift caused a 90-min outage on 2026-04-12.
  The gate is correct; the missing piece is better pre-flight.
- **Switch PgBouncer to session mode** — rejected. Transaction mode is
  a deliberate scaling choice; the prepared-statement interaction is
  handled by `statement_cache_size=0` + the recovery runbook.
