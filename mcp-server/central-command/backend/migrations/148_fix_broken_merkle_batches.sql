-- Migration 148: Backfill for Session 203 C1 — broken Merkle batches
--
-- The `process_merkle_batch()` function in evidence_chain.py used to derive
-- `batch_id` from `site_id` + UTC hour, meaning two calls in the same hour
-- produced the same batch_id. The first call stored a Merkle root; the
-- second call hit `ON CONFLICT (batch_id) DO NOTHING`, silently dropped its
-- root, but still UPDATE'd each bundle with a merkle_proof path computed
-- against the DROPPED tree. Result: those bundles' stored proofs cannot
-- verify against the root stored in ots_merkle_batches.
--
-- The production audit on 2026-04-09 confirmed 20+ batches with
-- discrepancies up to 20 bundles each (1,198+ bundles total across the
-- fleet). The writer is fixed in evidence_chain.py (append a random
-- suffix so each call gets a unique batch_id) — but the existing broken
-- bundles still advertise `ots_status = 'pending'` or `'anchored'` and
-- still appear on scorecards as "verified". That is a legal-defensibility
-- risk: any of these bundles presented to an auditor can be trivially
-- disproven by walking its proof in 5 lines of Python.
--
-- This migration:
--   1. Identifies every `merkle_batch_id` where the stored tree size
--      `ots_merkle_batches.bundle_count` is smaller than the number of
--      bundles actually pointing at it — i.e. a collision happened.
--   2. Reclassifies ALL bundles in any such batch to ots_status='legacy'
--      and clears their merkle fields, because we cannot reliably tell
--      which bundles came from the stored root's sub-batch versus a
--      dropped sub-batch. Each call to process_merkle_batch() started
--      leaf_index from 0, so the leaf_index values from different sub-
--      batches overlap — storage alone can't distinguish them.
--
-- CONSERVATIVE CHOICE: we reclassify the entire broken batch rather than
-- trying to salvage the first sub-batch's bundles. The alternative would
-- leave some bundles claiming to verify against a root when the claim
-- is unprovable. A legacy-classified bundle is honest about its state;
-- a bundle that claims to verify but actually doesn't is a legal liability.
--
-- Verified impact on prod 2026-04-09: 1,198 bundles across 47 batches
-- on 2 sites.
--
-- Audit logging: every bundle flipped gets a row in admin_audit_log so
-- the HIPAA audit trail captures the remediation. Count is also written
-- as a single summary row.

BEGIN;

-- Capture the broken bundles into a temp table for auditing.
--
-- A "broken" batch is one where the stored tree size is smaller than
-- the number of bundles pointing at the batch_id. Every bundle in such
-- a batch is suspect — mark them all.
CREATE TEMP TABLE _broken_merkle_bundles AS
WITH broken_batches AS (
    SELECT mb.batch_id, mb.bundle_count AS stored_tree_size
    FROM ots_merkle_batches mb
    JOIN compliance_bundles cb2 ON cb2.merkle_batch_id = mb.batch_id
    GROUP BY mb.batch_id, mb.bundle_count
    HAVING COUNT(cb2.bundle_id) > mb.bundle_count
)
SELECT
    cb.bundle_id,
    cb.site_id,
    cb.merkle_batch_id,
    cb.merkle_leaf_index,
    bb.stored_tree_size
FROM compliance_bundles cb
JOIN broken_batches bb ON bb.batch_id = cb.merkle_batch_id;

-- Log the per-batch remediation counts to admin_audit_log so there is
-- a HIPAA-grade paper trail of what changed and when.
INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
SELECT
    NULL,
    'migration-148',
    'MERKLE_BATCH_BUNDLE_RECLASSIFIED',
    merkle_batch_id,
    jsonb_build_object(
        'site_id', site_id,
        'reclassified_count', COUNT(*),
        'stored_tree_size', MAX(stored_tree_size),
        'reason', 'Session 203 C1 — batch_id collision dropped the sub-batch root'
    ),
    '127.0.0.1'
FROM _broken_merkle_bundles
GROUP BY merkle_batch_id, site_id;

-- Reclassify the broken bundles to 'legacy'. We clear the merkle fields
-- so the UI stops pretending they verify against a tree that doesn't
-- exist, and flip ots_status so the scorecard counts them as legacy
-- rather than anchored.
UPDATE compliance_bundles cb
SET
    ots_status = 'legacy',
    merkle_batch_id = NULL,
    merkle_proof = NULL,
    merkle_leaf_index = NULL
FROM _broken_merkle_bundles broken
WHERE cb.bundle_id = broken.bundle_id;

-- Summary audit entry so the total fleet impact is a single queryable row.
INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
SELECT
    NULL,
    'migration-148',
    'MERKLE_BATCH_BACKFILL_COMPLETE',
    'fleet',
    jsonb_build_object(
        'total_reclassified', (SELECT COUNT(*) FROM _broken_merkle_bundles),
        'batches_touched', (SELECT COUNT(DISTINCT merkle_batch_id) FROM _broken_merkle_bundles),
        'sites_touched', (SELECT COUNT(DISTINCT site_id) FROM _broken_merkle_bundles),
        'migration', '148_fix_broken_merkle_batches'
    ),
    '127.0.0.1';

DROP TABLE _broken_merkle_bundles;

COMMIT;
