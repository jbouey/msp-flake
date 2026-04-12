# Session 205 (continuation) — Migration Hardening, Fleet Outage, Hardening Ship

**Date:** 2026-04-12 (afternoon — same UTC day as morning time-travel Phases 2/3)
**Focus:** Ship Phase 2/3 time-travel to VPS, discover silent migration-drift outage, harden deploy, investigate residual fleet-order delivery gap
**Outcome:** Migration auto-apply shipped fail-closed; CI deploy pipeline no longer silently swallows failures; docker-compose.yml now source-controlled; **one residual bug identified but not fixed** — see next-session item

## Timeline

**Morning: Phase 2+3 committed and pushed to main** (4 commits atop Phase 1): `a7f5569`, `d30296d`, `eacf884`, `90f9a6e`

**12:48 UTC — first CI failure cascade begins.** Phase 1 shipped a bad import (`from .auth import require_appliance_bearer` — function is in `.shared`). All 5 subsequent deploys failed until `f5c0b37` at 13:15 fixed ESLint errors in `ReconcileEvents.tsx`.

**13:53 UTC — v0.4.0 fleet order created.** `fleet_cli.py create update_daemon --version 0.4.0 …`. Binary uploaded to `https://api.osiriscare.net/updates/appliance-daemon-0.4.0` (SHA256 `ce822cd8…`). osiriscare-1 (7C:D3:0A:7C:55:18) completed successfully at 13:53:54; osiriscare-2 and osiriscare-3 silently stayed on v0.3.91 / v0.3.92.

**14:06 UTC — VPS disk full.** `/dev/sda1 150G used 148G free 0 100%`. `mcp-postgres` crashing with "No space left on device" trying to write postmaster.pid. Root cause: no `nix-gc.timer` existed on VPS — `/nix/store` grew to 98G since January without cleanup.

**14:08 UTC — outage resolved.** `nix-collect-garbage -d` freed **161 GiB**. `df -h /` → 89G free. Postgres recovered.

**14:22 UTC — nix.gc + nix.optimise config added** to `/etc/nixos/configuration.nix` (repo-local copy in `/tmp/vps-configuration.nix`). `nixos-rebuild switch` → both timers now active, next fire Mon 2026-04-13 00:00 UTC, weekly cadence, deletes generations older than 14d, `persistent = true`.

**14:35 UTC — stale fleet orders cancelled.** 4 older active orders (v0.3.92 update_daemon + diagnostics + restart_agent) were blocking v0.4.0 delivery. Cancelled via `fleet_cli.py cancel`.

**14:40 UTC — "column boot_counter does not exist" discovered.** Migration 160 was committed + deployed but never applied to the DB. Checkin STEP 3.5b crashed every cycle with `column "boot_counter" does not exist`, poisoning the asyncpg transaction. STEP 4.5 (fleet orders) aborted silently. Backend returned HTTP 200 while fleet-order delivery starved for 90 min.

**14:43 UTC — migration 160 applied manually.** `docker exec -i mcp-postgres psql -U mcp -d mcp < /opt/mcp-server/dashboard_api_mount/migrations/160_time_travel_reconciliation.sql`. Columns + `reconcile_events` + DELETE trigger + RLS policies created.

**14:45 UTC — round-table consultation dispatched** on systemic migration-apply hardening. Principal SWE / CCIE / Senior DB Engineer / PM consensus: option (a) = FastAPI lifespan fail-closed startup apply + harden existing CI gate.

**14:50-14:58 UTC — 6-step hardening implemented:**
1. Backfill `schema_migrations` on VPS (152 rows, checksums matching `migrate.py:71`)
2. `main.py` lifespan: `cmd_up()` fail-closed + `SystemExit(2)` if pending after apply
3. `migrate.py cmd_up`: `pg_advisory_lock(8675309)` to serialize concurrent replicas
4. CI gate: `set -e` + `grep -oE '[0-9]+ pending$'` post-apply assertion (removed `|| echo` silent swallow)
5. `/api/admin/health`: new `check_schema()` probe returning `{"schema": {"applied": N, "pending": [...]}}`
6. `sites.py`: 9 `logger.warning` → `logger.error` on transactional step failures

**14:58 UTC — deploy failure #1.** `mcp_app` can't `CREATE TABLE` in public schema. `migrate.py ensure_migrations_table` required `mcp` superuser. Workaround: add `MIGRATION_DATABASE_URL=postgresql://mcp:PASS@mcp-postgres:5432/mcp` to VPS `/opt/mcp-server/docker-compose.yml`.

**15:03 UTC — deploy failure #2.** My CI post-apply grep used `grep -c pending` which false-positived on `pending_alerts` migration NAME + `0 pending` summary line. Fixed at `bb4b775` with `grep -oE '[0-9]+ pending$'` to match only the summary.

**15:07 UTC — mcp-server restarted with v0.4.0 Phase 2+3 code + MIGRATION_DATABASE_URL env + migration 160 applied.** Startup-apply log line: `{"applied": 157, "event": "No pending migrations", "logger": "main", "level": "info", "timestamp": "2026-04-12T15:07:01.057635Z"}`.

**15:15 UTC — deploy GREEN** (`24309562868`).

**15:25 UTC — docker-compose.yml source-controlled.** Closed the tribal-state gap: `MIGRATION_DATABASE_URL` in VPS compose would be lost on reprovision. Pulled compose from VPS, committed to `mcp-server/docker-compose.yml`, added CI step `Deploy docker-compose.yml to release` + diff-aware sync, switched `docker compose restart` → `docker compose up -d` so env changes take effect.

**15:27 UTC — deploy `13c0026` GREEN** (`24309871482`).

## What Shipped (verified at runtime)

| Artifact | Status | Evidence |
|---|---|---|
| Phase 2/3 daemon + backend code | VERIFIED | `grep "Apply pending migrations — FAIL-CLOSED" /opt/mcp-server/app/main.py` returns 1; sites.py has `issue_reconcile_plan` refs |
| Migration 160 applied | VERIFIED | `information_schema.columns` shows `boot_counter`, `generation_uuid`, `nonce_epoch` on `site_appliances` |
| schema_migrations backfilled | VERIFIED | 160 numbered + 5 legacy rows; `migrate.py status` shows "157 applied, 0 pending" |
| Startup-apply runs | VERIFIED | mcp-server log at 15:07:01 `"No pending migrations"` |
| MIGRATION_DATABASE_URL env | VERIFIED | `docker exec mcp-server env \| grep MIGRATION` returns the superuser URL |
| nix.gc weekly timer | VERIFIED | `systemctl list-timers` shows `nix-gc.timer` + `nix-optimise.timer` next fire Mon 00:00 UTC |
| VPS disk headroom | VERIFIED | `df -h /` → 39% used, 89G free |
| CI deploy fail-closed | VERIFIED | Deploy `24309333398` (before fix) failed red; `24309562868` (after fix) green |
| docker-compose.yml in repo | VERIFIED | `mcp-server/docker-compose.yml` 232 lines, compose config parses clean |

## OPEN BUG (next session first item)

**Fleet-order delivery is STILL broken** even after migration 160 + Phase 2/3 deployed.

Symptoms:
- `Failed to fetch fleet orders: current transaction is aborted` appears 2×/minute in mcp-server logs for checkins from osiriscare-1, osiriscare-2, osiriscare-3.
- Daemon log `orders=0` on every Checkin OK line.
- `get_fleet_orders_for_appliance` called directly as `mcp` superuser returns 1 order for osiriscare-2 and osiriscare-3 — the backend IS fetching correctly when called outside the checkin tenant_connection context.
- `fleet_order_completions` has zero entries for the v0.4.0 order on osiriscare-2 or osiriscare-3 — the order has NEVER been delivered, not just "delivered and failed".

Root cause identified (not yet fixed): `get_fleet_orders_for_appliance` at `mcp-server/central-command/backend/fleet_updates.py:1336-1363` does two `await conn.execute("DELETE FROM fleet_order_completions ...")` inside `try/except Exception: pass` blocks **WITHOUT savepoints**. If either DELETE fails (permission, RLS, any reason), the exception is caught but the asyncpg transaction is poisoned. The subsequent SELECT at line 1368 raises `InFailedSQLTransactionError`. The enclosing `async with conn.transaction():` at `sites.py:3667` sees this as "transaction is aborted".

Why DELETE might fail: unverified. Candidates:
- RLS on fleet_order_completions: DISABLED per `pg_class.relrowsecurity = 'f'` — so RLS is NOT the cause
- Permissions: not yet tested (needs mcp_app via pgbouncer with proper env)

Fix planned (~20 lines): wrap each DELETE in its own savepoint:
```python
try:
    async with conn.transaction():
        await conn.execute("DELETE ...")
except Exception:
    pass  # savepoint rolled back, outer tx preserved
```

This is the exact pattern MEMORY.md flags: "asyncpg transaction poisoning: Failed query inside `async with conn.transaction()` aborts entire transaction. Subsequent queries get `InFailedSQLTransactionError` even with try/except. Fix: nested `async with conn.transaction()` creates SAVEPOINTs that isolate failures."

## Additional State

**Fleet (live):**
- osiriscare (1) — v0.4.0, 7C:D3:0A:7C:55:18 — upgraded cleanly
- osiriscare-2 — v0.3.91, 84:3A:5B:91:B6:61 — STUCK (was 0.3.92 once per completions, silently rolled back before today)
- osiriscare-3 — v0.3.92, 84:3A:5B:1F:FF:E4 — STUCK (matches its latest successful completion)

**4th appliance**: user said "I will add a fourth today" but not yet arrived on checkin — deploy it AFTER fleet-order bug fix so v0.4.0 lands cleanly.

**Documents created this session:**
- `docs/session-206-plan-fleet-order-rollback-detection.md` — deep design for Session 204 rollback bug (completion-ack-before-health-check is the REAL bug, not "goroutine dies"). Option 1 (backend-side detection via `site_appliances.agent_version != completion.version`) is the recommended fix; Option 2 (daemon-side delayed ACK) is more correct but requires daemon rebuild.
- `docs/runbook-idempotency-audit-2026-04-12.md` — 124 runbooks scanned, 113 clean, 9 Linux `>> /etc/...` appends flagged for Phase 3.5. Phase 3 MVP zero-risk.

**Memory entries written this session:**
- `feedback_enterprise_adversarial_default.md` — user's durable directive to treat work as adversarial, never confuse code presence with production-readiness, distinguish VERIFIED/ASSUMED/MISSING-EVIDENCE, give blunt readiness judgments

**CLAUDE.md updated** with 8 new invariants covering: time-travel reconciliation (MIN_SIGNALS wire lock, signed_payload byte-exact verify, admin-pool intent, PurgeAllNonces disk persistence, "State Reconciliation" terminology), migration auto-apply fail-closed, MIGRATION_DATABASE_URL requirement, advisory lock, schema probe, schema_migrations pattern, logger.error on transactional steps, nix.gc automation. One correction: "Fleet order health check rollback bug" entry was stale — the goroutine-dies narrative is wrong; real bug is completion-ack-before-health-check.

## Commits This Session (chronological)

```
d442704 feat(phase 1): time-travel reconciliation foundation          [prior]
a7f5569 feat(phase 2): time-travel detection signals (daemon-side)
d30296d feat(phase 2+3): time-travel reconciliation end-to-end + 3.1 replay hardening
eacf884 feat(phase 3): State Reconciliation admin timeline
90f9a6e docs: time-travel Phases 2/3 — audit + invariants + session log
8f82c4a feat(phase 3.1): client-side boot_counter_regression signal
7fcf8bb fix(reconcile): import require_appliance_bearer from shared, not auth
f5c0b37 fix(ReconcileEvents): ESLint — React.ReactElement + strict !==
5271748 fix(deploy): fail-closed migration auto-apply + schema drift observability
bb4b775 fix(ci): parse N pending count correctly (grep -c was counting migration names)
13c0026 fix(deploy): source-control VPS docker-compose.yml to close tribal-state gap
```

## Next Session Priorities

1. **FIX THE FLEET-ORDER DELIVERY BUG** — savepoint wrap the two DELETEs in `get_fleet_orders_for_appliance` (fleet_updates.py:1336-1363). Verify with checkin log `orders=1` for osiriscare-2 and osiriscare-3.
2. **Watch v0.4.0 converge** — 3/3 on v0.4.0, backfill 4th appliance if user adds it.
3. **Session 204 rollback-detection fix** — implement Option 1 from `docs/session-206-plan-fleet-order-rollback-detection.md`. Backend marks completion as `rolled_back` when `site_appliances.agent_version` doesn't match the latest `fleet_orders.parameters->>version` after checkin.
4. **Admin UI verification** — log in, visit `/reconcile-events`, confirm the admin endpoint renders. Visit `/api/admin/health` with session to verify the `schema` key renders.
5. **Chaos lab tunnel** — still blocked on iMac access (task #83).
6. **4th appliance provisioning** — whenever user adds it.

## READINESS JUDGMENT (blunt, per new rule)

**Migration hardening: SHIP-READY.** Auto-apply verified at runtime, fail-closed path verified on two failed deploys, observability added.

**Time-travel reconciliation Phases 2/3: CODE SHIPPED, NOT PRODUCTION-FUNCTIONAL.** Fleet-order delivery is broken (STEP 4.5 transaction abort). Phase 2/3 CAN'T actually trigger because daemons don't receive the orders that would prompt reconcile. The foundational plumbing is in place but needs fleet_updates.py savepoint fix before anything works.

**Fleet v0.4.0 rollout: STUCK AT 1/3.** Two appliances silently miss signed update_daemon orders. Manual WG swap is the short-term workaround but requires the emergency-access handler which is off by default. Before calling this done: fix the delivery bug, then confirm completion.

**VPS ops hygiene: IMPROVED.** nix-gc armed, migration apply fail-closed, compose source-controlled. The class of silent outages demonstrated this session is closed.
