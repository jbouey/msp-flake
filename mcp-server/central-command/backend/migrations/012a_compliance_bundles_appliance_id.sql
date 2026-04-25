-- Migration 012a: Add appliance_id column to compliance_bundles.
--
-- Migration 011 creates compliance_bundles but doesn't include
-- appliance_id. Migration 013_multi_framework's CREATE VIEW body
-- references `cb.appliance_id`, aborting on fresh CI with
-- `column cb.appliance_id does not exist`. Migration 119 eventually
-- adds the column, but 013 needs it 100+ migrations earlier.
--
-- Prod has appliance_id already (added outside the ledger sometime
-- between 011 and 119, then 119's ADD COLUMN IF NOT EXISTS made the
-- migration a no-op). Fresh CI is the broken case.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS). Forward-only. No prod impact.
-- Sort order: 012_store_signed_data → 012a_*  → 013_multi_framework.

ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS appliance_id VARCHAR(255);
