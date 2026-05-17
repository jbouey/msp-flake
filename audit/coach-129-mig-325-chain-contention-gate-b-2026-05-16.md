# Gate B fork-review — commit 7d9c33db (#117 Sub-commit B, mig 325)

**Date:** 2026-05-16
**Reviewer:** fork (fresh-context Gate B)
**Subject:** mig 325 chain-contention seed site + sev2 invariant
**Protocol:** TWO-GATE adversarial review (CLAUDE.md lock-in 2026-05-11)

## Overall verdict: BLOCK

**One P0 (migration apply-time crash) + 1 P1 + 2 P2.** Mig 325 will fail to apply
on prod the moment it lands. Halt deploy; fix P0 in a new commit before
attempting CI push.

## Per-lens verdict

1. **Steve (Principal SWE):** BLOCK — `ON CONFLICT (key_hash)` references a
   non-existent UNIQUE constraint. Apply-time `InvalidColumnReferenceError`.
2. **Maya (Security/HIPAA):** APPROVE — deterministic bearers are safe in
   source because `_enforce_site_id` (shared.py:452) blocks cross-site use;
   bearer scope is the synthetic seed site only; admin_audit_log row written.
3. **Carol (Ops/CCIE):** APPROVE — runbook is operator-actionable: 4 root-
   cause categories, drill-down SQL, §164.316 quarantine-not-delete rule,
   escalation matrix with sev1 trigger conditions.
4. **Coach (DBA):** BLOCK — same P0; plus trigger interaction: mig 209's
   `enforce_one_active_api_key_per_appliance` BEFORE-INSERT fires per row.
   Harmless on first apply (distinct appliance_ids) but interacts with the
   missing UNIQUE.
5. **Auditor (Counsel Rule 4):** APPROVE — orphan invariant + carve-out is
   the canonical defense pattern; `client_org_id=NULL` correctly disclaims
   customer-data ownership.
6. **PM (scope):** APPROVE-WITH-FIXES — once P0 fixed, B is standalone-
   shippable and unblocks C+D.
7. **Counsel (7-rule filter):** APPROVE — Rule 2 (no PHI cross), Rule 4
   (orphan coverage), Rule 1 (canonical metric N/A — synthetic infra),
   Rule 7 (no unauth context — bearers behind site-bound auth). All clear.

## Test sweep

```
bash .githooks/full-test-sweep.sh
✓ 277 passed, 0 skipped (need backend deps)
```
Exit 0. Source-shape gates green; CI-pg gates (test_startup_invariants_pg
etc.) deferred to CI server (no asyncpg locally) — these will catch the P0
unless mig is squashed first.

## Findings

### P0-1 — mig 325 ON CONFLICT references non-existent UNIQUE constraint
**File:** `mcp-server/central-command/backend/migrations/325_load_test_chain_contention_site.sql:156`
**Severity:** P0 (apply-time crash, mig fails to land on every env)
**Class:** Same as Session 210-B `promoted_rules` lesson (CLAUDE.md rule).

Mig 325 uses:
```sql
ON CONFLICT (key_hash) DO NOTHING;
```

But `api_keys` has NO UNIQUE constraint on `key_hash`. Per
`prod_unique_indexes.json`: `api_keys: [["id"]]` only. All 5 prod INSERT
callsites (`provisioning.py:315/881`, `sites.py:5784`, `routes.py:1834/7373`)
use bare `ON CONFLICT DO NOTHING` (no target) or no ON CONFLICT at all.

Postgres will raise:
```
InvalidColumnReferenceError: there is no unique or exclusion constraint
matching the ON CONFLICT specification
```

**Minimal fix:** drop the column target — use `ON CONFLICT DO NOTHING`
without `(key_hash)`. Re-applies are idempotent because mig 209's
BEFORE-INSERT trigger auto-deactivates prior matching rows; a re-INSERT
won't error (no conflict path to engage). Alternative: add
`CREATE UNIQUE INDEX IF NOT EXISTS uniq_api_keys_key_hash ON api_keys(key_hash)`
earlier in mig 325 — but per-tuple uniqueness is questionable design and
the bare DO-NOTHING is simpler. Lesson is already in CLAUDE.md for promoted_rules.

### P1-1 — 4h COALESCE buffer can false-positive on long-running soak
**File:** `mcp-server/central-command/backend/assertions.py:2504-2507`

The invariant uses `started_at + INTERVAL '4 hours'` to bound runs with
NULL `completed_at`. The #117 design max is 30min, so 4h is generous, but
ANY soak run that legitimately exceeds 4h (a chaos test, an admin op, a
clock-stalled run row) will produce orphan-bundle false-positives on the
next 60s tick.

**Fix:** either (a) tighten to 1h and document max-soak as 1h in the
contract, or (b) widen to 24h to bound the false-positive class. Current
4h is the worst-of-both-worlds middle ground. Recommend (b) — synthetic
infra should never have a covering-row gap longer than a day.

### P2-1 — runbook drill-down SQL would scan partition-default
**File:** `substrate_runbooks/load_test_chain_contention_site_orphan.md:53-57`

The drill-down `SELECT ... FROM compliance_bundles WHERE site_id = '...'
AND created_at > NOW() - INTERVAL '7 days'` will hit the planner-fast
partition path. Acceptable, but if an operator runs the variant without
the `created_at` bound (e.g. to forensically inspect all-time orphans), it
will scan every monthly partition. Add a comment: "always include
`created_at > ...` predicate for partition pruning."

### P2-2 — admin_audit_log INSERT uses ON CONFLICT DO NOTHING with no UNIQUE
**File:** `mig 325:182`

`admin_audit_log` PK is `id SERIAL` — no row will ever conflict. The bare
`ON CONFLICT DO NOTHING` is a no-op. Cosmetic but misleading: re-applying
the migration writes ANOTHER `LOAD_TEST_SEED_APPLIED` row each time
(provenance grows, doesn't dedupe). Either drop the ON CONFLICT (clear
intent) or add `WHERE NOT EXISTS (SELECT 1 FROM admin_audit_log WHERE
action='LOAD_TEST_SEED_APPLIED' AND target='load-test-chain-contention-site')`
for true idempotency.

## Verification before unblock

1. Apply minimal-fix P0-1 commit; re-run pre-push sweep.
2. CI must pass `test_startup_invariants_pg.py` (it WILL run mig 325).
3. P1-1 + P2-* may be carried as TaskCreate followups in the same commit.

## Recommendation

Drop mig 325 from prod path until P0-1 is fixed. Fix is one-line:
`ON CONFLICT (key_hash) DO NOTHING` → `ON CONFLICT DO NOTHING`.
