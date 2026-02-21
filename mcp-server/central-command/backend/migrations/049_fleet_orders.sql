-- ============================================================
-- Fleet-wide Orders
--
-- Fleet orders are fleet-wide commands — one row per fleet command.
-- Every appliance checks for active fleet orders during checkin.
-- Appliances skip orders if they already meet skip_version or
-- have already completed the order.
-- ============================================================

CREATE TABLE IF NOT EXISTS fleet_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_type TEXT NOT NULL,           -- nixos_rebuild, update_agent, sync_rules, etc.
    parameters JSONB DEFAULT '{}',
    skip_version TEXT,                  -- appliances at this agent_version skip
    status TEXT NOT NULL DEFAULT 'active',  -- active, completed, cancelled
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS fleet_order_completions (
    fleet_order_id UUID NOT NULL REFERENCES fleet_orders(id) ON DELETE CASCADE,
    appliance_id TEXT NOT NULL,         -- canonical appliance_id from site_appliances
    status TEXT NOT NULL DEFAULT 'completed',  -- acknowledged, completed, failed, skipped
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fleet_order_id, appliance_id)
);

CREATE INDEX IF NOT EXISTS idx_fleet_orders_active
ON fleet_orders(status, expires_at) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_fleet_completions_appliance
ON fleet_order_completions(appliance_id);
