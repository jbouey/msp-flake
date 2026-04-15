-- Migration 216: roll back migration 164's global WinRM kill-switch
--
-- Migration 164 (applied 2026-04-13) marked 11 Windows check types as
-- is_monitoring_only=true to stop a 3,194-failure WinRM 401 cascade that
-- was poisoning the Flywheel learning pipeline. It worked (70 fails/day →
-- 3 fails/day on Apr 14) but at an unacceptable cost: EVERY customer's
-- Windows hosts lost auto-remediation for these check types, even
-- customers whose WinRM was perfectly healthy. That's a contract
-- regression masking one lab appliance's issue.
--
-- Migration 215 introduced v_appliance_winrm_circuit — a per-(site,
-- appliance) dynamic circuit breaker derived from execution_telemetry
-- over a 30-minute window. The dispatcher consults it before sending
-- WinRM runbooks; actively-failing appliances fall back to monitoring
-- locally and auto-recover on the first successful execution.
-- assertions.py substrate invariant `winrm_circuit_open` surfaces open
-- circuits on the dashboard so operators see the scoped failure
-- instead of a silent fleet-wide regression.
--
-- This migration flips the 11 Windows check types BACK to
-- is_monitoring_only=false. The circuit handles the blast radius.

BEGIN;

UPDATE check_type_registry
   SET is_monitoring_only = false,
       updated_at = NOW()
 WHERE check_name IN (
    'windows_update',
    'defender_exclusions',
    'registry_run_persistence',
    'screen_lock_policy',
    'bitlocker_status',
    'audit_policy',
    'windows_audit_policy',
    'rogue_scheduled_tasks',
    'windows_defender',
    'smb_signing',
    'firewall_status'
 )
 AND platform = 'windows';

INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system',
    'CHECK_REMEDIATION_RESTORED',
    'windows_runbooks',
    jsonb_build_object(
        'reason', 'Global kill-switch replaced by per-appliance circuit breaker (migration 215)',
        'supersedes_migration', '164',
        'circuit_view', 'v_appliance_winrm_circuit',
        'substrate_invariant', 'winrm_circuit_open',
        'phase', 'session_207_winrm_circuit_rollback',
        'affected_check_types', jsonb_build_array(
            'windows_update','defender_exclusions','registry_run_persistence',
            'screen_lock_policy','bitlocker_status','audit_policy',
            'windows_audit_policy','rogue_scheduled_tasks','windows_defender',
            'smb_signing','firewall_status'
        )
    ),
    NOW()
);

COMMIT;
