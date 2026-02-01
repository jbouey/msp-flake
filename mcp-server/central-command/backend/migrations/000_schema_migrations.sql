-- Migration tracking table
-- This migration must be applied first to enable tracking

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    checksum VARCHAR(64) NOT NULL,
    execution_time_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at ON schema_migrations(applied_at);

-- Record this migration
INSERT INTO schema_migrations (version, name, checksum, execution_time_ms)
VALUES ('000', 'schema_migrations', 'initial', 0)
ON CONFLICT (version) DO NOTHING;

-- DOWN
-- DROP TABLE IF EXISTS schema_migrations;
