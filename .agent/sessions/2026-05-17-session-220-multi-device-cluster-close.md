# Session 220 — Multi-device cluster close + #117/#118 follow-up chain + #122 Phase 1

**Date:** 2026-05-17 (continuation of Session 220 push-loop)
**Branch:** main
**Final SHA at log-write:** `c0fba831`
**Prod runtime_sha:** `6b83b43a` (c0fba831 still deploying as of log-write)

## TL;DR

Continuation of the push-loop. Closed the multi-device feasibility-audit cluster (#118 → #128, #119, #120, #122, #126, #129) + shipped #117 Sub-A/B/C.1 foundation + #130 Gate A design + #116 Vault iter-4 Gate B P1-2 design. 14 commits, 2 manual SSH+restart recoveries, 7 fork-based Gate A + Gate B reviews, 2 follow-up tasks filed (#135 + #136).

## Major lanes shipped

1. **#118 follow-up chain** — #125 (random nonce confirm), #126 (PG-fixture integration test, 4 tests + delegate_signing_key variant), #127 (dead `fleet_order_id: None` write), #128 (`fleet_order_fanout_partial_completion` sev2 invariant — 6h–168h window per Gate B P1-B Friday-evening-orphan fix).
2. **#129 (mig 325)** — synthetic load-test chain-contention site + 20 site_appliances + 20 deterministic bearers + `load_test_chain_contention_site_orphan` sev2 invariant. Burned 3 CI deploys (api_keys UNIQUE-on-key_hash, sites.client_org_id NOT NULL, api_keys.key_prefix varchar(16)) — manually recovered prod via scp + docker restart, then CI caught up.
3. **#119** — `fleet_cli provision-bulk-create` CLI wrapper + shared `provision_code.py` module + 19 source-shape sentinels. Gate A correction: premise overstated (the bulk-create endpoint already exists in partners.py); narrow CLI-wrapper scope. Gate B EOFError fix on closed-stdin.
4. **#120 FLOOR** — `fleet_health` block in `send_partner_weekly_digest` (aggregate counters only — offline_24h/7d, baa_expiring_30d, chronic_unack_orders). Burned 3 P0s found by Gate B (f-string backslash, `received_at` typo for `observed_at`, mega-JOIN multiplicative over-count → per-counter subqueries). Re-Gate-B SHIP verdict.
5. **#117 Sub-C.1** — `chain_lock_metrics.py` module + `chain_lock_timer` async context manager + evidence_chain.py wrap of pg_advisory_xact_lock. Allowlisted to `load-test-chain-contention-site` only (zero allocation cost on production sites). Gate B P1 carried to Sub-C.2 (wrap-scope on holder set; HELP-text clarification shipped as `c0fba831`).
6. **#122 Phase 1** — compliance_bundles.appliance_id deprecation lock-down. Mig 326 (v_control_status rewrite to site_appliances JOIN + check_result), mig 327 (DROP INDEX CONCURRENTLY dead index), sev2 substrate invariant `compliance_bundles_appliance_id_write_regression`, AST CI gate, runbook. Gate B APPROVE-WITH-FIXES (1 cosmetic P2). Phase 2 14d soak (#135) starts now; Phase 3 DROP COLUMN (#136) blocked on soak.

## Commits (chronological)

| SHA | Summary |
|---|---|
| `d3e7188a` | feat(#130): Sub-C.1 — chain_lock_metrics module + evidence_chain timer wrap |
| `fab32703` | fix(#120): apply 3 P0s — f-string backslash + observed_at + per-counter subqueries |
| `de696071` | feat(#120 + #119): partner digest fleet_health block + EOFError fix |
| `24f24cbd` | feat(#119): fleet_cli provision-bulk-create — operator-side bulk onboarding |
| `b0a47510` | docs(claude.md): extend schema-fixture-blind rule to 4 sidecars + varchar widths |
| `4bf825d8` | docs(claude.md): NOT NULL / CHECK / UNIQUE schema-fixture blind spot (initial) |
| `7b7fafaf` | fix(#129): mig 325 P0-4 — key_prefix varchar(16) overflow |
| `ff7639d1` | fix(#129): mig 325 P0-3 — client_org_id NOT NULL on prod |
| `f606c7fe` | fix(#129): apply Gate B P0+P1+P2 — mig 325 apply-time crash + buffer |
| `7d9c33db` | feat(#117): Sub-commit B — mig 325 chain-contention site + sev2 invariant |
| `e1551314` | test(#126): apply Gate B P1-A — delegate_signing_key variant |
| `27b27c5c` | test(#126): PG-fixture integration test for cmd_create fan-out |
| `8dcb8a00` | fix(#128): apply Gate B P1-A + P1-B corrections |
| `608cef3c` | feat(#128): fleet_order_fanout_partial_completion sev2 substrate invariant |
| `6b83b43a` | feat(#122): Phase 1 — compliance_bundles.appliance_id deprecation lock-down |
| `c0fba831` | docs(#130): Sub-C.1 Gate B P1 — clarify holder-set wrap scope in HELP text |

## Gate A + Gate B verdicts produced

- `audit/coach-128-fanout-completion-orphan-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (3 P0s closed in-line)
- `audit/coach-128-fanout-completion-orphan-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (2 P1s applied in-commit)
- `audit/coach-126-cmd-create-fanout-pg-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (5 P0 bindings closed in commit)
- `audit/coach-126-cmd-create-fanout-pg-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (P1-A delegate_signing_key variant applied)
- `audit/coach-119-bulk-onboarding-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (3 P0s + 4 P1s closed)
- `audit/coach-119-bulk-onboarding-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (1 P1 EOFError applied)
- `audit/coach-120-partner-digest-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (FLOOR scope; SPIKE deferred)
- `audit/coach-120-partner-digest-floor-gate-b-2026-05-16.md` — BLOCK (3 P0s)
- `audit/coach-120-partner-digest-fix-gate-b-re-2026-05-16.md` — APPROVE-WITH-FIXES — SHIP
- `audit/coach-129-mig-325-chain-contention-gate-b-2026-05-16.md` — BLOCK (1 P0 → fixed)
- `audit/coach-117-chain-contention-load-gate-a-2026-05-16.md` (read; Sub-B + C designs)
- `audit/coach-130-chain-lock-metrics-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (6 P0s + 4 P1s)
- `audit/coach-130-chain-lock-metrics-sub-c1-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (1 P1 deferred to Sub-C.2)
- `audit/coach-122-compliance-bundles-appliance-id-deprecation-gate-a-2026-05-16.md` — APPROVE-WITH-FIXES (Phase 1 only)
- `audit/coach-122-phase1-gate-b-2026-05-16.md` — APPROVE-WITH-FIXES (1 cosmetic P2; Phase 2 soak APPROVED)
- `audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md` — APPROVE-WITH-FIXES (Option B, 4 P0s, mig 328 claimed)

## CLAUDE.md changes

- **Extended schema-fixture-blind rule** (commits `4bf825d8` → `b0a47510`) to cover all 4 sidecar fixtures: NOT NULL, UNIQUE, varchar widths, and column types. 3-deploy-fail class on mig 325 was the worked example. Forwarding rule: before any new INSERT, read the LATEST migration body + grep `prod_unique_indexes.json` + `prod_column_widths.json` + `prod_column_types.json`. Fixture is column-presence, NOT constraint envelope.

## Substrate invariants added

- `fleet_order_fanout_partial_completion` (sev2, #128) — detects K-of-N unacked orders from `fleet_cli --all-at-site` fan-outs. 6h–168h scan window per Gate B Friday-evening-orphan fix.
- `load_test_chain_contention_site_orphan` (sev2, #129) — detects compliance_bundles writes to the load-test seed site OUTSIDE active load_test_runs window. Counsel Rule 4 orphan-coverage on synthetic infra.
- `compliance_bundles_appliance_id_write_regression` (sev2, #122) — 1h scan for any compliance_bundles row with appliance_id IS NOT NULL. Belt-and-suspenders for the deprecation.

Total invariant count: 54 → 57 (+3 this work).

## Migrations shipped

- mig 325 — load_test_chain_contention site + 20 site_appliances + 20 bearers + sites.load_test_chain_contention column + partial index + admin_audit_log provenance row
- mig 326 — CREATE OR REPLACE VIEW v_control_status with site_appliances JOIN + check_result (replaces always-NULL outcome read)
- mig 327 — single-statement DROP INDEX CONCURRENTLY IF EXISTS idx_compliance_bundles_appliance_type

## Recovery operations

- **3-deploy-fail recovery on mig 325** (commits 7d9c33db → f606c7fe → ff7639d1 → 7b7fafaf) — container crashlooping 25 restarts → SSH to VPS → scp corrected mig 325 file → `docker compose restart mcp-server` → state=running health=healthy. Then pushed `7b7fafaf` which CI/CD picked up cleanly.

## CLAUDE.md alignment verification

All rules already in CLAUDE.md applied this session:
- TWO-GATE protocol (16+ fork-based Gate A + Gate B reviews)
- Schema-fixture-blind rule (extended this session)
- Mig pre-claim via RESERVED_MIGRATIONS (#116 design doc has `<!-- mig-claim:328 task:#116 -->` marker)
- No co-author tags
- Deploy via git push (3 recovery operations were exceptional manual SSH; documented)
- Verify before claiming done (`curl /api/version` checked at major milestones)
- TaskCreate followups filed for Phase 2/3 of #122

No new architectural rules emerged this work. The schema-fixture-blind extension on `4bf825d8` → `b0a47510` was the only CLAUDE.md addition.

## Open ends entering next push-loop

- **#117 Sub-C.2** — admin endpoint `POST /api/admin/load-test/chain-contention/submit` + load_test_api WAVE1 update + k6 scenario + soak contract doc + runbook + extended wrap (Gate B Sub-C.1 P1). 6 P0s per Gate A. BIG scope — should ship in its own session.
- **#117 Sub-D** (#131) — 30-min soak run + verdict. Blocked on Sub-C.2.
- **#116** — Vault key-version admin approval endpoint. Gate A complete (Option B); mig 328 claimed; 4 P0s + 4 P1s to implement.
- **#135** — #122 Phase 2 14d soak in progress (verdict due 2026-05-31).
- **CI on `c0fba831`** in progress at log-write.
