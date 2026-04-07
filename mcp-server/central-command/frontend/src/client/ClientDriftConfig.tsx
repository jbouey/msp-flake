import React, { useState, useEffect, useCallback } from 'react';

interface DriftCheckConfig {
  check_type: string;
  enabled: boolean;
  platform: string;
  notes: string | null;
}

interface ClientDriftConfigProps {
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

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function csrfHeaders(): Record<string, string> {
  const token = getCsrfToken();
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) h['X-CSRF-Token'] = token;
  return h;
}

export const ClientDriftConfig: React.FC<ClientDriftConfigProps> = ({ siteId, siteName, onBack }) => {
  const [checks, setChecks] = useState<DriftCheckConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/client/sites/${siteId}/drift-config`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to load check configuration');
      const data = await response.json();
      setChecks(data.checks || []);
      setDirty({});
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load check configuration');
    } finally {
      setLoading(false);
    }
  }, [siteId]);

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
      const response = await fetch(`/api/client/sites/${siteId}/drift-config`, {
        method: 'PUT',
        headers: csrfHeaders(),
        credentials: 'include',
        body: JSON.stringify({ checks: changedChecks }),
      });
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
        className="flex items-center gap-2 text-sm text-teal-600 hover:text-teal-800 font-medium mb-4 transition"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to Dashboard
      </button>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-xl font-semibold text-slate-900 tracking-tight">Security Checks</h2>
            <p className="text-sm text-slate-500 mt-1">
              {siteName} &mdash; Toggle individual security checks on or off. Changes take effect on the next scan cycle.
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
                  ? 'bg-teal-600 text-white hover:bg-teal-700'
                  : 'bg-slate-100 text-slate-400 cursor-not-allowed'
              }`}
            >
              {saving ? 'Saving...' : dirtyCount > 0 ? `Save ${dirtyCount} Change${dirtyCount > 1 ? 's' : ''}` : 'No Changes'}
            </button>
          </div>
        </div>

        {/* Info banner */}
        <div className="mb-6 p-4 rounded-xl bg-teal-50 border border-teal-100">
          <p className="text-sm text-teal-800">
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
            <div className="w-8 h-8 border-2 border-teal-200 border-t-teal-600 rounded-full animate-spin mx-auto mb-4" />
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

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {group.checks.map(check => (
                  <button
                    key={check.check_type}
                    onClick={() => toggleCheck(check.check_type)}
                    className={`flex items-center gap-3 p-3 rounded-lg border transition text-left ${
                      check.enabled
                        ? 'border-green-200 bg-green-50 hover:bg-green-100'
                        : 'border-slate-200 bg-white hover:bg-slate-50'
                    } ${dirty[check.check_type] ? 'ring-2 ring-teal-300' : ''}`}
                  >
                    <div className={`w-8 h-5 rounded-full relative transition-colors flex-shrink-0 ${
                      check.enabled ? 'bg-green-500' : 'bg-slate-300'
                    }`}>
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                        check.enabled ? 'left-3.5' : 'left-0.5'
                      }`} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-slate-900 truncate">
                        {CHECK_TYPE_LABELS[check.check_type] || check.check_type}
                      </div>
                      {check.notes && (
                        <div className="text-xs text-slate-500 truncate">{check.notes}</div>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ClientDriftConfig;
