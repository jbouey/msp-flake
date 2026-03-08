import React, { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';
import { driftConfigApi } from '../utils/api';
import type { DriftCheckConfig } from '../utils/api';

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

export const DriftConfig: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const [checks, setChecks] = useState<DriftCheckConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await driftConfigApi.getConfig(siteId);
      setChecks(data.checks);
      setDirty({});
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load drift config');
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
    if (!siteId) return;
    const changedChecks = checks
      .filter(c => dirty[c.check_type])
      .map(c => ({ check_type: c.check_type, enabled: c.enabled }));

    if (changedChecks.length === 0) return;

    setSaving(true);
    setSaveMessage(null);
    try {
      await driftConfigApi.updateConfig(siteId, changedChecks);
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
    <div className="space-y-6 page-enter">
      {/* Back link */}
      <div className="flex items-center gap-2 text-sm">
        <Link to={`/sites/${siteId}`} className="text-accent-primary hover:underline">
          Site Detail
        </Link>
        <span className="text-label-tertiary">/</span>
        <span className="text-label-secondary">Drift Scan Config</span>
      </div>

      <GlassCard>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold text-label-primary tracking-tight">Drift Scan Configuration</h1>
            <p className="text-sm text-label-tertiary mt-1">
              Toggle individual security checks on or off for this site. Changes take effect on the next scan cycle.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {saveMessage && (
              <span className="text-sm text-health-healthy">{saveMessage}</span>
            )}
            <button
              onClick={saveChanges}
              disabled={dirtyCount === 0 || saving}
              className={`px-4 py-2 text-sm font-medium rounded-ios transition-colors ${
                dirtyCount > 0
                  ? 'bg-accent-primary text-white shadow-glow-teal hover:bg-accent-primary/90'
                  : 'bg-fill-tertiary text-label-tertiary cursor-not-allowed'
              }`}
            >
              {saving ? 'Saving...' : dirtyCount > 0 ? `Save ${dirtyCount} Change${dirtyCount > 1 ? 's' : ''}` : 'No Changes'}
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-ios bg-health-critical/10 border border-health-critical/20 text-health-critical text-sm">
            {error}
          </div>
        )}

        {loading && (
          <div className="text-center py-12">
            <Spinner size="lg" />
            <p className="text-label-tertiary mt-4">Loading drift config...</p>
          </div>
        )}

        {!loading && !error && grouped.map(group => {
          const enabledCount = group.checks.filter(c => c.enabled).length;
          const allEnabled = enabledCount === group.checks.length;
          const noneEnabled = enabledCount === 0;

          return (
            <div key={group.platform} className="mb-8 last:mb-0">
              <div className="flex items-center justify-between mb-3 pb-2 border-b border-separator-light">
                <div className="flex items-center gap-3">
                  <h2 className="text-base font-semibold text-label-primary">{group.label}</h2>
                  <span className="text-xs text-label-tertiary bg-fill-tertiary px-2 py-0.5 rounded-full">
                    {enabledCount}/{group.checks.length} enabled
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => togglePlatform(group.platform, true)}
                    disabled={allEnabled}
                    className="text-xs px-2 py-1 rounded bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    Enable All
                  </button>
                  <button
                    onClick={() => togglePlatform(group.platform, false)}
                    disabled={noneEnabled}
                    className="text-xs px-2 py-1 rounded bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
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
                    className={`flex items-center gap-3 p-3 rounded-ios-sm border transition-colors text-left ${
                      check.enabled
                        ? 'border-health-healthy/30 bg-health-healthy/5 hover:bg-health-healthy/10'
                        : 'border-separator-light bg-fill-primary hover:bg-fill-secondary'
                    } ${dirty[check.check_type] ? 'ring-2 ring-accent-primary/40' : ''}`}
                  >
                    <div className={`w-8 h-5 rounded-full relative transition-colors flex-shrink-0 ${
                      check.enabled ? 'bg-health-healthy' : 'bg-fill-quaternary'
                    }`}>
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                        check.enabled ? 'left-3.5' : 'left-0.5'
                      }`} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-label-primary truncate">
                        {CHECK_TYPE_LABELS[check.check_type] || check.check_type}
                      </div>
                      {check.notes && (
                        <div className="text-xs text-label-tertiary truncate">{check.notes}</div>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </GlassCard>
    </div>
  );
};

export default DriftConfig;
