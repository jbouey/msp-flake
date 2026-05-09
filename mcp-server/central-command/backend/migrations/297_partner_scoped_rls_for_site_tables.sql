-- Migration 297: tenant_partner_isolation RLS on site-scoped tables.
--
-- Round-table 2026-05-09 partner-portal runtime adversarial audit
-- (audit/coach-partner-portal-runtime-audit-2026-05-09.md, P1-1).
--
-- ROOT CAUSE: partner-portal endpoints in partners.py defend cross-
-- partner data leakage via 65 explicit `WHERE s.partner_id = $1`
-- filters in application code. There is NO database-level last-line
-- defense for partner-scoped reads. A direct psql probe with
-- `app.is_admin='false'` + `app.current_partner_id=<X>` returns
-- partner-Y's site rows. Cross-partner barrier is 100% code; one
-- forgotten WHERE filter = silent cross-partner leak.
--
-- This is the exact mirror of the RT33 P1 client-portal regression
-- class: pre-mig-278, every client_portal endpoint defended via
-- code-only `client_org_id` filters. Mig 278 added the
-- `tenant_org_isolation` policy as DB-level last-line defense via
-- `rls_site_belongs_to_current_org(site_id::text)`. The partner side
-- needs the same posture: a parallel `tenant_partner_isolation`
-- policy keyed off `app.current_partner_id`.
--
-- FIX: add a parallel `tenant_partner_isolation` policy that admits
-- rows whose site rolls up to the partner currently set via SET
-- LOCAL app.current_partner_id. Composes with existing `admin_bypass`
-- + `tenant_isolation` + `tenant_org_isolation` (Postgres ORs
-- permissive policies — any matching policy admits). The existing
-- per-site + per-org flows continue unchanged.
--
-- Posture: this does NOT broaden access. A partner_user session that
-- sets app.current_partner_id=X via partner_connection sees ONLY rows
-- belonging to sites with sites.partner_id=X. Cross-partner leak is
-- impossible because the policy joins through sites.partner_id, which
-- is the same key the application-layer WHERE filters already use.
--
-- Performance: the policy uses EXISTS over `sites` which has an index
-- on `partner_id` (idx_sites_partner_id, mig 003). Sub-millisecond
-- impact per row; STABLE marker lets the planner hoist the call once
-- per site_id seen in the query.
--
-- Tables in scope (per audit P1-1, 2026-05-09):
--   sites, site_appliances, compliance_bundles, incidents,
--   execution_telemetry, discovered_assets, site_credentials,
--   discovery_scans, admin_orders.
--
-- Of those, 6 have existing RLS enabled (mig 078): site_appliances,
-- compliance_bundles, incidents, execution_telemetry,
-- site_credentials, admin_orders. The partner-isolation policy is
-- applied to those 6 here. The remaining 3 (sites, discovered_assets,
-- discovery_scans) DO NOT have RLS enabled — enabling RLS on `sites`
-- in particular is high-risk (60+ call sites in admin_connection
-- contexts) and is deferred to a dedicated migration with a full
-- regression sweep. The CI gate `test_partner_endpoints_filter_partner_id`
-- (P1-2 sibling) covers these 3 at the source-code level until DB
-- coverage lands.

-- ─── Helper function: admit-by-partner ────────────────────────────
-- Mirrors `rls_site_belongs_to_current_org` from mig 278 but keys
-- on sites.partner_id instead of sites.client_org_id. STABLE so the
-- planner can hoist the call across rows of the same query.

CREATE OR REPLACE FUNCTION rls_site_belongs_to_current_partner(p_site_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    SELECT EXISTS (
        SELECT 1 FROM sites s
         WHERE s.site_id = p_site_id
           AND s.partner_id::text = current_setting('app.current_partner_id', true)
    );
$$;

COMMENT ON FUNCTION rls_site_belongs_to_current_partner(TEXT) IS
'Helper for tenant_partner_isolation RLS policies. Returns true iff '
'the given site_id rolls up to the partner currently set via SET '
'LOCAL app.current_partner_id. Used by partner_connection-flow '
'partner-portal queries. STABLE so the planner can hoist the call '
'across rows of the same query. NEVER use this with empty/NULL '
'current_partner_id — the policy short-circuits via the IS NOT NULL '
'guard at the policy level so the function never gets a no-op call.';

-- ─── Apply tenant_partner_isolation to site-scoped tables ─────────
-- Idempotent via DROP IF EXISTS first. Tables enumerated here are
-- exactly the audit P1-1 list intersected with mig-078 tables that
-- have RLS enabled. Tables without RLS skip with a NOTICE.

DO $$
DECLARE
    t TEXT;
    site_tables TEXT[] := ARRAY[
        'site_appliances',
        'compliance_bundles',
        'incidents',
        'execution_telemetry',
        'site_credentials',
        'admin_orders'
    ];
    rls_enabled BOOLEAN;
BEGIN
    FOREACH t IN ARRAY site_tables LOOP
        -- Skip if the table doesn't exist in this DB (e.g. partial
        -- checkout / older schema in dev).
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relname = t AND n.nspname = 'public'
        ) THEN
            RAISE NOTICE 'mig 297: table % missing, skipping', t;
            CONTINUE;
        END IF;

        -- Confirm RLS is enabled. If not, skip with explicit NOTICE —
        -- adding a policy without ENABLE is a silent no-op, and we
        -- want the operator to see the gap clearly.
        SELECT relrowsecurity INTO rls_enabled
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = t AND n.nspname = 'public';
        IF NOT rls_enabled THEN
            RAISE NOTICE 'mig 297: table % has no RLS — skipping policy add. '
                'Defense at app layer only until table is RLS-enabled.', t;
            CONTINUE;
        END IF;

        EXECUTE format(
            'DROP POLICY IF EXISTS tenant_partner_isolation ON %I',
            t
        );
        EXECUTE format(
            $f$CREATE POLICY tenant_partner_isolation ON %I FOR ALL
                USING (
                    current_setting('app.current_partner_id', true) IS NOT NULL
                    AND current_setting('app.current_partner_id', true) <> ''
                    AND rls_site_belongs_to_current_partner(site_id::text)
                )$f$,
            t
        );
        RAISE NOTICE 'mig 297: tenant_partner_isolation applied to %', t;
    END LOOP;
END $$;

-- ─── Audit log entry ──────────────────────────────────────────────
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_297_partner_scoped_rls',
    'tenant_partner_isolation',
    'jeff',
    jsonb_build_object(
        'migration', '297',
        'reason', 'Partner-portal runtime adversarial audit P1-1: cross-partner barrier was 100% application code (65 explicit WHERE filters). Direct psql probe with app.is_admin=false + app.current_partner_id=X returned partner-Y rows. Mirror of mig 278 client-org pattern: parallel tenant_partner_isolation policy keyed via rls_site_belongs_to_current_partner() helper. Applied to 6 site-scoped tables already RLS-enabled (mig 078). Deferred: sites, discovered_assets, discovery_scans (no RLS today; high-risk to enable on sites without dedicated regression sweep). CI gate test_partner_endpoints_filter_partner_id covers source-level until DB coverage lands.',
        'audit_ref', 'audit/coach-partner-portal-runtime-audit-2026-05-09.md P1-1',
        'tables_covered', jsonb_build_array(
            'site_appliances', 'compliance_bundles', 'incidents',
            'execution_telemetry', 'site_credentials', 'admin_orders'
        ),
        'tables_deferred', jsonb_build_array(
            'sites', 'discovered_assets', 'discovery_scans'
        )
    ),
    NOW()
);
