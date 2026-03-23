-- Migration 098: Security Events WORM Archival
-- Stores sanitized Windows security events for OCR audit readiness.
-- Separate from log_entries for audit isolation and partition-based retention.

CREATE TABLE IF NOT EXISTS security_events (
    id BIGSERIAL,
    site_id VARCHAR(255) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    event_id INTEGER NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    message TEXT NOT NULL,
    source_host VARCHAR(255),
    category VARCHAR(50),
    severity VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, event_timestamp)
) PARTITION BY RANGE (event_timestamp);

-- Monthly partitions (auto-create via pg_partman in production)
CREATE TABLE IF NOT EXISTS security_events_2026_03 PARTITION OF security_events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS security_events_2026_04 PARTITION OF security_events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS security_events_2026_05 PARTITION OF security_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS security_events_2026_06 PARTITION OF security_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Indexes for audit queries
CREATE INDEX IF NOT EXISTS idx_security_events_site ON security_events (site_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_type ON security_events (event_id, event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_hostname ON security_events (hostname, event_timestamp DESC);

-- RLS for multi-tenant isolation
ALTER TABLE security_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_events FORCE ROW LEVEL SECURITY;

-- Admin policy (bypasses tenant filtering)
CREATE POLICY security_events_admin ON security_events
    FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');

-- Tenant policy (site-scoped reads)
CREATE POLICY security_events_tenant ON security_events
    FOR SELECT
    USING (site_id = current_setting('app.current_tenant', true));

-- Insert policy for appliance writes (keyed on tenant)
CREATE POLICY security_events_insert ON security_events
    FOR INSERT
    WITH CHECK (site_id = current_setting('app.current_tenant', true)
                OR current_setting('app.is_admin', true) = 'true');

-- Grant access to app role
GRANT SELECT, INSERT ON security_events TO mcp_app;
GRANT USAGE ON SEQUENCE security_events_id_seq TO mcp_app;

-- Append-only: prevent UPDATE/DELETE on security_events (WORM compliance)
CREATE OR REPLACE FUNCTION prevent_security_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'security_events is append-only (WORM). UPDATE and DELETE are prohibited.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS security_events_no_update ON security_events;
CREATE TRIGGER security_events_no_update
    BEFORE UPDATE OR DELETE ON security_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_security_event_mutation();
