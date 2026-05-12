# Gate A — `ots_proofs.status` CHECK constraint (task #129)

**Date:** 2026-05-12
**Reviewers (fork lenses):** Steve (correctness) / Maya (customer impact) / Carol (compliance) / Coach (consistency)
**Design doc:** `audit/ots-proofs-status-check-constraint-design-2026-05-12.md`
**Proposed migration:** `mcp-server/central-command/backend/migrations/307_ots_proofs_status_check.sql`

---

## VERDICT: **APPROVE-WITH-FIXES**

Three P0s, one P1, two P2s. P0s must be closed before apply.
Design is fundamentally sound — the enum surface analysis is correct, the prod-row count is correct, the `'verified'` writer is verifiably gone — but the migration body and the writer enumeration both have material gaps that will either (a) make the migration fail at apply time, (b) silently lose the constraint at deploy time, or (c) introduce a forward-incompatibility when a future `'expired'` writer lands.

---

## P0-1 (Steve) — Migration body is NOT wrapped in `BEGIN; … COMMIT;`

**Evidence:** Design doc lines 55-72. Compare to siblings:

- `migrations/305_delegate_signing_key_privileged.sql` — wraps in `BEGIN; ... COMMIT;`
- `migrations/282_feature_flags_dual_admin.sql` — same
- `migrations/182_widen_appliance_status_check.sql` (the closest analog) — same
- `migrations/300_backfill_orphan_l2_decisions.sql` through `304_quarantine_synthetic_mttr_soak.sql` — ALL wrap in `BEGIN; ... COMMIT;`

**Why it matters:** `migrate.py:153` is `await conn.execute(up_sql)` with NO outer transaction. asyncpg's `execute()` runs multi-statement strings as a sequence of implicit-transaction statements (one per `;`). The design's DO-block is one implicit txn; the ALTER is a second; the COMMENT is a third. If the ALTER fails (e.g. unforeseen out-of-set row sneaks in between the DO check and the ALTER), the COMMENT — and any future statement we add — will still run on a half-applied migration. Sibling migrations all avoid this class with explicit `BEGIN; … COMMIT;`.

**Fix:** Prepend `BEGIN;` (line 1 after the header comment), append `COMMIT;` after the `COMMENT ON CONSTRAINT` block.

---

## P0-2 (Steve) — Writer enumeration in the design doc is incomplete

The design doc enumerates writers only from `evidence_chain.py` (lines 22-26). It MISSES the writers in `mcp-server/main.py`. Grep:

```
mcp-server/main.py:529 — UPDATE ots_proofs SET bitcoin_block = :height (no status touch, OK)
mcp-server/main.py:628 — UPDATE ots_proofs SET status = 'pending' (re-submit expired path)
mcp-server/main.py:662 — UPDATE ots_proofs SET error = ..., last_upgrade_attempt = NOW() (no status)
```

The 628 path is the `_ots_resubmit_expired_loop` background task. It writes `'pending'` only — already in the allowed set — so the migration is FUNCTIONALLY correct. BUT the design doc's claim "Writers cover: `{'pending', 'anchored', 'failed'}`" omits this code path entirely. If a future Steve audits the design doc as gospel, they may delete the file without realising it's a writer. This is a documentation-correctness P0 because the design doc is the artifact that locks in our understanding of the writer surface.

Also missed in the doc: `evidence_chain.py:776` (`mark_proof_anchored`, called from two upgrade sites at 894 and 921) sets `status = 'anchored'` inline via the `update_fields` f-string at line 754 — design doc credits 766 which is the wrong line number for current source. Confirm: `grep -n "status = 'anchored'" mcp-server/central-command/backend/evidence_chain.py` → line **754** (in `mark_proof_anchored`), not 766. Design's line citations are stale.

**Fix:** Update §"Source-side reality" to list ALL writers across `main.py` + `evidence_chain.py`, with corrected line numbers. Include:

- `evidence_chain.py:754` — `'anchored'` (via `mark_proof_anchored` f-string, called from 2 upgrade sites)
- `evidence_chain.py:823` — `'failed'` (calendar-retention timeout in `upgrade_pending_proofs`)
- `evidence_chain.py:3069` (current) — `'pending'` (resubmit-expired loop)
- `evidence_chain.py:2158, 2268` — INSERT literals `'pending'`
- `main.py:628` — `'pending'` (`_ots_resubmit_expired_loop`)

---

## P0-3 (Carol) — BEFORE UPDATE trigger `sync_ots_proof_status` propagates status into `compliance_bundles.ots_status` + `evidence_bundles.ots_status` — neither has a CHECK constraint, so no constraint failure, BUT design doc doesn't acknowledge this propagation

**Evidence:** `migrations/011_ots_blockchain.sql:198-233` defines `update_ots_proof_status()` BEFORE UPDATE trigger that copies `NEW.status` into `compliance_bundles.ots_status` AND `evidence_bundles.ots_status` on every UPDATE.

I verified `grep -rnE "ots_status.*CHECK|CHECK.*ots_status"` returned zero results. So no CHECK on the sink columns. The migration is SAFE — the trigger fires before the row-level CHECK and writes the same value to two more tables, neither of which constrains it.

**But** the design doc never names the trigger. If a future engineer narrows `ots_proofs.status` enum further (e.g. removes `'expired'`), they also need to consider: `compliance_bundles.ots_status` is read at `evidence_chain.py:2871-2872`, `org_management.py:839/1109/1138`, `client_wall_cert.py:88`, `routes.py:7752`, `partner_portfolio_attestation.py:154` — all of these will get `NULL` or `'expired'` propagated if the source row has it. Today they only filter on `'anchored'`/`'pending'` so it's fine. Document this dependency so future enum changes don't blindside us.

**Fix:** Add §"Trigger dependency" to design doc citing `sync_ots_proof_status` (mig 011:229) and noting that all 4 in-set values flow through to `compliance_bundles.ots_status` and `evidence_bundles.ots_status`, neither of which has a CHECK constraint. NOT a migration-body change — design-doc-only.

---

## P1 (Carol) — Race window between DO-block and ALTER is theoretically possible if BEGIN is missing

If P0-1 is fixed (`BEGIN; … COMMIT;`), then the DO-block and the ALTER run in the same transaction; the DO-block holds a `SELECT` snapshot of `ots_proofs`, the ALTER acquires `ACCESS EXCLUSIVE` on the table at COMMIT and re-validates every row. Concurrent writes blocked. No race.

Without the BEGIN/COMMIT wrap (current design), the DO-block commits its read, then the ALTER acquires its lock — a writer COULD interleave and INSERT a forbidden value, which would make the ALTER fail. The error would be a CHECK violation with a clear message, but it'd be a deploy fail rather than a clean apply. Closed by P0-1.

The design's Gate A ask #2 asks "is there a race condition?" — the answer is **YES, today, because the migration isn't transactional. Closes when P0-1 is fixed.**

---

## P2-1 (Steve) — Postgres `ALTER TABLE ADD CONSTRAINT CHECK` re-validation perf

Default (no `NOT VALID`) re-validates every existing row. Confirmed by Postgres docs: "When a new constraint is added to an existing table, the constraint is checked against the existing data immediately." For 128,328 rows on an indexed `varchar(20)` column with no scan complexity, this is sub-second.

The design's intent to NOT use `NOT VALID` is correct: `NOT VALID` would defer validation and let pre-existing bad rows live forever. We want immediate validation. APPROVED as-designed.

Operationally: the ALTER acquires `ACCESS EXCLUSIVE` for the duration. On prod with 128K rows, that's probably <500ms; on a hot table with concurrent writers (the OTS upgrade loop runs every 15 min), this could block writers for the duration. Acceptable — pick a deploy window that doesn't overlap an upgrade tick, or accept ~500ms of write-blocking.

---

## P2-2 (Coach) — Style nit: sibling migrations include `-- DOWN` rollback section

Migration runner `migrate.py:42-65` supports `-- DOWN` markers. Migration 263 (`go_agent_status_state_machine.sql`) and 182 (the closest sibling) both include `-- DOWN` blocks. Design doc has a "Rollback plan" section in the audit doc but no `-- DOWN` in the migration file body. Add:

```sql
-- DOWN
-- ALTER TABLE ots_proofs DROP CONSTRAINT IF EXISTS ots_proofs_status_check;
```

so `python migrate.py down 307` works.

---

## Lens findings — answers to design doc's Gate A asks

### Steve (asks 1-4)

1. **Is `'expired'` written anywhere I missed?** NO. Verified via:
   - `grep -rnE "ots_proofs.*expired|status.*=.*'expired'"` — every hit is a READ in evidence_chain.py / prometheus_metrics.py / main.py. The other 'expired' hits are on unrelated tables (`fleet_orders`, `appliance_provisions`, `admin_orders`, `orders`, `client_org_owner_transfer_requests`, `partner_admin_transfer_requests`, `compliance_packets`, `appliance_provisions`).
   - `grep -rnE "SET\s+status\s*=\s*'expired'"` in OTS context → ZERO hits.
   - No stored procedure / cron / trigger in `mcp-server/central-command/backend/migrations/*.sql` mutates `ots_proofs.status`. The only TRIGGER on `ots_proofs` is `sync_ots_proof_status` (mig 011:230) which is a one-way OUTBOUND sync trigger; it READS `NEW.status` and writes to other tables. It does NOT mutate `ots_proofs` itself.

   Conclusion: `'expired'` is a documented-but-unimplemented future-writer state. Keeping it in the allowed set is correct.

2. **Triggers on `ots_proofs`?** ONE — `sync_ots_proof_status` (mig 011:230). BEFORE UPDATE. Propagates `NEW.status` to `compliance_bundles.ots_status` + `evidence_bundles.ots_status`. Neither sink has a CHECK constraint. Safe.

3. **ALTER TABLE validation perf?** Sub-second on 128K rows. No `NOT VALID` needed; design correctly omits it.

4. **Race window between DO-block and ALTER?** YES, today (P1). Closes when P0-1 wraps in `BEGIN; ... COMMIT;`.

### Carol (asks 5-6)

5. **Compliance impact?** The customer-facing dashboard rollup at `evidence_chain.py:3722` uses `COUNT(*) FILTER (WHERE status IN ('anchored', 'verified'))` — buckets `'verified'` into the same surface as `'anchored'`. After this migration, the `'verified'` bucket is constant 0 → only `'anchored'` contributes. Functionally identical to today (prod has 0 `'verified'` rows already). NO customer-visible change in dashboard numbers.

   Compliance attestation contract: `ots_proofs` is part of the Ed25519+OTS evidence chain. The HIPAA controls 164.312(b) Audit Controls + 164.312(c)(1) Integrity Controls are unaffected — the constraint locks DOWN the writer surface to the already-active set. It does NOT alter the chain semantics, the proof data, or the verification logic.

   **No customer-facing claim changes.** No /api/dashboard, /api/evidence/* response shape changes (the `verified` field continues to be returned with value 0). Auditor kit unaffected.

6. **Rollback safety?** Confirmed: `ALTER TABLE ots_proofs DROP CONSTRAINT IF EXISTS ots_proofs_status_check` affects ZERO rows. Postgres constraints are metadata-only; dropping one doesn't rewrite or scan any rows. Rollback is instant + non-destructive.

### Maya (ask 7)

7. **Customer-visible "constant 0" metrics — remove from dashboard?** NO, leave them. The `verified` field is a documented part of the OTS proof-state enum surface from migration 011 (2024). Removing it from /api/dashboard would be a breaking API change for any integration that reads it. Constant-0 is a self-documenting signal that the state isn't being used. File a P3 followup (a separate task) to remove from the dashboard frontend when we ship the OTS proof-state UI refresh; do NOT couple it to this migration.

   The Prometheus gauge `osiriscare_ots_proofs{status="verified"}` will be constant 0. That's fine — Prometheus dropping zero-valued series is configurable; for operator clarity, the 0 is actually USEFUL (proves we never write that state).

### Coach (asks 8-10)

8. **Migration number 307 next-free?** YES. Verified:
   ```
   $ ls mcp-server/central-command/backend/migrations/ | sort -V | tail -10
   293, 294, 295, 296, 297, 298, 299, 300, 301, 302, 303, 304, 305
   ```
   305 is last applied. 306 is reserved for #117 PR-3c per the brief. 307 is correct.

9. **Sibling-pattern match.** Closest siblings: `182_widen_appliance_status_check.sql` and `282_feature_flags_dual_admin.sql`. Both use:
   - `BEGIN; … COMMIT;` wrap (P0-1 above)
   - Header comment block with date + task ID + rationale (current design HAS this ✓)
   - `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` pattern when widening — current design ONLY adds (no prior constraint exists, verified at mig 011:107-145), so add-only is correct
   - `COMMENT ON CONSTRAINT` — current design HAS this ✓
   - `-- DOWN` rollback (P2-2 above)

10. **Test fixtures?** Searched `tests/` for `ots_proofs` INSERT/UPDATE. Found references in 4 test files but NONE of them INSERT rows with arbitrary status values:
    - `test_dashboard_sla_strip.py:66` — body-string assertion only
    - `test_evidence_auth_audit_fixes.py:162` — source-string assertion (checks the SQL shape)
    - `test_rename_site_function.py:154` — table-list assertion
    - `test_h2_h3_inapp_downloads.py:83` — body-string assertion only

    NO fixture INSERTs `status='verified'` or `status='other'`. No test changes required in this commit.

---

## Required fixes before APPROVE

1. **P0-1 (BLOCKING):** Wrap migration body in `BEGIN; … COMMIT;`. Match sibling style (mig 182, 282, 305).
2. **P0-2 (BLOCKING):** Update design doc §"Source-side reality" writer enumeration with corrected line numbers and inclusion of `main.py:628` writer. (Documentation-only — no migration-body change.)
3. **P0-3 (BLOCKING):** Add §"Trigger dependency" to design doc citing `sync_ots_proof_status` propagation to `compliance_bundles.ots_status` and `evidence_bundles.ots_status`. (Documentation-only.)
4. **P2-2 (RECOMMENDED):** Add `-- DOWN` rollback block to migration file so `migrate.py down 307` works without manual SQL.

After these are closed, re-run Gate A (this fork) on the revised artifacts. P0s closed = APPROVE.

---

## Gate B prerequisites (for after implementation)

- Run full pre-push sweep (`.githooks/full-test-sweep.sh`) and cite pass/fail count — per Session 220 lock-in.
- Apply migration on local dev DB; `psql -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'ots_proofs'::regclass;"` confirms constraint visible.
- Negative test: `INSERT INTO ots_proofs (..., status) VALUES (..., 'verified');` should fail with check_violation. Then ROLLBACK.
- Confirm `python migrate.py down 307` runs cleanly (requires P2-2 fix).
- Runtime evidence on prod: post-deploy, `psql -c "SELECT conname FROM pg_constraint WHERE conrelid='ots_proofs'::regclass AND conname='ots_proofs_status_check';"` returns 1 row.

---

## Coach's closing note

The design is the right idea — lock the enum surface after deleting the only writer of one of its values. The substance is correct: 4 values allowed, `'verified'` excluded, `'expired'` allowed as read-only-future. The DO-block sanity check is a nice defensive touch.

The gaps are mechanical: missing `BEGIN/COMMIT`, stale line citations, untracked trigger dependency, missing `-- DOWN`. None of them is a redesign — all four are 5-minute fixes. APPROVE-WITH-FIXES, not BLOCK.

Re-run Gate A after fixes. Then proceed to apply, then Gate B (full sweep + runtime evidence).
