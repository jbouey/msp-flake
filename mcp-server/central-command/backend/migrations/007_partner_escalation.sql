-- Migration 007: Partner notification configuration and L3 escalation tracking
-- Date: 2026-01-08

BEGIN;

-- ============================================================================
-- PARTNER NOTIFICATION SETTINGS
-- ============================================================================

CREATE TABLE IF NOT EXISTS partner_notification_settings (
    id SERIAL PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,

    -- Email
    email_enabled BOOLEAN DEFAULT true,
    email_recipients TEXT[],
    email_from_name TEXT,

    -- Slack
    slack_enabled BOOLEAN DEFAULT false,
    slack_webhook_url TEXT,
    slack_channel TEXT,
    slack_username TEXT DEFAULT 'OsirisCare',
    slack_icon_emoji TEXT DEFAULT ':warning:',

    -- PagerDuty
    pagerduty_enabled BOOLEAN DEFAULT false,
    pagerduty_routing_key TEXT,
    pagerduty_service_id TEXT,

    -- Microsoft Teams
    teams_enabled BOOLEAN DEFAULT false,
    teams_webhook_url TEXT,

    -- Generic Webhook (for PSA/RMM integration)
    webhook_enabled BOOLEAN DEFAULT false,
    webhook_url TEXT,
    webhook_secret TEXT,
    webhook_headers JSONB,

    -- Behavior
    escalation_timeout_minutes INTEGER DEFAULT 60,
    auto_acknowledge BOOLEAN DEFAULT false,
    include_raw_data BOOLEAN DEFAULT true,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(partner_id)
);

-- Site-level overrides (optional)
CREATE TABLE IF NOT EXISTS site_notification_overrides (
    id SERIAL PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    partner_id TEXT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,

    -- Override specific channels for this site
    email_recipients TEXT[],
    slack_channel TEXT,
    pagerduty_routing_key TEXT,

    -- Site-specific escalation behavior
    escalation_timeout_minutes INTEGER,
    priority_override TEXT,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(site_id)
);

-- ============================================================================
-- ESCALATION TICKETS
-- ============================================================================

CREATE TABLE IF NOT EXISTS escalation_tickets (
    id TEXT PRIMARY KEY,

    -- Ownership
    partner_id TEXT NOT NULL REFERENCES partners(id),
    site_id TEXT NOT NULL REFERENCES sites(id),

    -- Incident details
    incident_id TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    priority TEXT NOT NULL,

    -- Ticket content
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    raw_data JSONB,
    hipaa_controls TEXT[],

    -- Context
    attempted_actions JSONB,
    similar_incidents JSONB,
    recommended_action TEXT,

    -- Status tracking
    status TEXT DEFAULT 'open',
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    acknowledged_by TEXT,
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolved_by TEXT,
    resolution_notes TEXT,

    -- SLA tracking
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sla_target_at TIMESTAMP WITH TIME ZONE,
    sla_breached BOOLEAN DEFAULT false,

    -- Notification tracking
    notifications_sent JSONB DEFAULT '[]'::jsonb,

    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_escalation_tickets_partner ON escalation_tickets(partner_id);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_site ON escalation_tickets(site_id);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_status ON escalation_tickets(status);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_created ON escalation_tickets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_sla ON escalation_tickets(sla_breached, sla_target_at);

-- ============================================================================
-- NOTIFICATION DELIVERY LOG
-- ============================================================================

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES escalation_tickets(id) ON DELETE CASCADE,

    -- Delivery details
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,

    -- Status
    status TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 1,

    -- Response
    response_code INTEGER,
    response_body TEXT,
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    sent_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    next_retry_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_ticket ON notification_deliveries(ticket_id);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status ON notification_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_retry ON notification_deliveries(next_retry_at)
    WHERE status IN ('pending', 'failed');

-- ============================================================================
-- SLA DEFINITIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS sla_definitions (
    id SERIAL PRIMARY KEY,
    partner_id TEXT REFERENCES partners(id) ON DELETE CASCADE,

    priority TEXT NOT NULL,
    response_time_minutes INTEGER NOT NULL,
    resolution_time_minutes INTEGER,

    -- Escalation path
    escalate_after_minutes INTEGER,
    escalate_to TEXT,

    UNIQUE(partner_id, priority)
);

-- Insert default SLAs (partner_id NULL = defaults)
INSERT INTO sla_definitions (partner_id, priority, response_time_minutes, resolution_time_minutes, escalate_after_minutes)
VALUES
    (NULL, 'critical', 15, 60, 30),
    (NULL, 'high', 60, 240, 120),
    (NULL, 'medium', 240, 480, 360),
    (NULL, 'low', 480, 1440, 720)
ON CONFLICT (partner_id, priority) DO NOTHING;

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE partner_notification_settings IS 'Partner-level notification channel configuration';
COMMENT ON TABLE site_notification_overrides IS 'Site-level overrides for notification routing';
COMMENT ON TABLE escalation_tickets IS 'L3 escalation tickets requiring human intervention';
COMMENT ON TABLE notification_deliveries IS 'Delivery log for all notification attempts';
COMMENT ON TABLE sla_definitions IS 'SLA response/resolution targets by priority';

COMMIT;
