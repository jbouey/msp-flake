# Gate B Re-Review — commit fab32703 (#120 partner-digest fix)

**Date:** 2026-05-16
**Scope:** fab32703 (3-P0 fix on de696071)
**Verdict:** **APPROVE-WITH-FIXES** (2 P1, 1 P2 — none block ship)

## Per-lens verdict

- **Steve (architecture):** APPROVE — per-counter subqueries are correct refactor; JOIN-explosion class eliminated by construction.
- **Maya (legal/HIPAA):** APPROVE — opaque-mode parity preserved; no PHI/clinic-name regression; aggregate-only counts.
- **Carol (security):** APPROVE — RLS posture unchanged; no new auth surface; base-table query (no MV bypass).
- **Brian (DBA/perf):** APPROVE-WITH-FIXES — 4 subqueries on planner is fine at small scale (1 partner, ≤250 appliances). Adversarial scaling — see P1-1, P1-2.
- **PM (product):** APPROVE — feature behavior identical; counts now CORRECT (no multiplicative inflation).
- **CCIE (ops):** APPROVE — Friday 09:00 cron path now executes without UndefinedColumnError.
- **Coach (consistency):** APPROVE — 3 new CI sentinels pin the regression class; tests align with fix intent.

## Test sweep

`bash .githooks/full-test-sweep.sh` → **279/279 pass, 0 skipped**.
Direct `tests/test_partner_digest_fleet_health.py` run → **13/13 pass**, including all 3 new sentinels.
Python 3.11 AST parse of both `email_alerts.py` + `background_tasks.py` → OK (P0-3 smoke-import class closed).

## 3 P0 closures — confirmation

- **P0-1 (`observed_at`):** CORRECT + COMPLETE. Verified against `prod_columns.json` — `appliance_heartbeats` has `observed_at`, NOT `received_at`. Both LATERAL subqueries (offline_24h + offline_7d) corrected. Pinned by sentinel.
- **P0-2 (per-counter subqueries):** CORRECT + COMPLETE. 4 scalar subqueries, each correlated to `partner_id` alone. JOIN-explosion class structurally impossible. Each subquery carries its own `sa.deleted_at IS NULL` + `s2.status != 'inactive'` + LATERAL window. Pinned by 2 sentinels.
- **P0-3 (f-string backslash):** CORRECT + COMPLETE. `hint` arg removed entirely (verified zero callers passed `hint` — 3 call sites all 2-arg). No future capability lost (4th tile is inlined for "Stuck orders", not via `_tile`).

## NEW findings (re-review)

### P1-1 — `fleet_orders` JOIN at scale (Brian)
`chronic_unack_orders` subquery JOINs `fleet_orders fo ON fo.parameters->>'target_appliance_id' = sa.appliance_id::text`. There is **no functional index on `parameters->>'target_appliance_id'`** (verified — only `idx_fleet_orders_active(status, expires_at) WHERE status='active'` exists). At 250 appliances × 100 chronic orders = 25K candidate rows the partial index prunes to active-only, then per-row JSONB extract + text-cast equality. For a 100K-row partial-active set this is a hash-or-merge join, acceptable. For ≥1M active orders, would warrant an expression index. Filed as **task followup**, not ship blocker — current fleet is well under threshold.

### P1-2 — LATERAL partition pruning (Brian)
`appliance_heartbeats` is partitioned (default partition + monthly?). The LATERAL `WHERE observed_at > NOW() - INTERVAL '30 days'` will partition-prune to the current + prior month partitions, then index-seek on `(appliance_id, observed_at)` if a composite exists. Per-(site, appliance) tuple cost is bounded. No issue at lab scale; flag for re-eval if heartbeat write rate >10K/sec.

### P2-1 — CI sentinel `received_at` bare-substring (Coach)
`test_gather_fleet_health_uses_observed_at_not_received_at` does `assert "received_at" not in query`. A TODO comment like `-- TODO: rename observed_at → received_at` would false-positive (raise BLOCK on a legit comment). Acceptable for now — comment is unlikely. Future hardening: regex against SQL identifier shape (`\breceived_at\b` outside `--` comments).

## P0 confirmation: NONE

No new P0 surfaced in the re-review. All 3 prior P0s closed correctly.

## Adversarial checks — all green

- BETWEEN inclusivity (BAA expiring exactly today / day-30): intended behavior, no bug.
- `s2.status != 'inactive'` NULL handling: NULL-status sites filtered out, intended.
- 4 round-trips vs old mega-JOIN: mega-JOIN was strictly worse (multiplicative row explosion); 4 subqueries faster + correct.
- `_tile` signature reduction: zero callers used `hint`; no capability loss.

## Recommendation

**APPROVE-WITH-FIXES — SHIP.** P1-1 (expression index on `target_appliance_id`) filed as task followup once fleet-orders cardinality crosses 1M active rows. P1-2 monitoring-only. P2-1 cosmetic.

Counter-signed: Steve / Maya / Carol / Brian / PM / CCIE / Coach.
