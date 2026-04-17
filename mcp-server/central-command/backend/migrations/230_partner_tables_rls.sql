-- Migration 230: RLS policies on partner_* tables
--
-- Consultant call-out: app.is_admin='true' default is a systemic trust
-- violation. This migration does NOT flip the database-level default
-- (that's a multi-session audit of every route to confirm no caller uses
-- raw get_pool() bypassing admin/tenant context). What it DOES:
--
--   1. ENABLES RLS on partners, partner_users, partner_invoices,
--      partner_agreements, partner_invites.
--   2. Adds policies keyed on `app.current_partner` GUC so a partner-scoped
--      connection (set by future require_partner middleware hardening) can
--      see only its own rows.
--   3. Keeps the admin-bypass clause (app.is_admin='true') in every policy
--      — safe no-op today because that GUC is true by default. The day we
--      flip the DB-level default, these policies become the enforcement
--      floor without additional DDL churn.
--
-- Why RLS even while admin='true' everywhere: it's the explicit model a
-- Postgres reviewer / auditor will inspect. "The table is marked RLS; the
-- policy allows admin bypass; when we remove bypass we already have the
-- rules in place." The alternative — leaving the tables with no policies —
-- means a migration flip has to BOTH change the GUC AND add policies, doubling
-- the surface area of the riskiest change in the project.

BEGIN;

-- Helper: read partner_id from GUC, coerce to UUID, fallback NULL when unset
CREATE OR REPLACE FUNCTION current_partner_uuid()
RETURNS UUID LANGUAGE plpgsql STABLE AS $$
DECLARE
    raw TEXT;
BEGIN
    raw := current_setting('app.current_partner', true);
    IF raw IS NULL OR raw = '' THEN
        RETURN NULL;
    END IF;
    RETURN raw::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION current_partner_uuid() IS
    'Reads app.current_partner GUC. Set by require_partner-aware connection '
    'helpers in Batch C. Returns NULL when unset or malformed.';


-- ─── partners ───────────────────────────────────────────────────────
ALTER TABLE partners ENABLE ROW LEVEL SECURITY;
ALTER TABLE partners FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS partners_admin_bypass ON partners;
CREATE POLICY partners_admin_bypass ON partners
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS partners_own_row ON partners;
CREATE POLICY partners_own_row ON partners
    USING (id = current_partner_uuid())
    WITH CHECK (id = current_partner_uuid());


-- ─── partner_users ──────────────────────────────────────────────────
ALTER TABLE partner_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_users FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS partner_users_admin_bypass ON partner_users;
CREATE POLICY partner_users_admin_bypass ON partner_users
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS partner_users_own_partner ON partner_users;
CREATE POLICY partner_users_own_partner ON partner_users
    USING (partner_id = current_partner_uuid())
    WITH CHECK (partner_id = current_partner_uuid());


-- ─── partner_invoices ───────────────────────────────────────────────
ALTER TABLE partner_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_invoices FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS partner_invoices_admin_bypass ON partner_invoices;
CREATE POLICY partner_invoices_admin_bypass ON partner_invoices
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS partner_invoices_own_partner ON partner_invoices;
CREATE POLICY partner_invoices_own_partner ON partner_invoices
    USING (partner_id = current_partner_uuid())
    WITH CHECK (partner_id = current_partner_uuid());


-- ─── partner_agreements (new in 228) ────────────────────────────────
ALTER TABLE partner_agreements ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_agreements FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS partner_agreements_admin_bypass ON partner_agreements;
CREATE POLICY partner_agreements_admin_bypass ON partner_agreements
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS partner_agreements_own_partner ON partner_agreements;
CREATE POLICY partner_agreements_own_partner ON partner_agreements
    USING (partner_id = current_partner_uuid())
    WITH CHECK (partner_id = current_partner_uuid());


-- ─── partner_invites (new in 229) ───────────────────────────────────
-- Public consumption path (clinic clicking the invite) uses admin_connection
-- since the clinic isn't authenticated as a partner. Partner-scoped
-- CREATE/REVOKE uses the partner policy.
ALTER TABLE partner_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_invites FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS partner_invites_admin_bypass ON partner_invites;
CREATE POLICY partner_invites_admin_bypass ON partner_invites
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS partner_invites_own_partner ON partner_invites;
CREATE POLICY partner_invites_own_partner ON partner_invites
    USING (partner_id = current_partner_uuid())
    WITH CHECK (partner_id = current_partner_uuid());


-- ─── Sanity: verify ENABLE succeeded ─────────────────────────────────
DO $$
DECLARE
    missing TEXT[];
BEGIN
    SELECT ARRAY_AGG(tbl) INTO missing FROM (
        SELECT unnest(ARRAY[
            'partners','partner_users','partner_invoices',
            'partner_agreements','partner_invites'
        ]) AS tbl
    ) t
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_tables
         WHERE schemaname = 'public' AND tablename = t.tbl AND rowsecurity = true
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'RLS not enabled on tables: %', missing;
    END IF;
END $$;


INSERT INTO schema_migrations (version, applied_at, checksum)
VALUES ('230_partner_tables_rls', NOW(), 'n/a')
ON CONFLICT (version) DO NOTHING;

COMMIT;
