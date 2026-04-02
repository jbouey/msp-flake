-- Migration 115: Daemon runtime health stats
-- Stores Go runtime metrics (goroutines, heap, GC) from each checkin.
-- Zero new dependencies on the daemon side — uses Go stdlib runtime package.
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS daemon_health JSONB;
