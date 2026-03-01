-- Migration: 066_companion_alerts.sql
-- Description: Companion compliance alerts — per-module deadline tracking with email notifications
-- Created: 2026-03-01

CREATE TABLE IF NOT EXISTS companion_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    module_key TEXT NOT NULL,
    expected_status TEXT NOT NULL,
    target_date DATE NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'triggered', 'resolved', 'dismissed')),
    triggered_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    last_notified_at TIMESTAMPTZ,
    notification_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companion_alerts_org ON companion_alerts(org_id, module_key);
CREATE INDEX IF NOT EXISTS idx_companion_alerts_user ON companion_alerts(companion_user_id);
CREATE INDEX IF NOT EXISTS idx_companion_alerts_active ON companion_alerts(status, target_date)
    WHERE status = 'active';
