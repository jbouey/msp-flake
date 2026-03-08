-- Migration: 075_drift_scan_config.sql
-- Per-site drift scan configuration — allows toggling individual check types on/off

BEGIN;

-- Per-site drift scan configuration
CREATE TABLE IF NOT EXISTS site_drift_config (
    site_id VARCHAR(100) NOT NULL,
    check_type VARCHAR(100) NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    modified_by VARCHAR(100),
    modified_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT,
    PRIMARY KEY (site_id, check_type)
);

CREATE INDEX IF NOT EXISTS idx_site_drift_config_site ON site_drift_config(site_id);

-- Seed with all known check types (all enabled by default)
-- Windows checks
INSERT INTO site_drift_config (site_id, check_type, enabled, notes) VALUES
  ('__defaults__', 'firewall_status', true, 'Windows Firewall profiles'),
  ('__defaults__', 'windows_defender', true, 'Windows Defender service'),
  ('__defaults__', 'windows_update', true, 'Windows Update service'),
  ('__defaults__', 'audit_logging', true, 'Event Log service'),
  ('__defaults__', 'rogue_admin_users', true, 'Unauthorized local admins'),
  ('__defaults__', 'rogue_scheduled_tasks', true, 'Unknown scheduled tasks'),
  ('__defaults__', 'agent_status', true, 'OsirisCare agent running'),
  ('__defaults__', 'bitlocker_status', true, 'BitLocker encryption'),
  ('__defaults__', 'smb_signing', true, 'SMB signing required'),
  ('__defaults__', 'smb1_protocol', true, 'SMB1 disabled'),
  ('__defaults__', 'screen_lock_policy', true, 'Screen lock timeout'),
  ('__defaults__', 'defender_exclusions', true, 'Defender exclusion review'),
  ('__defaults__', 'dns_config', true, 'DNS hijacking detection'),
  ('__defaults__', 'network_profile', true, 'Network profile check'),
  ('__defaults__', 'password_policy', true, 'Password policy compliance'),
  ('__defaults__', 'rdp_nla', true, 'RDP Network Level Auth'),
  ('__defaults__', 'guest_account', true, 'Guest account disabled'),
  ('__defaults__', 'service_dns', true, 'AD DNS service (DC)'),
  ('__defaults__', 'service_netlogon', true, 'AD Netlogon service (DC)'),
  ('__defaults__', 'wmi_event_persistence', true, 'WMI persistence detection'),
  ('__defaults__', 'registry_run_persistence', true, 'Registry Run key detection'),
  ('__defaults__', 'audit_policy', true, 'Audit policy subcategories'),
  ('__defaults__', 'defender_cloud_protection', true, 'Defender cloud/MAPS'),
  ('__defaults__', 'spooler_service', true, 'Print Spooler on DC'),
  -- Linux checks
  ('__defaults__', 'linux_firewall', true, 'UFW/iptables status'),
  ('__defaults__', 'linux_ssh_root', true, 'SSH root login'),
  ('__defaults__', 'linux_ssh_password', true, 'SSH password auth'),
  ('__defaults__', 'linux_failed_services', true, 'Failed systemd services'),
  ('__defaults__', 'linux_disk_space', true, 'Disk usage >90%'),
  ('__defaults__', 'linux_suid', true, 'SUID binary audit'),
  ('__defaults__', 'linux_unattended_upgrades', true, 'Auto-updates'),
  ('__defaults__', 'linux_audit', true, 'Auditd running'),
  ('__defaults__', 'linux_ntp', true, 'NTP sync'),
  ('__defaults__', 'linux_cert_expiry', true, 'Certificate expiry'),
  -- macOS checks
  ('__defaults__', 'macos_filevault', true, 'FileVault encryption'),
  ('__defaults__', 'macos_gatekeeper', true, 'Gatekeeper enabled'),
  ('__defaults__', 'macos_sip', true, 'System Integrity Protection'),
  ('__defaults__', 'macos_firewall', true, 'macOS Firewall'),
  ('__defaults__', 'macos_auto_update', true, 'Auto-updates'),
  ('__defaults__', 'macos_screen_lock', true, 'Screen lock'),
  ('__defaults__', 'macos_remote_login', false, 'SSH/Remote Login (off by default — management dependency)'),
  ('__defaults__', 'macos_file_sharing', true, 'SMB file sharing'),
  ('__defaults__', 'macos_time_machine', true, 'Time Machine backup'),
  ('__defaults__', 'macos_ntp_sync', true, 'NTP sync'),
  ('__defaults__', 'macos_admin_users', true, 'Admin user count'),
  ('__defaults__', 'macos_disk_space', true, 'Disk space >90%'),
  ('__defaults__', 'macos_cert_expiry', true, 'Certificate expiry')
ON CONFLICT DO NOTHING;

COMMIT;
