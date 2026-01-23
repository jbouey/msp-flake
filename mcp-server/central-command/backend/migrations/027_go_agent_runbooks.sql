-- Migration: 027_go_agent_runbooks.sql
-- Adds Windows Go agent L1 runbooks for healing
-- Maps to check_types from grpc_server.py: screen_lock, patching, firewall_status, etc.
--
-- Session 61: Required for Go agent healing to work with L1 rules

BEGIN;

-- ============================================================
-- WINDOWS GO AGENT RUNBOOKS (8 total)
-- These map to the check_types sent by the Go agent via gRPC
-- ============================================================

-- Screen Lock (mapped from Go agent "screenlock" -> "screen_lock")
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-SCRN-001', 'Screen Lock Policy', 'Enforce screen lock timeout and password requirement', 'security', 'screen_lock', 'high', false,
 ARRAY['164.312(a)(2)(iii)', '164.312(d)'],
 '[{"action": "powershell", "script": "Set-ItemProperty -Path ''HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization'' -Name ''NoLockScreenSlideshow'' -Value 1 -Force; powercfg /change monitor-timeout-ac 15; powercfg /change standby-timeout-ac 30"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Windows Update / Patching (mapped from Go agent "patches" -> "patching")
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-PATCH-001', 'Windows Update Service', 'Ensure Windows Update service is running and updates are applied', 'patching', 'patching', 'critical', true,
 ARRAY['164.308(a)(5)(ii)(B)', '164.312(c)(1)'],
 '[{"action": "powershell", "script": "Set-Service wuauserv -StartupType Automatic; Start-Service wuauserv"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Windows Firewall Status (Go agent "firewall" check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-FW-001', 'Windows Firewall Enabled', 'Ensure Windows Firewall is enabled on all profiles', 'firewall', 'firewall_status', 'critical', false,
 ARRAY['164.312(e)(1)'],
 '[{"action": "powershell", "script": "Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Windows Defender (Go agent "defender" check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-DEF-001', 'Windows Defender Active', 'Ensure Windows Defender antivirus is running with real-time protection', 'security', 'windows_defender', 'critical', false,
 ARRAY['164.308(a)(5)(ii)(B)', '164.312(e)(1)'],
 '[{"action": "powershell", "script": "Set-MpPreference -DisableRealtimeMonitoring $false; Start-Service WinDefend"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- BitLocker (Go agent "bitlocker" check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-BL-001', 'BitLocker Encryption', 'Ensure BitLocker drive encryption is enabled on system drive', 'encryption', 'bitlocker', 'high', true,
 ARRAY['164.312(a)(2)(iv)', '164.312(e)(2)(ii)'],
 '[{"action": "alert", "message": "BitLocker requires manual configuration - escalate to technician"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Admin Accounts (Go agent potential check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-ADMIN-001', 'Local Admin Account Audit', 'Audit local administrator accounts for compliance', 'accounts', 'admin_accounts', 'medium', false,
 ARRAY['164.312(a)(1)', '164.312(d)'],
 '[{"action": "audit", "check": "Enumerate local administrators and verify against approved list"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- USB Storage (Go agent potential check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-USB-001', 'USB Storage Policy', 'Enforce USB storage device restrictions', 'security', 'usb_storage', 'medium', false,
 ARRAY['164.310(d)(1)', '164.312(c)(1)'],
 '[{"action": "powershell", "script": "Set-ItemProperty -Path ''HKLM:\\SYSTEM\\CurrentControlSet\\Services\\USBSTOR'' -Name ''Start'' -Value 4"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- Audit Policy (Go agent potential check)
INSERT INTO runbooks (runbook_id, name, description, category, check_type, severity, is_disruptive, hipaa_controls, steps) VALUES
('WIN-AUDIT-001', 'Windows Audit Policy', 'Configure Windows audit policy for HIPAA compliance', 'audit', 'audit_policy', 'high', false,
 ARRAY['164.312(b)', '164.308(a)(1)(ii)(D)'],
 '[{"action": "powershell", "script": "auditpol /set /category:\"Logon/Logoff\" /success:enable /failure:enable; auditpol /set /category:\"Object Access\" /success:enable /failure:enable"}]'::jsonb)
ON CONFLICT (runbook_id) DO NOTHING;

-- ============================================================
-- Also add L1 rules mapping for the auto_healer
-- These are the rules that get synced to appliances
-- ============================================================

-- Ensure the l1_rules table has entries for Go agent check types
-- This is handled by the runbook sync mechanism in db_queries.py

COMMIT;
