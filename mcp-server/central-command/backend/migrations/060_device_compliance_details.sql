-- Device compliance check details synced from appliances
-- Stores per-check results so the dashboard can show drill-down compliance info

CREATE TABLE IF NOT EXISTS device_compliance_details (
    id SERIAL PRIMARY KEY,
    discovered_device_id INTEGER NOT NULL REFERENCES discovered_devices(id) ON DELETE CASCADE,
    check_type TEXT NOT NULL,
    hipaa_control TEXT,
    status TEXT NOT NULL,
    details JSONB,
    checked_at TIMESTAMPTZ NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(discovered_device_id, check_type)
);

CREATE INDEX IF NOT EXISTS idx_dcd_device ON device_compliance_details(discovered_device_id);
CREATE INDEX IF NOT EXISTS idx_dcd_status ON device_compliance_details(status);
