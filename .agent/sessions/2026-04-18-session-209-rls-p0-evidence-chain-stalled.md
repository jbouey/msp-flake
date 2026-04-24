# Session 209 — 2026-04-18

## RLS P0 + evidence_chain_stalled invariant

### Arc

Started continuing Session 208 wrap-up tasks:
1. Verify CI green on `eafe45e` (round-table P1 audit-closure batch)
2. Resume Task #38+ audit round-table
3. Watch substrate_health provisioning_stalled fire rate

Pivoted to an active P0 when dashboard data-quality contradictions (streaming data messed up) led to log scrape revealing **2,608 `InsufficientPrivilegeError` RLS rejections in 2h** on `compliance_bundles` INSERT.

### Root cause

Migration 234 (2026-04-18 earlier) flipped `ALTER ROLE mcp_app SET app.is_admin = 'false'` to make RLS fail-closed by default. `shared.py` was supposed to compensate via a SQLAlchemy `connect` event listener issuing `SET app.is_admin = 'true'`. That pattern is **fundamentally incompatible with PgBouncer transaction pooling** — PgBouncer's `server_reset_query = DISCARD ALL` wipes session-level SETs between client borrows, so only the FIRST transaction on each backend had admin context.

A first fix (`ebb9f17`) correctly moved to `after_begin` + `SET LOCAL` (transaction-scoped, survives DISCARD ALL). It failed to work because the listener was bound to `async_session.sync_session_class` — `async_sessionmaker` has no such attribute, AttributeError was swallowed by a `try/except Exception: pass`, and the listener silently never registered.

Second fix (`2ddc596`) corrected the target to `AsyncSession.sync_session_class` (class-level) and narrowed the silent-swallow handler to `(ImportError, AttributeError)` only so future bugs of this shape surface instead of hide.

### Commits

- `b7f6d87` — CI test-stage unblock (ESLint no-explicit-any + billing PHI boundary doc)
- `bf861d6` — Migration 234 column fix (`timestamp` → `created_at` on admin_audit_log)
- `eafe45e` — Migration 235 schema_migrations dup-key fix (removed manual INSERT)
- `ebb9f17` — First RLS fix attempt (loaded but listener never fired)
- `2ddc596` — **Actual P0 fix** — correct `AsyncSession.sync_session_class` target
- `f6c6121` — `evidence_chain_stalled` sev1 substrate invariant

### Evidence that the fix worked

```
# Before 2ddc596
tx1: is_admin= false user= mcp_app
tx2: is_admin= false

# After 2ddc596
tx1: is_admin= true user= mcp_app
tx2: is_admin= true
```

Prod RLS rejection rate: 22/min → 0/min. Evidence bundles resumed flowing (14 inserts in first 15 min post-deploy).

### Instrumentation

New substrate invariant `evidence_chain_stalled` (sev1) — if ≥1 appliance checked in in the last 15 min but 0 `compliance_bundles` inserted in that window, open a violation. Outcome-layer signal catches RLS failures AND any other evidence-insert failure (partition missing, signing key rotation bugs, disk pressure, silent asyncpg exception). Would have fired the 2026-04-18 outage within 15 min instead of waiting for dashboard anomaly analysis hours later.

Total substrate invariants: 28 → 32. Three of the four +4 invariants were shipped in Session 208 work (provisioning_stalled + two others) that memory hadn't reflected until this session's memory update.

### Lessons written into CLAUDE.md + memory

1. `AsyncSession.sync_session_class` is the event target — never the sessionmaker instance.
2. Silent-swallow exception handlers MUST be narrowed to the specific exception class expected. Bare `except Exception: pass` hides real bugs.
3. SQLAlchemy admin-context for RLS: `SET LOCAL` inside `after_begin`, never session-level `SET` via `connect` event (PgBouncer DISCARD ALL kills it).

### Post-P0 follow-through — shipped same session

- `222beca` — `submit_evidence` write path migrated to `tenant_connection(site_id)` + `SET LOCAL app.current_tenant`. RLS model now uniform across evidence write and read paths. P1 architectural item closed.
- `91cb05f` — `test_evidence.py` updated for F1 cross-site posture (tenant context switching).
- `9cec8ef` — Audit-p3 batch (6 findings): cross-site `/evidence`, savepoint hygiene gap, dead dup path, payout job, commission.
- `5ab3b79` — Dashboard null-safety: self-heal pct `number | null`, `/flywheel-events` 7-day window, installer sentinel guard. Precedent for the P2 nullable contract.
- `e291def` — Flywheel ledger + platform spine restoration (2026-04-18 all-zeros audit — 17/43 promoted_rules orphaned from ledger). Platform auto-promotion now issues per-site `sync_promoted_rule` fleet_orders through `safe_rollout_promoted_rule(scope='fleet')` + writes `rollout_issued` ledger events. `flywheel_ledger_stalled` sev1 invariant added. Three-list lockstep (event_type CHECK, EVENT_TYPES frozenset, transition matrix) documented as CI-gated invariant.
- `ae8c94b` — Flywheel backfill script import fix (`dashboard_api.*` imports).
- `8d10b51` — **P2 closure.** All 8 dashboard rendering contradictions resolved:
  - Nullable rates (`number | null`) propagated end-to-end: `db_queries` → `models` → `routes` → `learning_api` → TS types → `hooks/useFleet` → `Dashboard.tsx` → `Learning.tsx` → `PartnerLearning.tsx`.
  - Empty-state guards: stat-card counts use `text-label-tertiary` when 0; rate cards render `—` + helper text when null.
  - Unit conflation: percentage-point deltas use `pp` suffix, absolute percentages use `%`.
  - Never-deployed 0% render eliminated (backend returns null, frontend shows em-dash).
  - 109/109 vitest green, `tsc --noEmit` clean, CI deploy run 24607015135 shipped 14:47 UTC.

### Prod state at session close

- `8d10b51` running (runtime_sha == disk_sha verified post-deploy)
- 0 RLS rejections (steady 3h+ since `2ddc596`)
- Evidence chain flowing; `compliance_bundles` inserting at healthy rate
- Substrate invariants: 32 registered, `evidence_chain_stalled` + `flywheel_ledger_stalled` idle
- Flywheel ledger + platform spine restored — platform promotions now reach every site via fleet_orders
- Fleet SLA diagnostic: `/api/dashboard/sla-strip` returns `online_appliances_pct=75.0` (3/4 online) 10/10 curls; any user-visible "—" is stale browser bundle/React Query cache, not a backend regression
