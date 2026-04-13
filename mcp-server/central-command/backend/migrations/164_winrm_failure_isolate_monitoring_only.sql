-- Migration 164: Temporarily mark Windows checks affected by WinRM 401
-- cascade as monitoring_only.
--
-- Session 205 audit found 3,194 entries in v_learning_failures, all from
-- the same root error: "create shell: http response error: 401 - invalid
-- content type". Every Windows runbook execution is failing at the WinRM
-- shell-create step, indicating credential delivery or authentication
-- breakage on the appliance side.
--
-- Until the WinRM auth is restored on the appliance (Phase 3 ops work),
-- mark these checks monitoring_only so:
--   1. The appliance still reports the drift state (we keep observability)
--   2. No remediation runbook is dispatched (we stop the failure cascade)
--   3. L2 promotion learns from clean data instead of these failures
--
-- ROLLBACK: when WinRM is fixed, set is_monitoring_only=false for these
-- checks. The rollback SQL is included as a comment block at the bottom.

BEGIN;

UPDATE check_type_registry
   SET is_monitoring_only = true,
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

-- Audit log so we can prove this was an intentional ops decision
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system',
    'CHECK_MARKED_MONITORING_ONLY',
    'windows_runbooks',
    jsonb_build_object(
        'reason', 'WinRM 401 cascade — 3194 failures over 14d, root cause under investigation',
        'phase', 'session_205_phase_3',
        'rollback_when', 'WinRM auth restored on appliance'
    ),
    NOW()
);

COMMIT;

-- ROLLBACK (when WinRM is restored):
--
-- BEGIN;
-- UPDATE check_type_registry
--    SET is_monitoring_only = false, updated_at = NOW()
--  WHERE check_name IN (
--     'windows_update','defender_exclusions','registry_run_persistence',
--     'screen_lock_policy','bitlocker_status','audit_policy',
--     'windows_audit_policy','rogue_scheduled_tasks','windows_defender',
--     'smb_signing','firewall_status'
--  ) AND platform = 'windows';
-- INSERT INTO admin_audit_log (username, action, target, details, created_at)
-- VALUES ('system','CHECK_REMEDIATION_RESTORED','windows_runbooks',
--         '{"phase":"session_205_phase_3_rollback"}'::jsonb, NOW());
-- COMMIT;
