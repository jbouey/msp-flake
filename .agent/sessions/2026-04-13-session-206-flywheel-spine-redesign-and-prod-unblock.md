# Session 206 — Flywheel Spine redesign + prod unblock + 24h shadow window

**Date:** 2026-04-13
**Started:** 17:28 (continuation from Session 205)
**Last commit:** `90515dd` (migration 182 widen CHECK)
**Outcome:** Flywheel redesigned around an event-ledger + state-machine spine. Deployed in prod in shadow mode. 24h observation window kicked off; re-audit ~21:30 UTC 2026-04-14.

---

## TL;DR

Three-act day:

1. **Phase 15 closing** — fixed 5 original flywheel bugs + 4 orthogonal bugs surfaced during validation (silent-sig placeholder, action whitelist, missing conditions, deploy-workflow no-op restart). First-ever flywheel measurement-loop close in prod (`deployment_count=1` on `L1-AUTO-RANSOMWARE-INDICATOR` at 18:56:51 UTC).

2. **Enterprise audit** — round-table found auto-disable was silently broken (`logger.debug` swallowed errors; 2h of SCREEN_LOCK at 0%/83 went undetected). User demanded "ultrathink the solution, don't patch." Round-table: **the flywheel has no spine**. 9 asynchronous hops with no shared state model. Patch cycle will never end without a structural fix.

3. **Spine redesign (R1+R3+R4+R6)** — one append-only event ledger, one state machine, one orchestrator. Migration 181 + `flywheel_state.py` + dashboard endpoint + Prom funnel metrics. 5 transition classes, 12 PG integration tests. Deployed to prod in shadow mode at 21:14 UTC.

---

## Commits shipped (chronological, ~22 commits)

| Commit | Scope |
|---|---|
| `883e5ec` | flywheel bugs #1–3 + 3-way import shim |
| `1ce23f3` | dashboard surface for underperforming promoted rules |
| `4c66323` | kill silent SHA256-doubled signature placeholder |
| `49eaadf` | reconcile script: live-checkin filter |
| `e85d604` | enterprise offline detection F1–F4 + migration 180 |
| `75e23e1` | execute_runbook → run_{windows,linux}_runbook translation |
| `4c2d9a9` | test fixture rename for realistic runbook IDs |
| `5fe611a` | deploy workflow: force `docker compose restart` |
| `1a3aeee` | build_daemon_valid_rule_yaml synthesizer (3rd daemon gap) |
| `e305641` | drop nonexistent `description` column from l1_rules query |
| `c7d01d6` | 7-item round-table hardening batch |
| `7b4ca24` | RELEASE_SHA stamp/read paths |
| `3cf4490` | deploy retention: prune releases + compose backups |
| `ac8ae8c` | deploy self-trigger on workflow changes |
| `e3589e2` | YAML parse fix (duplicate `run:` key) |
| `d2af234` | **SPINE R1** — ledger + state machine + orchestrator |
| `c20cf12` | **SPINE R3** — dashboard endpoint + Prom funnel |
| `17cd8e8` | **SPINE R4+R6** — Canary + Graduation transitions + rollout wire |
| `fd8da55` | migration 181: move backfill before trigger install |
| `35fe761` | test fixture: add `l1_rules.source` column |
| `6f967ba` | emergency: `import os` fix in background_tasks.py |
| `90515dd` | migration 182: widen site_appliances status CHECK |

---

## Spine architecture (what got built)

### Migration 181 — ledger + state machine
- `promoted_rule_events` — partitioned (monthly), append-only ledger. 16 event_types, 4 outcomes. DELETE+UPDATE blocked.
- `promoted_rules.lifecycle_state` — 9-state CHECK-constrained column.
- `promoted_rule_lifecycle_transitions` — 23-entry legal-transition matrix.
- `advance_lifecycle()` — the ONLY sanctioned state mutation path.
- `enforce_lifecycle_via_advance` trigger — blocks direct UPDATE of `lifecycle_state` (tamper-evident).

### Orchestrator + 5 transitions (`flywheel_state.py`)
Each transition = ~50 LOC class. Own try/except. `logger.error(exc_info=True)`. No sibling can mask a failure.

| Transition | rule |
|---|---|
| RolloutAckedTransition | rolling_out → active (first completion) |
| CanaryFailureTransition | active → auto_disabled (<70% in first 48h) |
| RegimeAbsoluteLowTransition | */warning/graduated → auto_disabled on unack'd event |
| GraduationTransition | active → graduated + l1_rules.source → synced |
| ZombieSiteTransition | * → retired on dead site > 30d |

### Observability (R3)
- `GET /api/dashboard/flywheel-spine`
- `POST /api/dashboard/flywheel-spine/acknowledge`
- Prom: `osiriscare_flywheel_rules_by_state`, `osiriscare_flywheel_events_1h`, `osiriscare_flywheel_stuck_rules`, `osiriscare_flywheel_operator_ack_pending`

---

## Production state at session close (21:30 UTC)

| Metric | Value |
|---|---|
| Running SHA | `c20cf12` (scp emergency-patched with `6f967ba`+`90515dd`) |
| Latest migration | 182 |
| `promoted_rules.lifecycle_state` | active=1, approved=26, auto_disabled=16 = 43 total |
| `promoted_rule_events` ledger | 0 rows (shadow mode, no writes yet) |
| Orchestrator mode | `shadow` |
| First shadow tick | scanned=26, would_apply=26 (ZombieSiteTransition), failed=0, elapsed=33ms |
| north-valley-branch-2 | 2 live (osiriscare, osiriscare-2) + 1 decommissioned (osiriscare-3) |

---

## Emergency prod incidents

1. **Deploy silent-no-op** — `docker compose up -d` without `restart`. 6h of green deploys that never loaded. Fixed: explicit `restart` + `/api/version` SHA-match verify.
2. **Migration 181 blocked by own trigger** — bootstrap UPDATE ran after trigger install. Fixed by reordering.
3. **pgbouncer DuplicatePreparedStatement** — `docker compose down+up` cleared state. Root fix pending (DEALLOCATE ALL on acquire, or `prepared_statement_name_func`).
4. **`os` not imported in background_tasks.py** — module-level `os.getenv` crashed startup. Emergency-patched via scp, committed.
5. **site_appliances CHECK too narrow** — migration 180's IF-NOT-EXISTS guard skipped widening. Codified in migration 182.

---

## 24-hour shadow window (ACTIVE)

Started: 2026-04-13 21:14 UTC
Re-audit: 2026-04-14 ~21:30 UTC

Probes for re-audit:
- Zero `failed` outcomes across ~288 shadow ticks
- Tick elapsed_ms stays < 200ms (baseline: 33ms)
- No `orchestrator_find_candidates_failed` nor `orchestrator_transition_exception` logs
- `pg_stat_activity` shows no long-held locks on promoted_rules
- Prom `osiriscare_flywheel_rules_by_state` matches DB
- Mesh rebalance to 2-node ring landed cleanly

If all green → flip `FLYWHEEL_ORCHESTRATOR_MODE=enforce` → delete old step-5a-bis → second-site spin-up.

---

## Files changed

| File | Change |
|---|---|
| `mcp-server/central-command/backend/migrations/181_flywheel_spine.sql` | **NEW** — ledger, state machine, trigger, advance_lifecycle() |
| `mcp-server/central-command/backend/migrations/182_widen_appliance_status_check.sql` | **NEW** — widen CHECK (migration 180 was skipped) |
| `mcp-server/central-command/backend/flywheel_state.py` | **NEW** — orchestrator + 5 transitions |
| `mcp-server/central-command/backend/background_tasks.py` | +flywheel_orchestrator_loop, +mark_stale_appliances_loop, +import os |
| `mcp-server/central-command/backend/flywheel_promote.py` | +safe_rollout_promoted_rule + R6 advance_lifecycle wire |
| `mcp-server/central-command/backend/flywheel_math.py` | +classify_absolute_floor, +normalize_rule_action, +build_daemon_valid_rule_yaml |
| `mcp-server/central-command/backend/routes.py` | +flywheel-spine endpoint + acknowledge POST |
| `mcp-server/central-command/backend/prometheus_metrics.py` | +flywheel funnel gauges, +per-appliance offline gauge |
| `mcp-server/central-command/backend/sites.py` | checkin STEP 3 recovery detection + live_status as authoritative status |
| `mcp-server/central-command/backend/learning_api.py` | bulk-promote → safe_rollout_promoted_rule |
| `mcp-server/central-command/backend/client_portal.py` | client-approve → safe_rollout_promoted_rule |
| `mcp-server/central-command/backend/tests/test_flywheel_spine_pg.py` | **NEW** — 12 PG integration tests |
| `mcp-server/central-command/backend/tests/test_yaml_daemon_compat.py` | **NEW** — Python mirror of Go validator |
| `mcp-server/central-command/backend/tests/test_appliance_offline_detection_pg.py` | **NEW** — 7 state-machine tests |
| `mcp-server/central-command/backend/tests/test_flywheel_unhealthy_surface.py` | **NEW** — 6 source-level guardrail tests |
| `mcp-server/central-command/backend/tests/test_regime_detector.py` | +absolute-floor + YAML synthesizer tests (21 tests total) |
| `mcp-server/central-command/backend/tests/test_promotion_rollout_pg.py` | +l1_rules seed rows for 3 fixtures |
| `mcp-server/main.py` | +/api/version endpoint, +sign_data RuntimeError, +mark_stale + orchestrator in task_defs |
| `.github/workflows/deploy-central-command.yml` | +retention step, +SHA verify, +workflow self-trigger, +PG tests for spine |
| `scripts/reconcile_promoted_rules_orders.py` | **NEW** — backfill orphan rollout orders (audit-gated) |
| `scripts/backfill_promoted_rules_yaml.py` | **NEW** — backfill stub YAML with synthesized valid rule bodies |

---

## Carried-forward (NOT addressed)

- **Installer boot-loop diagnosis** — osiriscare-3 needs physical/VM console; blind fixes from repo aren't enterprise
- **Second-site spin-up** — blocked on installer fix
- **Slack/PagerDuty for appliance_offline** — currently email-only
- **R5 operator-ack UI** — backend endpoint exists, no frontend consumer
- **E2E daemon test in CI** — needs Docker-in-Docker infra
- **normalize_rule_action DB-lookup refactor** — hardcoded prefix list still in place
- **`_record_divergence` Prom surfacing** — in-memory counter only
- **pgbouncer DuplicatePreparedStatement root fix** — only workaround applied

---

## Next Session Priorities

1. **24h shadow re-audit** at 21:30 UTC 2026-04-14 — verify probe list above
2. **Flip to enforce** if shadow clean — 26 zombies retire in first tick
3. **Installer boot-loop diagnosis** — osiriscare-3 console + `iso/configuration.nix` review
4. **Second-site spin-up** — repurpose chaos lab once installer is verified
5. **Delete old step-5a-bis** — only after ≥24h enforce-mode stability
6. **R5 frontend panel** — `/api/dashboard/flywheel-spine` consumer with operator-ack button
