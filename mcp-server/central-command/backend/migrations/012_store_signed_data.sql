-- Store signed_data for correct signature verification
--
-- Fixes critical vulnerability where verify endpoint reconstructed signed data
-- using different fields than what was actually signed at submission time,
-- causing signature verification to always fail.
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 012_store_signed_data.sql

-- Store the exact signed data for later verification
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS signed_data TEXT;

-- Record whether signature was valid at submission time
ALTER TABLE compliance_bundles ADD COLUMN IF NOT EXISTS signature_valid BOOLEAN;

SELECT 'Migration 012_store_signed_data completed successfully' as status;
