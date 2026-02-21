-- ============================================================
-- Align l1_rules incident_pattern with Go daemon scanner check_types
--
-- The Go drift scanners (linuxscan.go, driftscan.go, netscan.go)
-- send prefixed check_types (linux_ssh_config, linux_firewall, etc.)
-- but l1_rules had short names (ssh_config, firewall, etc.).
-- Rules never matched because incident_type != scanner check_type.
-- ============================================================

-- === Fix existing Linux builtin rules ===
UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_ssh_config"}'
WHERE rule_id IN ('L1-SSH-001', 'L1-SSH-002', 'L1-LIN-SSH-001', 'L1-LIN-SSH-002', 'L1-LIN-SSH-003')
  AND incident_pattern->>'incident_type' = 'ssh_config';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_audit_logging"}'
WHERE rule_id IN ('L1-LIN-AUDIT-001', 'L1-LIN-AUDIT-002')
  AND incident_pattern->>'incident_type' = 'audit';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_kernel_params"}'
WHERE rule_id = 'L1-LIN-KERN-001'
  AND incident_pattern->>'incident_type' = 'kernel';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_cron_review"}'
WHERE rule_id = 'L1-LIN-CRON-001'
  AND incident_pattern->>'incident_type' = 'cron';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_firewall"}'
WHERE rule_id = 'L1-LIN-FW-001'
  AND incident_pattern->>'incident_type' = 'firewall';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_log_forwarding"}'
WHERE rule_id = 'L1-LIN-LOG-001'
  AND incident_pattern->>'incident_type' = 'logging';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_failed_services"}'
WHERE rule_id IN ('L1-LIN-SVC-001', 'L1-LIN-SVC-002')
  AND incident_pattern->>'incident_type' = 'services';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_suid_binaries"}'
WHERE rule_id IN ('L1-SUID-001', 'L1-LIN-SUID-001')
  AND incident_pattern->>'incident_type' = 'permissions';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_ntp_sync"}'
WHERE rule_id = 'L1-LIN-NTP-001'
  AND incident_pattern->>'incident_type' = 'ntp_sync';

-- === Fix existing Windows builtin rules ===
UPDATE l1_rules SET incident_pattern = '{"incident_type": "audit_logging"}'
WHERE rule_id = 'L1-AUDIT-001'
  AND incident_pattern->>'incident_type' = 'audit_policy';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "windows_update"}'
WHERE rule_id = 'L1-WIN-SVC-WUAUSERV'
  AND incident_pattern->>'incident_type' = 'service_wuauserv';

-- === Fix promoted rules with stale names ===
UPDATE l1_rules SET incident_pattern = '{"check_type": "linux_ssh_config"}'
WHERE rule_id = 'RB-AUTO-SSH_CONF'
  AND incident_pattern->>'check_type' = 'ssh_config';

UPDATE l1_rules SET incident_pattern = '{"check_type": "audit_logging"}'
WHERE rule_id = 'RB-AUTO-AUDIT_PO'
  AND incident_pattern->>'check_type' = 'audit_policy';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_cron_review"}'
WHERE rule_id LIKE 'RB-AUTO-CRON%'
  AND incident_pattern->>'incident_type' = 'cron';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_kernel_params"}'
WHERE rule_id LIKE 'RB-AUTO-KERNEL%'
  AND incident_pattern->>'incident_type' = 'kernel';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_failed_services"}'
WHERE rule_id LIKE 'RB-AUTO-SERVICES%'
  AND incident_pattern->>'incident_type' = 'services';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_suid_binaries"}'
WHERE rule_id LIKE 'RB-AUTO-PERMISSI%'
  AND incident_pattern->>'incident_type' = 'permissions';

UPDATE l1_rules SET incident_pattern = '{"incident_type": "linux_audit_logging"}'
WHERE rule_id LIKE 'RB-AUTO-AUDIT:AUDIT%'
  AND incident_pattern->>'incident_type' = 'audit';

-- === Add missing rules for all 38 scanner check types ===

-- Linux missing rules
INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
VALUES
  ('L1-LIN-DISK-001', '{"incident_type": "linux_disk_space"}', 'LIN-DISK-001', 0.8, true, 'builtin'),
  ('L1-LIN-PORTS-001', '{"incident_type": "linux_open_ports"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-LIN-USERS-001', '{"incident_type": "linux_user_accounts"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-LIN-UPGRADES-001', '{"incident_type": "linux_unattended_upgrades"}', 'LIN-UPGRADES-001', 0.8, true, 'builtin'),
  ('L1-LIN-CERT-001', '{"incident_type": "linux_cert_expiry"}', 'LIN-CERT-001', 0.8, true, 'builtin'),
  ('L1-LIN-PERM-001', '{"incident_type": "linux_file_permissions"}', 'LIN-PERM-001', 0.8, true, 'builtin')
ON CONFLICT (rule_id) DO UPDATE SET incident_pattern = EXCLUDED.incident_pattern;

-- Windows missing rules
INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
VALUES
  ('L1-WIN-ROGUE-ADMIN-001', '{"incident_type": "rogue_admin_users"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-WIN-ROGUE-TASKS-001', '{"incident_type": "rogue_scheduled_tasks"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-WIN-AGENT-001', '{"incident_type": "agent_status"}', 'RB-WIN-SVC-001', 0.8, true, 'builtin'),
  ('L1-WIN-PASSWD-001', '{"incident_type": "password_policy"}', 'RB-WIN-SEC-022', 0.8, true, 'builtin'),
  ('L1-WIN-RDP-NLA-001', '{"incident_type": "rdp_nla"}', 'RB-WIN-SEC-023', 0.8, true, 'builtin'),
  ('L1-WIN-GUEST-001', '{"incident_type": "guest_account"}', 'RB-WIN-SEC-024', 0.8, true, 'builtin'),
  ('L1-WIN-SEC-BITLOCKER', '{"incident_type": "bitlocker_status"}', 'RB-WIN-SEC-005', 0.8, true, 'builtin'),
  ('L1-WIN-SVC-NETLOGON', '{"incident_type": "service_netlogon"}', 'RB-WIN-SVC-001', 0.8, true, 'builtin')
ON CONFLICT (rule_id) DO UPDATE SET incident_pattern = EXCLUDED.incident_pattern;

-- Network rules (all escalate — can't auto-remediate network topology)
INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
VALUES
  ('L1-NET-PORTS-001', '{"incident_type": "net_unexpected_ports"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-NET-SVC-001', '{"incident_type": "net_expected_service"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-NET-REACH-001', '{"incident_type": "net_host_reachability"}', 'ESCALATE', 0.8, true, 'builtin'),
  ('L1-NET-DNS-001', '{"incident_type": "net_dns_resolution"}', 'ESCALATE', 0.8, true, 'builtin')
ON CONFLICT (rule_id) DO UPDATE SET incident_pattern = EXCLUDED.incident_pattern;
