-- Migration 109: Merkle proof batching for evidence bundles
--
-- Batches multiple evidence bundle hashes into a Merkle tree, anchoring
-- only the root via OTS.  Each bundle retains its Merkle path so
-- inclusion in the batch (and therefore the Bitcoin anchor) can be
-- verified independently.
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 109_merkle_batching.sql

-- ============================================================================
-- OTS MERKLE BATCHES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS ots_merkle_batches (
    id SERIAL PRIMARY KEY,
    batch_id VARCHAR(50) UNIQUE NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    merkle_root VARCHAR(64) NOT NULL,
    bundle_count INTEGER NOT NULL,
    tree_depth INTEGER NOT NULL,
    ots_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    ots_submitted_at TIMESTAMPTZ,
    bitcoin_block INTEGER,
    bitcoin_txid VARCHAR(64),
    anchored_at TIMESTAMPTZ,
    last_upgrade_attempt TIMESTAMPTZ,
    upgrade_attempts INTEGER DEFAULT 0,
    error TEXT,
    batch_hour TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_merkle_batches_site_hour
    ON ots_merkle_batches(site_id, batch_hour DESC);

CREATE INDEX IF NOT EXISTS idx_merkle_batches_pending
    ON ots_merkle_batches(ots_status)
    WHERE ots_status = 'pending';

-- ============================================================================
-- EXTEND COMPLIANCE_BUNDLES WITH MERKLE COLUMNS
-- ============================================================================

ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS merkle_batch_id VARCHAR(50);

ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS merkle_proof JSONB;

ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS merkle_leaf_index INTEGER;

CREATE INDEX IF NOT EXISTS idx_compliance_bundles_batch
    ON compliance_bundles(merkle_batch_id)
    WHERE merkle_batch_id IS NOT NULL;

-- ============================================================================
-- TRIGGER: propagate anchored status from batch to bundles
-- ============================================================================

CREATE OR REPLACE FUNCTION propagate_merkle_batch_status()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.ots_status = 'anchored' AND (OLD.ots_status IS NULL OR OLD.ots_status != 'anchored') THEN
        UPDATE compliance_bundles
        SET ots_status = 'anchored'
        WHERE merkle_batch_id = NEW.batch_id;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_merkle_batch_anchor ON ots_merkle_batches;
CREATE TRIGGER trg_merkle_batch_anchor
    BEFORE UPDATE ON ots_merkle_batches
    FOR EACH ROW
    EXECUTE FUNCTION propagate_merkle_batch_status();

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL ON ots_merkle_batches TO mcp;
GRANT ALL ON SEQUENCE ots_merkle_batches_id_seq TO mcp;
GRANT SELECT, INSERT, UPDATE ON ots_merkle_batches TO mcp_app;

SELECT 'Migration 109_merkle_batching completed successfully' AS status;
