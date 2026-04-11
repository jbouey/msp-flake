-- Migration 155: Trigger to sync execution_telemetry → incident_remediation_steps
-- Reason: Execution telemetry arrives from the Go daemon AFTER L1/L2 orders execute.
-- Without this trigger, remediation_steps only shows "order_created" but never the
-- actual execution result (success/failure/duration). The flywheel auto-candidate
-- scan needs execution outcomes to promote L2→L1.
--
-- Session 204: was applied manually on VPS. This migration formalizes it.

-- 1. Create the trigger function
-- IMPORTANT: incident_id from daemon may be a drift report ID (not UUID).
-- Only sync when incident_id is a valid UUID that exists in incidents table.
CREATE OR REPLACE FUNCTION sync_telemetry_to_remediation_steps()
RETURNS TRIGGER AS $$
DECLARE
    v_uuid UUID;
BEGIN
    -- Only sync if we have an incident_id that is a valid UUID
    IF NEW.incident_id IS NOT NULL THEN
        BEGIN
            v_uuid := NEW.incident_id::UUID;
        EXCEPTION WHEN invalid_text_representation THEN
            -- Not a UUID (e.g. "drift-192.168.88.251-windows_update-...")
            RETURN NEW;
        END;

        -- Only insert if the incident actually exists
        IF EXISTS (SELECT 1 FROM incidents WHERE id = v_uuid) THEN
            INSERT INTO incident_remediation_steps (
                incident_id, tier, runbook_id, result, confidence, created_at
            ) VALUES (
                v_uuid,
                COALESCE(NEW.resolution_level, 'L1'),
                NEW.runbook_id,
                CASE
                    WHEN NEW.success = true THEN 'executed_success'
                    WHEN NEW.success = false THEN 'executed_failure'
                    ELSE 'executed_unknown'
                END,
                NEW.confidence,
                COALESCE(NEW.completed_at, NOW())
            )
            ON CONFLICT DO NOTHING;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Drop if exists (idempotent for VPS where it was applied manually)
DROP TRIGGER IF EXISTS trg_sync_telemetry_to_remediation ON execution_telemetry;

-- 3. Create the trigger
CREATE TRIGGER trg_sync_telemetry_to_remediation
    AFTER INSERT ON execution_telemetry
    FOR EACH ROW
    EXECUTE FUNCTION sync_telemetry_to_remediation_steps();
