-- Migration: 053_companion_portal.sql
-- Description: Compliance Companion portal — new role, notes table, activity log
-- Created: 2026-02-24

-- =============================================================================
-- 1. Expand admin_users role CHECK to include 'companion'
-- =============================================================================
ALTER TABLE admin_users DROP CONSTRAINT IF EXISTS admin_users_role_check;
ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_check
    CHECK (role IN ('admin', 'operator', 'readonly', 'companion'));

-- =============================================================================
-- 2. Companion Notes — per module per client org working scratchpad
-- =============================================================================
CREATE TABLE IF NOT EXISTS companion_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    module_key TEXT NOT NULL,  -- sra, policies, training, baas, ir-plan, contingency, workforce, physical, officers, gap-analysis
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_companion_notes_org ON companion_notes(org_id, module_key);
CREATE INDEX idx_companion_notes_user ON companion_notes(companion_user_id);

-- =============================================================================
-- 3. Companion Activity Log — append-only audit trail
-- =============================================================================
CREATE TABLE IF NOT EXISTS companion_activity_log (
    id SERIAL PRIMARY KEY,
    companion_user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    action TEXT NOT NULL,       -- viewed_client, viewed_module, edited_sra, added_note, etc.
    module_key TEXT,            -- nullable for non-module actions
    details JSONB,             -- action-specific metadata
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_companion_activity_org ON companion_activity_log(org_id);
CREATE INDEX idx_companion_activity_user ON companion_activity_log(companion_user_id);
CREATE INDEX idx_companion_activity_created ON companion_activity_log(created_at);
