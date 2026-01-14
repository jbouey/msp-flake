-- Migration 006: Add sensor registry for dual-mode architecture
-- Tracks Windows sensors that push drift events to appliances

CREATE TABLE IF NOT EXISTS sensor_registry (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) NOT NULL,  -- Matches sites.site_id
    hostname TEXT NOT NULL,
    domain TEXT,
    sensor_version TEXT,
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    last_drift_count INTEGER DEFAULT 0,
    last_compliant BOOLEAN DEFAULT true,
    is_active BOOLEAN DEFAULT true,
    appliance_id TEXT,  -- Which appliance this sensor reports to

    UNIQUE(site_id, hostname)
);

CREATE INDEX IF NOT EXISTS idx_sensor_registry_site ON sensor_registry(site_id);
CREATE INDEX IF NOT EXISTS idx_sensor_registry_active ON sensor_registry(is_active, last_heartbeat);
CREATE INDEX IF NOT EXISTS idx_sensor_registry_appliance ON sensor_registry(appliance_id);

-- Add sensor deployment tracking to site_credentials
ALTER TABLE site_credentials
ADD COLUMN IF NOT EXISTS sensor_deployed BOOLEAN DEFAULT false,
ADD COLUMN IF NOT EXISTS sensor_deployed_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS sensor_version TEXT;

-- Add sensor commands table for queuing deploy/remove commands
CREATE TABLE IF NOT EXISTS sensor_commands (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) NOT NULL,  -- Matches sites.site_id
    appliance_id TEXT NOT NULL,
    command_type TEXT NOT NULL CHECK (command_type IN ('deploy_sensor', 'remove_sensor', 'check_sensor')),
    hostname TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'acknowledged', 'completed', 'failed')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sent_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    result JSONB
);

CREATE INDEX IF NOT EXISTS idx_sensor_commands_pending ON sensor_commands(site_id, appliance_id, status)
    WHERE status IN ('pending', 'sent');

-- Comments
COMMENT ON TABLE sensor_registry IS 'Tracks Windows sensors in dual-mode architecture';
COMMENT ON TABLE sensor_commands IS 'Queued sensor deployment commands for appliances';
COMMENT ON COLUMN sensor_registry.appliance_id IS 'ID of appliance receiving sensor events';
COMMENT ON COLUMN sensor_commands.command_type IS 'Type: deploy_sensor, remove_sensor, check_sensor';
