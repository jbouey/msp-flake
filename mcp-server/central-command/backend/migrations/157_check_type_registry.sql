-- Migration 157: Canonical check type registry
-- Single source of truth for all check names, categories, and display labels.
--
-- Round Table Session 205: "Check names are defined in 4 places with no
-- validation. Every time one gets fixed, another breaks. The registry
-- stops the naming mismatch cycle permanently."
--
-- The Go daemon's check names are CANONICAL. The scoring engine, frontend,
-- and healing pipeline all read from this table. New checks default to
-- unmapped and surface in the admin UI for assignment.

CREATE TABLE IF NOT EXISTS check_type_registry (
    check_name VARCHAR(100) PRIMARY KEY,
    platform VARCHAR(20) NOT NULL CHECK (platform IN ('windows', 'linux', 'macos', 'network', 'system')),
    category VARCHAR(50),  -- scoring category: patching, encryption, etc. NULL = unmapped
    hipaa_control VARCHAR(50),  -- e.g. §164.312(a)(2)(iv)
    display_label VARCHAR(200),  -- human-readable label for frontend
    is_scored BOOLEAN NOT NULL DEFAULT true,  -- false = operational/monitoring, excluded from compliance score
    is_monitoring_only BOOLEAN NOT NULL DEFAULT false,  -- true = can't be auto-remediated
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- No RLS needed — this is a global config table, not tenant-scoped

-- ============================================================================
-- Seed: Windows checks (29)
-- ============================================================================
INSERT INTO check_type_registry (check_name, platform, category, hipaa_control, display_label, is_scored, is_monitoring_only) VALUES
    ('firewall_status',          'windows', 'firewall',         '§164.312(e)(1)',      'Windows Firewall Status',         true,  false),
    ('windows_defender',         'windows', 'antivirus',        '§164.308(a)(5)(ii)(B)', 'Windows Defender',              true,  false),
    ('defender_exclusions',      'windows', 'antivirus',        '§164.308(a)(5)(ii)(B)', 'Defender Exclusions',           true,  false),
    ('defender_cloud_protection','windows', 'antivirus',        '§164.308(a)(5)(ii)(B)', 'Defender Cloud Protection',     true,  false),
    ('windows_update',           'windows', 'patching',         '§164.308(a)(5)(ii)(A)', 'Windows Update Service',        true,  false),
    ('bitlocker_status',         'windows', 'encryption',       '§164.312(a)(2)(iv)',    'BitLocker Encryption',          true,  false),
    ('smb_signing',              'windows', 'encryption',       '§164.312(e)(2)(ii)',    'SMB Signing',                   true,  false),
    ('smb1_protocol',            'windows', 'network',          '§164.312(e)(1)',        'SMB v1 Protocol',               true,  false),
    ('audit_logging',            'windows', 'logging',          '§164.312(b)',           'Audit Logging',                 true,  false),
    ('audit_policy',             'windows', 'logging',          '§164.312(b)',           'Audit Policy',                  true,  false),
    ('screen_lock_policy',       'windows', 'access_control',   '§164.312(a)(2)(iii)',   'Screen Lock Policy',            true,  false),
    ('password_policy',          'windows', 'access_control',   '§164.312(d)',           'Password Policy',               true,  false),
    ('rogue_admin_users',        'windows', 'access_control',   '§164.312(a)(1)',        'Rogue Admin Users',             true,  false),
    ('guest_account',            'windows', 'access_control',   '§164.312(a)(1)',        'Guest Account',                 true,  false),
    ('rdp_nla',                  'windows', 'access_control',   '§164.312(d)',           'RDP Network Level Auth',        true,  false),
    ('rogue_scheduled_tasks',    'windows', 'system_integrity', '§164.308(a)(1)(ii)(D)', 'Rogue Scheduled Tasks',         true,  false),
    ('registry_run_persistence', 'windows', 'system_integrity', '§164.308(a)(1)(ii)(D)', 'Registry Run Key Persistence',  true,  false),
    ('wmi_event_persistence',    'windows', 'system_integrity', '§164.308(a)(1)(ii)(D)', 'WMI Event Persistence',         true,  false),
    ('dns_config',               'windows', 'network',          '§164.312(e)(1)',        'DNS Configuration',             true,  false),
    ('network_profile',          'windows', 'network',          '§164.312(e)(1)',        'Network Profile',               true,  false),
    ('spooler_service',          'windows', 'services',         '§164.308(a)(3)',        'Print Spooler Service',         true,  false),
    ('agent_status',             'windows', 'services',         NULL,                    'Agent Status',                  true,  false),
    ('firewall_dangerous_rules', 'windows', 'firewall',         '§164.312(e)(1)',        'Firewall Dangerous Rules',      true,  false),
    -- Monitoring-only Windows checks
    ('device_unreachable',       'windows', NULL,               NULL,                    'Device Unreachable',            false, true),
    ('credential_stale',         'windows', NULL,               NULL,                    'Credential Stale',              false, true),
    ('credential_ip_mismatch',   'windows', NULL,               NULL,                    'Credential IP Mismatch',        false, true),
    ('backup_not_configured',    'windows', 'backup',           '§164.308(a)(7)',        'Backup Not Configured',         true,  false),
    ('backup_verification',      'windows', NULL,               '§164.308(a)(7)',        'Backup Verification',           false, true),
    ('subnet_dark',              'windows', NULL,               NULL,                    'Subnet Dark',                   false, true)
ON CONFLICT (check_name) DO UPDATE SET
    platform = EXCLUDED.platform,
    category = EXCLUDED.category,
    hipaa_control = EXCLUDED.hipaa_control,
    display_label = EXCLUDED.display_label,
    is_scored = EXCLUDED.is_scored,
    is_monitoring_only = EXCLUDED.is_monitoring_only,
    updated_at = NOW();

-- ============================================================================
-- Seed: Linux checks (17)
-- ============================================================================
INSERT INTO check_type_registry (check_name, platform, category, hipaa_control, display_label, is_scored, is_monitoring_only) VALUES
    ('linux_firewall',            'linux', 'firewall',         '§164.312(e)(1)',      'Linux Firewall',               true,  false),
    ('linux_ssh_config',          'linux', 'access_control',   '§164.312(a)(1)',      'SSH Configuration',            true,  false),
    ('linux_user_accounts',       'linux', 'access_control',   '§164.312(a)(1)',      'Linux User Accounts',          true,  false),
    ('linux_file_permissions',    'linux', 'access_control',   '§164.312(a)(1)',      'File Permissions',             true,  false),
    ('linux_audit_logging',       'linux', 'logging',          '§164.312(b)',         'Linux Audit Logging',          true,  false),
    ('linux_log_forwarding',      'linux', 'logging',          '§164.312(b)',         'Log Forwarding',               true,  false),
    ('linux_ntp_sync',            'linux', 'network',          '§164.312(b)',         'NTP Time Sync',                true,  false),
    ('linux_kernel_params',       'linux', 'system_integrity', NULL,                  'Kernel Parameters',            true,  false),
    ('linux_open_ports',          'linux', 'network',          '§164.312(e)(1)',      'Open Ports',                   true,  false),
    ('linux_disk_space',          'linux', 'system_integrity', NULL,                  'Disk Space',                   true,  false),
    ('linux_suid_binaries',       'linux', 'system_integrity', '§164.312(a)(1)',      'SUID Binaries',                true,  false),
    ('linux_failed_services',     'linux', 'services',         '§164.308(a)(3)',      'Failed Services',              true,  false),
    ('linux_unattended_upgrades', 'linux', 'patching',         '§164.308(a)(5)(ii)(A)','Unattended Upgrades',         true,  false),
    ('linux_cron_review',         'linux', 'system_integrity', '§164.308(a)(1)(ii)(D)','Cron Job Review',             true,  false),
    ('linux_cert_expiry',         'linux', 'system_integrity', '§164.312(e)(2)(ii)', 'Certificate Expiry',            true,  false),
    ('linux_backup_status',       'linux', 'backup',           '§164.308(a)(7)',      'Linux Backup Status',          true,  false),
    ('linux_encryption',          'linux', 'encryption',       '§164.312(a)(2)(iv)', 'Linux Disk Encryption',         false, true)
ON CONFLICT (check_name) DO UPDATE SET
    platform = EXCLUDED.platform,
    category = EXCLUDED.category,
    hipaa_control = EXCLUDED.hipaa_control,
    display_label = EXCLUDED.display_label,
    is_scored = EXCLUDED.is_scored,
    is_monitoring_only = EXCLUDED.is_monitoring_only,
    updated_at = NOW();

-- ============================================================================
-- Seed: macOS checks (12)
-- ============================================================================
INSERT INTO check_type_registry (check_name, platform, category, hipaa_control, display_label, is_scored, is_monitoring_only) VALUES
    ('macos_filevault',    'macos', 'encryption',       '§164.312(a)(2)(iv)',  'FileVault Encryption',   true,  false),
    ('macos_firewall',     'macos', 'firewall',         '§164.312(e)(1)',      'macOS Firewall',         true,  false),
    ('macos_gatekeeper',   'macos', 'system_integrity', NULL,                  'Gatekeeper',             true,  false),
    ('macos_sip',          'macos', 'system_integrity', NULL,                  'System Integrity Prot.', true,  false),
    ('macos_auto_update',  'macos', 'patching',         '§164.308(a)(5)(ii)(A)','Auto Updates',          true,  false),
    ('macos_screen_lock',  'macos', 'access_control',   '§164.312(a)(2)(iii)', 'Screen Lock',            true,  false),
    ('macos_admin_users',  'macos', 'access_control',   '§164.312(a)(1)',      'Admin Users',            true,  false),
    ('macos_disk_space',   'macos', 'system_integrity', NULL,                  'Disk Space',             true,  false),
    ('macos_cert_expiry',  'macos', 'system_integrity', '§164.312(e)(2)(ii)', 'Certificate Expiry',     true,  false),
    ('macos_ntp_sync',     'macos', 'network',          '§164.312(b)',         'NTP Time Sync',          true,  false),
    ('macos_file_sharing', 'macos', 'network',          '§164.312(e)(1)',      'File Sharing',           true,  false),
    ('macos_time_machine', 'macos', 'backup',           '§164.308(a)(7)',      'Time Machine Backup',    true,  false)
ON CONFLICT (check_name) DO UPDATE SET
    platform = EXCLUDED.platform,
    category = EXCLUDED.category,
    hipaa_control = EXCLUDED.hipaa_control,
    display_label = EXCLUDED.display_label,
    is_scored = EXCLUDED.is_scored,
    is_monitoring_only = EXCLUDED.is_monitoring_only,
    updated_at = NOW();

-- ============================================================================
-- Seed: Network + system checks (11)
-- ============================================================================
INSERT INTO check_type_registry (check_name, platform, category, display_label, is_scored, is_monitoring_only) VALUES
    ('net_host_reachability',     'network', NULL, 'Host Reachability',        false, true),
    ('net_unexpected_ports',      'network', NULL, 'Unexpected Ports',         false, true),
    ('net_expected_service',      'network', NULL, 'Expected Service',         false, true),
    ('net_dns_resolution',        'network', NULL, 'DNS Resolution',           false, true),
    ('NETWORK-ROGUE-DEVICE',      'network', NULL, 'Rogue Network Device',     false, true),
    ('NETWORK-UNEXPECTED-SUBNET', 'network', NULL, 'Unexpected Subnet',        false, true),
    ('AGENT-REDEPLOY-EXHAUSTED',  'system', NULL,  'Agent Redeploy Exhausted', false, true),
    ('WIN-DEPLOY-UNREACHABLE',    'system', NULL,  'Windows Deploy Unreachable',false, true),
    ('brute_force_detected',      'system', NULL,  'Brute Force Detected',     false, false),
    ('ransomware_indicator',      'system', NULL,  'Ransomware Indicator',     false, false),
    ('security_event_critical',   'system', NULL,  'Critical Security Event',  false, false)
ON CONFLICT (check_name) DO UPDATE SET
    platform = EXCLUDED.platform,
    category = EXCLUDED.category,
    display_label = EXCLUDED.display_label,
    is_scored = EXCLUDED.is_scored,
    is_monitoring_only = EXCLUDED.is_monitoring_only,
    updated_at = NOW();

-- Index for scoring queries
CREATE INDEX IF NOT EXISTS idx_check_registry_category
    ON check_type_registry(category) WHERE is_scored = true;
CREATE INDEX IF NOT EXISTS idx_check_registry_monitoring
    ON check_type_registry(is_monitoring_only) WHERE is_monitoring_only = true;
