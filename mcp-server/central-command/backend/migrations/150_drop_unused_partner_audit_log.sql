-- Migration 150: drop the unused partner_audit_log table
--
-- Migration 149 initially created both `partner_audit_log` and
-- `client_audit_log`, assuming neither portal had an existing audit
-- infrastructure. During the Session 203 implementation we discovered
-- that `partner_activity_log` + `partner_activity_logger.py` already
-- handle partner auditing (used by oauth_login, learning, admin
-- partner management). The correct fix is to EXTEND that existing
-- infrastructure rather than create a parallel table — which is what
-- Session 203 ended up doing (new event types added to
-- PartnerEventType enum).
--
-- This migration removes the unused `partner_audit_log` table so there
-- is a single source of truth for partner audit events. `client_audit_log`
-- stays — the client portal genuinely had no prior audit infra and now
-- uses the new table via the `_audit_client_action()` helper in
-- client_portal.py.
--
-- Safe to run: `partner_audit_log` is still empty (was created minutes
-- before this drop). No data loss.

BEGIN;

DROP TRIGGER IF EXISTS enforce_partner_audit_append_only ON partner_audit_log;
DROP TABLE IF EXISTS partner_audit_log;

COMMIT;
