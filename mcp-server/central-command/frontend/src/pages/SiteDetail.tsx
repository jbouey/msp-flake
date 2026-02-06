import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Badge, ActionDropdown } from '../components/shared';
import type { ActionItem } from '../components/shared';
import { DeploymentProgress } from '../components/deployment';
import { useSite, useAddCredential, useCreateApplianceOrder, useBroadcastOrder, useDeleteAppliance, useClearStaleAppliances, useUpdateHealingTier } from '../hooks';
import type { SiteDetail as SiteDetailType, SiteAppliance, OrderType } from '../utils/api';
import { fleetUpdatesApi, type FleetStats } from '../utils/api';

/**
 * Format relative time
 */
function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'Never';

  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins} min ago`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

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

const ApplianceCard: React.FC<{
  appliance: SiteAppliance;
  latestVersion: string | null;
  onCreateOrder: (applianceId: string, orderType: OrderType, parameters?: Record<string, unknown>) => void;
  onDelete: (applianceId: string) => void;
  isLoading?: boolean;
}> = ({ appliance, latestVersion, onCreateOrder, onDelete, isLoading }) => {
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showUpdateModal, setShowUpdateModal] = useState(false);

  // Check if agent is outdated (only if we know the latest version)
  const isOutdated = latestVersion && appliance.agent_version && appliance.agent_version !== latestVersion;

  const statusColors = {
    online: 'bg-health-healthy',
    stale: 'bg-health-warning',
    offline: 'bg-health-critical',
    pending: 'bg-gray-400',
  };

  const moreActions: ActionItem[] = [
    {
      label: 'View Logs',
      icon: <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>,
      onClick: () => onCreateOrder(appliance.appliance_id, 'view_logs'),
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
        <Badge variant={appliance.live_status === 'online' ? 'success' : 'default'}>
          {appliance.live_status}
        </Badge>
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
      const token = localStorage.getItem('auth_token');
      const res = await fetch(`/api/evidence/sites/${siteId}/signing-status`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
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

  const { data: site, isLoading, error } = useSite(siteId || null);
  const { data: fleetStats } = useQuery<FleetStats>({
    queryKey: ['fleet-stats'],
    queryFn: fleetUpdatesApi.getStats,
    staleTime: 60_000,
  });
  const latestVersion = fleetStats?.releases.latest_version ?? null;
  const addCredential = useAddCredential();
  const createOrder = useCreateApplianceOrder();
  const broadcastOrder = useBroadcastOrder();
  const deleteAppliance = useDeleteAppliance();
  const clearStale = useClearStaleAppliances();
  const updateHealingTier = useUpdateHealingTier();

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
      showToast(`Failed to generate portal link: ${error}`, 'error');
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
      showToast(`Failed to create order: ${error}`, 'error');
    }
  };

  // Handle broadcasting an order to all appliances
  const handleBroadcast = async (orderType: OrderType) => {
    if (!siteId) return;
    try {
      const result = await broadcastOrder.mutateAsync({ siteId, orderType });
      showToast(`Order "${orderType}" broadcast to ${result.length} appliances`, 'success');
    } catch (error) {
      showToast(`Failed to broadcast order: ${error}`, 'error');
    }
  };

  // Handle deleting an appliance
  const handleDeleteAppliance = async (applianceId: string) => {
    if (!siteId) return;
    try {
      await deleteAppliance.mutateAsync({ siteId, applianceId });
      showToast('Appliance deleted', 'success');
    } catch (error) {
      showToast(`Failed to delete appliance: ${error}`, 'error');
    }
  };

  // Handle clearing stale appliances
  const handleClearStale = async () => {
    if (!siteId) return;
    try {
      const result = await clearStale.mutateAsync({ siteId, staleHours: 24 });
      showToast(`Cleared ${result.deleted_count} stale appliances`, 'success');
    } catch (error) {
      showToast(`Failed to clear stale appliances: ${error}`, 'error');
    }
  };

  // Handle updating healing tier
  const handleHealingTierChange = async (tier: 'standard' | 'full_coverage') => {
    if (!siteId) return;
    try {
      await updateHealingTier.mutateAsync({ siteId, healingTier: tier });
      showToast(`Healing tier updated to ${tier === 'full_coverage' ? 'Full Coverage (21 rules)' : 'Standard (4 rules)'}`, 'success');
    } catch (error) {
      showToast(`Failed to update healing tier: ${error}`, 'error');
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
            </div>
            <p className="text-label-tertiary text-sm mt-0.5">{site.site_id}</p>
          </div>
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
        </div>

        {/* Row 2: Navigation pills */}
        <nav className="flex items-center gap-1.5 mt-4 pt-3 border-t border-separator-light overflow-x-auto">
          <Link
            to={`/sites/${siteId}/devices`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
            </svg>
            Devices
          </Link>
          <Link
            to={`/sites/${siteId}/workstations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Workstations
          </Link>
          <Link
            to={`/sites/${siteId}/agents`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
            </svg>
            Go Agents
          </Link>
          <div className="w-px h-5 bg-separator-medium mx-0.5 flex-shrink-0" />
          <Link
            to={`/sites/${siteId}/frameworks`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            Frameworks
          </Link>
          <Link
            to={`/sites/${siteId}/integrations`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-ios-sm bg-separator-light text-label-primary hover:bg-separator-medium transition-colors whitespace-nowrap"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Cloud Integrations
          </Link>
        </nav>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Deployment Progress (Zero-Friction Pipeline) */}
          {siteId && <DeploymentProgress siteId={siteId} />}

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
                    <option value="standard">Standard (4 rules)</option>
                    <option value="full_coverage">Full Coverage (21 rules)</option>
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
              <div className="text-center py-8">
                <p className="text-label-tertiary">No appliances connected yet.</p>
                <p className="text-label-tertiary text-sm mt-1">
                  The appliance will appear here when it phones home.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {site.appliances.map((appliance) => (
                  <ApplianceCard
                    key={appliance.appliance_id}
                    appliance={appliance}
                    latestVersion={latestVersion}
                    onCreateOrder={handleCreateOrder}
                    onDelete={handleDeleteAppliance}
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
              <div className="text-center py-8">
                <p className="text-label-tertiary">No credentials stored.</p>
                <p className="text-label-tertiary text-sm mt-1">
                  Add router, AD, or other credentials for the appliance.
                </p>
              </div>
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
