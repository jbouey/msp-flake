-- Migration 161: Restore DELETE on fleet_order_completions
--
-- Context: Migration 151 added a `prevent_audit_deletion` BEFORE DELETE
-- trigger to several tables for HIPAA audit immutability. The trigger
-- was applied to `fleet_order_completions` too, but THAT TABLE IS
-- OPERATIONAL STATE — it tracks which appliances have successfully
-- (or failed to) acknowledged a fleet order. Not audit.
--
-- `get_fleet_orders_for_appliance` (fleet_updates.py) relies on DELETE
-- to auto-expire stale 'failed' completions after 1h so orders retry,
-- and to reclaim 'skipped' rows when an appliance regresses versions.
-- Migration 151 silently broke both mechanisms. The consequence was
-- the 2026-04-12 90-min fleet-order delivery outage — a missing
-- migration 160 set off the INITIAL transaction-abort error, but even
-- after migration 160 landed the DELETE trigger continued to poison
-- every checkin's outer transaction in get_fleet_orders_for_appliance.
--
-- Audit immutability is still enforced on the tables that genuinely
-- hold audit records: compliance_bundles, admin_audit_log,
-- client_audit_log, incident_remediation_steps, portal_access_log.
-- fleet_order_completions is explicitly excluded — it's a
-- delivery-tracking state table, not a compliance-evidence table.
--
-- Belt-and-suspenders: fleet_updates.py now also wraps each DELETE in
-- a savepoint (commit ec0a9ed). If any future DELETE restriction is
-- added (expected or otherwise), the savepoint isolates the failure
-- instead of poisoning the checkin transaction.

BEGIN;

DROP TRIGGER IF EXISTS fleet_completions_no_delete ON fleet_order_completions;

-- Sanity: confirm the table exists and we haven't broken anything
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'fleet_order_completions'
    ) THEN
        RAISE EXCEPTION 'fleet_order_completions table not found — migration 161 cannot proceed';
    END IF;
END $$;

COMMIT;

-- DOWN
-- Recreate the trigger if this migration needs to be reverted.
-- Note: this reintroduces the bug that blocks fleet-order retry and
-- version-regression re-delivery, so only revert if you have an
-- alternative retry mechanism in place.
-- CREATE TRIGGER fleet_completions_no_delete
--     BEFORE DELETE ON fleet_order_completions
--     FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
