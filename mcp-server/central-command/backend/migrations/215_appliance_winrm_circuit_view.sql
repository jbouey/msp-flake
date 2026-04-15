-- Migration 215: per-appliance WinRM circuit breaker (view)
--
-- Replaces the global `is_monitoring_only=true` kill-switch pattern that
-- migration 164 used for 11 Windows check types. The global flag halted
-- remediation for every customer's Windows hosts regardless of whether
-- THEIR specific WinRM was healthy — a contract regression to mask one
-- lab appliance's issue.
--
-- Shape: a per-(site_id, appliance_id) circuit state derived from
-- execution_telemetry over a rolling 30-minute window. Zero new
-- application-writable state; the view is purely derived.
--
-- State 'open' when:
--   - ≥3 WinRM-flavor failures ("winrm", "401", "TLS pin") in the window
--   - AND zero successful Windows runbook executions in the window
-- Auto-closes on the next successful execution.
--
-- The dispatcher (agent_api.py) consults this view and treats an open
-- circuit identically to the monitoring-only fallback for WinRM-touching
-- check types. Customers with healthy WinRM keep remediating.
--
-- A substrate invariant (assertions.py `winrm_circuit_open`) opens a
-- sev2 violation per open circuit and auto-resolves when it closes —
-- the operator sees the incident in the dashboard with a remediation
-- tooltip rather than having to know to look.

BEGIN;

CREATE OR REPLACE VIEW v_appliance_winrm_circuit AS
SELECT et.site_id,
       et.appliance_id,
       COUNT(*) FILTER (WHERE NOT et.success AND (
                 et.error_message ILIKE '%winrm%'
              OR et.error_message ILIKE '%401%'
              OR et.error_message ILIKE '%TLS pin%'
            )) AS recent_winrm_fails,
       COUNT(*) FILTER (WHERE et.success) AS recent_successes,
       MAX(et.completed_at) FILTER (WHERE et.success) AS last_success_at,
       MAX(et.completed_at) FILTER (WHERE NOT et.success AND (
                 et.error_message ILIKE '%winrm%'
              OR et.error_message ILIKE '%401%'
              OR et.error_message ILIKE '%TLS pin%'
            )) AS last_fail_at,
       CASE
           WHEN COUNT(*) FILTER (WHERE NOT et.success AND (
                     et.error_message ILIKE '%winrm%'
                  OR et.error_message ILIKE '%401%'
                  OR et.error_message ILIKE '%TLS pin%'
               )) >= 3
             AND COUNT(*) FILTER (WHERE et.success) = 0
           THEN 'open'
           ELSE 'closed'
       END AS circuit_state
  FROM execution_telemetry et
 WHERE et.created_at > NOW() - INTERVAL '30 minutes'
   AND (et.runbook_id LIKE 'RB-WIN%' OR et.runbook_id LIKE 'L1-WIN%')
 GROUP BY et.site_id, et.appliance_id;

COMMENT ON VIEW v_appliance_winrm_circuit IS
    'Per-appliance WinRM circuit breaker. Replaces the global '
    'is_monitoring_only kill-switch pattern (migration 164) with a '
    'per-(site,appliance) dynamic gate derived from execution_telemetry '
    'over a 30-minute window. Open circuits auto-close on first success.';

COMMIT;
