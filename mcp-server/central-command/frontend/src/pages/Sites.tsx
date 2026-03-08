import React, { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSites, useCreateSite } from '../hooks';
import type { Site } from '../utils/api';

/**
 * Status indicator component
 */
const StatusBadge: React.FC<{ status: Site['live_status'] }> = ({ status }) => {
  const dotClass: Record<Site['live_status'], string> = {
    online: 'status-dot status-dot-healthy',
    stale: 'status-dot status-dot-warning',
    offline: 'status-dot status-dot-critical',
    pending: 'status-dot status-dot-neutral',
  };

  const textClass: Record<Site['live_status'], string> = {
    online: 'text-health-healthy',
    stale: 'text-health-warning',
    offline: 'text-health-critical',
    pending: 'text-label-tertiary',
  };

  const labels: Record<Site['live_status'], string> = {
    online: 'Online',
    stale: 'Stale',
    offline: 'Offline',
    pending: 'Pending',
  };

  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span className={dotClass[status] || dotClass.pending} />
      <span className={`font-medium ${textClass[status] || textClass.pending}`}>
        {labels[status] || 'Pending'}
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
const SiteRow: React.FC<{ site: Site; onClick: () => void; showOrg?: boolean }> = ({ site, onClick, showOrg }) => {
  return (
    <tr
      onClick={onClick}
      className="hover:bg-fill-quaternary cursor-pointer transition-colors"
    >
      <td className="px-4 py-3">
        <div>
          <p className="font-medium text-label-primary">{site.clinic_name}</p>
          <p className="text-xs text-label-tertiary">{site.site_id}</p>
        </div>
      </td>
      {showOrg && (
        <td className="px-4 py-3 text-sm text-label-secondary">
          {site.org_name || '-'}
        </td>
      )}
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
    <div className="fixed inset-0 modal-backdrop flex items-center justify-center z-50">
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
              className="w-full px-3 py-2 rounded-ios bg-fill-quaternary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
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
              className="w-full px-3 py-2 rounded-ios bg-fill-quaternary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
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
              className="w-full px-3 py-2 rounded-ios bg-fill-quaternary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-label-secondary mb-1">
              Tier
            </label>
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value)}
              className="w-full px-3 py-2 rounded-ios bg-fill-quaternary text-label-primary border border-separator-light focus:border-accent-primary focus:outline-none"
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
              className="flex-1 px-4 py-2 rounded-ios bg-fill-quaternary text-label-primary hover:bg-fill-secondary transition-colors"
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
  const [groupByOrg, setGroupByOrg] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [sortBy, setSortBy] = useState('clinic_name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(0);
  const limit = 25;

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const { data, isLoading } = useSites({
    status: statusFilter,
    search: debouncedSearch || undefined,
    sort_by: sortBy,
    sort_dir: sortDir,
    limit,
    offset: page * limit,
  });
  const createSite = useCreateSite();

  const sites = data?.sites || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / limit);
  const serverStats = data?.stats || {};

  // Group sites by org when toggled
  const orgGroups = useMemo(() => {
    if (!groupByOrg) return null;
    const groups: Record<string, { orgName: string; orgId: string | null; sites: Site[] }> = {};
    for (const site of sites) {
      const key = site.client_org_id || 'unassigned';
      if (!groups[key]) {
        groups[key] = {
          orgName: site.org_name || 'Unassigned',
          orgId: site.client_org_id || null,
          sites: [],
        };
      }
      groups[key].sites.push(site);
    }
    return Object.values(groups).sort((a, b) => a.orgName.localeCompare(b.orgName));
  }, [sites, groupByOrg]);

  const statusCounts = {
    all: (serverStats.online || 0) + (serverStats.stale || 0) + (serverStats.offline || 0) + (serverStats.pending || 0),
    online: serverStats.online || 0,
    offline: serverStats.offline || 0,
    pending: serverStats.pending || 0,
  };

  const orgCount = useMemo(() => {
    const orgIds = new Set(sites.map(s => s.client_org_id).filter(Boolean));
    return orgIds.size;
  }, [sites]);

  const handleSort = (col: string) => {
    if (sortBy === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(col);
      setSortDir('asc');
    }
    setPage(0);
  };

  const SortIcon: React.FC<{ col: string }> = ({ col }) => {
    if (sortBy !== col) return <span className="text-label-quaternary ml-1">↕</span>;
    return <span className="text-accent-primary ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>;
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

  const thClass = "px-4 py-3 text-left text-xs font-semibold text-label-secondary uppercase tracking-wider";
  const thSortClass = `${thClass} cursor-pointer select-none hover:text-label-primary`;

  const tableHeaders = (
    <tr>
      <th className={thSortClass} onClick={() => handleSort('clinic_name')}>
        Site<SortIcon col="clinic_name" />
      </th>
      {!groupByOrg && (
        <th className={thSortClass} onClick={() => handleSort('org_name')}>
          Organization<SortIcon col="org_name" />
        </th>
      )}
      <th className={thClass}>Status</th>
      <th className={thSortClass} onClick={() => handleSort('last_checkin')}>
        Last Checkin<SortIcon col="last_checkin" />
      </th>
      <th className={thSortClass} onClick={() => handleSort('onboarding_stage')}>
        Stage<SortIcon col="onboarding_stage" />
      </th>
      <th className={thSortClass} onClick={() => handleSort('appliance_count')}>
        Appliances<SortIcon col="appliance_count" />
      </th>
      <th className={thSortClass} onClick={() => handleSort('tier')}>
        Tier<SortIcon col="tier" />
      </th>
    </tr>
  );

  return (
    <div className="space-y-6 page-enter">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-label-primary tracking-tight">Sites</h1>
          <p className="text-label-tertiary text-sm mt-1">
            {statusCounts.all} site{statusCounts.all !== 1 ? 's' : ''} across {orgCount} organization{orgCount !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/organizations')}
            className="px-4 py-2 rounded-ios bg-fill-quaternary text-label-primary hover:bg-fill-secondary transition-colors text-sm"
          >
            View Organizations
          </button>
          <button
            onClick={() => setShowNewSiteModal(true)}
            className="btn-primary"
          >
            + New Site
          </button>
        </div>
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
          <p className="text-2xl font-bold text-accent-primary">{orgCount}</p>
          <p className="text-xs text-label-tertiary">Organizations</p>
        </GlassCard>
      </div>

      {/* Search + Filter + Group toggle */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search sites..."
            className="w-full pl-10 pr-3 py-2 text-sm border border-separator-light rounded-ios bg-fill-primary focus:ring-2 focus:ring-accent-primary focus:border-transparent"
          />
        </div>
        <div className="flex gap-1">
          {[
            { value: undefined, label: 'All' },
            { value: 'online', label: 'Online' },
            { value: 'offline', label: 'Offline' },
            { value: 'pending', label: 'Pending' },
          ].map((option) => (
            <button
              key={option.value || 'all'}
              onClick={() => { setStatusFilter(option.value); setPage(0); }}
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
        <button
          onClick={() => setGroupByOrg(!groupByOrg)}
          className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
            groupByOrg
              ? 'bg-accent-primary text-white'
              : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
          }`}
        >
          Group by Org
        </button>
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
        ) : groupByOrg && orgGroups ? (
          /* Grouped by org view */
          <div>
            {orgGroups.map((group) => (
              <div key={group.orgId || 'unassigned'}>
                <div
                  className="px-4 py-3 bg-fill-quaternary border-b border-separator-light flex items-center justify-between cursor-pointer hover:bg-fill-secondary transition-colors"
                  onClick={() => group.orgId && navigate(`/organizations/${group.orgId}`)}
                >
                  <div className="flex items-center gap-3">
                    <svg className="w-5 h-5 text-accent-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                    </svg>
                    <span className="font-semibold text-label-primary">{group.orgName}</span>
                    <Badge variant="default">{group.sites.length} site{group.sites.length !== 1 ? 's' : ''}</Badge>
                  </div>
                  {group.orgId && (
                    <span className="text-xs text-label-tertiary">View org detail</span>
                  )}
                </div>
                <table className="w-full">
                  <thead className="bg-fill-quaternary/50 border-b border-separator-light">
                    {tableHeaders}
                  </thead>
                  <tbody className="divide-y divide-separator-light">
                    {group.sites.map((site) => (
                      <SiteRow
                        key={site.site_id}
                        site={site}
                        onClick={() => navigate(`/sites/${site.site_id}`)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        ) : (
          /* Flat list view */
          <>
            <table className="w-full">
              <thead className="bg-fill-quaternary border-b border-separator-light">
                {tableHeaders}
              </thead>
              <tbody className="divide-y divide-separator-light stagger-list">
                {sites.map((site) => (
                  <SiteRow
                    key={site.site_id}
                    site={site}
                    onClick={() => navigate(`/sites/${site.site_id}`)}
                    showOrg
                  />
                ))}
              </tbody>
            </table>
            {totalPages > 1 && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-separator-light bg-fill-secondary">
                <p className="text-sm text-label-tertiary">
                  Showing {page * limit + 1}–{Math.min((page + 1) * limit, total)} of {total}
                </p>
                <div className="flex items-center gap-1">
                  <button onClick={() => setPage(0)} disabled={page === 0}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                    ««
                  </button>
                  <button onClick={() => setPage(p => p - 1)} disabled={page === 0}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                    «
                  </button>
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    let p: number;
                    if (totalPages <= 5) p = i;
                    else if (page < 3) p = i;
                    else if (page > totalPages - 4) p = totalPages - 5 + i;
                    else p = page - 2 + i;
                    return (
                      <button key={p} onClick={() => setPage(p)}
                        className={`px-3 py-1 text-sm rounded ${p === page ? 'bg-accent-primary text-white' : 'hover:bg-fill-tertiary text-label-secondary'}`}>
                        {p + 1}
                      </button>
                    );
                  })}
                  <button onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                    »
                  </button>
                  <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}
                    className="px-2 py-1 text-sm rounded hover:bg-fill-tertiary disabled:opacity-30 disabled:cursor-not-allowed text-label-secondary">
                    »»
                  </button>
                </div>
              </div>
            )}
          </>
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
