-- 307_ots_proofs_status_check.sql
--
-- Session 220 task #129 (2026-05-12). Defense-in-depth schema lockout
-- after task #120 PR-A (commit 972622a0) deleted `verify_ots_bitcoin`
-- — the only writer of `ots_proofs.status = 'verified'`. With the
-- writer gone + zero `'verified'` rows in prod (verified 2026-05-12:
-- 128,328 'anchored' + 3 'pending', no other values), this migration
-- locks the column's accepted-set to the 4 live statuses.
--
-- Live writers (verified by grep on `mcp-server/`, `appliance/`,
-- `agent/`, post-PR-A tree):
--   - INSERT … DEFAULT 'pending'             evidence_chain.py:2158, :2268
--   - UPDATE … SET status = 'pending'        evidence_chain.py:3070
--                                             main.py:628 (_ots_resubmit_expired_loop)
--   - UPDATE … SET status = 'anchored'       evidence_chain.py:754 (via update_fields)
--   - UPDATE … SET status = 'failed'         evidence_chain.py:830
--
-- Live readers reference {pending, anchored, verified, expired, failed}
-- — the `'verified'` + `'expired'` reads will return constant 0 after
-- this migration, which is the correct semantic value (no rows of
-- those states exist or can be written).
--
-- Trigger interaction (mig 011:230 `sync_ots_proof_status`): the
-- BEFORE UPDATE trigger propagates NEW.status to compliance_bundles.
-- ots_status + evidence_bundles.ots_status. Neither sink has a CHECK
-- constraint; the propagation is safe — after this migration the
-- trigger only ever sees values from the 4-item set.
--
-- Customer impact: zero. The dashboard rollup at evidence_chain.py:3722
-- buckets `('anchored', 'verified')` together; the 'verified' bucket
-- has been constant 0 since PR-A landed.

BEGIN;

-- Belt-and-suspenders: verify no surprise rows exist before constraining.
-- The DO block raises if ANY out-of-set row sneaks in between research
-- time (2026-05-12) and apply time. ALTER TABLE ADD CONSTRAINT below
-- ALSO validates every existing row at default settings — but the DO
-- block gives a clearer error message identifying the bad state.
DO $$
DECLARE
    bad_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO bad_count
    FROM ots_proofs
    WHERE status NOT IN ('pending', 'anchored', 'failed', 'expired');
    IF bad_count > 0 THEN
        RAISE EXCEPTION
            'ots_proofs has % rows with out-of-set status — migration '
            'aborted. Diagnose via: SELECT status, COUNT(*) FROM '
            'ots_proofs GROUP BY status;',
            bad_count;
    END IF;
END $$;

ALTER TABLE ots_proofs
    ADD CONSTRAINT ots_proofs_status_check
    CHECK (status IN ('pending', 'anchored', 'failed', 'expired'));

COMMENT ON CONSTRAINT ots_proofs_status_check ON ots_proofs IS
    'Session 220 task #129 (2026-05-12). Defense-in-depth lockout of '
    '`verified` after task #120 PR-A deleted the only writer '
    '(verify_ots_bitcoin commit 972622a0). 4 live values: pending '
    '(default + reset + ots_resubmit loop), anchored (anchor-success '
    'path), failed (upgrade-failure path), expired (read-only — no '
    'current writer but live readers expect the bucket).';

COMMIT;

-- DOWN (for emergency rollback only):
-- BEGIN;
-- ALTER TABLE ots_proofs DROP CONSTRAINT IF EXISTS ots_proofs_status_check;
-- COMMIT;
-- No data is at risk; the constraint only prevents NEW writes.
