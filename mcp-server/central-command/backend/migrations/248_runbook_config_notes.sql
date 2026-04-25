-- Migration 248: Add notes column to runbook_config tables
--
-- Both site_runbook_config and appliance_runbook_config are written
-- with a `notes` column from runbook_config.py:273 + :433 (partner UI
-- "explain why this runbook is disabled" feature). The column was
-- never created in prod — every write 500'd until now. The SQL
-- column-vs-schema linter (test_sql_columns_match_schema.py) caught
-- this on the 2026-04-25 baseline-grind pass.
--
-- This migration is forward-only and idempotent. If `notes` is later
-- removed from the model, drop it via a separate migration; the linter
-- baseline will tighten as a side-effect.

ALTER TABLE site_runbook_config
    ADD COLUMN IF NOT EXISTS notes TEXT;

ALTER TABLE appliance_runbook_config
    ADD COLUMN IF NOT EXISTS notes TEXT;
