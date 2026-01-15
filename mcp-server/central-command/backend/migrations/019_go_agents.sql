-- Migration 019: Go Agents (Workstation-scale gRPC agents)
-- Purpose: Track Go agents pushing drift events via gRPC to appliances
-- Related: Session 40 Go Agent Implementation

-- Go agents table - tracks connected workstation agents
CREATE TABLE IF NOT EXISTS go_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) UNIQUE NOT NULL,
    site_id VARCHAR(100) NOT NULL,

    -- Identity
    hostname VARCHAR(255) NOT NULL,
    ip_address VARCHAR(45),
    mac_address VARCHAR(17),

    -- Version info
    agent_version VARCHAR(50),
    os_name VARCHAR(100),
    os_version VARCHAR(100),

    -- Capability tier (server-controlled)
    -- 0 = MONITOR_ONLY (just reports, no remediation)
    -- 1 = SELF_HEAL (can fix drift locally)
    -- 2 = FULL_REMEDIATION (full automation)
    capability_tier INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'pending',  -- pending, active, offline, error
    checks_passed INTEGER DEFAULT 0,
    checks_total INTEGER DEFAULT 0,
    compliance_percentage DECIMAL(5,2) DEFAULT 0.0,

    -- RMM detection
    rmm_detected VARCHAR(100),  -- 'connectwise', 'datto', 'ninja', etc.
    rmm_disabled BOOLEAN DEFAULT FALSE,

    -- Offline queue
    offline_queue_size INTEGER DEFAULT 0,

    -- Timestamps
    connected_at TIMESTAMP,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(site_id, hostname)
);

CREATE INDEX IF NOT EXISTS idx_go_agents_site ON go_agents(site_id);
CREATE INDEX IF NOT EXISTS idx_go_agents_status ON go_agents(status);
CREATE INDEX IF NOT EXISTS idx_go_agents_heartbeat ON go_agents(last_heartbeat DESC);

-- Go agent check results - individual check results from agents
CREATE TABLE IF NOT EXISTS go_agent_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL REFERENCES go_agents(agent_id) ON DELETE CASCADE,
    site_id VARCHAR(100) NOT NULL,

    -- Check details
    check_type VARCHAR(50) NOT NULL,  -- bitlocker, defender, firewall, patches, screen_lock, services
    status VARCHAR(20) NOT NULL,       -- pass, fail, error, skipped
    message TEXT,
    details JSONB DEFAULT '{}',

    -- HIPAA mapping
    hipaa_control VARCHAR(50),

    -- Timing
    duration_ms INTEGER DEFAULT 0,
    checked_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_go_checks_agent ON go_agent_checks(agent_id);
CREATE INDEX IF NOT EXISTS idx_go_checks_site ON go_agent_checks(site_id);
CREATE INDEX IF NOT EXISTS idx_go_checks_type ON go_agent_checks(check_type);
CREATE INDEX IF NOT EXISTS idx_go_checks_time ON go_agent_checks(checked_at DESC);

-- Site Go agent summaries - aggregated stats per site
CREATE TABLE IF NOT EXISTS site_go_agent_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(100) UNIQUE NOT NULL,

    -- Counts
    total_agents INTEGER DEFAULT 0,
    active_agents INTEGER DEFAULT 0,
    offline_agents INTEGER DEFAULT 0,
    error_agents INTEGER DEFAULT 0,
    pending_agents INTEGER DEFAULT 0,

    -- Compliance
    overall_compliance_rate DECIMAL(5,2) DEFAULT 0.0,

    -- Distribution
    agents_by_tier JSONB DEFAULT '{"monitor_only": 0, "self_heal": 0, "full_remediation": 0}',
    agents_by_version JSONB DEFAULT '{}',

    -- RMM
    rmm_detected_count INTEGER DEFAULT 0,

    -- Timing
    last_event TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_go_summaries_site ON site_go_agent_summaries(site_id);

-- Go agent orders - commands queued for agents
CREATE TABLE IF NOT EXISTS go_agent_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id VARCHAR(64) UNIQUE NOT NULL,
    agent_id VARCHAR(100) NOT NULL REFERENCES go_agents(agent_id) ON DELETE CASCADE,
    site_id VARCHAR(100) NOT NULL,

    -- Order details
    order_type VARCHAR(50) NOT NULL,  -- run_check, update_tier, restart, update_agent
    parameters JSONB DEFAULT '{}',
    priority INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'pending',  -- pending, acknowledged, completed, failed, expired
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '1 hour'),
    acknowledged_at TIMESTAMP,
    completed_at TIMESTAMP,
    result JSONB,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_go_orders_agent_pending ON go_agent_orders(agent_id, status)
WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_go_orders_expires ON go_agent_orders(expires_at)
WHERE status = 'pending';

-- View for latest check per agent per check type
CREATE OR REPLACE VIEW v_go_agent_latest_checks AS
SELECT DISTINCT ON (agent_id, check_type)
    agent_id,
    site_id,
    check_type,
    status,
    message,
    details,
    hipaa_control,
    checked_at
FROM go_agent_checks
ORDER BY agent_id, check_type, checked_at DESC;

-- View for agent status with latest checks
CREATE OR REPLACE VIEW v_go_agents_with_checks AS
SELECT
    a.*,
    (
        SELECT jsonb_agg(jsonb_build_object(
            'check_type', c.check_type,
            'status', c.status,
            'message', c.message,
            'details', c.details,
            'hipaa_control', c.hipaa_control,
            'checked_at', c.checked_at
        ))
        FROM v_go_agent_latest_checks c
        WHERE c.agent_id = a.agent_id
    ) as checks
FROM go_agents a;

-- Function to update site summary after agent changes
CREATE OR REPLACE FUNCTION update_go_agent_summary()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO site_go_agent_summaries (site_id, total_agents, active_agents, offline_agents,
                                         error_agents, pending_agents, overall_compliance_rate,
                                         agents_by_tier, agents_by_version, rmm_detected_count, updated_at)
    SELECT
        COALESCE(NEW.site_id, OLD.site_id) as site_id,
        COUNT(*) as total_agents,
        COUNT(*) FILTER (WHERE status = 'active') as active_agents,
        COUNT(*) FILTER (WHERE status = 'offline') as offline_agents,
        COUNT(*) FILTER (WHERE status = 'error') as error_agents,
        COUNT(*) FILTER (WHERE status = 'pending') as pending_agents,
        COALESCE(AVG(compliance_percentage), 0) as overall_compliance_rate,
        jsonb_build_object(
            'monitor_only', COUNT(*) FILTER (WHERE capability_tier = 0),
            'self_heal', COUNT(*) FILTER (WHERE capability_tier = 1),
            'full_remediation', COUNT(*) FILTER (WHERE capability_tier = 2)
        ) as agents_by_tier,
        (
            SELECT jsonb_object_agg(agent_version, cnt)
            FROM (
                SELECT agent_version, COUNT(*) as cnt
                FROM go_agents
                WHERE site_id = COALESCE(NEW.site_id, OLD.site_id) AND agent_version IS NOT NULL
                GROUP BY agent_version
            ) v
        ) as agents_by_version,
        COUNT(*) FILTER (WHERE rmm_detected IS NOT NULL) as rmm_detected_count,
        NOW() as updated_at
    FROM go_agents
    WHERE site_id = COALESCE(NEW.site_id, OLD.site_id)
    ON CONFLICT (site_id) DO UPDATE SET
        total_agents = EXCLUDED.total_agents,
        active_agents = EXCLUDED.active_agents,
        offline_agents = EXCLUDED.offline_agents,
        error_agents = EXCLUDED.error_agents,
        pending_agents = EXCLUDED.pending_agents,
        overall_compliance_rate = EXCLUDED.overall_compliance_rate,
        agents_by_tier = EXCLUDED.agents_by_tier,
        agents_by_version = EXCLUDED.agents_by_version,
        rmm_detected_count = EXCLUDED.rmm_detected_count,
        updated_at = EXCLUDED.updated_at;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update summary
DROP TRIGGER IF EXISTS go_agent_summary_trigger ON go_agents;
CREATE TRIGGER go_agent_summary_trigger
AFTER INSERT OR UPDATE OR DELETE ON go_agents
FOR EACH ROW EXECUTE FUNCTION update_go_agent_summary();
