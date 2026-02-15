-- Migration 043: Fix evidence chain race condition
--
-- Problem: Concurrent evidence submissions could read the same MAX(chain_position),
-- causing duplicate positions and broken hash chain links (~1,125 affected).
--
-- Fix: Re-sequence chain_positions per site ordered by checked_at,
-- then recompute prev_hash and chain_hash for consistency.
-- The application code now uses pg_advisory_xact_lock to prevent future races.

-- Step 1: Re-sequence chain_positions per site using checked_at order
-- This fixes duplicate chain_position values caused by the race condition
WITH resequenced AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY site_id ORDER BY checked_at, id) AS new_position
    FROM compliance_bundles
)
UPDATE compliance_bundles cb
SET chain_position = r.new_position
FROM resequenced r
WHERE cb.id = r.id AND cb.chain_position != r.new_position;

-- Step 2: Fix prev_bundle_id and prev_hash to point to the actual previous bundle
-- (the one with chain_position - 1 for the same site)
-- Genesis bundles (chain_position=1) use zero sentinel since prev_hash is NOT NULL
WITH chain_links AS (
    SELECT
        cb.id,
        cb.site_id,
        cb.chain_position,
        prev.bundle_id AS correct_prev_bundle_id,
        COALESCE(prev.bundle_hash, '0000000000000000000000000000000000000000000000000000000000000000') AS correct_prev_hash
    FROM compliance_bundles cb
    LEFT JOIN compliance_bundles prev
        ON prev.site_id = cb.site_id
        AND prev.chain_position = cb.chain_position - 1
)
UPDATE compliance_bundles cb
SET prev_bundle_id = cl.correct_prev_bundle_id,
    prev_hash = cl.correct_prev_hash
FROM chain_links cl
WHERE cb.id = cl.id
AND (cb.prev_hash IS DISTINCT FROM cl.correct_prev_hash
     OR cb.prev_bundle_id IS DISTINCT FROM cl.correct_prev_bundle_id);

-- Step 3: Recompute chain_hash for all bundles
-- chain_hash = SHA256(bundle_hash:prev_hash:chain_position)
UPDATE compliance_bundles
SET chain_hash = encode(
    digest(
        bundle_hash || ':' || prev_hash || ':' || chain_position::text,
        'sha256'
    ),
    'hex'
);

-- Step 4: Add a unique constraint to prevent duplicate chain positions per site
-- (belt-and-suspenders with the advisory lock)
CREATE UNIQUE INDEX IF NOT EXISTS uq_compliance_bundles_site_chain_position
ON compliance_bundles (site_id, chain_position);
