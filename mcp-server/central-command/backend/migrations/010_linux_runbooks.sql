-- Migration: 010_linux_runbooks.sql
-- Adds Linux runbooks to the catalog
--
-- Depends on: 005_runbook_tables.sql

BEGIN;

-- ============================================================
-- LINUX RUNBOOKS (17 total)
-- ============================================================

-- SSH Configuration (3)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-SSH-001', 'SSH Root Login Disabled', 'Ensure PermitRootLogin is set to no in sshd_config', 'ssh', 'ssh_config', 'high', false, ARRAY['164.312(d)', '164.312(a)(1)'], '[]'::jsonb),
('LIN-SSH-002', 'SSH Password Authentication Disabled', 'Ensure PasswordAuthentication is set to no (use keys only)', 'ssh', 'ssh_config', 'high', false, ARRAY['164.312(d)', '164.312(a)(1)'], '[]'::jsonb),
('LIN-SSH-003', 'SSH Max Auth Tries', 'Limit SSH authentication attempts to 3', 'ssh', 'ssh_config', 'medium', false, ARRAY['164.312(a)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Firewall (1)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-FW-001', 'Firewall Active', 'Ensure firewall (ufw or firewalld) is active', 'firewall', 'firewall', 'critical', false, ARRAY['164.312(e)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Services (4)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-SVC-001', 'Critical Service Running', 'Ensure critical services (sshd, chronyd, auditd) are running', 'services', 'service_health', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb),
('LIN-SVC-002', 'Unnecessary Services Disabled', 'Ensure unnecessary services (telnet, rsh, rlogin) are disabled', 'services', 'service_health', 'medium', false, ARRAY['164.312(a)(1)'], '[]'::jsonb),
('LIN-SVC-003', 'Service Auto-Restart Enabled', 'Ensure critical services are configured to restart on failure', 'services', 'service_health', 'medium', false, ARRAY['164.308(a)(7)(ii)(A)'], '[]'::jsonb),
('LIN-SVC-004', 'NTP Time Sync', 'Ensure chronyd or ntpd is running and synchronized', 'services', 'ntp_sync', 'high', false, ARRAY['164.312(b)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Audit (2)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-AUDIT-001', 'Auditd Running', 'Ensure auditd service is running and enabled', 'audit', 'logging', 'high', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)'], '[]'::jsonb),
('LIN-AUDIT-002', 'Audit Rules Configured', 'Ensure HIPAA-required audit rules are configured', 'audit', 'logging', 'high', false, ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Patching (1)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-PATCH-001', 'Security Updates Applied', 'Check for and apply pending security updates', 'patching', 'patching', 'critical', true, ARRAY['164.308(a)(5)(ii)(B)', '164.312(c)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Permissions (3)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-PERM-001', 'Secure File Permissions', 'Ensure /etc/passwd, /etc/shadow have correct permissions', 'permissions', 'service_health', 'high', false, ARRAY['164.312(a)(1)', '164.312(d)'], '[]'::jsonb),
('LIN-PERM-002', 'SUID/SGID Files Audit', 'Audit and report on SUID/SGID files', 'permissions', 'service_health', 'medium', false, ARRAY['164.312(a)(1)'], '[]'::jsonb),
('LIN-PERM-003', 'World-Writable Files', 'Find and report world-writable files', 'permissions', 'service_health', 'medium', false, ARRAY['164.312(a)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Accounts (2)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-ACCT-001', 'Password Aging Policy', 'Ensure password aging is configured per policy', 'accounts', 'service_health', 'medium', false, ARRAY['164.312(d)'], '[]'::jsonb),
('LIN-ACCT-002', 'Empty Password Check', 'Ensure no accounts have empty passwords', 'accounts', 'service_health', 'critical', false, ARRAY['164.312(d)', '164.312(a)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- MAC/SELinux (1)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('LIN-MAC-001', 'SELinux/AppArmor Enforcing', 'Ensure SELinux or AppArmor is in enforcing mode', 'mac', 'service_health', 'high', false, ARRAY['164.312(a)(1)', '164.312(e)(1)'], '[]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

COMMIT;
