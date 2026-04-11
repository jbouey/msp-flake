-- Migration 155: Trigger to sync execution_telemetry → incident_remediation_steps
-- Reason: Execution telemetry arrives from the Go daemon AFTER L1/L2 orders execute.
-- Without this trigger, remediation_steps only shows "order_created" but never the
-- actual execution result (success/failure/duration). The flywheel auto-candidate
-- scan needs execution outcomes to promote L2→L1.
--
-- Session 204: was applied manually on VPS. This migration formalizes it.

-- 1. Create the trigger function
CREATE OR REPLACE FUNCTION sync_telemetry_to_remediation_steps()
RETURNS TRIGGER AS $$
BEGIN
    -- Only sync if we have an incident_id to link to
    IF NEW.incident_id IS NOT NULL THEN
        INSERT INTO incident_remediation_steps (
            incident_id, tier, runbook_id, result, confidence, created_at
        ) VALUES (
            NEW.incident_id,
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
