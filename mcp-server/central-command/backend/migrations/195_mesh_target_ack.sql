-- Migration 195: mesh_target_assignments with TTL + appliance ACK (#M3).
--
-- Session 206 round-table, CCIE perspective: "Mesh assigned targets to
-- appliances it never verified were alive." Today, hash_ring.py computes
-- target assignments based on site_appliances — which was lying about
-- liveness for 3 days. Targets to phantom appliances = silent monitoring
-- gaps.
--
-- Fix: assignments become a first-class table with appliance ACK + TTL.
-- Appliances re-ACK every checkin; assignments older than the TTL
-- without a re-ACK are expired and reassigned. Phantom appliances
-- can't re-ACK, so their targets get redistributed within one TTL cycle.

BEGIN;

CREATE TABLE IF NOT EXISTS mesh_target_assignments (
    assignment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         TEXT NOT NULL,
    appliance_id    TEXT NOT NULL,    -- Who is supposed to monitor this target
    target_key      TEXT NOT NULL,    -- What they're monitoring (MAC / host_id / other)
    target_type     TEXT NOT NULL,    -- 'device', 'subnet', 'service', etc.
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_ack_at     TIMESTAMPTZ,      -- Most recent ACK from the assignee
    ack_count       INTEGER NOT NULL DEFAULT 0,
    ttl_seconds     INTEGER NOT NULL DEFAULT 900,  -- 15 min default
    -- Postgres refuses GENERATED columns mixing column refs + make_interval
    -- ("not immutable"), so we maintain expires_at via trigger instead.
    expires_at      TIMESTAMPTZ NOT NULL,
    reassigned_from TEXT,             -- Previous owner if reassigned
    reassigned_at   TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION compute_mesh_assignment_expires_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.expires_at = COALESCE(NEW.last_ack_at, NEW.assigned_at)
        + (NEW.ttl_seconds || ' seconds')::interval;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_mesh_assignment_expires ON mesh_target_assignments;
CREATE TRIGGER trg_mesh_assignment_expires
    BEFORE INSERT OR UPDATE ON mesh_target_assignments
    FOR EACH ROW EXECUTE FUNCTION compute_mesh_assignment_expires_at();

CREATE UNIQUE INDEX IF NOT EXISTS idx_mesh_targets_site_key
    ON mesh_target_assignments(site_id, target_key, target_type);
CREATE INDEX IF NOT EXISTS idx_mesh_targets_appliance
    ON mesh_target_assignments(appliance_id, expires_at);
-- Full index on expires_at. Previous partial predicate used NOW() which
-- Postgres rejects (not immutable in index predicates). A full B-tree on
-- expires_at is still cheap and the reassignment loop filters in query.
CREATE INDEX IF NOT EXISTS idx_mesh_targets_expires
    ON mesh_target_assignments(expires_at);

COMMENT ON TABLE mesh_target_assignments IS
    'Session 206 M3: first-class mesh target assignments with appliance '
    'ACK + TTL. Replaces in-memory hash_ring computation that assigned '
    'targets to phantom appliances. Unackd assignments expire + get '
    'reassigned on the next rebalance pass.';

COMMENT ON COLUMN mesh_target_assignments.expires_at IS
    'Auto-computed: last_ack_at (or assigned_at if never ACKd) + ttl_seconds. '
    'An appliance that stops checking in cannot ACK, so its targets age '
    'out and get reassigned to a live appliance.';

-- =============================================================================
-- Helper: record an ACK from an appliance for a target it owns.
-- =============================================================================

CREATE OR REPLACE FUNCTION record_mesh_target_ack(
    p_site_id TEXT,
    p_appliance_id TEXT,
    p_target_key TEXT,
    p_target_type TEXT
) RETURNS BOOLEAN AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE mesh_target_assignments
    SET last_ack_at = NOW(),
        ack_count = ack_count + 1
    WHERE site_id = p_site_id
      AND appliance_id = p_appliance_id
      AND target_key = p_target_key
      AND target_type = p_target_type;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count > 0;
END;
$$ LANGUAGE plpgsql;

COMMIT;
