-- Migration 278: tenant_org_isolation RLS on site-scoped tables.
--
-- Round-table 2026-05-05
-- (.agent/plans/25-client-portal-data-display-roundtable-2026-05-05.md)
-- Stage 1 P0.
--
-- ROOT CAUSE: tenant_middleware.org_connection() sets
--   SET LOCAL app.current_tenant = ''
-- but every site-scoped table has the policy
--   site_id::text = current_setting('app.current_tenant')
-- so empty-string tenant matches NO rows. Result: every client-portal
-- endpoint that uses org_connection to read site-scoped tables silently
-- returns zero rows even when 155K+ rows exist for the org's sites.
-- User-visible: top-tile shows 20.8% (falls through to agent_compliance
-- only), Evidence Archive shows 0 bundles, Reports → Current shows 100%
-- (0/0 antipattern).
--
-- FIX: add a parallel `tenant_org_isolation` policy that recognizes
-- `app.current_org` and admits rows whose site_id rolls up to that org.
-- Composes with existing `admin_bypass` + `tenant_isolation` (Postgres
-- ORs policies — any matching policy admits the row). The existing
-- per-site flow continues to work unchanged.
--
-- Posture: this does NOT broaden access. A client_users session that
-- sets app.current_org=X via org_connection sees ONLY rows belonging
-- to sites under org X. Cross-org leak is still impossible because
-- the policy joins through `sites.client_org_id`.
--
-- Performance: the policy uses EXISTS over `sites` which has an index
-- on `client_org_id`. Verified: queries already filter by
-- `cb.site_id = ANY($1)` at the application layer, so the EXISTS only
-- runs over ~150 site rows max per org. Sub-millisecond impact.

-- ─── Helper function: admit-by-org ────────────────────────────────
-- IMMUTABLE-by-row (depends on `sites` lookup which is a different
-- table; PG does this kind of subquery in policies routinely). Wrapped
-- as a STABLE function so the optimizer can hoist the call when used
-- across multiple rows of the same query.

CREATE OR REPLACE FUNCTION rls_site_belongs_to_current_org(p_site_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    SELECT EXISTS (
        SELECT 1 FROM sites s
         WHERE s.site_id = p_site_id
           AND s.client_org_id::text = current_setting('app.current_org', true)
    );
$$;

COMMENT ON FUNCTION rls_site_belongs_to_current_org(TEXT) IS
'Helper for tenant_org_isolation RLS policies. Returns true iff the '
'given site_id rolls up to the org currently set via SET LOCAL '
'app.current_org. Used by org_connection-flow client portal queries. '
'STABLE so the planner can hoist the call across rows of the same '
'query. NEVER use this with empty/NULL current_org — the policy '
'short-circuits via the IS NOT NULL guard at the policy level so the '
'function never gets a no-op call.';

-- ─── Apply to every site-scoped table ─────────────────────────────
-- DO block iterates over the known site-RLS table list and adds the
-- new policy. Idempotent via DROP IF EXISTS first. The tables enumerated
-- here are exactly the tables with `site_id` + a `tenant_isolation`
-- (or named-equivalent) site-scoped policy as of 2026-05-05.

DO $$
DECLARE
    t TEXT;
    site_tables TEXT[] := ARRAY[
        'compliance_bundles',
        'execution_telemetry',
        'incidents',
        'incident_correlation_pairs',
        'incident_recurrence_velocity',
        'l2_decisions',
        'l2_rate_limits',
        'log_entries',
        'go_agents',
        'go_agent_checks',
        'go_agent_orders',
        'agent_deployments',
        'admin_orders',
        'orders',
        'reconcile_events',
        'security_events',
        'sensor_registry',
        'site_appliances',
        'site_credentials',
        'site_drift_config',
        'site_healing_sla',
        'site_notification_overrides',
        'target_health',
        'app_protection_profiles',
        'discovered_devices',
        'device_compliance_details',
        'enumeration_results',
        'escalation_tickets',
        'evidence_bundles'
    ];
BEGIN
    FOREACH t IN ARRAY site_tables LOOP
        -- Skip if the table doesn't exist in this DB (e.g. partial
        -- checkout / older schema in dev).
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relname = t AND n.nspname = 'public'
        ) THEN
            RAISE NOTICE 'mig 278: table % missing, skipping', t;
            CONTINUE;
        END IF;

        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_org_isolation ON %I',
            t
        );
        EXECUTE format(
            $f$CREATE POLICY tenant_org_isolation ON %I FOR ALL
                USING (
                    current_setting('app.current_org', true) IS NOT NULL
                    AND current_setting('app.current_org', true) <> ''
                    AND rls_site_belongs_to_current_org(site_id::text)
                )$f$,
            t
        );
        RAISE NOTICE 'mig 278: tenant_org_isolation applied to %', t;
    END LOOP;
END $$;

-- ─── client_audit_log — different shape (org-keyed directly) ─────
-- The audit log is keyed on client_org_id, not site_id. It currently
-- has only admin_bypass — under org_connection (is_admin='false') the
-- client portal cannot read its OWN audit history. Add a self-org
-- policy.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = 'client_audit_log' AND n.nspname = 'public'
    ) THEN
        DROP POLICY IF EXISTS client_audit_self_org ON client_audit_log;
        CREATE POLICY client_audit_self_org ON client_audit_log FOR ALL
            USING (
                current_setting('app.current_org', true) IS NOT NULL
                AND current_setting('app.current_org', true) <> ''
                AND org_id::text = current_setting('app.current_org', true)
            );
        RAISE NOTICE 'mig 278: client_audit_self_org applied';
    END IF;
END $$;

-- ─── client_user_email_change_log (mig 277) needs the same shape ─
-- The audit ledger from #23 was created without an org-scoped policy.
-- Add it for parity with client_audit_log so customers can view their
-- own email-change history.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = 'client_user_email_change_log'
           AND n.nspname = 'public'
    ) THEN
        -- Only enable RLS if not already; default tables in PG don't
        -- have it on. The existing append-only triggers handle write
        -- protection; RLS handles read scoping.
        ALTER TABLE client_user_email_change_log ENABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS admin_bypass ON client_user_email_change_log;
        CREATE POLICY admin_bypass ON client_user_email_change_log FOR ALL
            USING (current_setting('app.is_admin', true) = 'true');

        DROP POLICY IF EXISTS client_email_change_self_org ON client_user_email_change_log;
        CREATE POLICY client_email_change_self_org ON client_user_email_change_log FOR ALL
            USING (
                current_setting('app.current_org', true) IS NOT NULL
                AND current_setting('app.current_org', true) <> ''
                AND client_org_id::text = current_setting('app.current_org', true)
            );
        RAISE NOTICE 'mig 278: RLS applied to client_user_email_change_log';
    END IF;
END $$;
