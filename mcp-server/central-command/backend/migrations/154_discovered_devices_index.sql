-- Migration 154: Index on discovered_devices for unregistered device queries.
-- The client portal and admin dashboard query by (site_id via appliance_id, device_status)
-- frequently. This index speeds up the unregistered devices listing.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_discovered_devices_status
ON discovered_devices (appliance_id, device_status);
