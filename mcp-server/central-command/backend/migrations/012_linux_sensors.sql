-- Migration 012: Add Linux sensor support to dual-mode architecture
-- Extends sensor_registry and sensor_commands for Linux sensors

-- Add platform and sensor_id columns to sensor_registry
ALTER TABLE sensor_registry
ADD COLUMN IF NOT EXISTS platform TEXT DEFAULT 'windows' CHECK (platform IN ('windows', 'linux')),
ADD COLUMN IF NOT EXISTS sensor_id TEXT;  -- Unique ID for Linux sensors

-- Add platform column to sensor_commands
ALTER TABLE sensor_commands
ADD COLUMN IF NOT EXISTS platform TEXT DEFAULT 'windows' CHECK (platform IN ('windows', 'linux'));

-- Drop and recreate the command_type constraint to include Linux commands
ALTER TABLE sensor_commands DROP CONSTRAINT IF EXISTS sensor_commands_command_type_check;
ALTER TABLE sensor_commands ADD CONSTRAINT sensor_commands_command_type_check
    CHECK (command_type IN (
        'deploy_sensor', 'remove_sensor', 'check_sensor',
        'deploy_linux_sensor', 'remove_linux_sensor', 'check_linux_sensor'
    ));

-- Create index for platform-specific queries
CREATE INDEX IF NOT EXISTS idx_sensor_registry_platform ON sensor_registry(platform);
CREATE INDEX IF NOT EXISTS idx_sensor_commands_platform ON sensor_commands(platform);

-- Update comments
COMMENT ON COLUMN sensor_registry.platform IS 'Sensor platform: windows or linux';
COMMENT ON COLUMN sensor_registry.sensor_id IS 'Unique sensor ID (for Linux sensors)';
COMMENT ON COLUMN sensor_commands.platform IS 'Target platform: windows or linux';
