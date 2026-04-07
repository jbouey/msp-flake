import React, { useState, useEffect, useCallback } from 'react';
import { usePartner } from './PartnerContext';
import { InfoTip } from '../components/shared';

interface DriftCheckConfig {
  check_type: string;
  enabled: boolean;
  platform: string;
  notes: string | null;
}

interface PartnerDriftConfigProps {
  siteId: string;
  siteName: string;
  onBack: () => void;
}

const PLATFORM_LABELS: Record<string, string> = {
  windows: 'Windows',
  linux: 'Linux',
  macos: 'macOS',
};

const PLATFORM_ORDER = ['windows', 'linux', 'macos'];

const CHECK_TYPE_LABELS: Record<string, string> = {
  // Windows
  firewall_status: 'Firewall Profiles',
  windows_defender: 'Windows Defender',
  windows_update: 'Windows Update Service',
  audit_logging: 'Event Log Service',
  rogue_admin_users: 'Rogue Admin Users',
  rogue_scheduled_tasks: 'Rogue Scheduled Tasks',
  agent_status: 'OsirisCare Agent',
  bitlocker_status: 'BitLocker Encryption',
  smb_signing: 'SMB Signing',
  smb1_protocol: 'SMB1 Protocol',
  screen_lock_policy: 'Screen Lock Policy',
  defender_exclusions: 'Defender Exclusions',
  dns_config: 'DNS Configuration',
  network_profile: 'Network Profile',
  password_policy: 'Password Policy',
  rdp_nla: 'RDP Network Level Auth',
  guest_account: 'Guest Account',
  service_dns: 'AD DNS Service',
  service_netlogon: 'AD Netlogon Service',
  wmi_event_persistence: 'WMI Persistence',
  registry_run_persistence: 'Registry Run Keys',
  audit_policy: 'Audit Policy',
  defender_cloud_protection: 'Defender Cloud Protection',
  spooler_service: 'Print Spooler (DC)',
  // Linux
  linux_firewall: 'Firewall (UFW/iptables)',
  linux_ssh_root: 'SSH Root Login',
  linux_ssh_password: 'SSH Password Auth',
  linux_failed_services: 'Failed Services',
  linux_disk_space: 'Disk Space',
  linux_suid: 'SUID Binaries',
  linux_unattended_upgrades: 'Auto-Updates',
  linux_audit: 'Auditd',
  linux_ntp: 'NTP Sync',
  linux_cert_expiry: 'Certificate Expiry',
  // macOS
  macos_filevault: 'FileVault Encryption',
  macos_gatekeeper: 'Gatekeeper',
  macos_sip: 'System Integrity Protection',
  macos_firewall: 'Firewall',
  macos_auto_update: 'Auto-Updates',
  macos_screen_lock: 'Screen Lock',
  macos_remote_login: 'Remote Login (SSH)',
  macos_file_sharing: 'File Sharing (SMB)',
  macos_time_machine: 'Time Machine Backup',
  macos_ntp_sync: 'NTP Sync',
  macos_admin_users: 'Admin User Count',
  macos_disk_space: 'Disk Space',
  macos_cert_expiry: 'Certificate Expiry',
};

const CHECK_TYPE_TIPS: Record<string, string> = {
  // Windows
  firewall_status: 'Monitors whether the host firewall is enabled. A failure means the system is exposed to network threats.',
  windows_defender: 'Checks if antivirus protection is active. A failure means malware could go undetected.',
  windows_update: 'Verifies the Windows Update service is running. A failure means security patches may not install.',
  audit_logging: 'Checks if security events are being recorded. Required for audit trails.',
  rogue_admin_users: 'Detects unauthorized administrator accounts. Extra admins increase breach risk.',
  rogue_scheduled_tasks: 'Finds unexpected scheduled tasks that could indicate malware or unauthorized activity.',
  agent_status: 'Verifies the monitoring agent is running. A failure means this device is not being monitored.',
  bitlocker_status: 'Checks if disk encryption is active. Reduces exposure if a device is lost or stolen.',
  smb_signing: 'Verifies network file sharing is signed. Prevents tampering with data in transit.',
  smb1_protocol: 'Checks that the old, insecure SMBv1 protocol is disabled. SMBv1 has known vulnerabilities.',
  screen_lock_policy: 'Verifies screens lock automatically after inactivity. Helps restrict unauthorized access.',
  defender_exclusions: 'Monitors antivirus exclusions for suspicious entries that could hide malware.',
  dns_config: 'Checks DNS settings are correct. Wrong DNS can redirect users to malicious sites.',
  network_profile: 'Verifies the network is set to the correct profile. Public profiles have weaker protections.',
  password_policy: 'Checks that password rules meet security standards. Weak passwords are easy to guess.',
  rdp_nla: 'Verifies Remote Desktop requires authentication before connecting. Monitors for unauthorized remote access.',
  guest_account: 'Checks that the Guest account is disabled. Guest accounts bypass normal access controls.',
  service_dns: 'Monitors Active Directory DNS service health. DNS failures break authentication.',
  service_netlogon: 'Monitors Active Directory login service. Failures prevent users from signing in.',
  wmi_event_persistence: 'Detects malware that hides in Windows Management events to survive reboots.',
  registry_run_persistence: 'Checks for unauthorized programs set to run at startup via the registry.',
  audit_policy: 'Verifies security audit policies are properly configured for compliance logging.',
  defender_cloud_protection: 'Checks cloud-based threat detection is active. Catches new threats faster.',
  spooler_service: 'Checks Print Spooler on domain controllers. Should be disabled to reduce known attack surface.',
  // Linux
  linux_firewall: 'Checks if the Linux firewall is active. A failure means the system is exposed.',
  linux_ssh_root: 'Verifies root SSH login is disabled. Direct root access is a security risk.',
  linux_ssh_password: 'Checks if password-based SSH is disabled. Key-based auth is more secure.',
  linux_failed_services: 'Detects crashed or failed system services that may affect security.',
  linux_disk_space: 'Monitors available disk space. Full disks can cause logging and backup failures.',
  linux_suid: 'Checks for unexpected privileged binaries. Unauthorized SUID files can be exploited.',
  linux_unattended_upgrades: 'Verifies automatic security updates are enabled. Unpatched systems are vulnerable.',
  linux_audit: 'Checks the audit logging daemon is active. Required for compliance event tracking.',
  linux_ntp: 'Verifies time synchronization is active. Incorrect time breaks audit log accuracy.',
  linux_cert_expiry: 'Monitors SSL certificate expiration. Expired certs cause outages and security warnings.',
  // macOS
  macos_filevault: 'Checks if FileVault disk encryption is active. Helps reduce exposure if a Mac is lost.',
  macos_gatekeeper: 'Verifies only trusted software can run. Helps detect malicious app installation.',
  macos_sip: 'Checks System Integrity Protection is on. Monitors for malware modifying system files.',
  macos_firewall: 'Monitors whether the macOS firewall is enabled and blocking unauthorized connections.',
  macos_auto_update: 'Verifies automatic software updates are enabled. Keeps security patches current.',
  macos_screen_lock: 'Checks that the screen locks automatically. Helps reduce unauthorized access when unattended.',
  macos_remote_login: 'Monitors SSH access settings. Unrestricted remote login increases attack surface.',
  macos_file_sharing: 'Checks if file sharing is enabled. Unnecessary sharing exposes data.',
  macos_time_machine: 'Verifies Time Machine backup is active. Required for disaster recovery.',
  macos_ntp_sync: 'Checks time synchronization. Accurate time is needed for audit log integrity.',
  macos_admin_users: 'Monitors the number of admin accounts. Too many admins increases risk.',
  macos_disk_space: 'Monitors available disk space. Full disks can cause backup and logging failures.',
  macos_cert_expiry: 'Monitors SSL certificate expiration dates. Expired certs cause outages.',
};

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export const PartnerDriftConfig: React.FC<PartnerDriftConfigProps> = ({ siteId, siteName, onBack }) => {
  const { apiKey } = usePartner();

  const [checks, setChecks] = useState<DriftCheckConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const buildFetchOptions = useCallback((method: string = 'GET', body?: unknown): RequestInit => {
    const headers: Record<string, string> = {};
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    if (body) {
      headers['Content-Type'] = 'application/json';
      const csrfToken = getCsrfToken();
      if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    }
    return {
      method,
      headers,
      credentials: apiKey ? undefined : 'include',
      body: body ? JSON.stringify(body) : undefined,
    };
  }, [apiKey]);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/partners/me/sites/${siteId}/drift-config`,
        buildFetchOptions()
      );
      if (!response.ok) throw new Error('Failed to load check configuration');
      const data = await response.json();
      setChecks(data.checks || []);
      setDirty({});
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load check config');
    } finally {
      setLoading(false);
    }
  }, [siteId, buildFetchOptions]);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const toggleCheck = (checkType: string) => {
    setChecks(prev => prev.map(c =>
      c.check_type === checkType ? { ...c, enabled: !c.enabled } : c
    ));
    setDirty(prev => ({ ...prev, [checkType]: true }));
    setSaveMessage(null);
  };

  const togglePlatform = (platform: string, enable: boolean) => {
    setChecks(prev => prev.map(c =>
      c.platform === platform ? { ...c, enabled: enable } : c
    ));
    const platformChecks = checks.filter(c => c.platform === platform);
    const newDirty = { ...dirty };
    platformChecks.forEach(c => { newDirty[c.check_type] = true; });
    setDirty(newDirty);
    setSaveMessage(null);
  };

  const saveChanges = async () => {
    const changedChecks = checks
      .filter(c => dirty[c.check_type])
      .map(c => ({ check_type: c.check_type, enabled: c.enabled }));

    if (changedChecks.length === 0) return;

    setSaving(true);
    setSaveMessage(null);
    try {
      const response = await fetch(
        `/api/partners/me/sites/${siteId}/drift-config`,
        buildFetchOptions('PUT', { checks: changedChecks })
      );
      if (!response.ok) throw new Error('Failed to save check configuration');
      setDirty({});
      setSaveMessage(`Saved ${changedChecks.length} change${changedChecks.length > 1 ? 's' : ''}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const dirtyCount = Object.keys(dirty).length;
  const grouped = PLATFORM_ORDER.map(p => ({
    platform: p,
    label: PLATFORM_LABELS[p] || p,
    checks: checks.filter(c => c.platform === p),
  })).filter(g => g.checks.length > 0);

  return (
    <div>
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 font-medium mb-4 transition"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to Sites
      </button>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-xl font-semibold text-slate-900 tracking-tight">Security Checks</h2>
            <p className="text-sm text-slate-500 mt-1">
              {siteName} &mdash; Toggle individual compliance checks on or off. Changes take effect on the next scan cycle.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {saveMessage && (
              <span className="text-sm text-green-600 font-medium">{saveMessage}</span>
            )}
            <button
              onClick={saveChanges}
              disabled={dirtyCount === 0 || saving}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition ${
                dirtyCount > 0
                  ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                  : 'bg-slate-100 text-slate-400 cursor-not-allowed'
              }`}
            >
              {saving ? 'Saving...' : dirtyCount > 0 ? `Save ${dirtyCount} Change${dirtyCount > 1 ? 's' : ''}` : 'No Changes'}
            </button>
          </div>
        </div>

        {/* Info banner */}
        <div className="mb-6 p-4 rounded-xl bg-indigo-50 border border-indigo-100">
          <p className="text-sm text-indigo-800">
            Disabled checks are excluded from compliance scoring. Your compliance score reflects only the checks you have enabled.
          </p>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm">
            {error}
          </div>
        )}

        {loading && (
          <div className="text-center py-12">
            <div className="w-8 h-8 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin mx-auto mb-4" />
            <p className="text-slate-500 text-sm">Loading security checks...</p>
          </div>
        )}

        {!loading && !error && grouped.map(group => {
          const enabledCount = group.checks.filter(c => c.enabled).length;
          const allEnabled = enabledCount === group.checks.length;
          const noneEnabled = enabledCount === 0;

          return (
            <div key={group.platform} className="mb-8 last:mb-0">
              <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-200">
                <div className="flex items-center gap-3">
                  <h3 className="text-base font-semibold text-slate-900">{group.label}</h3>
                  <span className="text-xs text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
                    {enabledCount}/{group.checks.length} enabled
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => togglePlatform(group.platform, true)}
                    disabled={allEnabled}
                    className="text-xs px-2 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
                  >
                    Enable All
                  </button>
                  <button
                    onClick={() => togglePlatform(group.platform, false)}
                    disabled={noneEnabled}
                    className="text-xs px-2 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
                  >
                    Disable All
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-2">
                {group.checks.map(check => {
                  const checkLabel = CHECK_TYPE_LABELS[check.check_type] || check.check_type;
                  return (
                    <label
                      key={check.check_type}
                      className={`flex items-center gap-3 p-3 rounded-lg border transition cursor-pointer ${
                        check.enabled
                          ? 'border-green-200 bg-green-50 hover:bg-green-100'
                          : 'border-slate-200 bg-white hover:bg-slate-50'
                      } ${dirty[check.check_type] ? 'ring-2 ring-indigo-300' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={check.enabled}
                        onChange={() => toggleCheck(check.check_type)}
                        className="sr-only"
                        aria-label={`Enable ${checkLabel}`}
                      />
                      <div className={`w-8 h-5 rounded-full relative transition-colors flex-shrink-0 ${
                        check.enabled ? 'bg-green-500' : 'bg-slate-300'
                      }`} aria-hidden="true">
                        <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                          check.enabled ? 'left-3.5' : 'left-0.5'
                        }`} />
                      </div>
                      <div className="min-w-0">
                        <span className="text-sm font-medium text-slate-900 truncate block">
                          {checkLabel}{CHECK_TYPE_TIPS[check.check_type] && <InfoTip text={CHECK_TYPE_TIPS[check.check_type]} />}
                        </span>
                        {check.notes && (
                          <span className="text-xs text-slate-500 truncate block">{check.notes}</span>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default PartnerDriftConfig;
