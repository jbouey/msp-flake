import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge, ActionDropdown, EmptyState, OnboardingChecklist } from '../components/shared';
import type { ActionItem } from '../components/shared';
import { StatusBadge } from '../components/composed';
import { DeploymentProgress } from '../components/deployment';
import { useSite, useAddCredential, useCreateApplianceOrder, useBroadcastOrder, useDeleteAppliance, useClearStaleAppliances, useUpdateHealingTier, useUpdateL2Mode } from '../hooks';
import type { SiteDetail as SiteDetailType, SiteAppliance, OrderType } from '../utils/api';
import { fleetUpdatesApi, decommissionApi, applianceApi, type FleetStats } from '../utils/api';
import { ComplianceHealthInfographic } from '../client/ComplianceHealthInfographic';
import { DevicesAtRisk } from '../client/DevicesAtRisk';
import { formatTimeAgo } from '../constants';

function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

// formatRelativeTime replaced by centralized formatTimeAgo from constants
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

const ApplianceCard: React.FC<{
  appliance: SiteAppliance;
  latestVersion: string | null;
  onCreateOrder: (applianceId: string, orderType: OrderType, parameters?: Record<string, unknown>) => void;
  onDelete: (applianceId: string) => void;
  onUpdateL2Mode: (applianceId: string, mode: string) => void;
  onMove?: (applianceId: string) => void;
  isLoading?: boolean;
}> = ({ appliance, latestVersion, onCreateOrder, onDelete, onUpdateL2Mode, onMove, isLoading }) => {
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
            {appliance.hostname || appliance.appliance_id}
          </h3>
        </div>
        <StatusBadge status={appliance.live_status} />
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
            Run Drift
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
              className="px-3 py-1.5 text-xs rounded-ios bg-gradient-to-r from-blue-600 to-purple-500 hover:from-blue-700 hover:to-purple-600 text-white font-medium disabled:opacity-50 transition-all shadow-sm animate-pulse"
            >
              Push Update ({appliance.agent_version} &rarr; {latestVersion})
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
              <code className="bg-fill-secondary px-1 rounded">{latestVersion}</code>
            </p>
            <div className="bg-fill-secondary rounded-ios p-3 mb-4 text-sm">
              <p className="text-label-tertiary mb-1">Package URL:</p>
              <p className="text-label-primary font-mono text-xs break-all">
                {AGENT_PACKAGE_BASE_URL}/compliance_agent-{latestVersion}.tar.gz
              </p>
            </div>
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

/**
 * Onboarding progress component
 */
const OnboardingProgress: React.FC<{ timestamps: SiteDetailType['timestamps']; stage: string }> = ({ timestamps, stage }) => {
  const stages = [
    { key: 'lead_at', label: 'Lead', icon: '👤' },
    { key: 'discovery_at', label: 'Discovery', icon: '🔍' },
    { key: 'proposal_at', label: 'Proposal', icon: '📋' },
    { key: 'contract_at', label: 'Contract', icon: '✍️' },
    { key: 'intake_at', label: 'Intake', icon: '📝' },
    { key: 'creds_at', label: 'Credentials', icon: '🔑' },
    { key: 'shipped_at', label: 'Shipped', icon: '📦' },
    { key: 'received_at', label: 'Received', icon: '📬' },
    { key: 'connectivity_at', label: 'Connected', icon: '🔌' },
    { key: 'scanning_at', label: 'Scanning', icon: '🔬' },
    { key: 'baseline_at', label: 'Baseline', icon: '📊' },
    { key: 'active_at', label: 'Active', icon: '✅' },
  ];

  return (
    <div className="space-y-2">
      {stages.map((s) => {
        const completed = timestamps[s.key as keyof typeof timestamps] !== null;
        const isCurrent = stage === s.key.replace('_at', '');

        return (
          <div
            key={s.key}
            className={`flex items-center gap-3 py-2 px-3 rounded-ios transition-colors ${
              isCurrent ? 'bg-accent-primary/10' :
              completed ? 'bg-fill-secondary' : ''
            }`}
          >
            <span className="text-lg">{completed ? '✅' : s.icon}</span>
            <span className={`flex-1 ${completed ? 'text-label-primary' : 'text-label-tertiary'}`}>
              {s.label}
            </span>
            {timestamps[s.key as keyof typeof timestamps] && (
              <span className="text-xs text-label-tertiary">
                {new Date(timestamps[s.key as keyof typeof timestamps]!).toLocaleDateString()}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
};

/**
 * Add Credential Modal
 */
const AddCredentialModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { credential_type: string; credential_name: string; username?: string; password?: string; host?: string }) => void;
  isLoading: boolean;
}> = ({ isOpen, onClose, onSubmit, isLoading }) => {
  const [credType, setCredType] = useState('router');
  const [credName, setCredName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [host, setHost] = useState('');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      credential_type: credType,
      credential_name: credName,
      username: username || undefined,
      password: password || undefined,
      host: host || undefined,
    });
    // Reset form
    setCredName('');
    setUsername('');
    setPassword('');
    setHost('');
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Add Credential</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Type</label>
            <select
              value={credType}
              onChange={(e) => setCredType(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            >
              <option value="router">Router</option>
              <option value="active_directory">Active Directory</option>
              <option value="ehr">EHR System</option>
              <option value="backup">Backup Service</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Name *</label>
            <input
              type="text"
              value={credName}
              onChange={(e) => setCredName(e.target.value)}
              placeholder="e.g., Main Router, Domain Admin"
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Host/IP</label>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="192.168.1.1"
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light"
            />
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!credName || isLoading}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white disabled:opacity-50"
            >
              {isLoading ? 'Adding...' : 'Add Credential'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Site action toolbar for bulk operations
 */
const SiteActionToolbar: React.FC<{
  applianceCount: number;
  onBroadcast: (orderType: OrderType) => void;
  onClearStale: () => void;
  isLoading: boolean;
}> = ({ applianceCount, onBroadcast, onClearStale, isLoading }) => {
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  if (applianceCount === 0) return null;

  return (
    <>
      <div className="flex items-center gap-2 mb-4">
        <span className="text-label-tertiary text-sm mr-2">Site Actions:</span>
        <button
          onClick={() => onBroadcast('force_checkin')}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 disabled:opacity-50 transition-colors"
        >
          Force All Checkin
        </button>
        <button
          onClick={() => onBroadcast('sync_rules')}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary disabled:opacity-50 transition-colors"
        >
          Sync All Rules
        </button>
        <button
          onClick={() => setShowClearConfirm(true)}
          disabled={isLoading}
          className="px-3 py-1.5 text-xs rounded-ios bg-health-warning/10 text-health-warning hover:bg-health-warning/20 disabled:opacity-50 transition-colors"
        >
          Clear Stale
        </button>
      </div>

      {/* Clear Stale Confirmation Modal */}
      {showClearConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-sm">
            <h2 className="text-lg font-semibold text-label-primary mb-2">Clear Stale Appliances?</h2>
            <p className="text-label-secondary text-sm mb-4">
              This will remove all appliances that haven't checked in for more than 24 hours.
              This action cannot be undone.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowClearConfirm(false)}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  onClearStale();
                  setShowClearConfirm(false);
                }}
                className="flex-1 px-4 py-2 rounded-ios bg-health-warning text-white"
              >
                Clear Stale
              </button>
            </div>
          </GlassCard>
        </div>
      )}
    </>
  );
};

/**
 * Evidence chain signing status for partner visibility
 */
const EvidenceChainStatus: React.FC<{ siteId: string }> = ({ siteId }) => {
  const { data, isLoading } = useQuery<{
    status: string;
    has_key: boolean;
    key_fingerprint: string | null;
    evidence_rejection_count: number;
    last_rejection: string | null;
    last_accepted: string | null;
    verified_bundle_count: number;
    last_evidence: string | null;
  }>({
    queryKey: ['evidence-signing-status', siteId],
    queryFn: async () => {
      const res = await fetch(`/api/evidence/sites/${siteId}/signing-status`, {
        credentials: 'same-origin',
      });
      if (!res.ok) return null;
      return res.json();
    },
    staleTime: 30_000,
    retry: false,
  });

  if (isLoading || !data) return null;

  const statusColor = data.status === 'healthy' ? 'success' : data.status === 'broken' ? 'error' : 'default';
  const statusLabel = data.status === 'healthy' ? 'Active' : data.status === 'broken' ? 'Broken' : 'No Key';

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Evidence Chain</h2>
        <Badge variant={statusColor}>{statusLabel}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-label-tertiary">Signing Key</p>
          <p className="text-label-primary font-mono text-xs">
            {data.key_fingerprint || 'Not registered'}
          </p>
        </div>
        <div>
          <p className="text-label-tertiary">Verified Bundles</p>
          <p className="text-label-primary">{data.verified_bundle_count}</p>
        </div>
        <div>
          <p className="text-label-tertiary">Last Accepted</p>
          <p className="text-label-primary">{data.last_accepted ? formatRelativeTime(data.last_accepted) : 'Never'}</p>
        </div>
        {data.evidence_rejection_count > 0 && (
          <div>
            <p className="text-label-tertiary text-red-500">Rejections</p>
            <p className="text-red-500 font-semibold">
              {data.evidence_rejection_count} ({data.last_rejection ? formatRelativeTime(data.last_rejection) : ''})
            </p>
          </div>
        )}
      </div>
    </GlassCard>
  );
};

/**
 * Edit Site Modal
 */
const EditSiteModal: React.FC<{
  site: SiteDetailType;
  onClose: () => void;
  onSaved: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}> = ({ site, onClose, onSaved, showToast }) => {
  const [clinicName, setClinicName] = useState(site.clinic_name || '');
  const [contactName, setContactName] = useState(site.contact_name || '');
  const [contactEmail, setContactEmail] = useState(site.contact_email || '');
  const [contactPhone, setContactPhone] = useState(site.contact_phone || '');
  const [tier, setTier] = useState(site.tier || 'mid');
  const [stage, setStage] = useState(site.onboarding_stage || 'active');
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const res = await fetch(`/api/sites/${site.site_id}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        body: JSON.stringify({
          clinic_name: clinicName,
          contact_name: contactName || null,
          contact_email: contactEmail || null,
          contact_phone: contactPhone || null,
          tier,
          onboarding_stage: stage,
        }),
      });
      if (res.ok) {
        showToast('Site updated', 'success');
        onSaved();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        showToast(`Failed: ${err.detail}`, 'error');
      }
    } catch {
      showToast('Failed to update site', 'error');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-lg" onClick={e => e.stopPropagation()}>
      <GlassCard>
        <h2 className="text-xl font-semibold mb-4">Edit Site</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-label-tertiary uppercase mb-1">Clinic Name</label>
            <input type="text" value={clinicName} onChange={e => setClinicName(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Name</label>
              <input type="text" value={contactName} onChange={e => setContactName(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Contact Email</label>
              <input type="email" value={contactEmail} onChange={e => setContactEmail(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Phone</label>
              <input type="text" value={contactPhone} onChange={e => setContactPhone(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm" />
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Tier</label>
              <select value={tier} onChange={e => setTier(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                <option value="small">Small</option>
                <option value="mid">Mid</option>
                <option value="large">Large</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-label-tertiary uppercase mb-1">Stage</label>
              <select value={stage} onChange={e => setStage(e.target.value)}
                className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                <option value="provisioning">Provisioning</option>
                <option value="connectivity">Connectivity</option>
                <option value="scanning">Scanning</option>
                <option value="active">Active</option>
              </select>
            </div>
          </div>
          <div className="border-t border-separator-light pt-3">
            <p className="text-xs text-label-tertiary mb-1">Site ID</p>
            <p className="text-sm font-mono text-label-secondary">{site.site_id}</p>
          </div>
          <div className="flex gap-3 pt-2">
            <button onClick={onClose}
              className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">Cancel</button>
            <button onClick={handleSave} disabled={isSaving || !clinicName}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
              {isSaving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </GlassCard>
      </div>
    </div>
  );
};


/**
 * Move Appliance Modal
 */
const MoveApplianceModal: React.FC<{
  applianceId: string;
  currentSiteId: string;
  onClose: () => void;
  onMove: (applianceId: string, targetSiteId: string) => void;
}> = ({ applianceId, currentSiteId, onClose, onMove }) => {
  const [targetSiteId, setTargetSiteId] = useState('');
  const [sites, setSites] = useState<Array<{ site_id: string; clinic_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);

  React.useEffect(() => {
    const fetchSites = async () => {
      try {
        const res = await fetch('/api/sites', {
          credentials: 'same-origin',
        });
        if (res.ok) {
          const data = await res.json();
          setSites((data.sites || []).filter((s: { site_id: string }) => s.site_id !== currentSiteId));
        }
      } catch {
        // ignore
      } finally {
        setIsLoading(false);
      }
    };
    fetchSites();
  }, [currentSiteId]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-md" onClick={e => e.stopPropagation()}>
      <GlassCard>
        <h2 className="text-xl font-semibold mb-4">Move Appliance</h2>
        <p className="text-sm text-label-secondary mb-4">
          Move <span className="font-mono text-xs">{applianceId.slice(0, 30)}...</span> to a different site.
        </p>
        {isLoading ? (
          <div className="flex justify-center py-8"><Spinner size="md" /></div>
        ) : sites.length === 0 ? (
          <p className="text-label-tertiary text-center py-8">No other sites available.</p>
        ) : (
          <div className="space-y-3">
            <select value={targetSiteId} onChange={e => setTargetSiteId(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
              <option value="">Select target site...</option>
              {sites.map(s => (
                <option key={s.site_id} value={s.site_id}>{s.clinic_name} ({s.site_id.slice(0, 20)})</option>
              ))}
            </select>
            <div className="flex gap-3 pt-2">
              <button onClick={onClose}
                className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">Cancel</button>
              <button onClick={() => onMove(applianceId, targetSiteId)} disabled={!targetSiteId}
                className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                Move Appliance
              </button>
            </div>
          </div>
        )}
      </GlassCard>
      </div>
    </div>
  );
};


/**
 * Transfer Appliance Modal — move an appliance to a different site by MAC address
 */
const TransferApplianceModal: React.FC<{
  appliances: SiteAppliance[];
  currentSiteId: string;
  onClose: () => void;
  onTransferred: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}> = ({ appliances, currentSiteId, onClose, onTransferred, showToast }) => {
  const [selectedMac, setSelectedMac] = useState(appliances.length === 1 ? (appliances[0].mac_address || '') : '');
  const [targetSiteId, setTargetSiteId] = useState('');
  const [sites, setSites] = useState<Array<{ site_id: string; clinic_name: string }>>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isTransferring, setIsTransferring] = useState(false);

  React.useEffect(() => {
    const fetchSites = async () => {
      try {
        const res = await fetch('/api/sites', { credentials: 'same-origin' });
        if (res.ok) {
          const data = await res.json();
          setSites((data.sites || []).filter((s: { site_id: string }) => s.site_id !== currentSiteId));
        }
      } catch {
        // ignore
      } finally {
        setIsLoading(false);
      }
    };
    fetchSites();
  }, [currentSiteId]);

  const handleTransfer = async () => {
    if (!selectedMac || !targetSiteId) return;
    setIsTransferring(true);
    try {
      const result = await applianceApi.transfer(selectedMac, currentSiteId, targetSiteId);
      showToast(`Appliance transferred to ${result.to_site_name}`, 'success');
      onTransferred();
    } catch (err) {
      showToast(`Transfer failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsTransferring(false);
    }
  };

  const appliancesWithMac = appliances.filter(a => a.mac_address);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-md" onClick={e => e.stopPropagation()}>
        <GlassCard>
          <h2 className="text-xl font-semibold mb-4 text-label-primary">Transfer Appliance</h2>
          <p className="text-sm text-label-secondary mb-4">
            Move an appliance from this site to a different site. The appliance will pick up its new configuration on the next check-in.
          </p>
          {isLoading ? (
            <div className="flex justify-center py-8"><Spinner size="md" /></div>
          ) : appliancesWithMac.length === 0 ? (
            <p className="text-label-tertiary text-center py-8">No appliances with MAC addresses available to transfer.</p>
          ) : sites.length === 0 ? (
            <p className="text-label-tertiary text-center py-8">No other sites available.</p>
          ) : (
            <div className="space-y-3">
              {appliancesWithMac.length > 1 && (
                <div>
                  <label className="block text-sm font-medium text-label-secondary mb-1">Appliance</label>
                  <select value={selectedMac} onChange={e => setSelectedMac(e.target.value)}
                    className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                    <option value="">Select appliance...</option>
                    {appliancesWithMac.map(a => (
                      <option key={a.appliance_id} value={a.mac_address || ''}>
                        {a.hostname || 'Unknown'} ({a.mac_address})
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {appliancesWithMac.length === 1 && (
                <div className="text-sm text-label-secondary">
                  Appliance: <span className="font-mono">{appliancesWithMac[0].hostname || appliancesWithMac[0].mac_address}</span>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-label-secondary mb-1">Destination Site</label>
                <select value={targetSiteId} onChange={e => setTargetSiteId(e.target.value)}
                  className="w-full px-3 py-2 rounded-ios bg-fill-secondary border border-separator-light focus:border-accent-primary focus:outline-none text-sm">
                  <option value="">Select target site...</option>
                  {sites.map(s => (
                    <option key={s.site_id} value={s.site_id}>{s.clinic_name} ({s.site_id})</option>
                  ))}
                </select>
              </div>
              <div className="flex gap-3 pt-2">
                <button onClick={onClose}
                  className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm">
                  Cancel
                </button>
                <button onClick={handleTransfer} disabled={!selectedMac || !targetSiteId || isTransferring}
                  className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50 text-sm">
                  {isTransferring ? 'Transferring...' : 'Transfer Appliance'}
                </button>
              </div>
            </div>
          )}
        </GlassCard>
      </div>
    </div>
  );
};


/**
 * Decommission Site Modal
 */
const DecommissionModal: React.FC<{
  site: SiteDetailType;
  onClose: () => void;
  onDecommissioned: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}> = ({ site, onClose, onDecommissioned, showToast }) => {
  const [isExporting, setIsExporting] = useState(false);
  const [isDecommissioning, setIsDecommissioning] = useState(false);
  const [exportDone, setExportDone] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const data = await decommissionApi.exportSiteData(site.site_id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `site-export-${site.site_id}-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setExportDone(true);
      showToast('Site data exported successfully', 'success');
    } catch (err) {
      showToast(`Export failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsExporting(false);
    }
  };

  const handleDecommission = async () => {
    setIsDecommissioning(true);
    try {
      const result = await decommissionApi.decommissionSite(site.site_id);
      showToast(`Site decommissioned: ${result.actions.join(', ')}`, 'success');
      onDecommissioned();
    } catch (err) {
      showToast(`Decommission failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
    } finally {
      setIsDecommissioning(false);
    }
  };

  const canDecommission = confirmText === site.site_id;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <GlassCard>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-full bg-health-critical/10 flex items-center justify-center flex-shrink-0">
              <svg className="w-5 h-5 text-health-critical" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
            </div>
            <div>
              <h2 className="text-xl font-semibold text-label-primary">Decommission Site</h2>
              <p className="text-sm text-label-tertiary">This action cannot be undone</p>
            </div>
          </div>

          {/* Site summary */}
          <div className="bg-fill-secondary rounded-ios p-4 mb-4 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Site</span>
              <span className="text-label-primary font-medium">{site.clinic_name}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Site ID</span>
              <span className="text-label-primary font-mono text-xs">{site.site_id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Appliances</span>
              <span className="text-label-primary">{site.appliances.length}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-label-tertiary">Credentials</span>
              <span className="text-label-primary">{site.credentials.length}</span>
            </div>
          </div>

          {/* Warning */}
          <div className="bg-health-warning/10 border border-health-warning/20 rounded-ios p-3 mb-4">
            <p className="text-sm text-label-primary">
              <strong>What happens:</strong>
            </p>
            <ul className="text-sm text-label-secondary mt-1 space-y-1 list-disc list-inside">
              <li>All API keys for this site will be revoked</li>
              <li>Portal access tokens will be invalidated</li>
              <li>Appliances will receive a stop order</li>
              <li>Site status will be set to inactive</li>
            </ul>
            <p className="text-sm text-label-tertiary mt-2">
              Data is retained for HIPAA compliance (6-year requirement). Export first to create an offline archive.
            </p>
          </div>

          {/* Export button */}
          <div className="mb-4">
            <button
              onClick={handleExport}
              disabled={isExporting}
              className={`w-full px-4 py-2.5 rounded-ios text-sm font-medium transition-all flex items-center justify-center gap-2 ${
                exportDone
                  ? 'bg-health-healthy/10 text-health-healthy border border-health-healthy/20'
                  : 'bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 border border-accent-primary/20'
              } disabled:opacity-50`}
            >
              {isExporting ? (
                <>
                  <Spinner size="sm" />
                  Exporting...
                </>
              ) : exportDone ? (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Export Downloaded
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Export Site Data (JSON)
                </>
              )}
            </button>
          </div>

          {/* Confirmation input */}
          <div className="mb-4">
            <label className="block text-sm text-label-secondary mb-1.5">
              Type <code className="bg-fill-secondary px-1.5 py-0.5 rounded text-xs font-mono">{site.site_id}</code> to confirm:
            </label>
            <input
              type="text"
              value={confirmText}
              onChange={e => setConfirmText(e.target.value)}
              placeholder={site.site_id}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-health-critical focus:outline-none text-sm font-mono"
            />
          </div>

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={isDecommissioning}
              className="flex-1 px-4 py-2.5 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors text-sm disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleDecommission}
              disabled={!canDecommission || isDecommissioning}
              className="flex-1 px-4 py-2.5 rounded-ios bg-gradient-to-r from-red-600 to-red-500 hover:from-red-700 hover:to-red-600 text-white font-semibold shadow-md transition-all disabled:opacity-50 disabled:cursor-not-allowed text-sm"
            >
              {isDecommissioning ? (
                <span className="flex items-center justify-center gap-2">
                  <Spinner size="sm" />
                  Decommissioning...
                </span>
              ) : (
                'Confirm Decommission'
              )}
            </button>
          </div>
        </GlassCard>
      </div>
    </div>
  );
};


/**
 * Site detail page
 */
export const SiteDetail: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const [showCredModal, setShowCredModal] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [showPortalLinkModal, setShowPortalLinkModal] = useState(false);
  const [portalLink, setPortalLink] = useState<{ url: string; token: string } | null>(null);
  const [isGeneratingLink, setIsGeneratingLink] = useState(false);
  const [showEditSiteModal, setShowEditSiteModal] = useState(false);
  const [showMoveApplianceModal, setShowMoveApplianceModal] = useState<string | null>(null);
  const [showTransferModal, setShowTransferModal] = useState(false);
  const [showDecommissionModal, setShowDecommissionModal] = useState(false);

  const { data: site, isLoading, error } = useSite(siteId || null);
  const { data: fleetStats } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: fleetUpdatesApi.getStats,
    staleTime: 60_000,
  });
  const { data: coverageData } = useQuery<{
    network_coverage_pct: number;
    unmanaged_device_count: number;
  }>({
    queryKey: ['compliance-health-coverage', siteId],
    queryFn: async () => {
      const res = await fetch(`/api/dashboard/sites/${siteId}/compliance-health`, {
        credentials: 'same-origin',
      });
      if (!res.ok) return null;
      return res.json();
    },
    enabled: !!siteId,
    staleTime: 60_000,
    retry: false,
  });
  const latestVersion = fleetStats?.releases.latest_version ?? null;

  // WireGuard VPN connection status — connected if handshake within last 5 minutes
  const isWgConnected = (() => {
    if (!site?.wg_connected_at) return false;
    const connectedAt = new Date(site.wg_connected_at);
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000);
    return connectedAt > fiveMinAgo;
  })();

  const addCredential = useAddCredential();
  const createOrder = useCreateApplianceOrder();
  const broadcastOrder = useBroadcastOrder();
  const deleteAppliance = useDeleteAppliance();
  const clearStale = useClearStaleAppliances();
  const updateHealingTier = useUpdateHealingTier();
  const updateL2Mode = useUpdateL2Mode();

  const isOrderLoading = createOrder.isPending || broadcastOrder.isPending || deleteAppliance.isPending || clearStale.isPending;

  // Show toast notification
  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  // Generate portal link for client access
  const handleGeneratePortalLink = async () => {
    if (!siteId) return;
    setIsGeneratingLink(true);
    try {
      const response = await fetch(`/api/portal/sites/${siteId}/generate-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to generate portal link');
      }
      const data = await response.json();
      setPortalLink({ url: data.portal_url, token: data.token });
      setShowPortalLinkModal(true);
    } catch (error) {
      showToast(`Failed to generate portal link: ${error instanceof Error ? error.message : String(error)}`, 'error');
    } finally {
      setIsGeneratingLink(false);
    }
  };

  // Handle creating an order for a specific appliance
  const handleCreateOrder = async (applianceId: string, orderType: OrderType, parameters?: Record<string, unknown>) => {
    if (!siteId) return;
    try {
      await createOrder.mutateAsync({ siteId, applianceId, orderType, parameters });
      showToast(`Order "${orderType}" sent to appliance`, 'success');
    } catch (error) {
      showToast(`Failed to create order: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle broadcasting an order to all appliances
  const handleBroadcast = async (orderType: OrderType) => {
    if (!siteId) return;
    try {
      const result = await broadcastOrder.mutateAsync({ siteId, orderType });
      showToast(`Order "${orderType}" broadcast to ${result.length} appliances`, 'success');
    } catch (error) {
      showToast(`Failed to broadcast order: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle deleting an appliance
  const handleDeleteAppliance = async (applianceId: string) => {
    if (!siteId) return;
    try {
      await deleteAppliance.mutateAsync({ siteId, applianceId });
      showToast('Appliance deleted', 'success');
    } catch (error) {
      showToast(`Failed to delete appliance: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle clearing stale appliances
  const handleClearStale = async () => {
    if (!siteId) return;
    try {
      const result = await clearStale.mutateAsync({ siteId, staleHours: 24 });
      showToast(`Cleared ${result.deleted_count} stale appliances`, 'success');
    } catch (error) {
      showToast(`Failed to clear stale appliances: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle updating L2 mode for an appliance
  const handleUpdateL2Mode = async (applianceId: string, l2Mode: string) => {
    if (!siteId) return;
    try {
      await updateL2Mode.mutateAsync({ siteId, applianceId, l2Mode });
      const labels: Record<string, string> = { auto: 'Auto', manual: 'Manual', disabled: 'Disabled' };
      showToast(`L2 healing set to ${labels[l2Mode] || l2Mode}`, 'success');
    } catch (error) {
      showToast(`Failed to update L2 mode: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  // Handle updating healing tier
  const handleHealingTierChange = async (tier: 'standard' | 'full_coverage') => {
    if (!siteId) return;
    try {
      await updateHealingTier.mutateAsync({ siteId, healingTier: tier });
      showToast(`Healing tier updated to ${tier === 'full_coverage' ? 'Full Coverage' : 'Standard'}`, 'success');
    } catch (error) {
      showToast(`Failed to update healing tier: ${error instanceof Error ? error.message : String(error)}`, 'error');
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error || !site) {
    return (
      <GlassCard className="text-center py-12">
        <h2 className="text-xl font-semibold text-label-primary mb-2">Site Not Found</h2>
        <p className="text-label-tertiary mb-4">The site "{siteId}" could not be found.</p>
        <button onClick={() => navigate('/sites')} className="btn-primary">
          Back to Sites
        </button>
      </GlassCard>
    );
  }

  // Handle moving an appliance to a different site
  const handleMoveAppliance = async (applianceId: string, targetSiteId: string) => {
    if (!siteId) return;
    try {
      const res = await fetch(`/api/sites/${siteId}/appliances/${applianceId}/move`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
        body: JSON.stringify({ target_site_id: targetSiteId }),
      });
      if (res.ok) {
        showToast('Appliance moved successfully', 'success');
        setShowMoveApplianceModal(null);
        // Refresh page
        window.location.reload();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        showToast(`Failed to move appliance: ${err.detail}`, 'error');
      }
    } catch {
      showToast('Failed to move appliance', 'error');
    }
  };

  const handleAddCredential = async (data: Parameters<typeof addCredential.mutate>[0]['data']) => {
    try {
      await addCredential.mutateAsync({ siteId: site.site_id, data });
      setShowCredModal(false);
    } catch (error) {
      console.error('Failed to add credential:', error);
    }
  };

  return (
    <div className="space-y-6">
      {/* Decommissioned banner */}
      {site.status === 'inactive' && (
        <div className="bg-health-critical/10 border border-health-critical/20 rounded-ios p-4 flex items-center gap-3">
          <svg className="w-5 h-5 text-health-critical flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
          </svg>
          <div>
            <p className="text-sm font-semibold text-health-critical">This site has been decommissioned</p>
            <p className="text-xs text-label-tertiary mt-0.5">
              Status is inactive. API keys revoked, portal tokens invalidated. Data retained for HIPAA compliance.
            </p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="space-y-0">
        {/* Row 1: Site identity + status + action */}
        <div className="flex items-start gap-4">
          <button
            onClick={() => navigate('/sites')}
            className="p-2 mt-1 rounded-ios-sm hover:bg-fill-secondary transition-colors"
          >
            <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-label-primary truncate">{site.clinic_name}</h1>
              <Badge variant={site.live_status === 'online' ? 'success' : site.live_status === 'offline' ? 'error' : 'default'}>
                {site.live_status}
              </Badge>
              {site.wg_ip && (
                <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full ${
                  isWgConnected ? 'bg-health-healthy/10 text-health-healthy' : 'bg-fill-secondary text-label-tertiary'
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${isWgConnected ? 'bg-health-healthy' : 'bg-label-tertiary'}`} />
                  VPN {site.wg_ip}
                </span>
              )}
            </div>
            <p className="text-label-tertiary text-sm mt-0.5">{site.site_id}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowEditSiteModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-label-secondary hover:bg-fill-secondary rounded-ios-sm transition-colors whitespace-nowrap"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
              Edit Site
            </button>
            <button
              onClick={handleGeneratePortalLink}
              disabled={isGeneratingLink}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-accent-primary hover:bg-accent-tint rounded-ios-sm transition-colors disabled:opacity-50 whitespace-nowrap"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              {isGeneratingLink ? 'Generating...' : 'Portal Link'}
            </button>
            {site.status !== 'inactive' && site.appliances.length > 0 && (
              <button
                onClick={() => setShowTransferModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-label-secondary hover:bg-fill-secondary rounded-ios-sm transition-colors whitespace-nowrap"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                </svg>
                Transfer Appliance
              </button>
            )}
            {site.status !== 'inactive' && (
              <button
                onClick={() => setShowDecommissionModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-health-critical hover:bg-health-critical/10 rounded-ios-sm transition-colors whitespace-nowrap"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                </svg>
                Decommission
              </button>
            )}
          </div>
        </div>

        {/* Row 2: Navigation pills */}
        <div className="overflow-x-auto -mx-4 px-4 mt-4 pt-3 border-t border-separator-light">
        <nav className="flex items-center gap-1.5 min-w-max">
          <Link
            to={`/sites/${siteId}/devices`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
            Devices
          </Link>
          <Link
            to={`/sites/${siteId}/workstations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Workstations
          </Link>
          <Link
            to={`/sites/${siteId}/agents`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
            </svg>
            Go Agents
          </Link>
          <Link
            to={`/sites/${siteId}/protection`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
            App Protection
          </Link>
          <Link
            to={`/sites/${siteId}/drift-config`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Drift Config
          </Link>
          <div className="w-px h-5 bg-separator-medium mx-0.5 flex-shrink-0" />
          <Link
            to={`/sites/${siteId}/frameworks`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            Frameworks
          </Link>
          <Link
            to={`/sites/${siteId}/integrations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap min-h-[44px]"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Cloud Integrations
          </Link>
        </nav>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Deployment Progress (Zero-Friction Pipeline) */}
          {siteId && <DeploymentProgress siteId={siteId} />}

          {/* Compliance Health Infographic */}
          {siteId && site && (
            <ComplianceHealthInfographic
              sites={[{ site_id: site.site_id, clinic_name: site.clinic_name }]}
              apiPrefix="/api/dashboard"
              onCategoryClick={(category, sid) => navigate(`/incidents?site_id=${sid}&category=${category}`)}
            />
          )}

          {/* Devices at Risk */}
          {siteId && site && (
            <DevicesAtRisk
              siteId={site.site_id}
              apiPrefix="/api/dashboard"
              onDeviceClick={(hostname, sid) => navigate(`/incidents?site_id=${sid}&hostname=${hostname}`)}
            />
          )}

          {/* Contact Information */}
          <GlassCard>
            <h2 className="text-lg font-semibold mb-4">Contact Information</h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-label-tertiary text-sm">Contact Name</p>
                <p className="text-label-primary">{site.contact_name || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Email</p>
                <p className="text-label-primary">{site.contact_email || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Phone</p>
                <p className="text-label-primary">{site.contact_phone || '-'}</p>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Tier</p>
                <Badge variant={site.tier === 'large' ? 'success' : site.tier === 'mid' ? 'info' : 'default'}>
                  {site.tier}
                </Badge>
              </div>
              <div>
                <p className="text-label-tertiary text-sm">Healing Mode</p>
                <div className="flex items-center gap-2">
                  <select
                    value={site.healing_tier || 'standard'}
                    onChange={(e) => handleHealingTierChange(e.target.value as 'standard' | 'full_coverage')}
                    disabled={updateHealingTier.isPending}
                    className="px-2 py-1 text-sm rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:outline-none focus:ring-2 focus:ring-accent-primary disabled:opacity-50"
                  >
                    <option value="standard">Standard</option>
                    <option value="full_coverage">Full Coverage</option>
                  </select>
                  {updateHealingTier.isPending && (
                    <Spinner size="sm" />
                  )}
                </div>
              </div>
              {site.address && (
                <div className="col-span-2">
                  <p className="text-label-tertiary text-sm">Address</p>
                  <p className="text-label-primary">{site.address}</p>
                </div>
              )}
            </div>
          </GlassCard>

          {/* Appliances */}
          <GlassCard className="relative z-10">
            <h2 className="text-lg font-semibold mb-4">
              Appliances ({site.appliances.length})
            </h2>
            <SiteActionToolbar
              applianceCount={site.appliances.length}
              onBroadcast={handleBroadcast}
              onClearStale={handleClearStale}
              isLoading={isOrderLoading}
            />
            {site.appliances.length === 0 ? (
              <EmptyState
                icon={
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
                  </svg>
                }
                title="No appliances connected"
                description="The appliance will appear here automatically once it is installed and phones home to Central Command."
              />
            ) : (
              <div className="space-y-4">
                {site.appliances.map((appliance) => (
                  <ApplianceCard
                    key={appliance.appliance_id}
                    appliance={appliance}
                    latestVersion={latestVersion}
                    onCreateOrder={handleCreateOrder}
                    onDelete={handleDeleteAppliance}
                    onUpdateL2Mode={handleUpdateL2Mode}
                    onMove={(id) => setShowMoveApplianceModal(id)}
                    isLoading={isOrderLoading}
                  />
                ))}
              </div>
            )}
          </GlassCard>

          {/* Evidence Chain Status */}
          {siteId && <EvidenceChainStatus siteId={siteId} />}

          {/* Credentials */}
          <GlassCard>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Credentials ({site.credentials.length})</h2>
              <button
                onClick={() => setShowCredModal(true)}
                className="text-sm text-accent-primary hover:text-accent-primary/80"
              >
                + Add Credential
              </button>
            </div>
            {site.credentials.length === 0 ? (
              <EmptyState
                icon={
                  <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                  </svg>
                }
                title="No credentials configured"
                description="Add router, Active Directory, or other credentials so the appliance can perform deep compliance scans."
                action={{
                  label: 'Add credential',
                  onClick: () => setShowCredModal(true),
                }}
              />
            ) : (
              <div className="space-y-2">
                {site.credentials.map((cred) => (
                  <div
                    key={cred.id}
                    className="flex items-center justify-between py-3 px-4 bg-fill-secondary rounded-ios"
                  >
                    <div>
                      <p className="font-medium text-label-primary">{cred.credential_name}</p>
                      <p className="text-xs text-label-tertiary">{cred.credential_type}</p>
                    </div>
                    <Badge variant="default">Stored</Badge>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Network Coverage Score */}
          {coverageData !== null && coverageData !== undefined && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-3">Network Coverage</h2>
              <div className="bg-white/5 rounded-lg p-4">
                <div className="text-sm text-label-secondary mb-1">Agent Coverage</div>
                <div className={`text-2xl font-bold ${
                  (coverageData.network_coverage_pct ?? 0) >= 90
                    ? 'text-emerald-400'
                    : (coverageData.network_coverage_pct ?? 0) >= 70
                      ? 'text-yellow-400'
                      : 'text-red-400'
                }`}>
                  {coverageData.network_coverage_pct ?? 0}%
                </div>
                <div className="text-xs text-label-tertiary mt-1">
                  {(coverageData.unmanaged_device_count ?? 0) > 0
                    ? `${coverageData.unmanaged_device_count} unmanaged device${coverageData.unmanaged_device_count > 1 ? 's' : ''}`
                    : 'All devices managed'}
                </div>
              </div>
            </GlassCard>
          )}

          {/* Onboarding Checklist — shows for new sites that aren't fully active yet */}
          {(site.onboarding_stage !== 'active' || (site.timestamps.baseline_at === null && site.timestamps.scanning_at === null)) && (
            <GlassCard>
              <OnboardingChecklist
                site={site}
                onNavigateDevices={() => navigate(`/sites/${siteId}/devices`)}
                onAddCredential={() => setShowCredModal(true)}
              />
            </GlassCard>
          )}

          {/* Onboarding Progress */}
          <GlassCard>
            <h2 className="text-lg font-semibold mb-4">Onboarding Progress</h2>
            <OnboardingProgress timestamps={site.timestamps} stage={site.onboarding_stage} />
          </GlassCard>

          {/* Notes */}
          {site.notes && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-2">Notes</h2>
              <p className="text-label-secondary text-sm">{site.notes}</p>
            </GlassCard>
          )}

          {/* Blockers */}
          {site.blockers.length > 0 && (
            <GlassCard className="border-l-4 border-health-warning">
              <h2 className="text-lg font-semibold mb-2">Blockers</h2>
              <ul className="space-y-2">
                {site.blockers.map((blocker, i) => (
                  <li key={i} className="text-sm text-label-secondary flex items-start gap-2">
                    <span className="text-health-warning">!</span>
                    {blocker}
                  </li>
                ))}
              </ul>
            </GlassCard>
          )}

          {/* Tracking */}
          {site.tracking_number && (
            <GlassCard>
              <h2 className="text-lg font-semibold mb-2">Shipping</h2>
              <p className="text-label-tertiary text-sm">Carrier</p>
              <p className="text-label-primary">{site.tracking_carrier || 'Unknown'}</p>
              <p className="text-label-tertiary text-sm mt-2">Tracking Number</p>
              <p className="text-label-primary font-mono text-sm">{site.tracking_number}</p>
            </GlassCard>
          )}
        </div>
      </div>

      {/* Add Credential Modal */}
      <AddCredentialModal
        isOpen={showCredModal}
        onClose={() => setShowCredModal(false)}
        onSubmit={handleAddCredential}
        isLoading={addCredential.isPending}
      />

      {/* Portal Link Modal */}
      {showPortalLinkModal && portalLink && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <GlassCard className="w-full max-w-lg">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-label-primary">Client Portal Link</h2>
              <button
                onClick={() => setShowPortalLinkModal(false)}
                className="p-2 hover:bg-fill-secondary rounded-ios transition-colors"
              >
                <svg className="w-5 h-5 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <p className="text-label-secondary text-sm mb-4">
              Share this link with your client to give them access to their compliance dashboard.
              The link does not expire.
            </p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-label-tertiary mb-1">Portal URL</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={portalLink.url}
                    readOnly
                    className="flex-1 px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light font-mono text-sm"
                  />
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(portalLink.url);
                      showToast('Portal URL copied to clipboard', 'success');
                    }}
                    className="px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors"
                  >
                    Copy
                  </button>
                </div>
              </div>
              <div className="pt-2 border-t border-separator-light">
                <p className="text-xs text-label-tertiary">
                  <strong>Security note:</strong> This link provides read-only access to compliance reports and evidence.
                  Generate a new link if you need to revoke access.
                </p>
              </div>
            </div>
            <div className="flex justify-end mt-6">
              <button
                onClick={() => setShowPortalLinkModal(false)}
                className="px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors"
              >
                Done
              </button>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Edit Site Modal */}
      {showEditSiteModal && site && (
        <EditSiteModal
          site={site}
          onClose={() => setShowEditSiteModal(false)}
          onSaved={() => { setShowEditSiteModal(false); window.location.reload(); }}
          showToast={showToast}
        />
      )}

      {/* Move Appliance Modal */}
      {showMoveApplianceModal && siteId && (
        <MoveApplianceModal
          applianceId={showMoveApplianceModal}
          currentSiteId={siteId}
          onClose={() => setShowMoveApplianceModal(null)}
          onMove={handleMoveAppliance}
        />
      )}

      {/* Transfer Appliance Modal */}
      {showTransferModal && site && siteId && (
        <TransferApplianceModal
          appliances={site.appliances}
          currentSiteId={siteId}
          onClose={() => setShowTransferModal(false)}
          onTransferred={() => {
            setShowTransferModal(false);
            window.location.reload();
          }}
          showToast={showToast}
        />
      )}

      {/* Decommission Modal */}
      {showDecommissionModal && site && (
        <DecommissionModal
          site={site}
          onClose={() => setShowDecommissionModal(false)}
          onDecommissioned={() => {
            setShowDecommissionModal(false);
            navigate('/sites');
          }}
          showToast={showToast}
        />
      )}

      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 px-4 py-3 rounded-ios shadow-lg z-50 ${
            toast.type === 'success' ? 'bg-health-healthy text-white' : 'bg-health-critical text-white'
          }`}
        >
          {toast.message}
        </div>
      )}
    </div>
  );
};

export default SiteDetail;
