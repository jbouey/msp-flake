-- Migration 117: Add RLS policies to security_events table
-- RLS was enabled+forced but had NO policies — blocked all operations for mcp_app role.
-- This caused every /api/security-events/archive call to 500.
CREATE POLICY IF NOT EXISTS admin_bypass ON security_events
    USING ((current_setting('app.is_admin', true))::boolean = true);
CREATE POLICY IF NOT EXISTS tenant_isolation ON security_events
    USING ((site_id)::text = current_setting('app.current_tenant', true));
