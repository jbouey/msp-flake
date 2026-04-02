-- Migration 116: Peer witness attestations for evidence bundles
-- Each row is one appliance's counter-signature of another appliance's bundle hash.
-- Used for legal defensibility: evidence is witnessed by multiple independent parties.
CREATE TABLE IF NOT EXISTS witness_attestations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bundle_id VARCHAR(100) NOT NULL,
    bundle_hash VARCHAR(128) NOT NULL,
    source_appliance VARCHAR(200) NOT NULL,  -- appliance that created the bundle
    witness_appliance VARCHAR(200) NOT NULL, -- appliance that counter-signed
    witness_public_key VARCHAR(128) NOT NULL,
    witness_signature VARCHAR(256) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (bundle_id, witness_appliance)    -- one attestation per witness per bundle
);

CREATE INDEX IF NOT EXISTS idx_witness_attestations_bundle ON witness_attestations(bundle_id);
CREATE INDEX IF NOT EXISTS idx_witness_attestations_source ON witness_attestations(source_appliance);
