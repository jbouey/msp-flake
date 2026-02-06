-- Migration 030: Fix WORM trigger to distinguish evidence content from chain metadata
--
-- Problem: The original trigger protected prev_hash as a "core field" alongside
-- checks, bundle_hash, and signature. But prev_hash is chain metadata, not evidence
-- content. This prevented chain repair/migration operations.
--
-- Evidence content (immutable): checks, bundle_hash, signature
--   - These fields represent the actual compliance evidence and its integrity proof
--   - HIPAA requires these to be immutable once written
--
-- Chain metadata (repairable): prev_hash, chain_position, chain_hash, prev_bundle_id
--   - These fields define the chain structure linking bundles together
--   - May need repair if legacy bundles were written without proper chain linking
--   - The bundle_hash still guarantees evidence content integrity regardless of chain structure

CREATE OR REPLACE FUNCTION prevent_compliance_bundle_update()
RETURNS TRIGGER AS $$
BEGIN
    -- Protect evidence content (HIPAA-required immutability)
    IF (OLD.checks IS DISTINCT FROM NEW.checks) OR
       (OLD.bundle_hash IS DISTINCT FROM NEW.bundle_hash) OR
       (OLD.signature IS DISTINCT FROM NEW.signature) THEN
        RAISE EXCEPTION 'Compliance bundles are append-only. Cannot modify evidence content fields (checks, bundle_hash, signature).';
    END IF;
    -- Chain metadata (prev_hash, chain_position, chain_hash, prev_bundle_id)
    -- is allowed to be updated for chain repair/migration operations.
    -- The bundle_hash still guarantees evidence content integrity.
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
