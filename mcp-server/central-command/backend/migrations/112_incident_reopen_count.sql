-- Migration 112: Add reopen_count to incidents
-- Tracks how many times an incident has been resolved then reopened due to
-- recurring drift. Used by the 30-min grace period logic to prevent churn.
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS reopen_count INTEGER DEFAULT 0;
