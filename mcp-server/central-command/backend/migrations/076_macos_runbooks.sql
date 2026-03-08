-- Migration 076: macOS runbooks + L1 rules
--
-- Adds 12 macOS-specific runbooks for all drift scan check types
-- and corresponding L1 auto-healing rules.
-- SSH-based remediation targeting macOS hosts via label="macos" credentials.

-- =============================================================================
-- macOS RUNBOOKS (12 — one per macOS drift check type)
-- =============================================================================

INSERT INTO runbooks (runbook_id, name, description, category, severity, hipaa_controls, check_type, steps, enabled, version)
VALUES

-- 1. FileVault encryption
('MAC-FV-001', 'FileVault Encryption', 'Enable or verify FileVault full-disk encryption on macOS',
 'encryption', 'high', ARRAY['164.312(a)(2)(iv)'], 'macos_filevault',
 '[
   {"name": "check_filevault_status", "command": "fdesetup status", "description": "Check current FileVault status"},
   {"name": "check_filevault_users", "command": "fdesetup list", "description": "List FileVault-enabled users"},
   {"name": "enable_filevault", "command": "sudo fdesetup enable -user \"$(whoami)\" -defer /var/db/FileVaultDeferred.plist", "description": "Enable FileVault with deferred enablement (requires user password at next login)"},
   {"name": "verify_filevault", "command": "fdesetup status | grep -q \"FileVault is On\" && echo PASS || echo FAIL", "description": "Verify FileVault is now enabled"}
 ]'::jsonb, true, 1),

-- 2. Gatekeeper
('MAC-GK-001', 'Gatekeeper Enabled', 'Ensure Gatekeeper is enabled to prevent unsigned app execution',
 'security', 'high', ARRAY['164.308(a)(5)(ii)(B)'], 'macos_gatekeeper',
 '[
   {"name": "check_gatekeeper", "command": "spctl --status", "description": "Check Gatekeeper status"},
   {"name": "enable_gatekeeper", "command": "sudo spctl --master-enable", "description": "Enable Gatekeeper"},
   {"name": "verify_gatekeeper", "command": "spctl --status 2>&1 | grep -q \"assessments enabled\" && echo PASS || echo FAIL", "description": "Verify Gatekeeper is enabled"}
 ]'::jsonb, true, 1),

-- 3. System Integrity Protection
('MAC-SIP-001', 'System Integrity Protection', 'Verify SIP is enabled (cannot be enabled remotely — escalate if disabled)',
 'security', 'critical', ARRAY['164.312(c)(1)'], 'macos_sip',
 '[
   {"name": "check_sip", "command": "csrutil status", "description": "Check SIP status"},
   {"name": "escalate_if_disabled", "command": "csrutil status | grep -q \"enabled\" && echo PASS || echo FAIL_ESCALATE:SIP_disabled_requires_recovery_mode", "description": "SIP can only be enabled from Recovery Mode — escalate to L3 if disabled"}
 ]'::jsonb, true, 1),

-- 4. macOS Firewall
('MAC-FW-001', 'macOS Firewall', 'Enable the macOS Application Firewall',
 'firewall', 'high', ARRAY['164.312(e)(1)'], 'macos_firewall',
 '[
   {"name": "check_firewall", "command": "/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate", "description": "Check firewall state"},
   {"name": "enable_firewall", "command": "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on", "description": "Enable macOS firewall"},
   {"name": "enable_stealth", "command": "sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setstealthmode on", "description": "Enable stealth mode (drop unsolicited inbound)"},
   {"name": "verify_firewall", "command": "/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate | grep -q \"enabled\" && echo PASS || echo FAIL", "description": "Verify firewall is enabled"}
 ]'::jsonb, true, 1),

-- 5. Auto-updates
('MAC-UPD-001', 'macOS Auto-Updates', 'Enable automatic software updates',
 'patching', 'medium', ARRAY['164.308(a)(5)(ii)(B)'], 'macos_auto_update',
 '[
   {"name": "check_auto_update", "command": "defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled 2>/dev/null || echo NOT_SET", "description": "Check if auto-update is enabled"},
   {"name": "enable_auto_check", "command": "sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled -bool true", "description": "Enable automatic update checking"},
   {"name": "enable_auto_download", "command": "sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate AutomaticDownload -bool true", "description": "Enable automatic update downloads"},
   {"name": "enable_critical_updates", "command": "sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate CriticalUpdateInstall -bool true", "description": "Enable automatic critical/security updates"},
   {"name": "verify_auto_update", "command": "defaults read /Library/Preferences/com.apple.SoftwareUpdate AutomaticCheckEnabled 2>/dev/null | grep -q 1 && echo PASS || echo FAIL", "description": "Verify auto-updates enabled"}
 ]'::jsonb, true, 1),

-- 6. Screen lock
('MAC-SCR-001', 'macOS Screen Lock', 'Configure screen lock timeout for workstation security',
 'security', 'medium', ARRAY['164.312(a)(2)(iii)'], 'macos_screen_lock',
 '[
   {"name": "check_screen_saver_timeout", "command": "defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null || echo NOT_SET", "description": "Check screensaver idle timeout"},
   {"name": "set_screen_lock_timeout", "command": "sudo defaults -currentHost write com.apple.screensaver idleTime -int 900", "description": "Set screensaver to activate after 15 minutes"},
   {"name": "require_password", "command": "sudo defaults write com.apple.screensaver askForPassword -int 1", "description": "Require password after screensaver"},
   {"name": "set_password_delay", "command": "sudo defaults write com.apple.screensaver askForPasswordDelay -int 5", "description": "Require password within 5 seconds of screensaver"},
   {"name": "verify_screen_lock", "command": "defaults read com.apple.screensaver askForPassword 2>/dev/null | grep -q 1 && echo PASS || echo FAIL", "description": "Verify screen lock password requirement"}
 ]'::jsonb, true, 1),

-- 7. Remote Login (SSH)
('MAC-SSH-001', 'macOS Remote Login', 'Verify SSH/Remote Login configuration',
 'ssh', 'medium', ARRAY['164.312(d)'], 'macos_remote_login',
 '[
   {"name": "check_remote_login", "command": "sudo systemsetup -getremotelogin 2>/dev/null || echo UNKNOWN", "description": "Check if Remote Login (SSH) is enabled"},
   {"name": "check_ssh_config", "command": "cat /etc/ssh/sshd_config 2>/dev/null | grep -E \"^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication)\" || echo DEFAULT_CONFIG", "description": "Check SSH configuration"},
   {"name": "harden_ssh", "command": "sudo sed -i.bak \"s/^#*PermitRootLogin.*/PermitRootLogin no/\" /etc/ssh/sshd_config && sudo sed -i \"s/^#*PasswordAuthentication.*/PasswordAuthentication no/\" /etc/ssh/sshd_config", "description": "Disable root login and password auth if SSH is enabled"}
 ]'::jsonb, true, 1),

-- 8. File Sharing (SMB)
('MAC-SMB-001', 'macOS File Sharing', 'Disable unnecessary SMB file sharing',
 'network', 'medium', ARRAY['164.312(e)(1)'], 'macos_file_sharing',
 '[
   {"name": "check_file_sharing", "command": "sudo launchctl list | grep -c smbd || echo 0", "description": "Check if SMB file sharing is running"},
   {"name": "check_shared_folders", "command": "sharing -l 2>/dev/null || echo NO_SHARES", "description": "List shared folders"},
   {"name": "disable_file_sharing", "command": "sudo launchctl unload -w /System/Library/LaunchDaemons/com.apple.smbd.plist 2>/dev/null; echo DONE", "description": "Disable SMB file sharing service"},
   {"name": "verify_sharing_off", "command": "sudo launchctl list 2>/dev/null | grep -q smbd && echo FAIL || echo PASS", "description": "Verify file sharing is disabled"}
 ]'::jsonb, true, 1),

-- 9. Time Machine backup
('MAC-TM-001', 'Time Machine Backup', 'Verify Time Machine backup is configured and running',
 'backup', 'high', ARRAY['164.308(a)(7)(ii)(A)'], 'macos_time_machine',
 '[
   {"name": "check_time_machine", "command": "tmutil status 2>/dev/null || echo NOT_CONFIGURED", "description": "Check Time Machine status"},
   {"name": "check_last_backup", "command": "tmutil latestbackup 2>/dev/null || echo NEVER", "description": "Get latest backup timestamp"},
   {"name": "check_destination", "command": "tmutil destinationinfo 2>/dev/null || echo NO_DESTINATION", "description": "Check backup destination"},
   {"name": "enable_time_machine", "command": "sudo tmutil enable 2>/dev/null; echo ENABLED", "description": "Enable Time Machine (requires a destination to be configured)"}
 ]'::jsonb, true, 1),

-- 10. NTP sync
('MAC-NTP-001', 'macOS NTP Sync', 'Ensure time synchronization is configured',
 'drift', 'medium', ARRAY['164.312(b)'], 'macos_ntp_sync',
 '[
   {"name": "check_ntp_enabled", "command": "sudo systemsetup -getusingnetworktime 2>/dev/null || echo UNKNOWN", "description": "Check if network time is enabled"},
   {"name": "check_ntp_server", "command": "sudo systemsetup -getnetworktimeserver 2>/dev/null || echo UNKNOWN", "description": "Check NTP server"},
   {"name": "enable_ntp", "command": "sudo systemsetup -setusingnetworktime on", "description": "Enable network time sync"},
   {"name": "verify_ntp", "command": "sudo systemsetup -getusingnetworktime 2>/dev/null | grep -q \"On\" && echo PASS || echo FAIL", "description": "Verify NTP is enabled"}
 ]'::jsonb, true, 1),

-- 11. Admin users
('MAC-ADM-001', 'macOS Admin Users', 'Audit and reduce admin user count',
 'accounts', 'high', ARRAY['164.312(a)(1)'], 'macos_admin_users',
 '[
   {"name": "list_admin_users", "command": "dscl . -read /Groups/admin GroupMembership 2>/dev/null || echo UNKNOWN", "description": "List all admin users"},
   {"name": "count_admins", "command": "dscl . -read /Groups/admin GroupMembership 2>/dev/null | tr \" \" \"\\n\" | grep -v GroupMembership | wc -l | tr -d \" \"", "description": "Count admin users (should be minimal)"},
   {"name": "escalate_if_excessive", "command": "ADMIN_COUNT=$(dscl . -read /Groups/admin GroupMembership 2>/dev/null | tr \" \" \"\\n\" | grep -v GroupMembership | wc -l | tr -d \" \"); [ \"$ADMIN_COUNT\" -gt 3 ] && echo FAIL_ESCALATE:excessive_admin_users_$ADMIN_COUNT || echo PASS", "description": "Escalate if more than 3 admin users"}
 ]'::jsonb, true, 1),

-- 12. Disk space
('MAC-DISK-001', 'macOS Disk Space', 'Monitor and remediate low disk space',
 'storage', 'medium', ARRAY['164.310(d)(2)(iv)'], 'macos_disk_space',
 '[
   {"name": "check_disk_usage", "command": "df -h / | tail -1", "description": "Check root volume disk usage"},
   {"name": "check_large_files", "command": "sudo find /var/log -name \"*.log\" -size +100M -exec ls -lh {} \\; 2>/dev/null | head -10", "description": "Find large log files"},
   {"name": "clear_caches", "command": "sudo rm -rf /Library/Caches/* /System/Library/Caches/* 2>/dev/null; echo CACHES_CLEARED", "description": "Clear system caches"},
   {"name": "purge_old_logs", "command": "sudo find /var/log -name \"*.log.*\" -mtime +30 -delete 2>/dev/null; echo OLD_LOGS_PURGED", "description": "Remove logs older than 30 days"},
   {"name": "verify_disk_space", "command": "USAGE=$(df / | tail -1 | awk \"{print \\$5}\" | tr -d \"%\"); [ \"$USAGE\" -lt 90 ] && echo PASS || echo FAIL", "description": "Verify disk usage is below 90%"}
 ]'::jsonb, true, 1)

ON CONFLICT (runbook_id) DO UPDATE SET
  name = EXCLUDED.name,
  steps = EXCLUDED.steps,
  hipaa_controls = EXCLUDED.hipaa_controls,
  check_type = EXCLUDED.check_type,
  updated_at = NOW();


-- =============================================================================
-- macOS L1 AUTO-HEALING RULES
-- =============================================================================

INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
VALUES
  ('MAC-FV-001',   '{"incident_type": "macos_filevault"}'::jsonb,    'MAC-FV-001',   0.85, true, 'builtin'),
  ('MAC-GK-001',   '{"incident_type": "macos_gatekeeper"}'::jsonb,   'MAC-GK-001',   0.90, true, 'builtin'),
  ('MAC-SIP-001',  '{"incident_type": "macos_sip"}'::jsonb,          'ESC-MAC-SIP',  0.95, true, 'builtin'),
  ('MAC-FW-001',   '{"incident_type": "macos_firewall"}'::jsonb,     'MAC-FW-001',   0.90, true, 'builtin'),
  ('MAC-UPD-001',  '{"incident_type": "macos_auto_update"}'::jsonb,  'MAC-UPD-001',  0.85, true, 'builtin'),
  ('MAC-SCR-001',  '{"incident_type": "macos_screen_lock"}'::jsonb,  'MAC-SCR-001',  0.85, true, 'builtin'),
  ('MAC-SSH-001',  '{"incident_type": "macos_remote_login"}'::jsonb, 'MAC-SSH-001',  0.80, true, 'builtin'),
  ('MAC-SMB-001',  '{"incident_type": "macos_file_sharing"}'::jsonb, 'MAC-SMB-001',  0.85, true, 'builtin'),
  ('MAC-TM-001',   '{"incident_type": "macos_time_machine"}'::jsonb, 'MAC-TM-001',   0.80, true, 'builtin'),
  ('MAC-NTP-001',  '{"incident_type": "macos_ntp_sync"}'::jsonb,     'MAC-NTP-001',  0.85, true, 'builtin'),
  ('MAC-ADM-001',  '{"incident_type": "macos_admin_users"}'::jsonb,  'MAC-ADM-001',  0.80, true, 'builtin'),
  ('MAC-DISK-001', '{"incident_type": "macos_disk_space"}'::jsonb,   'MAC-DISK-001', 0.85, true, 'builtin')
ON CONFLICT (rule_id) DO UPDATE SET
  incident_pattern = EXCLUDED.incident_pattern,
  runbook_id = EXCLUDED.runbook_id,
  confidence = EXCLUDED.confidence,
  enabled = EXCLUDED.enabled,
  source = EXCLUDED.source,
  updated_at = NOW();

-- SIP escalation rule (SIP cannot be fixed remotely — requires Recovery Mode)
INSERT INTO runbooks (runbook_id, name, description, category, severity, hipaa_controls, check_type, steps, enabled, version)
VALUES (
  'ESC-MAC-SIP', 'Escalate: SIP Disabled', 'System Integrity Protection is disabled — requires physical access to Recovery Mode to re-enable. Escalate to on-site technician.',
  'security', 'critical', ARRAY['164.312(c)(1)'], 'macos_sip',
  '[
    {"name": "escalate", "command": "echo ESCALATE:SIP_disabled_requires_recovery_mode_boot", "description": "SIP can only be re-enabled from macOS Recovery Mode (Cmd+R at boot). Escalate to L3 for on-site remediation."}
  ]'::jsonb, true, 1
)
ON CONFLICT (runbook_id) DO UPDATE SET
  name = EXCLUDED.name,
  steps = EXCLUDED.steps,
  updated_at = NOW();

-- Rollback:
-- DELETE FROM l1_rules WHERE rule_id LIKE 'MAC-%';
-- DELETE FROM runbooks WHERE runbook_id LIKE 'MAC-%' OR runbook_id = 'ESC-MAC-SIP';
