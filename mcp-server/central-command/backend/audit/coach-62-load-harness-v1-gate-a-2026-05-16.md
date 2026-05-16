# Gate A — Load Testing Harness v1 (Task #62 / #97)

**Date:** 2026-05-16
**Verdict:** APPROVE-WITH-FIXES (P0s: 3, P1s: 7) — v2.1 must close all P0s before any infra spin-up; P1s land in same commit OR named TaskCreate followups
**Design under review:** `.agent/plans/40-load-testing-harness-design-2026-05-12.md` (2026-05-12)
**Prior verdict refreshed:** `audit/coach-load-test-harness-design-gate-a-2026-05-12.md` (v1 BLOCK 2026-05-12); v2 packet at `audit/load-harness-v2-design-2026-05-13.md` partially addresses but has not received its own fresh-context Gate A
**Reviewers (fork, fresh context):** Steve / Maya / Carol / Coach / Auditor / PM / Counsel

## Verdict summary
v1 design as written cannot ship — three structural P0s (wrong endpoint paths, cryptographic-table isolation breach on `compliance_bundles`, isolation-pattern duplication of plan-24's `details->>'soak_test'` marker). The v2 packet drops `/evidence/upload` and adopts plan-24's marker pattern but introduces NEW concerns that the v2.1 redesign must inherit as Gate A baseline. Seven P1s span tool-ceiling math, kill-switch, customer-blast-radius SLA, bearer rotation, sequencing pin to #94/#98, auditor-kit determinism interaction, and CX22 firewall posture. **Top blocker:** P0-2 (any synthetic write into `compliance_bundles` corrupts the Ed25519 chain + auditor-kit determinism — Counsel Rule 1 + Rule 7).

## P0s (blocking — must close before v2.1 ships)
1. **Steve: Wave-1 endpoint paths don't match the route table.** `/api/appliances/order` and `/api/evidence/sites/{id}/submit` (design lines 32+34) do not exist — actual paths are `GET /api/appliances/orders/{site_id}` (agent_api.py:521) and `POST /evidence/upload` (agent_api.py:2519). A k6 script written against v1 verbatim 404s on 2/5 scenarios. **Fix:** every wave-1 row must cite `file:line` for the route and the auth dependency; CI grep gate to prove path exists.
2. **Maya: Isolation pattern is incompatible with `compliance_bundles` Ed25519+OTS+RLS+partitioning invariants.** "Separate table or partition" framing collides with the immutable per-site hash chain, the live monthly partition's `pg_class.reltuples` (Session 219 COUNT(*) timeout class), and the `tenant_org_isolation`/admin-bypass RLS split. 180K synthetic bundles/hr would either corrupt the chain (real site_id) or admin-leak into fleet aggregations (synthetic site_id). **Fix:** drop `/evidence/upload` from Wave 1 (v2 already did this — preserve in v2.1) OR spec a `load_test_bundles` table with no chain/OTS coupling.
3. **Coach: Duplicates plan-24's soak-isolation pattern instead of generalizing it.** Plan-24 + mig 303 established `details->>'soak_test'='true'` column marker + partial index + filter discipline. v1 proposes a header `X-Load-Test: true` + separate `load_test_checkins` table — two parallel disciplines for the same risk class. **Fix:** unify on `details->>'synthetic' IN ('mttr_soak','load_test')` enum-style marker; one partial-index pattern; one filter-coverage AST gate.

## P1s (close inside v2.1 same commit OR named TaskCreate followup)
1. **Steve: Wave-1 missing high-volume bearer endpoints.** `/api/agent/executions` (~30 POST/min at 100-fleet), `/agent/patterns`, `/api/agent/sync/pattern-stats`, `/api/devices/sync`, `/api/logs/`, `/incidents`. Justify exclusions or include.
2. **Steve: k6 VU ceiling on CX22 (2 vCPU/4GB) unspecified for Scenario C 10× ramp.** k6 scripts run in goja JS VM; CPU-bound payload generation doesn't scale past ~500 VUs on a 2-vCPU box. Pin VU ceiling math; plan distributed execution if needed.
3. **Maya: Pre-flight kill-switch unspecified.** 60-min Scenario C against prod with no operator-callable abort is the worst-case shape. Spec abort-file polled per iteration + `/api/admin/load-test-status` flag re-read every 30s + AlertManager rule that flips the flag on 5xx-storm.
4. **Maya: Real-customer degradation SLA during runs unquantified.** "0 5xx" alone doesn't bound real-customer p95 impact. Spec external probe (real appliance heartbeat latency) with `< 500ms` ceiling during runs; abort if breached.
5. **Carol: Bearer storage + rotation + revocation + audit-log unspecified.** v1 §Gate-A-asks #3 is a one-liner counter-argument (the exact antipattern the user called out 2026-05-11). Spec: storage (Vault Transit or 1Password — NOT local CX22 fs), rotation cadence (7d), revocation path (`site_appliances.bearer_revoked` column needs to exist), and per-run `admin_audit_log` row (run_id + actor + token_id).
6. **Auditor: Auditor-kit determinism interaction is a data-integrity invariant, not a P2.** Kit hash pins to chain progression + OTS state (CLAUDE.md auditor-kit determinism contract). ANY load-test write that lands in `compliance_bundles` for a real-customer site flips the kit hash between downloads — visible as a tamper-evidence violation. Elevated from v1's P2-2. CI gate: assert no load-test marker row in `compliance_bundles` for any `client_org_id IS NOT NULL` site.
7. **Carol: CX22 WG-peer firewall posture is a security-perimeter requirement, not P2.** Load-test box on WG peer .4 sits alongside Vault Transit (.3) + VPS (.2) + appliance (.2). Spec outbound `central-command.osiriscare.com:443` ONLY; inbound denied; explicitly NOT able to reach Vault Transit API. Elevated from v1's P2-3 — Vault reachability from a synthetic-load host is a Rule-7-class context leak.

## Per-lens findings
- **Steve (Principal SWE):** k6 IS the right tool — scenarios DSL + Prometheus integration carry SLA-grade work; vegeta lacks stages/executor. But CX22 underspec for Scenario C + 2/5 endpoint paths wrong = design needs route-table re-verification pass.
- **Maya (HIPAA/legal):** PHI-free synthetic data is correct framing but `compliance_bundles` write path breaks it — synthetic bundles touch the same Ed25519 chain that customer auditor kits hash. No raw PHI risk; cryptographic-anchor corruption risk YES.
- **Carol (DBA/security):** PgBouncer pool at 100 req/s × 5 endpoints with current 25-server-conn budget is the critical question — design doesn't size it. Bearer token surface needs full lifecycle spec, not one-liner. `admin_transaction` routing fine for new endpoint but checkin's savepoint discipline (Session 200) must be re-verified at load.
- **Coach (consistency):** Three scenarios (A steady / B burst / C ramp) cover the curve correctly but Scenario C's "anti-goal: don't crash prod" contradicts the "run against prod" decision — pick one. Baseline comparability across runs requires pinned k6 + CX22 image SHA.
- **Auditor (chain-of-evidence):** Load-run reproducibility needs signed artifact — every run emits a manifest (scenario SHA + k6 SHA + image SHA + run_id) signed by Vault Transit, persisted to `load_test_runs` table. Without this, regression claims aren't audit-defensible.
- **PM (sequencing):** #97 → #94 v2 → #98 dependency chain unpinned. v1 says #98 needs #97 load floor + #94 v2 needs #97 throughput baseline. Pin commit order: (a) #97 v2.1 Wave-1 ships standalone, (b) #94 v2 redesign references it, (c) #98 24h SLA soak runs after both green for ≥7d.
- **Counsel (Rule 1 + Rule 4 + Rule 7):** Rule 1 — capacity-number ("N simultaneous clinics") MUST declare canonical source (`load_test_runs.summary->>'max_sustained_clinics'`) and ship behind a non-authoritative label until 3 consecutive runs corroborate. Rule 4 — load-test traffic against real prod paths creates phantom-load orphan-coverage risk if the marker filter is missed in any aggregation; substrate invariant required (sev2 scan for load-test-marked rows leaking into non-marker-filtered queries). Rule 7 — capacity number in any unauthenticated channel (sales deck, webhook subject) is a Rule-7 context leak; gate behind auth or strip from previews.

## Execution order for v2.1
1. **Commit 1 (~1 day):** Spec doc revision — close P0-1 (route paths + grep CI gate), P0-2 (drop `/evidence/upload` OR `load_test_bundles` table spec), P0-3 (unify on `details->>'synthetic'` marker + extend mig 303 partial index).
2. **Commit 2 (~1 day):** P1-3 + P1-4 (kill-switch backend flag + customer-degradation probe + abort wiring) — these are runtime-safety primitives that MUST exist before any infra runs.
3. **Commit 3 (~1 day):** P1-5 (bearer lifecycle: Vault Transit storage + rotation + revocation column mig + audit-log row) + P1-7 (CX22 firewall spec in `.agent/reference/NETWORK.md`).
4. **Commit 4 (~2 days):** Infra spin-up — CX22 + WG peer .4 + k6 binary + first dry-run Scenario A at 10% load against synthetic site.
5. **Commit 5 (~1 day):** P1-6 (auditor-kit CI gate) + P1-1 (Wave-1 endpoint expansion) + P1-2 (VU ceiling math + distributed plan if needed).

## Pre-execution blockers
- **All 3 P0s closed in v2.1 design doc** before CX22 provisioning. P0-2 in particular — provisioning the box before the cryptographic-table carve-out is decided creates pressure to ship the wrong shape.
- **Sequencing pin to #94 v2 + #98** (currently both Gate-A-BLOCKED) — if #94 v2 redesign needs throughput numbers from #97, the seam MUST be pinned in writing in BOTH plans before either ships.
- **Substrate invariant for load-test marker leakage** (Counsel Rule 4 / Coach P0-3) must be designed alongside the marker unification, not deferred.

## Gate B preview
v2.1 Gate B fork (post-implementation, pre-completion) MUST verify:
- All 5 Wave-1 endpoint paths return 200 (or expected non-5xx) under `curl` from CX22 with the synthetic bearer.
- `details->>'synthetic'='load_test'` marker present on every k6-generated row in EVERY destination table (`appliance_checkins`, `fleet_orders`, `journal_entries`, `agent_executions` if added).
- Substrate invariant for marker-leak fires sev2 alert when test row inserted without marker (positive control).
- Full pre-push CI sweep green (`bash .githooks/full-test-sweep.sh`) per Session 220 Gate B lock-in — diff-scoped review is automatic BLOCK.
- Vault Transit signing path NOT reachable from CX22 (Carol P1-7 verification via `nc -zv vault.osiriscare.com 8200` from .4 returns refused).
- One dry-run Scenario A executed end-to-end with kill-switch tested (flip flag mid-run, assert k6 exits within 30s).
