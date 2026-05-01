-- Migration 243: 30-day retention prune for client_telemetry_events.
--
-- Session 210 round-table #5: the table ships with a docstring claiming
-- "30-day retention; prune via cron" but no cron exists. Rather than
-- introduce an external cron dependency, we use a DELETE inside a
-- Postgres function and schedule it via `pg_cron` if available. If not,
-- a background task in mcp-server calls the function every 24h.
--
-- This migration creates only the function + an index supporting fast
-- prune. Scheduling is wired via:
--   * background_tasks.py::client_telemetry_retention_loop (shipped in
--     the same commit as this migration)
--
-- The function is idempotent + safe to call hourly/daily. DELETE by
-- recorded_at range is fast because idx_client_telemetry_recorded_at
-- already exists (migration 242).

BEGIN;

CREATE OR REPLACE FUNCTION prune_client_telemetry_events(
    retention_days integer DEFAULT 30
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count integer;
BEGIN
    DELETE FROM client_telemetry_events
     WHERE recorded_at < NOW() - (retention_days || ' days')::interval;  -- noqa: sql-fn-interval-concat — `retention_days` is a plpgsql function parameter (DEFAULT 30 integer); the body's || runs server-side, not via asyncpg bind. Pattern safe; mig already applied to prod (file matches prod state).
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

COMMENT ON FUNCTION prune_client_telemetry_events(integer) IS
    'Delete client_telemetry_events older than retention_days (default 30). '
    'Called every 24h by client_telemetry_retention_loop in background_tasks.py. '
    'Session 210 round-table #5 2026-04-24.';

COMMIT;
