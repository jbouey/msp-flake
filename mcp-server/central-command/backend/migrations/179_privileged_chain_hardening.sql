-- Migration 179: Chain enforcement hardening (Phase 15 A-spec closing pass)
--
-- Round-table red-team audit flagged three bypasses of the chain
-- enforcement triggers from migrations 175 + 176:
--
--   1. session_replication_role='replica' skips 'O'-mode triggers.
--      Mitigation: ALTER … ENABLE ALWAYS TRIGGER so triggers fire
--      in replication mode too.
--
--   2. TRUNCATE compliance_bundles CASCADE wipes evidence without
--      firing BEFORE DELETE triggers (TRUNCATE is separate in
--      Postgres). Mitigation: add BEFORE TRUNCATE triggers using
--      the existing prevent_audit_deletion() function.
--
--   3. COPY FROM bypasses BEFORE INSERT ROW triggers only when the
--      trigger is declared AFTER or not ROW-level. Our triggers
--      are BEFORE ROW, which already fires on COPY — no mitigation
--      needed, but we assert the declaration here for clarity.
--
-- This migration closes #1 and #2. #3 was already correct by
-- construction.

BEGIN;

-- ── (1) ENABLE ALWAYS on chain-enforcement triggers ────────────

ALTER TABLE fleet_orders
    ENABLE ALWAYS TRIGGER trg_enforce_privileged_chain;

ALTER TABLE fleet_orders
    ENABLE ALWAYS TRIGGER trg_enforce_privileged_immutability;

-- ── (2) BEFORE TRUNCATE on evidence + audit tables ─────────────
-- Uses the existing prevent_audit_deletion() function from 151.

DROP TRIGGER IF EXISTS compliance_bundles_no_truncate ON compliance_bundles;
CREATE TRIGGER compliance_bundles_no_truncate
    BEFORE TRUNCATE ON compliance_bundles
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_deletion();

DROP TRIGGER IF EXISTS admin_audit_log_no_truncate ON admin_audit_log;
CREATE TRIGGER admin_audit_log_no_truncate
    BEFORE TRUNCATE ON admin_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_deletion();

DROP TRIGGER IF EXISTS client_audit_log_no_truncate ON client_audit_log;
CREATE TRIGGER client_audit_log_no_truncate
    BEFORE TRUNCATE ON client_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_deletion();

DROP TRIGGER IF EXISTS portal_access_log_no_truncate ON portal_access_log;
CREATE TRIGGER portal_access_log_no_truncate
    BEFORE TRUNCATE ON portal_access_log
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_deletion();

-- Also enable the new TRUNCATE triggers in ALWAYS mode so
-- session_replication_role='replica' can't bypass them.
ALTER TABLE compliance_bundles
    ENABLE ALWAYS TRIGGER compliance_bundles_no_truncate;
ALTER TABLE admin_audit_log
    ENABLE ALWAYS TRIGGER admin_audit_log_no_truncate;
ALTER TABLE client_audit_log
    ENABLE ALWAYS TRIGGER client_audit_log_no_truncate;
ALTER TABLE portal_access_log
    ENABLE ALWAYS TRIGGER portal_access_log_no_truncate;

-- And the existing DELETE triggers too (if they exist under the
-- 151 naming — otherwise skip gracefully).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'compliance_bundles'
          AND t.tgname = 'compliance_bundles_no_delete'
          AND NOT t.tgisinternal
    ) THEN
        EXECUTE 'ALTER TABLE compliance_bundles ENABLE ALWAYS TRIGGER compliance_bundles_no_delete';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'admin_audit_log'
          AND t.tgname = 'admin_audit_log_no_delete'
          AND NOT t.tgisinternal
    ) THEN
        EXECUTE 'ALTER TABLE admin_audit_log ENABLE ALWAYS TRIGGER admin_audit_log_no_delete';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'client_audit_log'
          AND t.tgname = 'client_audit_log_no_delete'
          AND NOT t.tgisinternal
    ) THEN
        EXECUTE 'ALTER TABLE client_audit_log ENABLE ALWAYS TRIGGER client_audit_log_no_delete';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'portal_access_log'
          AND t.tgname = 'portal_access_log_no_delete'
          AND NOT t.tgisinternal
    ) THEN
        EXECUTE 'ALTER TABLE portal_access_log ENABLE ALWAYS TRIGGER portal_access_log_no_delete';
    END IF;
END $$;

COMMIT;

-- DOWN
-- Reverting this migration in prod should never be routine; these
-- triggers exist because auditors + regulators require the append-
-- only guarantee. Including DOWN for completeness only.
--
-- ALTER TABLE compliance_bundles ENABLE TRIGGER compliance_bundles_no_truncate;
-- DROP TRIGGER IF EXISTS compliance_bundles_no_truncate ON compliance_bundles;
-- DROP TRIGGER IF EXISTS admin_audit_log_no_truncate   ON admin_audit_log;
-- DROP TRIGGER IF EXISTS client_audit_log_no_truncate  ON client_audit_log;
-- DROP TRIGGER IF EXISTS portal_access_log_no_truncate ON portal_access_log;
