-- Migration 012b: Add outcome column to compliance_bundles for the
-- 013_multi_framework VIEW.
--
-- 013's CREATE VIEW references cb.outcome alongside cb.appliance_id.
-- Prod's compliance_bundles has neither column — 013 was pre-Session-205
-- backfilled and the VIEW body never executed. Fresh CI cmd_up needs
-- both columns present before 013 runs.
--
-- 012a added appliance_id; this adds outcome. Separate migration so
-- checksums on already-deployed 012a stay stable (avoids noise warning
-- on every prod startup).
--
-- Idempotent (ADD COLUMN IF NOT EXISTS). Forward-only. No prod impact:
-- 013 still won't actually run a SELECT against this column on prod
-- (the view body produced a different error class there originally),
-- but the column existing makes the VIEW DDL itself parse cleanly.

ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(50);
