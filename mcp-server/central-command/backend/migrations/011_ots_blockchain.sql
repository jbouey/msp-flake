-- OpenTimestamps Blockchain Anchoring Migration
--
-- Adds OTS (OpenTimestamps) blockchain anchoring support for evidence bundles.
-- This enables Enterprise tier feature: Provable evidence timestamps via Bitcoin.
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 011_ots_blockchain.sql
--
-- HIPAA Controls:
-- - 164.312(b) - Audit Controls (tamper-evident audit trail)
-- - 164.312(c)(1) - Integrity Controls (provable evidence authenticity)

-- ============================================================================
-- CREATE/UPDATE COMPLIANCE_BUNDLES TABLE (main evidence storage)
-- ============================================================================

-- Create compliance_bundles table if not exists (for hash-chain evidence)
CREATE TABLE IF NOT EXISTS compliance_bundles (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    bundle_id VARCHAR(50) UNIQUE NOT NULL,
    bundle_hash VARCHAR(64) NOT NULL,
    check_type VARCHAR(50),
    check_result VARCHAR(20),
    checked_at TIMESTAMP NOT NULL,

    -- Evidence data
    checks JSONB,
    summary JSONB,

    -- Signing
    agent_signature TEXT,
    ntp_verification JSONB,

    -- Hash chain
    prev_bundle_id VARCHAR(50),
    prev_hash VARCHAR(64),
    chain_position INTEGER DEFAULT 1,
    chain_hash VARCHAR(64),

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site ON compliance_bundles(site_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_chain ON compliance_bundles(site_id, chain_position);

-- Add OTS columns to compliance_bundles
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_proof TEXT;
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_status VARCHAR(20) DEFAULT 'none';
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_calendar_url VARCHAR(256);
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_submitted_at TIMESTAMP;
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_bitcoin_txid VARCHAR(64);
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_bitcoin_block INTEGER;
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_anchored_at TIMESTAMP;
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS ots_error TEXT;

-- Index for pending OTS proofs
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_ots_pending
ON compliance_bundles(ots_status)
WHERE ots_status = 'pending';

-- ============================================================================
-- ADD OTS COLUMNS TO EVIDENCE_BUNDLES TABLE (if using both tables)
-- ============================================================================

-- OTS proof data (base64-encoded binary proof)
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_proof TEXT;

-- OTS status: pending, anchored, verified, failed
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_status VARCHAR(20) DEFAULT 'none';

-- Calendar server that issued the proof
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_calendar_url VARCHAR(256);

-- When OTS proof was submitted
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_submitted_at TIMESTAMP;

-- Bitcoin transaction ID (when anchored)
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_bitcoin_txid VARCHAR(64);

-- Bitcoin block height (when anchored)
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_bitcoin_block INTEGER;

-- When anchored to Bitcoin (proof upgraded)
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_anchored_at TIMESTAMP;

-- Error message if OTS failed
ALTER TABLE evidence_bundles
ADD COLUMN IF NOT EXISTS ots_error TEXT;

-- Index for finding pending OTS proofs that need upgrading
CREATE INDEX IF NOT EXISTS idx_evidence_bundles_ots_pending
ON evidence_bundles(ots_status)
WHERE ots_status = 'pending';

-- ============================================================================
-- OTS PROOFS TABLE (for detailed tracking and batch operations)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ots_proofs (
    id SERIAL PRIMARY KEY,

    -- Bundle reference
    bundle_id VARCHAR(50) NOT NULL,
    bundle_hash VARCHAR(64) NOT NULL,
    site_id VARCHAR(50),

    -- Proof data (base64-encoded OTS proof)
    proof_data TEXT NOT NULL,

    -- Calendar info
    calendar_url VARCHAR(256) NOT NULL,
    submitted_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Status: pending, anchored, verified, failed
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- Bitcoin anchor info (NULL until anchored)
    bitcoin_txid VARCHAR(64),
    bitcoin_block INTEGER,
    bitcoin_merkle_root VARCHAR(64),
    anchored_at TIMESTAMP,

    -- Verification
    last_upgrade_attempt TIMESTAMP,
    upgrade_attempts INTEGER DEFAULT 0,
    verified_at TIMESTAMP,

    -- Error tracking
    error TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Constraints
    CONSTRAINT unique_bundle_proof UNIQUE (bundle_id),
    CONSTRAINT fk_ots_site FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE SET NULL
);

-- Index for finding pending proofs
CREATE INDEX IF NOT EXISTS idx_ots_proofs_pending
ON ots_proofs(status)
WHERE status = 'pending';

-- Index for looking up by bundle_id
CREATE INDEX IF NOT EXISTS idx_ots_proofs_bundle_id
ON ots_proofs(bundle_id);

-- Index for site-based queries
CREATE INDEX IF NOT EXISTS idx_ots_proofs_site
ON ots_proofs(site_id, status);

-- ============================================================================
-- OTS BATCH JOBS TABLE (for tracking background upgrade jobs)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ots_batch_jobs (
    id SERIAL PRIMARY KEY,

    -- Job info
    job_id VARCHAR(50) UNIQUE NOT NULL,
    job_type VARCHAR(20) NOT NULL,  -- upgrade, verify
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed

    -- Scope
    site_id VARCHAR(50),  -- NULL = all sites

    -- Progress
    total_proofs INTEGER DEFAULT 0,
    processed_proofs INTEGER DEFAULT 0,
    upgraded_proofs INTEGER DEFAULT 0,
    failed_proofs INTEGER DEFAULT 0,

    -- Timing
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    -- Error tracking
    error TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to update proof status when upgraded
CREATE OR REPLACE FUNCTION update_ots_proof_status()
RETURNS TRIGGER AS $$
BEGIN
    -- Sync status to compliance_bundles when proof is updated
    UPDATE compliance_bundles
    SET
        ots_status = NEW.status,
        ots_bitcoin_txid = NEW.bitcoin_txid,
        ots_bitcoin_block = NEW.bitcoin_block,
        ots_anchored_at = NEW.anchored_at,
        ots_error = NEW.error
    WHERE bundle_id = NEW.bundle_id;

    -- Also sync to evidence_bundles if it exists
    UPDATE evidence_bundles
    SET
        ots_status = NEW.status,
        ots_bitcoin_txid = NEW.bitcoin_txid,
        ots_bitcoin_block = NEW.bitcoin_block,
        ots_anchored_at = NEW.anchored_at,
        ots_error = NEW.error
    WHERE bundle_id = NEW.bundle_id;

    -- Update timestamp
    NEW.updated_at = NOW();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to sync proof status to evidence_bundles
DROP TRIGGER IF EXISTS sync_ots_proof_status ON ots_proofs;
CREATE TRIGGER sync_ots_proof_status
    BEFORE UPDATE ON ots_proofs
    FOR EACH ROW
    EXECUTE FUNCTION update_ots_proof_status();

-- ============================================================================
-- VIEWS FOR MONITORING
-- ============================================================================

-- View: OTS status summary by site
CREATE OR REPLACE VIEW ots_status_summary AS
SELECT
    site_id,
    COUNT(*) as total_proofs,
    COUNT(*) FILTER (WHERE status = 'pending') as pending,
    COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
    COUNT(*) FILTER (WHERE status = 'verified') as verified,
    COUNT(*) FILTER (WHERE status = 'failed') as failed,
    MAX(anchored_at) as last_anchored_at,
    MIN(submitted_at) FILTER (WHERE status = 'pending') as oldest_pending
FROM ots_proofs
GROUP BY site_id;

-- View: Proofs needing upgrade (pending for more than 1 hour)
CREATE OR REPLACE VIEW ots_proofs_needing_upgrade AS
SELECT *
FROM ots_proofs
WHERE status = 'pending'
AND submitted_at < NOW() - INTERVAL '1 hour'
AND (last_upgrade_attempt IS NULL OR last_upgrade_attempt < NOW() - INTERVAL '1 hour')
ORDER BY submitted_at ASC;

-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================

GRANT ALL ON ots_proofs TO mcp;
GRANT ALL ON ots_batch_jobs TO mcp;
GRANT ALL ON SEQUENCE ots_proofs_id_seq TO mcp;
GRANT ALL ON SEQUENCE ots_batch_jobs_id_seq TO mcp;
GRANT SELECT ON ots_status_summary TO mcp;
GRANT SELECT ON ots_proofs_needing_upgrade TO mcp;

-- ============================================================================
-- SUMMARY
-- ============================================================================

-- Show created objects
SELECT 'ots_proofs' as table_name, COUNT(*) as row_count FROM ots_proofs
UNION ALL
SELECT 'ots_batch_jobs', COUNT(*) FROM ots_batch_jobs;

SELECT 'Migration 011_ots_blockchain completed successfully' as status;
