-- Migration 068: Organization-level role assignments
-- Allows scoping admin users to specific organizations.
-- Users with NO rows = global admin (backward compatible).
-- Users with rows = scoped to those orgs only.

CREATE TABLE IF NOT EXISTS admin_org_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL CHECK (role IN ('org_admin', 'org_viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (admin_user_id, client_org_id)
);

CREATE INDEX IF NOT EXISTS idx_admin_org_assignments_user ON admin_org_assignments(admin_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_org_assignments_org ON admin_org_assignments(client_org_id);
