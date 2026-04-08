-- Migration 142: Incident Pipeline Hardening
-- INC-1: Unique index on dedup_key for ON CONFLICT upsert (prevents race condition)
-- INC-4: Index for resolve endpoint rate limiting

-- Partial unique index: only enforced when dedup_key is set
-- (NULL dedup_keys are pre-dedup-era incidents)
CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_dedup_key_unique
    ON incidents (dedup_key)
    WHERE dedup_key IS NOT NULL AND status NOT IN ('resolved', 'closed');

-- Index for resolve-by-type queries
CREATE INDEX IF NOT EXISTS idx_incidents_resolve_lookup
    ON incidents (site_id, incident_type, status)
    WHERE status IN ('open', 'resolving', 'escalated');
