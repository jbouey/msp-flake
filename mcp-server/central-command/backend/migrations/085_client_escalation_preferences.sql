-- Migration 085: Client Escalation Preferences
-- Allows client orgs to control L3 escalation routing:
--   'partner' (default) — L3s route to partner
--   'direct'  — L3s route directly to client org (email notification)
--   'both'    — notify both partner and client org

-- Client escalation preferences per org
CREATE TABLE IF NOT EXISTS client_escalation_preferences (
    id SERIAL PRIMARY KEY,
    client_org_id UUID NOT NULL UNIQUE REFERENCES client_orgs(id) ON DELETE CASCADE,
    escalation_mode TEXT NOT NULL DEFAULT 'partner' CHECK (escalation_mode IN ('partner', 'direct', 'both')),
    email_enabled BOOLEAN NOT NULL DEFAULT true,
    email_recipients TEXT[] DEFAULT '{}',
    slack_enabled BOOLEAN NOT NULL DEFAULT false,
    slack_webhook_url TEXT,
    teams_enabled BOOLEAN NOT NULL DEFAULT false,
    teams_webhook_url TEXT,
    escalation_timeout_minutes INTEGER NOT NULL DEFAULT 60,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS
ALTER TABLE client_escalation_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_escalation_preferences FORCE ROW LEVEL SECURITY;

CREATE POLICY client_escalation_preferences_admin ON client_escalation_preferences
    FOR ALL USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY client_escalation_preferences_tenant ON client_escalation_preferences
    FOR ALL USING (client_org_id::text = current_setting('app.current_org', true));

-- Index
CREATE INDEX IF NOT EXISTS idx_client_escalation_prefs_org ON client_escalation_preferences(client_org_id);

-- Also add client_org_id to escalation_tickets for direct-to-client routing
ALTER TABLE escalation_tickets ADD COLUMN IF NOT EXISTS client_org_id TEXT;
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_client_org ON escalation_tickets(client_org_id) WHERE client_org_id IS NOT NULL;
