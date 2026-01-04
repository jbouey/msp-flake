-- Orders Table Migration
-- Run this on the VPS PostgreSQL database
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 002_orders_table.sql

-- ============================================================================
-- Orders table for queuing commands to appliances
-- ============================================================================

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(64) UNIQUE NOT NULL,

    -- Target (nullable for broadcast orders)
    appliance_id VARCHAR(64),  -- NULL = broadcast to all site appliances
    site_id VARCHAR(50) NOT NULL,

    -- Order details
    order_type VARCHAR(50) NOT NULL,  -- force_checkin, run_drift, sync_rules, restart_agent, etc.
    parameters JSONB DEFAULT '{}',     -- Order-specific parameters
    priority INTEGER DEFAULT 0,        -- Higher = more urgent

    -- Status tracking
    status VARCHAR(20) DEFAULT 'pending',  -- pending, acknowledged, executing, completed, failed, expired
    acknowledged_at TIMESTAMP,
    completed_at TIMESTAMP,
    result JSONB,                      -- Execution result from appliance
    error_message TEXT,

    -- Audit
    created_by VARCHAR(100) DEFAULT 'admin',
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '1 hour'),

    CONSTRAINT valid_order_status CHECK (status IN ('pending', 'acknowledged', 'executing', 'completed', 'failed', 'expired'))
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_orders_appliance_pending ON orders(appliance_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_orders_site_pending ON orders(site_id, status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_expires ON orders(expires_at) WHERE status = 'pending';

-- ============================================================================
-- Grant permissions
-- ============================================================================

GRANT ALL ON orders TO mcp;
GRANT ALL ON orders_id_seq TO mcp;

-- ============================================================================
-- Summary
-- ============================================================================

SELECT 'Orders table created' AS status;
