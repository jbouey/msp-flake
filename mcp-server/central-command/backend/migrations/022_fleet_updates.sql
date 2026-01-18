-- Migration 022: Fleet Updates Infrastructure
-- Phase 13: Zero-Touch Update System

-- Update releases (ISO versions)
CREATE TABLE IF NOT EXISTS update_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version VARCHAR(50) NOT NULL UNIQUE,
    iso_url TEXT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    size_bytes BIGINT,
    release_notes TEXT,
    agent_version VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES admin_users(id),
    is_active BOOLEAN DEFAULT true,
    is_latest BOOLEAN DEFAULT false
);

-- Update rollouts (deployment campaigns)
CREATE TABLE IF NOT EXISTS update_rollouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID REFERENCES update_releases(id) ON DELETE CASCADE,
    name VARCHAR(100),
    strategy VARCHAR(20) DEFAULT 'staged' CHECK (strategy IN ('immediate', 'staged', 'canary', 'manual')),
    current_stage INT DEFAULT 0,
    stages JSONB DEFAULT '[{"percent": 5, "delay_hours": 24}, {"percent": 25, "delay_hours": 24}, {"percent": 100, "delay_hours": 0}]',
    maintenance_window JSONB DEFAULT '{"start": "02:00", "end": "05:00", "timezone": "America/New_York", "days": ["sunday", "monday", "tuesday", "wednesday", "thursday"]}',
    target_filter JSONB,  -- Optional: filter by site_id, partner_id, tags
    started_at TIMESTAMPTZ DEFAULT NOW(),
    paused_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'paused', 'completed', 'failed', 'cancelled')),
    created_by UUID REFERENCES admin_users(id),
    failure_threshold_percent INT DEFAULT 10,  -- Pause if this % fails
    auto_rollback BOOLEAN DEFAULT true
);

-- Per-appliance update tracking
CREATE TABLE IF NOT EXISTS appliance_updates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id UUID REFERENCES appliances(id) ON DELETE CASCADE,
    rollout_id UUID REFERENCES update_rollouts(id) ON DELETE CASCADE,
    stage_assigned INT DEFAULT 0,  -- Which rollout stage this appliance is in
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN (
        'pending',      -- Waiting for its stage
        'notified',     -- Appliance received update command
        'downloading',  -- Downloading ISO
        'ready',        -- Downloaded, waiting for maintenance window
        'rebooting',    -- Rebooting into new version
        'verifying',    -- Post-boot health check
        'succeeded',    -- Update successful
        'failed',       -- Update failed
        'rolled_back'   -- Rolled back to previous version
    )),
    download_started_at TIMESTAMPTZ,
    download_completed_at TIMESTAMPTZ,
    reboot_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    error_code VARCHAR(50),
    previous_version VARCHAR(50),
    new_version VARCHAR(50),
    boot_attempts INT DEFAULT 0,
    health_checks JSONB DEFAULT '[]',  -- Array of health check results
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(appliance_id, rollout_id)
);

-- Update audit log
CREATE TABLE IF NOT EXISTS update_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,  -- release_created, rollout_started, appliance_updated, etc.
    release_id UUID REFERENCES update_releases(id) ON DELETE SET NULL,
    rollout_id UUID REFERENCES update_rollouts(id) ON DELETE SET NULL,
    appliance_id UUID REFERENCES appliances(id) ON DELETE SET NULL,
    user_id UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_update_releases_version ON update_releases(version);
CREATE INDEX IF NOT EXISTS idx_update_releases_active ON update_releases(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_update_releases_latest ON update_releases(is_latest) WHERE is_latest = true;

CREATE INDEX IF NOT EXISTS idx_update_rollouts_status ON update_rollouts(status);
CREATE INDEX IF NOT EXISTS idx_update_rollouts_release ON update_rollouts(release_id);
CREATE INDEX IF NOT EXISTS idx_update_rollouts_active ON update_rollouts(status) WHERE status IN ('pending', 'in_progress');

CREATE INDEX IF NOT EXISTS idx_appliance_updates_appliance ON appliance_updates(appliance_id);
CREATE INDEX IF NOT EXISTS idx_appliance_updates_rollout ON appliance_updates(rollout_id);
CREATE INDEX IF NOT EXISTS idx_appliance_updates_status ON appliance_updates(status);
CREATE INDEX IF NOT EXISTS idx_appliance_updates_pending ON appliance_updates(status, stage_assigned) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_update_audit_log_event ON update_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_update_audit_log_created ON update_audit_log(created_at DESC);

-- Trigger to update updated_at
CREATE OR REPLACE FUNCTION update_appliance_updates_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_appliance_updates_timestamp ON appliance_updates;
CREATE TRIGGER trigger_appliance_updates_timestamp
    BEFORE UPDATE ON appliance_updates
    FOR EACH ROW EXECUTE FUNCTION update_appliance_updates_timestamp();

-- Trigger to ensure only one release is marked as latest
CREATE OR REPLACE FUNCTION ensure_single_latest_release()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_latest = true THEN
        UPDATE update_releases SET is_latest = false WHERE id != NEW.id AND is_latest = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_single_latest_release ON update_releases;
CREATE TRIGGER trigger_single_latest_release
    BEFORE INSERT OR UPDATE OF is_latest ON update_releases
    FOR EACH ROW WHEN (NEW.is_latest = true)
    EXECUTE FUNCTION ensure_single_latest_release();

-- View for rollout progress
CREATE OR REPLACE VIEW rollout_progress AS
SELECT
    r.id as rollout_id,
    r.status as rollout_status,
    rel.version,
    r.current_stage,
    COUNT(au.id) as total_appliances,
    COUNT(au.id) FILTER (WHERE au.status = 'succeeded') as succeeded,
    COUNT(au.id) FILTER (WHERE au.status = 'failed') as failed,
    COUNT(au.id) FILTER (WHERE au.status = 'rolled_back') as rolled_back,
    COUNT(au.id) FILTER (WHERE au.status IN ('pending', 'notified', 'downloading', 'ready')) as pending,
    COUNT(au.id) FILTER (WHERE au.status IN ('rebooting', 'verifying')) as in_progress,
    ROUND(
        100.0 * COUNT(au.id) FILTER (WHERE au.status = 'succeeded') / NULLIF(COUNT(au.id), 0),
        1
    ) as success_rate_percent
FROM update_rollouts r
JOIN update_releases rel ON r.release_id = rel.id
LEFT JOIN appliance_updates au ON r.id = au.rollout_id
GROUP BY r.id, r.status, rel.version, r.current_stage;

-- Add version tracking columns to appliances table if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appliances' AND column_name = 'current_version') THEN
        ALTER TABLE appliances ADD COLUMN current_version VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appliances' AND column_name = 'previous_version') THEN
        ALTER TABLE appliances ADD COLUMN previous_version VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appliances' AND column_name = 'last_update_at') THEN
        ALTER TABLE appliances ADD COLUMN last_update_at TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appliances' AND column_name = 'update_status') THEN
        ALTER TABLE appliances ADD COLUMN update_status VARCHAR(20) DEFAULT 'idle';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appliances' AND column_name = 'active_partition') THEN
        ALTER TABLE appliances ADD COLUMN active_partition CHAR(1) DEFAULT 'A';
    END IF;
END $$;

-- Comments
COMMENT ON TABLE update_releases IS 'ISO versions available for deployment';
COMMENT ON TABLE update_rollouts IS 'Deployment campaigns with staged rollout support';
COMMENT ON TABLE appliance_updates IS 'Per-appliance update status tracking';
COMMENT ON TABLE update_audit_log IS 'Audit trail for all update operations';
COMMENT ON VIEW rollout_progress IS 'Aggregated rollout progress statistics';
