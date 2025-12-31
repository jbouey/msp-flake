import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSites, useCreateSite } from '../hooks';
import type { Site } from '../utils/api';

/**
 * Status indicator component
 */
const StatusBadge: React.FC<{ status: Site['live_status'] }> = ({ status }) => {
  const variants: Record<Site['live_status'], { color: string; icon: string; label: string }> = {
    online: { color: 'bg-health-healthy', icon: '🟢', label: 'Online' },
    stale: { color: 'bg-health-warning', icon: '🟡', label: 'Stale' },
    offline: { color: 'bg-health-critical', icon: '🔴', label: 'Offline' },
    pending: { color: 'bg-gray-400', icon: '⚪', label: 'Pending' },
  };

  const variant = variants[status] || variants.pending;

  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span>{variant.icon}</span>
      <span className={`font-medium ${
        status === 'online' ? 'text-health-healthy' :
        status === 'stale' ? 'text-health-warning' :
        status === 'offline' ? 'text-health-critical' :
        'text-label-tertiary'
      }`}>
        {variant.label}
      </span>
    </span>
  );
};

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
 * Site row component
 */
const SiteRow: React.FC<{ site: Site; onClick: () => void }> = ({ site, onClick }) => {
  return (
    <tr
      onClick={onClick}
      className="hover:bg-fill-tertiary/50 cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-label-primary">{site.clinic_name}</p>
          <p className="text-xs text-label-tertiary">{site.site_id}</p>
        </div>
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={site.live_status} />
      </td>
      <td className="px-4 py-3 text-sm text-label-secondary">
        {formatRelativeTime(site.last_checkin)}
      </td>
      <td className="px-4 py-3">
        <Badge variant={
          site.onboarding_stage === 'active' ? 'success' :
          site.onboarding_stage === 'connectivity' ? 'info' :
          'default'
        }>
          {site.onboarding_stage.replace('_', ' ')}
        </Badge>
      </td>
      <td className="px-4 py-3 text-sm text-label-secondary">
        {site.appliance_count}
      </td>
      <td className="px-4 py-3">
        <Badge variant={
          site.tier === 'large' ? 'success' :
          site.tier === 'mid' ? 'info' :
          'default'
        }>
          {site.tier}
        </Badge>
      </td>
    </tr>
  );
};

/**
 * New Site Modal
 */
const NewSiteModal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { clinic_name: string; contact_name?: string; contact_email?: string; tier?: string }) => void;
  isLoading: boolean;
}> = ({ isOpen, onClose, onSubmit, isLoading }) => {
  const [clinicName, setClinicName] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [tier, setTier] = useState('mid');

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      clinic_name: clinicName,
      contact_name: contactName || undefined,
      contact_email: contactEmail || undefined,
      tier,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <GlassCard className="w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">New Site</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Clinic Name *
            </label>
            <input
              type="text"
              value={clinicName}
              onChange={(e) => setClinicName(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Contact Name
            </label>
            <input
              type="text"
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Contact Email
            </label>
            <input
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Tier
            </label>
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-secondary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
            >
              <option value="small">Small (1-5 providers)</option>
              <option value="mid">Mid (6-15 providers)</option>
              <option value="large">Large (15-50 providers)</option>
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2 rounded-ios bg-fill-secondary text-label-primary hover:bg-fill-tertiary transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!clinicName || isLoading}
              className="flex-1 px-4 py-2 rounded-ios bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors disabled:opacity-50"
            >
              {isLoading ? 'Creating...' : 'Create Site'}
            </button>
          </div>
        </form>
      </GlassCard>
    </div>
  );
};

/**
 * Sites page - View all client sites with real-time status
 */
export const Sites: React.FC = () => {
  const navigate = useNavigate();
  const [showNewSiteModal, setShowNewSiteModal] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);

  const { data, isLoading } = useSites(statusFilter);
  const createSite = useCreateSite();

  const sites = data?.sites || [];

  // Count by status
  const statusCounts = {
    all: sites.length,
    online: sites.filter(s => s.live_status === 'online').length,
    offline: sites.filter(s => s.live_status === 'offline').length,
    pending: sites.filter(s => s.live_status === 'pending').length,
  };

  const handleCreateSite = async (siteData: Parameters<typeof createSite.mutate>[0]) => {
    try {
      const result = await createSite.mutateAsync(siteData);
      setShowNewSiteModal(false);
      navigate(`/sites/${result.site_id}`);
    } catch (error) {
      console.error('Failed to create site:', error);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary">Sites</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {sites.length} client site{sites.length !== 1 ? 's' : ''}
          </p>
        </div>
        <button
          onClick={() => setShowNewSiteModal(true)}
          className="btn-primary"
        >
          + New Site
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-health-healthy">{statusCounts.online}</p>
          <p className="text-xs text-label-tertiary">Online</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${statusCounts.offline > 0 ? 'text-health-critical' : 'text-label-primary'}`}>
            {statusCounts.offline}
          </p>
          <p className="text-xs text-label-tertiary">Offline</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className={`text-2xl font-bold ${statusCounts.pending > 0 ? 'text-health-warning' : 'text-label-primary'}`}>
            {statusCounts.pending}
          </p>
          <p className="text-xs text-label-tertiary">Pending</p>
        </GlassCard>
        <GlassCard padding="md" className="text-center">
          <p className="text-2xl font-bold text-accent-primary">{statusCounts.all}</p>
          <p className="text-xs text-label-tertiary">Total</p>
        </GlassCard>
      </div>

      {/* Filter tabs */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-label-tertiary">Filter:</span>
        <div className="flex gap-1">
          {[
            { value: undefined, label: 'All' },
            { value: 'online', label: 'Online' },
            { value: 'offline', label: 'Offline' },
            { value: 'pending', label: 'Pending' },
          ].map((option) => (
            <button
              key={option.value || 'all'}
              onClick={() => setStatusFilter(option.value)}
              className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                statusFilter === option.value
                  ? 'bg-accent-primary text-white'
                  : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {/* Sites table */}
      <GlassCard padding="none">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Spinner size="lg" />
          </div>
        ) : sites.length === 0 ? (
          <div className="text-center py-12">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-accent-primary/10 flex items-center justify-center">
              <svg className="w-8 h-8 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
            <h3 className="font-semibold text-label-primary mb-2">No sites yet</h3>
            <p className="text-label-tertiary text-sm mb-4">
              Click "New Site" to add your first client.
            </p>
            <button
              onClick={() => setShowNewSiteModal(true)}
              className="btn-primary"
            >
              + New Site
            </button>
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-fill-secondary border-b border-separator-light">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Site
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Last Checkin
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Stage
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Appliances
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider">
                  Tier
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-separator-light">
              {sites.map((site) => (
                <SiteRow
                  key={site.site_id}
                  site={site}
                  onClick={() => navigate(`/sites/${site.site_id}`)}
                />
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>

      {/* New Site Modal */}
      <NewSiteModal
        isOpen={showNewSiteModal}
        onClose={() => setShowNewSiteModal(false)}
        onSubmit={handleCreateSite}
        isLoading={createSite.isPending}
      />
    </div>
  );
};

export default Sites;
