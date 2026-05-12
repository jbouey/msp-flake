-- Migration 309: Composite index on l2_decisions (site_id, escalation_reason,
-- created_at DESC) for the chronic_without_l2_escalation substrate invariant.
-- Carol P0-D from Gate A 2026-05-12 (audit/coach-p1-persistence-drift-l2-gate-a-2026-05-12.md).
--
-- NOTE: CONCURRENTLY cannot run inside a transaction; this file deliberately
-- omits BEGIN/COMMIT. asyncpg's simple-query runner treats the multi-statement
-- script as a single batched command — explicit BEGIN/COMMIT plus a trailing
-- CONCURRENTLY in the same file fails with ActiveSQLTransactionError. Working
-- pattern: mig 136 + mig 154 (CONCURRENTLY-only files, no BEGIN/COMMIT).
-- Originally landed inside mig 308 — split out post-deploy-failure on
-- 096de200 (2026-05-12).
--
-- The substrate invariant's 60s-tick NOT-EXISTS subquery JOINs through
-- incidents on (site_id, incident_type) and filters
-- escalation_reason IN ('recurrence', 'recurrence_backfill') AND
-- created_at > NOW() - INTERVAL '24 hours'. Existing indexes are
-- (incident_id), (pattern_signature), (created_at), (runbook_id), (site_id),
-- (prompt_version) — none satisfy the composite predicate efficiently at
-- 232K+ rows.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_l2_decisions_site_reason_created
    ON l2_decisions (site_id, escalation_reason, created_at DESC);

COMMENT ON INDEX idx_l2_decisions_site_reason_created IS
  'Supports the chronic_without_l2_escalation substrate invariant 60s tick query.';
