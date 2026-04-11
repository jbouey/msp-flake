import React, { useState } from 'react';
import { GlassCard, ActionDropdown } from '../../../components/shared';
import type { ActionItem } from '../../../components/shared';
import { StatusBadge } from '../../../components/composed';
import type { SiteAppliance, OrderType } from '../../../utils/api';
import { formatTimeAgo } from '../../../constants';

const formatRelativeTime = formatTimeAgo;

/**
 * Format uptime
 */
function formatUptime(seconds: number | null): string {
  if (!seconds) return 'Unknown';

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

/**
 * Appliance card component with action buttons
 */
const AGENT_PACKAGE_BASE_URL = 'https://api.osiriscare.net/agent-packages';

const L2ModeToggle: React.FC<{
  mode: string;
  onChange: (mode: string) => void;
  disabled?: boolean;
}> = ({ mode, onChange, disabled }) => {
  const modes = [
    { value: 'auto', label: 'Auto', color: 'bg-health-healthy text-white' },
    { value: 'manual', label: 'Manual', color: 'bg-health-warning text-white' },
    { value: 'disabled', label: 'Off', color: 'bg-health-critical text-white' },
  ];

  return (
    <div className="inline-flex rounded-lg bg-fill-secondary p-0.5">
      {modes.map((m) => (
        <button
          key={m.value}
          onClick={() => onChange(m.value)}
          disabled={disabled}
          className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
            mode === m.value
              ? m.color + ' shadow-sm'
              : 'text-label-tertiary hover:text-label-secondary'
          } disabled:opacity-50`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
};

export interface ApplianceCardProps {
  appliance: SiteAppliance;
  latestVersion: string | null;
  onCreateOrder: (applianceId: string, orderType: OrderType, parameters?: Record<string, unknown>) => void;
  onDelete: (applianceId: string) => void;
  onUpdateL2Mode: (applianceId: string, mode: string) => void;
  onMove?: (applianceId: string) => void;
  isLoading?: boolean;
  applianceCount?: number;
}

export const ApplianceCard: React.FC<ApplianceCardProps> = ({ appliance, latestVersion, onCreateOrder, onDelete, onUpdateL2Mode, onMove, isLoading, applianceCount }) => {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showUpdateModal, setShowUpdateModal] = useState(false);

  // Check if agent is outdated (only if we know the latest version)
  const isOutdated = latestVersion && appliance.agent_version && appliance.agent_version !== latestVersion;

  const statusColors = {
    online: 'bg-health-healthy',
    stale: 'bg-health-warning',
    offline: 'bg-health-critical',
    pending: 'bg-label-tertiary',
  };

  const moreActions: ActionItem[] = [
    {
      label: 'View Logs',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>,
      onClick: () => onCreateOrder(appliance.appliance_id, 'collect_logs'),
    },
    {
      label: 'Restart Agent',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>,
      onClick: () => onCreateOrder(appliance.appliance_id, 'restart_agent'),
    },
    {
      label: 'Update Agent',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>,
      onClick: () => setShowUpdateModal(true),
    },
    ...(onMove ? [{
      label: 'Move to Site',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" /></svg>,
      onClick: () => onMove(appliance.appliance_id),
    }] : []),
    {
      label: 'Delete Appliance',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>,
      onClick: () => setShowDeleteConfirm(true),
      danger: true,
    },
  ];

  return (
    <GlassCard className="p-4">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`w-3 h-3 rounded-full ${statusColors[appliance.live_status]}`} />
          <h3 className="font-semibold text-label-primary">
            {appliance.display_name || appliance.hostname || appliance.appliance_id}
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {(appliance as any).boot_source === 'live_usb' && (
            <span className="px-2 py-0.5 text-xs font-bold rounded-full bg-amber-100 text-amber-700 animate-pulse">
              Installing
            </span>
          )}
          <StatusBadge status={appliance.live_status} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-label-tertiary">MAC Address</p>
          <p className="text-label-secondary font-mono">{appliance.mac_address || '-'}</p>
        </div>
        <div>
          <p className="text-label-tertiary">IP Addresses</p>
          <p className="text-label-secondary font-mono">
            {appliance.ip_addresses.length > 0 ? appliance.ip_addresses.join(', ') : '-'}
          </p>
        </div>
        <div>
          <p className="text-label-tertiary">Agent Version</p>
          <p className="text-label-secondary">{appliance.agent_version || '-'}</p>
        </div>
        <div>
          <p className="text-label-tertiary">NixOS Version</p>
          <p className="text-label-secondary">{appliance.nixos_version || '-'}</p>
        </div>
        <div>
          <p className="text-label-tertiary">Last Checkin</p>
          <p className="text-label-secondary">{formatRelativeTime(appliance.last_checkin)}</p>
        </div>
        <div>
          <p className="text-label-tertiary">Uptime</p>
          <p className="text-label-secondary">{formatUptime(appliance.uptime_seconds)}</p>
        </div>
      </div>

      {/* Assigned Targets */}
      {(applianceCount ?? 0) >= 1 && (
        <div className="mt-3 pt-3 border-t border-separator-light">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${
                (appliance.assigned_target_count || 0) > 0 ? 'bg-health-healthy' : 'bg-label-tertiary'
              }`} />
              <p className="text-xs text-label-tertiary">Devices Monitored</p>
            </div>
            <p className="text-xs text-label-secondary">
              {appliance.mesh_ring_size > 1
                ? `${appliance.assigned_target_count || 0} devices (${appliance.mesh_ring_size} appliances coordinating)`
                : `${appliance.assigned_target_count || 0} devices`}
            </p>
          </div>
        </div>
      )}

      {/* L2 Healing Mode */}
      <div className="mt-3 pt-3 border-t border-separator-light flex items-center justify-between">
        <div>
          <p className="text-xs text-label-tertiary">L2 Healing (LLM)</p>
        </div>
        <L2ModeToggle
          mode={appliance.l2_mode || 'auto'}
          onChange={(mode) => onUpdateL2Mode(appliance.appliance_id, mode)}
          disabled={isLoading}
        />
      </div>

      {/* Action Buttons */}
      <div className="mt-4 pt-4 border-t border-separator-light flex items-center justify-between">
        <div className="flex gap-2">
          <button
            onClick={() => onCreateOrder(appliance.appliance_id, 'force_checkin')}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs rounded-ios bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 disabled:opacity-50 transition-colors"
          >
            Force Checkin
          </button>
          <button
            onClick={() => onCreateOrder(appliance.appliance_id, 'run_drift')}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary disabled:opacity-50 transition-colors"
          >
            Run Scan
          </button>
          <button
            onClick={() => onCreateOrder(appliance.appliance_id, 'sync_rules')}
            disabled={isLoading}
            className="px-3 py-1.5 text-xs rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary disabled:opacity-50 transition-colors"
          >
            Sync Rules
          </button>
          {/* Prominent Push Update button for outdated agents */}
          {isOutdated && (
            <button
              onClick={() => setShowUpdateModal(true)}
              disabled={isLoading}
              className="px-3 py-1.5 text-xs rounded-ios bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white font-medium disabled:opacity-50 transition-all shadow-sm"
            >
              <span className="inline-block w-1.5 h-1.5 bg-white rounded-full mr-1.5 align-middle" />
              Update Available ({appliance.agent_version} &rarr; {latestVersion || 'Unknown'})
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Show prominent delete for offline appliances */}
          {appliance.live_status === 'offline' && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              disabled={isLoading}
              className="px-3 py-1.5 text-xs rounded-ios bg-gradient-to-r from-red-600 to-orange-500 hover:from-red-700 hover:to-orange-600 text-white font-medium disabled:opacity-50 transition-all shadow-sm"
            >
              🗑️ Delete Stale
            </button>
          )}
          <ActionDropdown actions={moreActions} label="" disabled={isLoading} />
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-sm">
            <h2 className="text-lg font-semibold text-label-primary mb-2">Delete Appliance?</h2>
            <p className="text-label-secondary text-sm mb-4">
              Are you sure you want to delete <strong>{appliance.hostname || appliance.appliance_id}</strong>?
              This will remove it from the site and cancel any pending orders.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onDelete(appliance.appliance_id);
                  setShowDeleteConfirm(false);
                }}
                className="flex-1 px-4 py-2 rounded-ios bg-gradient-to-r from-red-600 to-orange-500 hover:from-red-700 hover:to-orange-600 text-white font-semibold shadow-md transition-all"
              >
                Delete Forever
              </button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Agent Update Modal */}
      {showUpdateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-md">
            <h2 className="text-lg font-semibold text-label-primary mb-2">Push Agent Update</h2>
            <p className="text-label-secondary text-sm mb-4">
              Update <strong>{appliance.hostname || appliance.appliance_id}</strong> from version{' '}
              <code className="bg-fill-secondary px-1 rounded">{appliance.agent_version || 'unknown'}</code> to{' '}
              <code className="bg-fill-secondary px-1 rounded">{latestVersion || 'Unknown'}</code>
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowUpdateModal(false)}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onCreateOrder(appliance.appliance_id, 'update_agent', {
                    package_url: `${AGENT_PACKAGE_BASE_URL}/compliance_agent-${latestVersion!}.tar.gz`,
                    version: latestVersion!,
                  });
                  setShowUpdateModal(false);
                }}
                disabled={isLoading}
                className="flex-1 px-4 py-2 rounded-ios bg-gradient-to-r from-blue-600 to-purple-500 hover:from-blue-700 hover:to-purple-600 text-white font-semibold shadow-md transition-all disabled:opacity-50"
              >
                Push Update
              </button>
            </div>
          </GlassCard>
        </div>
      )}
    </GlassCard>
  );
};
