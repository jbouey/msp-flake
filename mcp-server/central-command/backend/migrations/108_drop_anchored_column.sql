-- Drop dead 'anchored' column from compliance_bundles
--
-- This column was never populated; OTS anchoring status is tracked via
-- ots_status (on compliance_bundles) and the ots_proofs table.
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 108_drop_anchored_column.sql

ALTER TABLE compliance_bundles DROP COLUMN IF EXISTS anchored;

SELECT 'Migration 108_drop_anchored_column completed successfully' AS status;
