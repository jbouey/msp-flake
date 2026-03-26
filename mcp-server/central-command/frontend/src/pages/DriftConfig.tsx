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

const HIPAA_CONTROL_MAP: Record<string, string> = {
  // Access Control
  'windows_password_policy': '\u00a7164.312(d)',
  'windows_screen_lock_policy': '\u00a7164.312(a)(2)(iii)',
  'rogue_admin_users': '\u00a7164.312(a)(1)',
  'linux_ssh_config': '\u00a7164.312(a)(1)',
  'linux_accounts': '\u00a7164.312(a)(1)',
  'linux_permissions': '\u00a7164.312(a)(2)(i)',
  // Encryption
  'bitlocker': '\u00a7164.312(a)(2)(iv)',
  'windows_bitlocker_status': '\u00a7164.312(a)(2)(iv)',
  'windows_smb_signing': '\u00a7164.312(e)(1)',
  'linux_crypto': '\u00a7164.312(a)(2)(iv)',
  // Audit/Logging
  'audit_logging': '\u00a7164.312(b)',
  'windows_audit_policy': '\u00a7164.312(b)',
  'linux_audit': '\u00a7164.312(b)',
  'linux_logging': '\u00a7164.312(b)',
  // Firewall
  'firewall': '\u00a7164.312(a)(1)',
  'windows_firewall_status': '\u00a7164.312(a)(1)',
  'firewall_status': '\u00a7164.312(a)(1)',
  'linux_firewall': '\u00a7164.312(a)(1)',
  // Patching
  'windows_update': '\u00a7164.308(a)(1)',
  'nixos_generation': '\u00a7164.308(a)(1)',
  'linux_patching': '\u00a7164.308(a)(1)',
  // Backup
  'backup_status': '\u00a7164.308(a)(7)',
  'windows_backup_status': '\u00a7164.308(a)(7)',
  // Antivirus
  'windows_defender': '\u00a7164.308(a)(5)',
  'windows_defender_exclusions': '\u00a7164.308(a)(5)',
  // Services
  'critical_services': '\u00a7164.308(a)(1)(ii)(D)',
  'linux_services': '\u00a7164.308(a)(1)(ii)(D)',
};

export const DriftConfig: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const [checks, setChecks] = useState<DriftCheckConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [disableAllWarning, setDisableAllWarning] = useState<string | null>(null);
  // N/A modal state
  const [naModal, setNaModal] = useState<{ checkType: string; reason: string } | null>(null);

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
    setChecks(prev => prev.map(c => {
      if (c.check_type !== checkType) return c;
      // If currently N/A, cycle back to enabled
      if (c.status === 'not_applicable') {
        return { ...c, enabled: true, status: 'enabled' as const, exception_reason: '' };
      }
      // Normal toggle: enabled <-> disabled
      const newEnabled = !c.enabled;
      return { ...c, enabled: newEnabled, status: newEnabled ? 'enabled' as const : 'disabled' as const };
    }));
    setDirty(prev => ({ ...prev, [checkType]: true }));
    setSaveMessage(null);
  };

  const markNotApplicable = (checkType: string, reason: string) => {
    setChecks(prev => prev.map(c =>
      c.check_type === checkType
        ? { ...c, enabled: false, status: 'not_applicable' as const, exception_reason: reason }
        : c
    ));
    setDirty(prev => ({ ...prev, [checkType]: true }));
    setSaveMessage(null);
  };

  const togglePlatform = (platform: string, enable: boolean) => {
    setChecks(prev => prev.map(c =>
      c.platform === platform
        ? { ...c, enabled: enable, status: enable ? 'enabled' as const : 'disabled' as const, exception_reason: enable ? '' : c.exception_reason }
        : c
    ));
    const platformChecks = checks.filter(c => c.platform === platform);
    const newDirty = { ...dirty };
    platformChecks.forEach(c => { newDirty[c.check_type] = true; });
    setDirty(newDirty);
    setSaveMessage(null);
    if (!enable) {
      const label = PLATFORM_LABELS[platform] || platform;
      setDisableAllWarning(`Warning: Disabling all checks removes HIPAA compliance monitoring for ${label}.`);
    } else {
      setDisableAllWarning(null);
    }
  };

  const saveChanges = async () => {
    if (!siteId) return;
    const changedChecks = checks
      .filter(c => dirty[c.check_type])
      .map(c => ({
        check_type: c.check_type,
        enabled: c.enabled,
        status: c.status,
        exception_reason: c.exception_reason || undefined,
      }));

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
              Toggle individual security checks on or off for this site. Mark checks as N/A with a documented reason when handled externally. Changes take effect on the next scan cycle.
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

        {/* Legend */}
        <div className="flex items-center gap-4 mb-4 text-xs text-label-tertiary">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-health-healthy" />
            <span>Enabled</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-fill-quaternary" />
            <span>Disabled</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-indigo-500/20 border border-indigo-400/40" />
            <span>N/A (not applicable)</span>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-ios bg-health-critical/10 border border-health-critical/20 text-health-critical text-sm">
            {error}
          </div>
        )}

        {disableAllWarning && (
          <div className="mb-4 p-3 rounded-ios bg-health-warning/10 border border-health-warning/20 text-health-warning text-sm">
            {disableAllWarning}
          </div>
        )}

        {loading && (
          <div className="text-center py-12">
            <Spinner size="lg" />
            <p className="text-label-tertiary mt-4">Loading drift config...</p>
          </div>
        )}

        {!loading && !error && grouped.map(group => {
          const enabledCount = group.checks.filter(c => c.status === 'enabled').length;
          const naCount = group.checks.filter(c => c.status === 'not_applicable').length;
          const allEnabled = enabledCount === group.checks.length;
          const noneEnabled = enabledCount === 0 && naCount === 0;

          return (
            <div key={group.platform} className="mb-8 last:mb-0">
              <div className="flex items-center justify-between mb-3 pb-2 border-b border-separator-light">
                <div className="flex items-center gap-3">
                  <h2 className="text-base font-semibold text-label-primary">{group.label}</h2>
                  <span className="text-xs text-label-tertiary bg-fill-tertiary px-2 py-0.5 rounded-full">
                    {enabledCount}/{group.checks.length} enabled
                    {naCount > 0 && `, ${naCount} N/A`}
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
                {group.checks.map(check => {
                  const isNA = check.status === 'not_applicable';
                  const isEnabled = check.status === 'enabled';

                  return (
                    <div
                      key={check.check_type}
                      className={`flex items-center gap-3 p-3 rounded-ios-sm border transition-colors text-left ${
                        isNA
                          ? 'border-indigo-400/30 bg-indigo-500/5'
                          : isEnabled
                            ? 'border-health-healthy/30 bg-health-healthy/5 hover:bg-health-healthy/10'
                            : 'border-separator-light bg-fill-primary hover:bg-fill-secondary'
                      } ${dirty[check.check_type] ? 'ring-2 ring-accent-primary/40' : ''}`}
                    >
                      {/* Toggle -- clicking cycles enabled/disabled (skips N/A) */}
                      <button
                        onClick={() => toggleCheck(check.check_type)}
                        className="flex-shrink-0"
                        title={isNA ? 'Click to re-enable' : isEnabled ? 'Click to disable' : 'Click to enable'}
                      >
                        {isNA ? (
                          <div className="w-8 h-5 rounded-full bg-indigo-500/30 relative">
                            <div className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-indigo-400">
                              N/A
                            </div>
                          </div>
                        ) : (
                          <div className={`w-8 h-5 rounded-full relative transition-colors ${
                            isEnabled ? 'bg-health-healthy' : 'bg-fill-quaternary'
                          }`}>
                            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                              isEnabled ? 'left-3.5' : 'left-0.5'
                            }`} />
                          </div>
                        )}
                      </button>

                      {/* Label */}
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-label-primary truncate">
                          {CHECK_TYPE_LABELS[check.check_type] || check.check_type}
                          {HIPAA_CONTROL_MAP[check.check_type] && (
                            <span className="ml-2 text-xs text-label-tertiary font-mono">
                              {HIPAA_CONTROL_MAP[check.check_type]}
                            </span>
                          )}
                        </div>
                        {isNA && check.exception_reason && (
                          <div className="text-xs text-indigo-400 truncate" title={check.exception_reason}>
                            N/A: {check.exception_reason}
                          </div>
                        )}
                        {!isNA && check.notes && (
                          <div className="text-xs text-label-tertiary truncate">{check.notes}</div>
                        )}
                      </div>

                      {/* N/A button */}
                      {!isNA ? (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setNaModal({ checkType: check.check_type, reason: '' });
                          }}
                          className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 hover:bg-indigo-500/20 border border-indigo-400/20 transition-colors"
                          title="Mark as Not Applicable"
                        >
                          N/A
                        </button>
                      ) : (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleCheck(check.check_type);
                          }}
                          className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-fill-tertiary text-label-tertiary hover:bg-fill-secondary border border-separator-light transition-colors"
                          title="Re-enable this check"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </GlassCard>

      {/* N/A Modal */}
      {naModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="bg-background-primary border border-separator-light rounded-ios shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-lg font-semibold text-label-primary mb-2">
              Mark as Not Applicable
            </h3>
            <p className="text-sm text-label-secondary mb-1">
              <span className="font-medium">{CHECK_TYPE_LABELS[naModal.checkType] || naModal.checkType}</span>
            </p>
            <p className="text-xs text-label-tertiary mb-4">
              This check will be excluded from compliance scoring and will not trigger the healing pipeline. Provide a documented reason for audit purposes.
            </p>
            <textarea
              autoFocus
              value={naModal.reason}
              onChange={e => setNaModal({ ...naModal, reason: e.target.value })}
              placeholder='e.g., "Backup handled by cloud EHR vendor per BAA with athenahealth"'
              className="w-full h-24 px-3 py-2 text-sm bg-fill-primary border border-separator-light rounded-ios text-label-primary placeholder:text-label-tertiary resize-none focus:outline-none focus:ring-2 focus:ring-accent-primary/40"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setNaModal(null)}
                className="px-4 py-2 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (naModal.reason.trim()) {
                    markNotApplicable(naModal.checkType, naModal.reason.trim());
                    setNaModal(null);
                  }
                }}
                disabled={!naModal.reason.trim()}
                className={`px-4 py-2 text-sm font-medium rounded-ios transition-colors ${
                  naModal.reason.trim()
                    ? 'bg-indigo-600 text-white hover:bg-indigo-500'
                    : 'bg-fill-tertiary text-label-tertiary cursor-not-allowed'
                }`}
              >
                Mark as N/A
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DriftConfig;
