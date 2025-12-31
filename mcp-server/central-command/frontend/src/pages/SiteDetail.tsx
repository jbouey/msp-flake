import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSite, useAddCredential } from '../hooks';
import type { SiteDetail as SiteDetailType, SiteAppliance } from '../utils/api';

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
 * Appliance card component
 */
const ApplianceCard: React.FC<{ appliance: SiteAppliance }> = ({ appliance }) => {
  const statusColors = {
    online: 'bg-health-healthy',
    stale: 'bg-health-warning',
    offline: 'bg-health-critical',
    pending: 'bg-gray-400',
  };

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
 * Site detail page
 */
export const SiteDetail: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const [showCredModal, setShowCredModal] = useState(false);

  const { data: site, isLoading, error } = useSite(siteId || null);
  const addCredential = useAddCredential();

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
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/sites')}
          className="p-2 rounded-ios hover:bg-fill-secondary transition-colors"
        >
          <svg className="w-5 h-5 text-label-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold text-label-primary">{site.clinic_name}</h1>
          <p className="text-label-tertiary text-sm">{site.site_id}</p>
        </div>
        <Badge variant={site.live_status === 'online' ? 'success' : site.live_status === 'offline' ? 'error' : 'default'}>
          {site.live_status}
        </Badge>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main content */}
        <div className="lg:col-span-2 space-y-6">
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
              {site.address && (
                <div className="col-span-2">
                  <p className="text-label-tertiary text-sm">Address</p>
                  <p className="text-label-primary">{site.address}</p>
                </div>
              )}
            </div>
          </GlassCard>

          {/* Appliances */}
          <GlassCard>
            <h2 className="text-lg font-semibold mb-4">
              Appliances ({site.appliances.length})
            </h2>
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
                  <ApplianceCard key={appliance.appliance_id} appliance={appliance} />
                ))}
              </div>
            )}
          </GlassCard>

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
    </div>
  );
};

export default SiteDetail;
