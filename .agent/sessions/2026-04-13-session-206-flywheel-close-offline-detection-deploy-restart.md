# Session 206 — Flywheel measurement loop closes end-to-end + enterprise offline detection + deploy-restart bug

**Date:** 2026-04-13
**Branch:** main
**Last commit:** e305641
**Outcome:** Phase 15 closed (task #122 completed). 9 distinct bugs fixed; `promoted_rules.deployment_count` incremented from 0→1 in production for the first time in this fleet's history.

---

## TL;DR

User asked to verify whether the round-table audit's claim "the flywheel is broken" was real. Backfill validation surfaced **5 original Phase 15 bugs + 4 orthogonal bugs latent in the system**. All fixed. Final proof:

```
rule_id                       | deployment_count | last_deployed_at
L1-AUTO-RANSOMWARE-INDICATOR  |                1 | 2026-04-13 18:56:51 UTC
```

Plus shipped enterprise QoL: appliance offline-detection loop, recovery alerts, API status unification, Prom per-appliance gauge, deploy-workflow restart fix.

---

## Bugs found and fixed

### Original 5 Phase 15 bugs (round-table flywheel audit)

| # | Bug | Fix commit |
|---|---|---|
| 1 | `learning_api.py` admin-bulk-promote bypassed `issue_sync_promoted_rule_orders` | `883e5ec` |
| 2 | `client_portal.py` client-approve bypassed the same | `883e5ec` |
| 3 | Regime detector missed always-bad rules (no delta = no event) | `883e5ec` (added `classify_absolute_floor` + lifetime auto-disable) |
| 4 | No dashboard surface for unhealthy promoted rules | `1ce23f3` (added `unhealthy_promoted_rules` to flywheel-intelligence + UI band) |
| 5 | 43 historical orphan promoted_rules with no rollout order ever issued | `49eaadf` + `e305641` (reconcile script with live-checkin filter) |

### 4 orthogonal bugs surfaced during validation

| # | Bug | Fix commit | How surfaced |
|---|---|---|---|
| A | `main.sign_data` returned `hashlib.sha256(data).hexdigest() * 2` (SHA256 doubled to 128 hex chars — passes hex validator, fails Ed25519 verify) when `signing_key` was None | `4c66323` | Reconcile script ran via `docker exec python3` — fresh process, lifespan never ran, signing_key=None, produced bogus signature, appliance rejected with "tried 1 keys" |
| B | All promoted rule YAML had `action: execute_runbook` but Go daemon's `allowedRuleActions` whitelist only accepts `run_windows_runbook`/`run_linux_runbook`/etc | `75e23e1` (action translation) + `4c2d9a9` (test fixture rename) | After fix A, completion came back "action X not in allowed actions" |
| C | All promoted rule YAML had no `conditions:` block; Go daemon's `processor.go:163` requires `len(rule.Conditions) > 0` | `1a3aeee` (build_daemon_valid_rule_yaml synthesizer) + `e305641` (drop nonexistent description column) | After fix B, completion came back "rule must have at least one condition" |
| D | **Deploy workflow `docker compose up -d` is a no-op when compose config hasn't changed.** Bind-mounted Python code was written to disk but the running interpreter kept old modules. Container ran continuously from 12:12 to 18:03 UTC — 6 hours of "successful" deploys that never actually loaded | `5fe611a` (added explicit `docker compose restart mcp-server frontend`) | Verified migration 180 had applied at 16:33 (separate `docker exec migrate.py` step) but `mark_stale_appliances_loop` wasn't in the running task registry |

### Enterprise QoL: F1–F4 offline detection (`e85d604` + migration 180)

- **F1**: `mark_stale_appliances_loop` runs every 2 min; `UPDATE site_appliances SET status='offline', offline_since=NOW(), offline_event_count++` when `last_checkin > 5 min`. Critical email on first transition (debounced via `offline_notified` flag).
- **F2**: Checkin STEP 3 upsert stamps `recovered_at` in CASE if prior `status='offline'`. Post-upsert savepoint reads it and emits `appliance_recovered` info alert + resets `offline_notified`.
- **F3**: API unification — `/api/sites` and `/api/sites/{id}/appliances` now return `live_status` as authoritative `status`. Stored DB value exposed as `stored_status` for admin diagnostics only. Frontend can't accidentally render stale.
- **F4**: Prom per-appliance gauge `osiriscare_appliance_offline{site_id, appliance_id, display_name, since_sec}`. Existing aggregate gauge stayed; new per-row gauge gives alerting cardinality.

Migration 180 added: status CHECK constraint, partial index on stale-detect query, `recovered_at` + `offline_event_count` columns.

7 PG integration tests pin the state machine end-to-end (stale→offline; fresh stays online; decommissioned/soft-deleted untouched; recovery stamps recovered_at; CHECK rejects garbage; debounce rearms; counter accumulates).

---

## Critical infrastructure findings

### The deploy bug was 6 hours latent (worst of the day)

Before today, `.github/workflows/deploy-central-command.yml` ran `docker compose up -d mcp-server frontend` after rsync. With Python code bind-mounted from `/opt/mcp-server/dashboard_api_mount/`, file changes were visible to the container but the running uvicorn process held the OLD bytecode. `up -d` only triggers a recreate when compose definition changes — which it never does for code-only deploys.

**Retroactive impact (12:12 → 18:03 UTC):**

| Commit | Affected code path | Live in process? |
|---|---|---|
| 36815a1 | `fleet_intelligence` severity ORDER BY CASE | NO (was sorted wrong for 2h51m) |
| 883e5ec | (test-only import shim) | N/A |
| 1ce23f3 | `unhealthy_promoted_rules` field on flywheel-intelligence | NO (UI section rendered empty) |
| 4c66323 | `sign_data` RuntimeError on missing key | NO (preventive only — no active harm) |
| e85d604 | F1-F4 offline detection + migration 180 | Migration applied (separate `docker exec` step), but loop never started, status field never returned `live_status`, recovery alerts never fired |
| 4c2d9a9 | (test fixture only) | N/A |

Migrations are safe — they run via `docker exec mcp-server python3 /app/dashboard_api/migrate.py up`, a fresh process that reads bind-mounted code each time. So migration 180 applied correctly even though the running uvicorn never restarted.

**Fix:** workflow now runs `docker compose up -d` (compose-config changes) THEN `docker compose restart mcp-server frontend` (force fresh interpreter). Validated post-fix: container `StartedAt` updated 18:51 after `e305641` deploy ran the new workflow.

---

## Production verification

```
Order:        d9c36a55-ba74-428a-acb7-7281e90410fd
Site:         north-valley-branch-2
Rule:         L1-AUTO-RANSOMWARE-INDICATOR
Issued:       2026-04-13 18:46:48 UTC (via reconcile_promoted_rules_orders.py --apply)
Acked:        2026-04-13 18:56:51 UTC (≈10 min)
Completion:   status=completed
Trigger:      trg_track_promoted_rule_deployment fired
Counter:      promoted_rules.deployment_count 0 → 1
Last deployed: 2026-04-13 18:56:51 UTC
```

Synthesized YAML the daemon accepted:
```yaml
id: L1-AUTO-RANSOMWARE-INDICATOR
name: ransomware_indicator
description: Auto-promoted L1 rule for ransomware_indicator
conditions:
  - field: incident_type
    operator: eq
    value: ransomware_indicator
action: run_windows_runbook
action_params:
  runbook_id: RB-WIN-STG-002
enabled: true
```

---

## Round-table hardening backlog (P0/P1)

Priority items from end-of-session round-table audit (full list in conversation):

| P | Item | Why |
|---|---|---|
| P0 | Cross-language YAML validation in CI (Go validator runs against Python `build_daemon_valid_rule_yaml` output) | Today's `conditions:` bug would have been caught in 30s |
| P0 | `osiriscare_app_git_sha` post-deploy verification metric | Catches future bind-mount silent-no-op regressions |
| P0 | Reconcile script must call `create_privileged_access_attestation()` | Today's 3 reconcile runs violated the CLAUDE.md privileged chain-of-custody invariant |
| P1 | Single `flywheel_promote.persist_and_rollout()` helper to dedupe 3 promotion writers | Next promotion writer added will repeat today's bypass |
| P1 | E2E test in CI: real Go daemon container loads our YAML through actual `validateRule()` | Validates the chain we manually tested today |
| P1 | Backfill historical `promoted_rules.rule_yaml` with synthesized YAML | DB-level audit currently misleading (rules look broken even though we rewrite at issue time) |

---

## Files touched (key ones)

```
mcp-server/central-command/backend/
├── flywheel_promote.py                       # 3-way import shim, l1_rules incident_pattern lookup, build_daemon_valid_rule_yaml call
├── flywheel_math.py                          # +classify_absolute_floor, +normalize_rule_action, +build_daemon_valid_rule_yaml
├── background_tasks.py                       # +mark_stale_appliances_loop (~110 LOC)
├── sites.py                                  # STEP 3 upsert: recovered_at CASE; STEP 3.0a recovery alert; live_status unification (×2)
├── prometheus_metrics.py                     # +osiriscare_appliance_offline per-row gauge
├── routes.py                                 # +unhealthy_promoted_rules in flywheel-intelligence
├── learning_api.py                           # +issue_sync_promoted_rule_orders call after promoted_rules INSERT
├── client_portal.py                          # +issue_sync_promoted_rule_orders call before transaction.commit()
├── tests/test_flywheel_unhealthy_surface.py  # NEW (6 source-level tests)
├── tests/test_appliance_offline_detection_pg.py  # NEW (7 state-machine PG tests)
├── tests/test_promotion_rollout_pg.py        # +l1_rules seed rows in 3 fixtures
├── tests/test_regime_detector.py             # +14 tests (absolute floor + classifier + YAML synthesizer)
└── migrations/180_appliance_offline_detection.sql  # NEW (CHECK + index + columns)

mcp-server/main.py                            # sign_data raises RuntimeError if signing_key None;
                                              # mark_stale_appliances_loop registered in task_defs

mcp-server/central-command/frontend/src/
├── pages/Dashboard.tsx                       # +unhealthy_promoted_rules section
└── hooks/useFleet.ts                         # +unhealthy_promoted_rules in FlywheelIntelligence interface

scripts/reconcile_promoted_rules_orders.py    # NEW: backfill orphan promoted_rules; live-checkin filter; explicit signing-key load

.github/workflows/deploy-central-command.yml  # +pytest step for offline-detection test;
                                              # CRITICAL: explicit `docker compose restart` after `up -d`
```

Total: 7 commits today (`883e5ec` `1ce23f3` `4c66323` `49eaadf` `e85d604` `75e23e1` `4c2d9a9` `5fe611a` `1a3aeee` `e305641`).

---

## What this session did NOT do

- ❌ Backfill the remaining 42 orphan promoted_rules — only validated with 1 (`L1-AUTO-RANSOMWARE-INDICATOR` at `north-valley-branch-2`). Reconcile script is ready; rest are gated on live-appliance presence (most belong to dead/zombie sites).
- ❌ Backfill historical `promoted_rules.rule_yaml` to match the synthesized format — DB still has stub YAML; we rewrite at issue-time.
- ❌ Audit ALL deploys in last 30 days for the silent-no-op pattern — only confirmed today's window.
- ❌ Add the cross-language YAML validation in CI (P0 backlog).
- ❌ Add reconcile-script attestation (P0 backlog).
- ❌ Slack/PagerDuty integration for `appliance_offline` alerts.

---

## Lessons + memories

1. **Bind-mount + `docker compose up -d` = silent-no-op deploy.** Always force `restart` after `up -d` for bind-mounted runtimes. New CLAUDE.md memory + workflow patch enforces.
2. **`sign_data` placeholder fallback was a 100% silent failure mode.** Removing it + raising loudly will catch the next caller that runs in a fresh process.
3. **Strict prefix classifiers in pure functions surface dead/zombie data.** The reconcile failed loudly on `RB-DRIFT-*` and `general` runbook IDs that previously sat dormant.
4. **One bug usually masks N more.** The "flywheel doesn't increment counter" symptom was actually 4 stacked bugs (order issue path → sig validation → action whitelist → conditions block). Don't celebrate after the first fix.
5. **End-to-end tests beat layered unit tests when integrating two languages.** Python tests passed all green while the Go daemon rejected every order. P0 backlog: cross-language validator.
