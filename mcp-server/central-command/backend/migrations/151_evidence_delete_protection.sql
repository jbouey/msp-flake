-- Migration 151: DELETE protection on evidence + audit tables.
--
-- Adversarial audit finding: UPDATE triggers existed but DELETE was unprotected.
-- A compromised superuser could silently erase evidence and audit trails.
-- This migration closes that gap with explicit DELETE triggers.
--
-- Also: fleet_orders immutability after completion (prevents retroactive
-- parameter tampering on completed orders).
--
-- HIPAA: 164.312(b) Audit Controls, 164.316(b)(2)(i) 6-year retention.

-- Reusable function: block DELETE on append-only tables.
CREATE OR REPLACE FUNCTION prevent_audit_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'DELETE denied on append-only table %: attestation integrity requires immutable records', TG_TABLE_NAME;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- === compliance_bundles: block DELETE (evidence is immutable) ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'compliance_bundles_no_delete'
    ) THEN
        CREATE TRIGGER compliance_bundles_no_delete
            BEFORE DELETE ON compliance_bundles
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

-- === admin_audit_log: block DELETE ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'admin_audit_log_no_delete'
    ) THEN
        CREATE TRIGGER admin_audit_log_no_delete
            BEFORE DELETE ON admin_audit_log
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

-- === client_audit_log: block DELETE ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'client_audit_log_no_delete'
    ) THEN
        CREATE TRIGGER client_audit_log_no_delete
            BEFORE DELETE ON client_audit_log
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

-- === incident_remediation_steps: block DELETE + UPDATE ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'remediation_steps_no_delete'
    ) THEN
        CREATE TRIGGER remediation_steps_no_delete
            BEFORE DELETE ON incident_remediation_steps
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'remediation_steps_no_update'
    ) THEN
        CREATE TRIGGER remediation_steps_no_update
            BEFORE UPDATE ON incident_remediation_steps
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

-- === fleet_orders: block UPDATE on completed orders (immutability) ===
CREATE OR REPLACE FUNCTION prevent_completed_order_modification()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'completed' THEN
        RAISE EXCEPTION 'UPDATE denied on completed fleet order %: signed order parameters are immutable after completion', OLD.id;
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'fleet_orders_immutable_completed'
    ) THEN
        CREATE TRIGGER fleet_orders_immutable_completed
            BEFORE UPDATE ON fleet_orders
            FOR EACH ROW EXECUTE FUNCTION prevent_completed_order_modification();
    END IF;
END $$;

-- === fleet_order_completions: block DELETE (audit trail of order execution) ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'fleet_completions_no_delete'
    ) THEN
        CREATE TRIGGER fleet_completions_no_delete
            BEFORE DELETE ON fleet_order_completions
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;

-- === portal_access_log: block DELETE (already has UPDATE trigger from mig 084) ===
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'portal_access_log_no_delete'
    ) THEN
        CREATE TRIGGER portal_access_log_no_delete
            BEFORE DELETE ON portal_access_log
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();
    END IF;
END $$;
