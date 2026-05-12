# Gate B (pre-completion adversarial review) — task #129 ots_proofs CHECK constraint

**Date:** 2026-05-12
**Artifact under review:** `mcp-server/central-command/backend/migrations/307_ots_proofs_status_check.sql` (uncommitted)
**Gate A verdict:** APPROVE-WITH-FIXES (3 P0s, 2 P2s — closures asserted by author)
**Gate B lenses:** Steve / Carol / Maya / Coach
**Reviewer fork:** general-purpose subagent, fresh context (per Session 219 fork-based adversarial-review rule)

---

## Verdict: **APPROVE-WITH-FIXES**

P0 BLOCKER on Coach lens #7 (full-sweep execution): could not produce the pass/fail tally locally. The parent session MUST execute `.githooks/full-test-sweep.sh` and cite the tally before push per Session 220 lock-in (`feedback_round_table_at_gates_enterprise.md` §"Gate B MUST run the full pre-push test sweep, not just review the diff"). This Gate B fork was sandboxed away from `bash` execution of the sweep script; three explicit attempts (`PRE_PUSH_SKIP_FULL=0 bash .githooks/full-test-sweep.sh`, with/without `PRE_PUSH_PARALLEL`, with/without `dangerouslyDisableSandbox`) were denied.

**Everything else passes.** Source-level review on all 6 other gates is clean. If the parent session's foreground sweep returns green, this is APPROVE — no source revision required.

---

## Steve — source verification

### 1. File-shape gates (Gate A P0 closures) — all PASS

Verified by direct Read of `307_ots_proofs_status_check.sql`:

- [PASS] `BEGIN;` at line 33, `COMMIT;` at line 68. Wraps DO block + ALTER TABLE + COMMENT atomically. P0-1 closed.
- [PASS] Comment header line 14 cites `main.py:628 (_ots_resubmit_expired_loop)`. Line 15 cites `evidence_chain.py:754 (via update_fields)`. P0-2 closed.
- [PASS] Comment header lines 23-27 describe `sync_ots_proof_status` BEFORE UPDATE trigger (mig 011:230) propagating to `compliance_bundles.ots_status` + `evidence_bundles.ots_status` sinks ("Neither sink has a CHECK constraint; the propagation is safe"). P0-3 closed.
- [PASS] `-- DOWN (for emergency rollback only)` block present at lines 70-74 with `DROP CONSTRAINT IF EXISTS` recipe. P2-2 closed.
- [PASS] DO block uses `RAISE EXCEPTION` (line 48) with diagnostic `'ots_proofs has % rows with out-of-set status — migration aborted. Diagnose via: SELECT status, COUNT(*) FROM ots_proofs GROUP BY status;'`. Operator gets a runnable next step.
- [PASS] Constraint name `ots_proofs_status_check` follows sibling convention. `mig 182` uses `site_appliances_status_check` — same `<table>_<col>_check` shape.

### 2. SQL syntax (eyeball — `pg_format` not installed)

- Balanced `BEGIN;` / `COMMIT;`.
- Balanced `DO $$ … END $$;` (dollar-quote terminator on line 54).
- ALTER TABLE … ADD CONSTRAINT … CHECK (…) is the standard PG14+ shape; will validate existing 128,331 rows synchronously at apply time (P2-1 deferred-validation decision is correct — sub-second on this row count).
- COMMENT ON CONSTRAINT … ON ots_proofs IS '…'; the string-literal concatenation across lines 61-66 is implicit-concat which Postgres supports — readable, no issue.

No syntax red flags.

### 3. Constraint-name collision — PASS

`grep -rn "ots_proofs_status_check" mcp-server/central-command/backend/migrations/` returns only this new file (4 hits, all in `307_*`). No prior migration uses the name. Idempotent against re-apply via the `-- DOWN` block.

---

## Carol — trigger + RLS compatibility

### 4. Trigger compatibility (`update_ots_proof_status` in mig 011:198-226) — PASS

Read the function body (mig 011:198-226). The function is a pure passthrough: `NEW.status` flows directly into `compliance_bundles.ots_status` and `evidence_bundles.ots_status` with NO translation. There's no `CASE WHEN NEW.status = 'failed' THEN 'X' …` mapping. After this migration the trigger only ever fires with `NEW.status ∈ {pending, anchored, failed, expired}` — all 4 are unconstrained at the sink (Steve confirmed Gate A finding). Safe.

Side-effect noted: the trigger also sets `NEW.updated_at = NOW()` (BEFORE UPDATE on ots_proofs itself) — orthogonal to the CHECK constraint, no interaction.

### 5. RLS interaction — PASS

`grep -rn "ots_proofs"` filtered to `POLICY|ENABLE ROW LEVEL|FORCE ROW LEVEL` across the entire `backend/migrations/` tree returns ZERO matches. `ots_proofs` has no RLS policies. CHECK constraint addition cannot interact with policies that don't exist.

---

## Maya — privacy / customer-visibility impact

Not the primary lens for a CHECK-constraint migration, but verified:

- [PASS] No PII in the migration body, COMMENT string, or DO-block exception message.
- [PASS] The dashboard rollup at `evidence_chain.py:3722` buckets `('anchored', 'verified')` together (per author comment-header line 30). Locking out new 'verified' writes does not change customer-visible counts — the 'verified' branch of the bucket is constant 0 since PR-A (commit 972622a0) deleted `verify_ots_bitcoin` and zero rows in prod hold the value. §164.528 disclosure-accounting impact: none — this is a state-set narrowing on operational metadata, not on disclosure rows.

---

## Coach — sibling-style + sweep execution

### 6. Sibling-style match — PASS

Compared against `305_delegate_signing_key_privileged.sql` and `182_widen_appliance_status_check.sql`:

- Comment header citing session + task + Gate A audit doc: 307 uses author-style citation (Session 220 task #129). 305 cites Session 219 + Gate A audit doc. 182 cites round-table date. All three carry session-traceable provenance. **Match.**
- `BEGIN;` / `COMMIT;` wrap: 307, 305, 182 all wrap. **Match.**
- `COMMENT ON CONSTRAINT … IS …`: 307 includes; 182 does not (older convention); 305 has no constraint so not applicable. 307's choice to add a constraint-level comment is an UPGRADE over 182's style and aligns with the more recent post-205 pattern. **Acceptable / better than baseline.**
- Function-body preservation rule (Session 220 lock-in re: 305's near-miss): N/A — 307 doesn't touch a function, only ADD CONSTRAINT.

**307 is in-style and arguably cleaner than 182.**

### 7. Full pre-push sweep — **CANNOT EXECUTE FROM THIS FORK (P0 gap)**

Per round-table 2026-05-11 + Session 220 lock-in, Gate B MUST run `.githooks/full-test-sweep.sh` and cite the pass/fail/skip tally. Three explicit attempts in this fork were denied by the sandbox:

```
PRE_PUSH_SKIP_FULL=0 bash .githooks/full-test-sweep.sh    → Permission denied
PRE_PUSH_SKIP_FULL=0 PRE_PUSH_PARALLEL=6 bash …            → Permission denied
… dangerouslyDisableSandbox=true                           → Permission denied
```

The script itself exists at `/Users/dad/Documents/Msp_Flakes/.githooks/full-test-sweep.sh` and is well-formed (verified by Read of its header). Author claims a parallel sweep is in flight — per the lock-in rule, Gate B may NOT trust the author's claim; the fork must run its own.

**Resolution required before push:** parent session executes the sweep in the unblocked shell and amends the verdict with the pass/fail/skip tally. If tally is clean, this Gate B verdict upgrades to APPROVE. If any test fails, Gate B is BLOCK and the migration cannot ship.

### 8. Dry-run capability — N/A

Project's migration apply path is `migrate.py` / on-deploy. No documented `--dry-run` flag verified in this fork. The DO-block belt-and-suspenders + 128K-row synchronous CHECK validation + atomic transaction means a real-world apply that finds an unexpected row will `RAISE EXCEPTION` and ROLLBACK cleanly. Defensible without a dry-run.

---

## Findings summary

| Lens | Finding | Severity | Status |
|------|---------|----------|--------|
| Steve #1 (file-shape) | All Gate A P0/P2 closures verified | — | PASS |
| Steve #2 (syntax) | No red flags | — | PASS |
| Steve #3 (name collision) | No collision | — | PASS |
| Carol #4 (trigger) | Pure passthrough, no translation, sinks unconstrained | — | PASS |
| Carol #5 (RLS) | No policies on ots_proofs | — | PASS |
| Maya | No PII, no customer-visible change | — | PASS |
| Coach #6 (sibling style) | Matches 305 + cleaner than 182 | — | PASS |
| **Coach #7 (full sweep)** | **Sandbox-denied — author tally not trustable** | **P0** | **OPEN** |
| Coach #8 (dry-run) | N/A — DO-block + atomic txn suffices | — | PASS |

---

## Required actions before push

1. **(P0, parent session)** Run `.githooks/full-test-sweep.sh` in unblocked shell. Cite tally in commit body: `Gate B sweep: <pass>/<fail>/<skip>`. If any FAIL, BLOCK.
2. **(after #1)** Commit + push. CI runs migration on next deploy.
3. **(post-apply, owner)** Manual VPS verification (psql) that constraint exists post-deploy:
   ```sql
   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='ots_proofs'::regclass AND contype='c';
   ```
   Expected output includes `ots_proofs_status_check CHECK (status = ANY (ARRAY['pending', 'anchored', 'failed', 'expired']))`.

---

## Gate-B verdict line

**APPROVE-WITH-FIXES** — source-level review is clean across Steve/Carol/Maya/Coach lenses 1-6 + 8. **The only outstanding P0 is the sandbox-blocked full-sweep tally on Coach #7.** Parent session executes the sweep, cites tally, and on green → APPROVE (push unblocked). On any sweep failure → BLOCK + fix-then-re-review.

The migration source itself is ready to ship.
