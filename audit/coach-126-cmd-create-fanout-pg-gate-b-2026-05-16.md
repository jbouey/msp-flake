# Gate B — #126 cmd_create fan-out PG-fixture test (commit `27b27c5c`)

**Date:** 2026-05-16
**Reviewer:** Gate B fork (fresh context), 7-lens panel
**Artifact:** `mcp-server/central-command/backend/tests/test_cmd_create_fanout_pg.py` (+325 LOC, +91 LOC Gate A doc)
**Gate A reference:** `audit/coach-126-cmd-create-fanout-pg-gate-a-2026-05-16.md` (APPROVE-WITH-FIXES, 5 P0s closed)

## Overall verdict: **APPROVE-WITH-FIXES** (1 P1, 1 P2 — neither blocks merge)

## Pre-push sweep (CLAUDE.md Session 220 lock-in)
Ran `bash .githooks/full-test-sweep.sh`:
- **276/276 source-level pass, 0 skipped** (PG-fixture file correctly skips locally — needs asyncpg + PG sidecar; runs in CI)
- Exit code 0

## Per-lens verdicts
1. **Steve (Principal SWE):** APPROVE — clean fixture, autocommit + per-test DROP/CREATE pattern matches sibling pg tests, regex strip verified (0 residual top-level BEGIN/COMMIT across all 4 migs), no leaked state between tests.
2. **Maya (Security/Privacy/HIPAA):** APPROVE — tests C/D/F together prove the 3 contract axes (allows fan-out, rejects no-bundle, rejects cross-site). Literal-substring check on `PRIVILEGED_CHAIN_VIOLATION` is the right shape to pin auditor-grep tooling.
3. **Carol (CCIE Network/Ops):** N/A — pure test, no network/ops surface.
4. **Coach (DBA):** APPROVE — mig 175 installs trigger (line 88-92); 218/223/305 only `CREATE OR REPLACE FUNCTION` (no trigger re-binding) so sequential load reaches `delegate_signing_key` + 11 other types in `v_privileged_types` while trigger stays bound. EXISTS index supporting predicate from mig 175 also loads. JSONB params built via UUID f-strings — UUIDs are `[0-9a-f-]` only, zero escape risk.
5. **Auditor:** APPROVE — Gate A doc + commit body name all 4 tests + scope/anti-scope, deferred B/E/G/H rationale explicit.
6. **PM:** APPROVE — #126 cleanly scoped as #118 Gate B P1-2 closure. Will mark in-progress→completed post-merge.
7. **Counsel (Rule 3):** APPROVE — behavioral pin on the trigger-body contract is the missing layer; AST gates (`test_no_param_cast_against_mismatched_column`, `test_privileged_order_four_list_lockstep`) prove SHAPE parity, this test proves BEHAVIORAL parity. Closes the "lockstep checker proves list parity but NOT body parity" gap noted in CLAUDE.md Session 220 lock-in.

## Findings

### P1-A (non-blocking, file followup)
Tests D + F both use `enable_emergency_access`. The trigger body is order-type-agnostic past the `ANY(v_privileged_types)` early-return — so D/F coverage IS sufficient for the EXISTS check. BUT a 4th test exercising `delegate_signing_key` (the mig 305 addition, newest member of the array) would catch a regression where a future mig 175 rewrite drops the new entry — same class as the lockstep-checker-proves-list-not-body bug. **Fix sketch:** add `test_e_trigger_rejects_missing_bundle_delegate_signing_key` (~20 LOC, copy D, swap order_type literal). Defer to followup task — current 4 tests are not weaker than Gate A scope.

### P2-A (cosmetic)
`test_a_enumeration_filters_soft_deleted_and_orders_by_appliance_id` uses an f-string with a literal `"now()"` / `"NULL"` (line 160-164) — works but mixes parameter binding ($1, $2) with SQL-fragment interpolation. **Fix sketch:** use `NULLIF($3::timestamptz, 'epoch')` or pass `datetime.now(UTC) if i==2 else None` as $3. Cosmetic — UUIDs are hex-only so no injection risk, but the mixed-binding style is a soft-cite for future review.

### Adversarial checks cleared
- BEGIN/COMMIT regex: verified 0 residual top-level BEGIN/COMMIT in all 4 stripped migrations
- mig 175→218→223→305 chain: 218/223/305 only `CREATE OR REPLACE FUNCTION` (no trigger DROP/CREATE) — last-write-wins on function, trigger installed once by 175 stays bound to latest body
- `enable_emergency_access` is the FIRST entry in mig 305's v_privileged_types (line 32) — trigger fires
- #128 invariant's `details->>'fleet_order_ids'` read path — that's a write-time denormalization scanned by the SQL invariant itself, not a runtime read needing behavioral test coverage; AST gate is sufficient
- mig 218 watchdog_* types: D/F's `enable_emergency_access` exercises the same EXISTS code path; per-type coverage is the P1-A above

**Verdict: APPROVE-WITH-FIXES.** Merge as-is; file P1-A as followup task; P2-A optional polish on next touch.
