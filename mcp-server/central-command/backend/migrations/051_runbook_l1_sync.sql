-- Migration 051: Sync runbooks table with L1 rules + create runbook_id_mapping
--
-- Problem: Runbook Library shows 51 entries but Learning Loop shows 81 L1 rules.
-- The gap comes from:
--   1. Migration 050 references runbook IDs (LIN-DISK-001, RB-WIN-SEC-022, etc.)
--      that were never created in the runbooks table
--   2. Promoted L2→L1 patterns create l1_rules entries but no runbooks entries
--   3. ESCALATE rules have no runbook representation
--   4. runbook_id_mapping table (used by runbook_config.py) was never created
--
-- Fixes:
--   A. Create missing canonical runbooks referenced by L1 rules
--   B. Create runbook_id_mapping for telemetry correlation
--   C. Insert promoted L2→L1 patterns into runbooks as auto-promoted entries
--   D. Create ESCALATE placeholder runbooks for L3 escalation rules

BEGIN;

-- ============================================================================
-- A. Create missing canonical runbooks referenced by L1 rules (migration 050)
-- ============================================================================

-- Linux missing runbooks
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-DISK-001', 'Disk Space Remediation', 'Clean up disk space on Linux systems (temp files, old logs, package cache)', 'storage', 'linux_disk_space', 'high', false, ARRAY['164.312(b)', '164.308(a)(7)(ii)(A)'], '[]'::jsonb),
('LIN-UPGRADES-001', 'Unattended Upgrades Configuration', 'Ensure unattended-upgrades is installed and configured for security updates', 'patching', 'linux_unattended_upgrades', 'high', true, ARRAY['164.308(a)(5)(ii)(B)', '164.312(c)(1)'], '[]'::jsonb),
('LIN-CERT-001', 'Certificate Expiry Remediation', 'Detect and alert on expiring TLS/SSL certificates', 'security', 'linux_cert_expiry', 'high', false, ARRAY['164.312(e)(1)', '164.312(a)(2)(iv)'], '[]'::jsonb),
('LIN-KERN-001', 'Kernel Parameters Hardening', 'Ensure sysctl parameters are set per HIPAA security baseline', 'security', 'linux_kernel_params', 'medium', false, ARRAY['164.312(a)(1)', '164.312(e)(1)'], '[]'::jsonb),
('LIN-LOG-001', 'Log Forwarding Configuration', 'Ensure log forwarding (rsyslog/journald) is configured and active', 'audit', 'linux_log_forwarding', 'high', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)'], '[]'::jsonb),
('LIN-SUID-001', 'SUID Binary Audit', 'Audit SUID/SGID binaries against known-good baseline', 'permissions', 'linux_suid_binaries', 'medium', false, ARRAY['164.312(a)(1)'], '[]'::jsonb),
('LIN-CRON-001', 'Cron Job Review', 'Audit cron jobs for unauthorized or suspicious entries', 'audit', 'linux_cron_review', 'medium', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)'], '[]'::jsonb),
('LIN-NTP-001', 'NTP Sync Remediation', 'Ensure chronyd/ntpd is running and time is synchronized', 'services', 'linux_ntp_sync', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Windows missing runbooks (referenced by migration 050 L1 rules)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('RB-WIN-SEC-007', 'SMB Signing Enforcement', 'Ensure SMB signing is required on all connections', 'security', 'smb_signing', 'high', false, ARRAY['164.312(e)(1)', '164.312(c)(1)'], '[]'::jsonb),
('RB-WIN-SEC-016', 'Screen Lock Policy Enforcement', 'Enforce screen lock timeout and password requirement via GPO', 'security', 'screen_lock_policy', 'medium', false, ARRAY['164.312(a)(2)(iii)', '164.312(d)'], '[]'::jsonb),
('RB-WIN-SEC-017', 'Defender Exclusion Audit', 'Audit Windows Defender exclusions for unauthorized entries', 'security', 'defender_exclusions', 'medium', false, ARRAY['164.308(a)(5)(ii)(B)'], '[]'::jsonb),
('RB-WIN-SEC-020', 'SMBv1 Protocol Disable', 'Ensure SMBv1 protocol is disabled for security', 'security', 'smb1_protocol', 'high', true, ARRAY['164.312(e)(1)'], '[]'::jsonb),
('RB-WIN-SEC-022', 'Password Policy Compliance', 'Enforce password complexity, length, and expiration policies', 'security', 'password_policy', 'high', false, ARRAY['164.312(d)', '164.308(a)(5)(ii)(D)'], '[]'::jsonb),
('RB-WIN-SEC-023', 'RDP NLA Enforcement', 'Ensure Network Level Authentication is required for RDP', 'security', 'rdp_nla', 'high', false, ARRAY['164.312(a)(1)', '164.312(d)'], '[]'::jsonb),
('RB-WIN-SEC-024', 'Guest Account Disable', 'Ensure Guest account is disabled', 'security', 'guest_account', 'medium', false, ARRAY['164.312(a)(1)', '164.312(d)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- L3 escalation runbooks (for ESCALATE rules — these are audit-only, no auto-remediation)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('ESC-LIN-PORTS-001', 'Open Ports Review (L3 Escalation)', 'Unexpected open ports detected — requires human review', 'network', 'linux_open_ports', 'high', false, ARRAY['164.312(e)(1)'], '[]'::jsonb),
('ESC-LIN-USERS-001', 'User Accounts Review (L3 Escalation)', 'Unauthorized user accounts detected — requires human review', 'accounts', 'linux_user_accounts', 'high', false, ARRAY['164.312(a)(1)', '164.312(d)'], '[]'::jsonb),
('ESC-WIN-ADMIN-001', 'Rogue Admin Review (L3 Escalation)', 'Unauthorized admin accounts detected — requires human review', 'accounts', 'rogue_admin_users', 'critical', false, ARRAY['164.312(a)(1)', '164.312(d)'], '[]'::jsonb),
('ESC-WIN-TASKS-001', 'Rogue Scheduled Tasks Review (L3 Escalation)', 'Unauthorized scheduled tasks detected — requires human review', 'security', 'rogue_scheduled_tasks', 'high', false, ARRAY['164.312(a)(1)', '164.308(a)(1)(ii)(D)'], '[]'::jsonb),
('ESC-NET-PORTS-001', 'Network Unexpected Ports (L3 Escalation)', 'Unexpected network ports detected — requires human review', 'network', 'net_unexpected_ports', 'high', false, ARRAY['164.312(e)(1)'], '[]'::jsonb),
('ESC-NET-SVC-001', 'Network Service Down (L3 Escalation)', 'Expected network service unavailable — requires human review', 'network', 'net_expected_service', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb),
('ESC-NET-REACH-001', 'Host Unreachable (L3 Escalation)', 'Network host unreachable — requires human review', 'network', 'net_host_reachability', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb),
('ESC-NET-DNS-001', 'DNS Resolution Failure (L3 Escalation)', 'DNS resolution failing — requires human review', 'network', 'net_dns_resolution', 'medium', false, ARRAY['164.312(b)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Generic runbooks for common operations
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('RB-SERVICE-001', 'Service Recovery', 'Restart failed critical services', 'services', 'service_health', 'high', true, ARRAY['164.308(a)(7)(ii)(C)'], '[]'::jsonb),
('RB-PATCH-001', 'Security Patching', 'Apply pending security updates', 'patching', 'patching', 'high', true, ARRAY['164.308(a)(5)(ii)(B)'], '[]'::jsonb),
('RB-FIREWALL-001', 'Firewall Baseline Restore', 'Restore firewall ruleset to baseline configuration', 'firewall', 'firewall', 'critical', true, ARRAY['164.312(a)(1)', '164.312(e)(1)'], '[]'::jsonb),
('RB-BACKUP-001', 'Backup Verification', 'Verify and remediate backup issues', 'backup', 'backup', 'critical', true, ARRAY['164.308(a)(7)(ii)(A)', '164.310(d)(2)(iv)'], '[]'::jsonb),
('RB-CERT-001', 'Certificate Renewal', 'Renew expiring TLS/SSL certificates', 'security', 'cert_expiry', 'high', true, ARRAY['164.312(e)(1)', '164.312(a)(2)(iv)'], '[]'::jsonb),
('RB-DRIFT-001', 'Configuration Drift Restore', 'Restore NixOS configuration to baseline', 'security', 'config_drift', 'high', true, ARRAY['164.308(a)(1)(ii)(B)', '164.312(b)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- ============================================================================
-- B. Update ESCALATE rules to reference the new escalation runbooks
-- ============================================================================

UPDATE l1_rules SET runbook_id = 'ESC-LIN-PORTS-001' WHERE rule_id = 'L1-LIN-PORTS-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-LIN-USERS-001' WHERE rule_id = 'L1-LIN-USERS-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-WIN-ADMIN-001' WHERE rule_id = 'L1-WIN-ROGUE-ADMIN-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-WIN-TASKS-001' WHERE rule_id = 'L1-WIN-ROGUE-TASKS-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-NET-PORTS-001' WHERE rule_id = 'L1-NET-PORTS-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-NET-SVC-001' WHERE rule_id = 'L1-NET-SVC-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-NET-REACH-001' WHERE rule_id = 'L1-NET-REACH-001' AND runbook_id = 'ESCALATE';
UPDATE l1_rules SET runbook_id = 'ESC-NET-DNS-001' WHERE rule_id = 'L1-NET-DNS-001' AND runbook_id = 'ESCALATE';

-- ============================================================================
-- C. Create runbook_id_mapping for telemetry correlation
--    Maps agent L1 rule IDs (from execution_telemetry.runbook_id) to canonical
--    runbook IDs (in the runbooks table). Used by runbook_config.py LIST JOIN.
-- ============================================================================

CREATE TABLE IF NOT EXISTS runbook_id_mapping (
    l1_rule_id VARCHAR(255) PRIMARY KEY,
    runbook_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Populate from l1_rules: map each rule_id to its runbook_id
INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
SELECT rule_id, runbook_id
FROM l1_rules
WHERE runbook_id IS NOT NULL
  AND rule_id != runbook_id  -- skip self-referencing rules
  AND enabled = true
ON CONFLICT (l1_rule_id) DO NOTHING;

-- ============================================================================
-- D. Auto-insert promoted L2→L1 patterns into runbooks table
--    These come from l1_rules with source='promoted' (RB-AUTO-* prefix).
--    We create ONE canonical runbook per unique check_type (not per-host).
-- ============================================================================

-- Extract unique check_types from promoted rules and create runbooks for them
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps)
SELECT DISTINCT ON (check_type)
    'RB-PROMOTED-' || UPPER(check_type) as runbook_id,
    'Auto-Promoted: ' || REPLACE(REPLACE(check_type, '_', ' '), ':', ' ') as name,
    'L2→L1 promoted pattern with proven success rate. Originally resolved by LLM, now deterministic.' as description,
    CASE
        WHEN check_type LIKE '%firewall%' OR check_type LIKE '%fw%' THEN 'firewall'
        WHEN check_type LIKE '%ssh%' THEN 'ssh'
        WHEN check_type LIKE '%service%' OR check_type LIKE '%svc%' THEN 'services'
        WHEN check_type LIKE '%audit%' OR check_type LIKE '%log%' THEN 'audit'
        WHEN check_type LIKE '%defender%' OR check_type LIKE '%bitlocker%' OR check_type LIKE '%sec%' THEN 'security'
        WHEN check_type LIKE '%backup%' THEN 'backup'
        WHEN check_type LIKE '%patch%' OR check_type LIKE '%update%' THEN 'patching'
        WHEN check_type LIKE '%net%' OR check_type LIKE '%dns%' OR check_type LIKE '%smb%' THEN 'network'
        WHEN check_type LIKE '%perm%' OR check_type LIKE '%suid%' THEN 'permissions'
        WHEN check_type LIKE '%cron%' OR check_type LIKE '%kernel%' THEN 'security'
        WHEN check_type LIKE '%screen%' OR check_type LIKE '%password%' THEN 'security'
        ELSE 'general'
    END as category,
    check_type,
    'medium',
    false,
    ARRAY[]::text[],
    '[]'::jsonb
FROM (
    SELECT DISTINCT
        COALESCE(
            incident_pattern->>'incident_type',
            incident_pattern->>'check_type',
            -- Extract check_type from pattern signature format: check_type:check_type:host
            SPLIT_PART(
                REPLACE(REPLACE(rule_id, 'RB-AUTO-', ''), UPPER(SPLIT_PART(rule_id, ':', 3)), ''),
                ':',
                1
            )
        ) as check_type
    FROM l1_rules
    WHERE source = 'promoted'
      AND enabled = true
) promoted_checks
WHERE check_type IS NOT NULL
  AND check_type != ''
  AND NOT EXISTS (
    SELECT 1 FROM runbooks r WHERE r.check_type = promoted_checks.check_type
  )
ON CONFLICT (runbook_id) DO NOTHING;

-- Map promoted l1_rules to their canonical runbooks (existing or newly created)
INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
SELECT
    lr.rule_id,
    COALESCE(
        -- First try: existing runbook matching check_type
        (SELECT r.runbook_id FROM runbooks r
         WHERE r.check_type = COALESCE(lr.incident_pattern->>'incident_type', lr.incident_pattern->>'check_type')
         LIMIT 1),
        -- Fallback: the promoted rule's own runbook_id
        lr.runbook_id
    )
FROM l1_rules lr
WHERE lr.source = 'promoted'
  AND lr.enabled = true
  AND lr.rule_id NOT IN (SELECT l1_rule_id FROM runbook_id_mapping)
ON CONFLICT (l1_rule_id) DO NOTHING;

-- ============================================================================
-- E. Catch-all: any remaining l1_rules with runbook_ids not in runbooks
-- ============================================================================

INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps)
SELECT DISTINCT ON (lr.runbook_id)
    lr.runbook_id,
    REPLACE(REPLACE(lr.runbook_id, '-', ' '), '_', ' ') as name,
    'Auto-synced from L1 rules table' as description,
    'general' as category,
    COALESCE(lr.incident_pattern->>'incident_type', lr.incident_pattern->>'check_type', 'general') as check_type,
    'medium' as severity,
    false as is_disruptive,
    ARRAY[]::text[] as hipaa_controls,
    '[]'::jsonb as steps
FROM l1_rules lr
WHERE lr.runbook_id IS NOT NULL
  AND lr.runbook_id != 'ESCALATE'
  AND lr.enabled = true
  AND lr.runbook_id NOT IN (SELECT runbook_id FROM runbooks)
ON CONFLICT (runbook_id) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_runbook_id_mapping_runbook ON runbook_id_mapping(runbook_id);

COMMIT;
