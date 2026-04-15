-- Migration 207: substrate_violations + helpers
--
-- Backbone of the Substrate Integrity Engine.
--
-- Each row represents one ASSERTION FAILURE detected by the
-- assertions engine (see assertions.py). The engine runs every 60s
-- inside the health_monitor_loop and INSERTs a violation if the
-- assertion is still failing. Violations are auto-resolved (resolved_at
-- set) when a follow-up tick observes the assertion passing again.
--
-- The point: an enterprise customer should see a single page that
-- says "0 substrate violations" or "3 active violations: ...". Every
-- failure mode we found tonight (NULL legacy_uuid, stale
-- discovered_devices, install_loop, mismatched agent_version, etc.)
-- becomes one row in this table — a structured, dedupable, per-site
-- signal — instead of a one-off log line we hope someone reads.

BEGIN;

CREATE TABLE IF NOT EXISTS substrate_violations (
    id              BIGSERIAL PRIMARY KEY,
    invariant_name  VARCHAR(100)   NOT NULL,
    severity        VARCHAR(20)    NOT NULL DEFAULT 'sev1',
    site_id         VARCHAR(50),
    detected_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    details         JSONB                   DEFAULT '{}'::jsonb,
    notification_id BIGINT
);

-- Partial unique index: at most one OPEN violation per (invariant, site).
-- New tick observes the same failure → bumps last_seen_at via UPDATE.
-- Tick observes failure cleared → resolved_at populated, the row falls
-- out of the partial index and a fresh recurrence later opens a new row.
CREATE UNIQUE INDEX IF NOT EXISTS substrate_violations_open_uniq
    ON substrate_violations (invariant_name, COALESCE(site_id, ''))
    WHERE resolved_at IS NULL;

-- Fast lookup for the dashboard "active violations" rollup.
CREATE INDEX IF NOT EXISTS substrate_violations_active_idx
    ON substrate_violations (site_id, severity, detected_at DESC)
    WHERE resolved_at IS NULL;

-- Read-only view used by the dashboard + admin UI.
CREATE OR REPLACE VIEW v_substrate_violations_active AS
SELECT site_id,
       invariant_name,
       severity,
       detected_at,
       last_seen_at,
       details,
       EXTRACT(EPOCH FROM (NOW() - detected_at))/60 AS minutes_open
  FROM substrate_violations
 WHERE resolved_at IS NULL
 ORDER BY detected_at DESC;

GRANT SELECT, INSERT, UPDATE ON substrate_violations TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE substrate_violations_id_seq TO mcp_app;
GRANT SELECT ON v_substrate_violations_active TO mcp_app;

COMMIT;

SELECT 'Migration 207_substrate_violations complete' AS status;
