# Task #129 design — add CHECK constraint to `ots_proofs.status`

**Status:** RESEARCH DELIVERABLE — Gate A required before implementation.
**Date:** 2026-05-12.

## Background

Task #120 PR-A (commit 972622a0) deleted `verify_ots_bitcoin` — the only writer of `ots_proofs.status='verified'`. Gate B flagged this as Carol-class housekeeping: the enum value is now write-orphan but `ots_proofs` has NO `CHECK` constraint preventing a future code path from re-introducing `'verified'` writes.

## Reality check (prod, 2026-05-12)

```sql
SELECT status, COUNT(*) FROM ots_proofs GROUP BY status;
-- anchored: 128328
-- pending:  3
```

Plus `SELECT DISTINCT status` → only `anchored` + `pending`. Zero `verified`, zero `failed`, zero `expired` rows in prod ever.

## Source-side reality

**Writers** (`grep -nE "SET status = 'X'" evidence_chain.py + main.py + central-command/backend/*.py`):
- `evidence_chain.py:766` — `SET status = 'anchored'` (anchor-success path)
- `evidence_chain.py:830` — `SET status = 'failed'` (upgrade-failure path)
- `evidence_chain.py:3070` — `SET status = 'pending'` (reset-to-pending path)
- `evidence_chain.py:2158, 2268` — `INSERT ... DEFAULT 'pending'`

Writers cover: `{'pending', 'anchored', 'failed'}`.

**Readers**:
- `evidence_chain.py:2780/2907`, `prometheus_metrics.py:997` — aggregations that COUNT `('pending', 'anchored', 'verified', 'expired', 'failed')`
- `evidence_chain.py:3722` — dashboard rollup buckets `('anchored', 'verified')` together
- `evidence_chain.py:985, 3036, 3045, 3123` — checks `WHERE status = 'expired'`

Readers reference: `{'pending', 'anchored', 'verified', 'expired', 'failed'}`.

The `'expired'` status is read but never written. Either it WAS written historically and the writer was removed, or it's planned-but-never-implemented. Either way: live code reads expect it, so the migration MUST keep `'expired'` allowed.

The `'verified'` status is now dead (no writers, 0 prod rows). The migration's role is to lock it out.

## Proposed migration

`mcp-server/central-command/backend/migrations/307_ots_proofs_status_check.sql`:

```sql
-- Session 220 task #129 (2026-05-12). Defense-in-depth: lock the
-- ots_proofs.status enum to the live writer-set + the read-expected
-- 'expired' value. Prevents any future code path from re-introducing
-- 'verified' writes (the deleted verify_ots_bitcoin handler at
-- commit 972622a0 was the only writer of that value; 0 prod rows).

-- Belt-and-suspenders: verify no surprise rows exist before constraining.
-- This DO block FAILS the migration if any out-of-set row sneaks in
-- between research-time (2026-05-12) and apply-time.
DO $$
DECLARE
    bad_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO bad_count
    FROM ots_proofs
    WHERE status NOT IN ('pending', 'anchored', 'failed', 'expired');
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'ots_proofs has % rows with out-of-set status — '
            'migration aborted. Investigate via '
            'SELECT status, COUNT(*) FROM ots_proofs GROUP BY status;',
            bad_count;
    END IF;
END $$;

ALTER TABLE ots_proofs
    ADD CONSTRAINT ots_proofs_status_check
    CHECK (status IN ('pending', 'anchored', 'failed', 'expired'));

COMMENT ON CONSTRAINT ots_proofs_status_check ON ots_proofs IS
    'Session 220 task #129 — defense-in-depth lockout of `verified` '
    'after task #120 PR-A deleted the only writer (verify_ots_bitcoin '
    'commit 972622a0). 4 live values: pending (default + reset), '
    'anchored (anchor success), failed (upgrade failure), expired '
    '(read-only — no current writer but live readers expect it).';
```

## What the migration does NOT do

- Does NOT touch existing `'verified'` reads (`evidence_chain.py:2780/2907`, `prometheus_metrics.py:997`, `evidence_chain.py:3722`). They keep returning constant 0, which is the correct value. Filed as cosmetic-cleanup followup if anyone cares.
- Does NOT add an `'expired'` writer. The existing readers cover it; if writers were lost, that's a separate bug.
- Does NOT touch `compliance_bundles.ots_status` (different column on a different table; not in scope).

## Verification plan (post-deploy)

1. `psql -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'ots_proofs'::regclass;"` — confirm constraint visible.
2. `psql -c "INSERT INTO ots_proofs (bundle_id, bundle_hash, site_id, proof_data, calendar_url, status) VALUES ('test-bundle-129-verify', 'abc', 'test-site', '', '', 'verified');"` — confirm INSERT fails with check-constraint violation.
3. `psql -c "ROLLBACK;"` — undo the failed-INSERT attempt (no rows committed).

## Gate A asks

1. **Steve:** Is `'expired'` actually live (some daemon job sets it via a path I missed)? grep `appliance/` + `agent/` for ots-status writes.
2. **Carol:** The migration runs the `DO` block ONCE at deploy time. Race condition: what if a writer is mid-flight when the DO check completes but before ALTER TABLE acquires its lock? Postgres `ALTER TABLE ADD CONSTRAINT` is a synchronous DDL lock; it can't interleave with concurrent inserts. Confirm.
3. **Carol:** Does Postgres `CHECK` constraint enforcement apply to existing rows on ALTER TABLE, or only to future writes? Default behavior is to validate existing rows — the DO block above is a sanity check before the ALTER; the ALTER itself will RE-VALIDATE all 128K rows. On 128K rows the validation should be sub-second; flag if it's not.
4. **Maya:** Customer impact = zero (status enum is operator-facing only via /api/dashboard rollup which buckets `(anchored, verified)` together; verified bucket is constant 0).
5. **Coach:** Migration filename `307_ots_proofs_status_check.sql` — confirm 307 is the next free number. The last applied migration in `migrations/` ordering should be 30X.

## Rollback plan

If the migration fails or causes prod problems:
```sql
ALTER TABLE ots_proofs DROP CONSTRAINT IF EXISTS ots_proofs_status_check;
```

No data is at risk — the constraint only prevents NEW writes. Rollback is a single DROP CONSTRAINT.
