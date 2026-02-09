import React, { useState, useMemo } from 'react';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSites, useSiteRunbookConfig, useSetSiteRunbook, useRunbookCategories } from '../hooks';
import type { SiteRunbookConfig } from '../utils/api';

/**
 * Category badge colors
 */
const CATEGORY_COLORS: Record<string, string> = {
  services: 'bg-blue-100 text-blue-700',
  security: 'bg-red-100 text-red-700',
  network: 'bg-purple-100 text-purple-700',
  storage: 'bg-amber-100 text-amber-700',
  updates: 'bg-green-100 text-green-700',
  active_directory: 'bg-indigo-100 text-indigo-700',
  backup: 'bg-cyan-100 text-cyan-700',
  antivirus: 'bg-rose-100 text-rose-700',
};

/**
 * Severity badge component
 */
const SeverityBadge: React.FC<{ severity: string }> = ({ severity }) => {
  const colors: Record<string, string> = {
    critical: 'bg-health-critical/10 text-health-critical',
    high: 'bg-orange-100 text-orange-700',
    medium: 'bg-yellow-100 text-yellow-700',
    low: 'bg-slate-100 text-slate-600',
  };
  return (
    <span className={`px-2 py-0.5 text-xs rounded-full ${colors[severity] || colors.medium}`}>
      {severity}
    </span>
  );
};

/**
 * Toggle switch component
 */
const Toggle: React.FC<{
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}> = ({ checked, onChange, disabled }) => {
  return (
    <button
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-accent-primary focus:ring-offset-2 ${
        checked ? 'bg-health-healthy' : 'bg-slate-300'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
};

/**
 * Runbook row component
 */
const RunbookRow: React.FC<{
  runbook: SiteRunbookConfig;
  onToggle: (runbookId: string, enabled: boolean) => void;
  isLoading: boolean;
}> = ({ runbook, onToggle, isLoading }) => {
  return (
    <tr className="border-b border-separator-light hover:bg-fill-secondary/50 transition-colors">
      <td className="py-3 px-4">
        <div className="flex items-center gap-3">
          <Toggle
            checked={runbook.enabled}
            onChange={(checked) => onToggle(runbook.runbook_id, checked)}
            disabled={isLoading}
          />
        </div>
      </td>
      <td className="py-3 px-4">
        <div>
          <p className="font-medium text-label-primary">{runbook.name}</p>
          <p className="text-xs text-label-tertiary font-mono">{runbook.runbook_id}</p>
        </div>
      </td>
      <td className="py-3 px-4">
        <span className={`px-2 py-1 text-xs rounded-full ${CATEGORY_COLORS[runbook.category] || 'bg-slate-100 text-slate-700'}`}>
          {runbook.category.replace(/_/g, ' ')}
        </span>
      </td>
      <td className="py-3 px-4">
        <SeverityBadge severity={runbook.severity} />
      </td>
      <td className="py-3 px-4">
        {runbook.is_disruptive && (
          <Badge variant="warning">Disruptive</Badge>
        )}
      </td>
      <td className="py-3 px-4 text-sm text-label-secondary max-w-xs truncate">
        {runbook.description || '-'}
      </td>
    </tr>
  );
};

/**
 * RunbookConfig page component
 */
export const RunbookConfig: React.FC = () => {
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  // Fetch data
  const { data: sitesData, isLoading: isLoadingSites } = useSites();
  const { data: categories = [] } = useRunbookCategories();
  const { data: runbooks = [], isLoading: isLoadingRunbooks } = useSiteRunbookConfig(selectedSiteId);
  const setRunbook = useSetSiteRunbook();

  const sites = sitesData?.sites || [];

  // Filter runbooks
  const filteredRunbooks = useMemo(() => {
    return runbooks.filter((rb) => {
      const matchesCategory = filterCategory === 'all' || rb.category === filterCategory;
      const matchesSearch =
        searchQuery === '' ||
        rb.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        rb.runbook_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (rb.description?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);
      return matchesCategory && matchesSearch;
    });
  }, [runbooks, filterCategory, searchQuery]);

  // Stats
  const enabledCount = runbooks.filter((rb) => rb.enabled).length;
  const disabledCount = runbooks.filter((rb) => !rb.enabled).length;
  const disruptiveCount = runbooks.filter((rb) => rb.is_disruptive).length;

  // Get category counts
  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    runbooks.forEach((rb) => {
      counts[rb.category] = (counts[rb.category] || 0) + 1;
    });
    return counts;
  }, [runbooks]);

  // Handle toggle
  const handleToggle = async (runbookId: string, enabled: boolean) => {
    if (!selectedSiteId) return;
    try {
      await setRunbook.mutateAsync({ siteId: selectedSiteId, runbookId, enabled });
      setToast({ message: `Runbook ${enabled ? 'enabled' : 'disabled'}`, type: 'success' });
      setTimeout(() => setToast(null), 2000);
    } catch (error) {
      setToast({ message: `Failed to update runbook: ${error}`, type: 'error' });
      setTimeout(() => setToast(null), 3000);
    }
  };

  // Bulk enable/disable category
  const handleBulkToggle = async (category: string, enabled: boolean) => {
    if (!selectedSiteId) return;
    const categoryRunbooks = runbooks.filter((rb) => rb.category === category);
    for (const rb of categoryRunbooks) {
      try {
        await setRunbook.mutateAsync({ siteId: selectedSiteId, runbookId: rb.runbook_id, enabled });
      } catch (error) {
        console.error(`Failed to update ${rb.runbook_id}:`, error);
      }
    }
    setToast({ message: `${category} runbooks ${enabled ? 'enabled' : 'disabled'}`, type: 'success' });
    setTimeout(() => setToast(null), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Runbook Configuration</h1>
          <p className="text-label-tertiary mt-1">
            Enable or disable automated remediation runbooks per site
          </p>
        </div>
      </div>

      {/* Site Selector */}
      <GlassCard>
        <div className="flex items-center gap-4">
          <label className="text-sm font-medium text-label-secondary">Select Site:</label>
          {isLoadingSites ? (
            <Spinner size="sm" />
          ) : (
            <select
              value={selectedSiteId || ''}
              onChange={(e) => setSelectedSiteId(e.target.value || null)}
              className="flex-1 max-w-md px-3 py-2 bg-fill-secondary text-label-primary border border-separator-light rounded-ios focus:outline-none focus:ring-2 focus:ring-accent-primary"
            >
              <option value="">Choose a site...</option>
              {sites.map((site) => (
                <option key={site.site_id} value={site.site_id}>
                  {site.clinic_name} ({site.site_id})
                </option>
              ))}
            </select>
          )}
        </div>
      </GlassCard>

      {!selectedSiteId ? (
        <GlassCard>
          <div className="text-center py-12">
            <svg className="w-16 h-16 mx-auto text-label-tertiary mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
            <p className="text-label-secondary text-lg">Select a site to configure runbooks</p>
            <p className="text-label-tertiary text-sm mt-1">
              Each site can have its own runbook configuration
            </p>
          </div>
        </GlassCard>
      ) : isLoadingRunbooks ? (
        <div className="flex items-center justify-center py-12">
          <Spinner size="lg" />
        </div>
      ) : (
        <>
          {/* Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <GlassCard padding="md">
              <p className="text-xs text-label-tertiary uppercase tracking-wide">Total Runbooks</p>
              <p className="text-2xl font-semibold mt-1">{runbooks.length}</p>
            </GlassCard>
            <GlassCard padding="md">
              <p className="text-xs text-label-tertiary uppercase tracking-wide">Enabled</p>
              <p className="text-2xl font-semibold text-health-healthy mt-1">{enabledCount}</p>
            </GlassCard>
            <GlassCard padding="md">
              <p className="text-xs text-label-tertiary uppercase tracking-wide">Disabled</p>
              <p className="text-2xl font-semibold text-label-tertiary mt-1">{disabledCount}</p>
            </GlassCard>
            <GlassCard padding="md">
              <p className="text-xs text-label-tertiary uppercase tracking-wide">Disruptive</p>
              <p className="text-2xl font-semibold text-health-warning mt-1">{disruptiveCount}</p>
            </GlassCard>
          </div>

          {/* Filters & Category Actions */}
          <div className="flex flex-wrap items-center gap-4">
            {/* Search */}
            <div className="relative flex-1 max-w-md">
              <svg
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-label-tertiary"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              <input
                type="text"
                placeholder="Search runbooks..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 bg-white/50 border border-separator-light rounded-ios-md text-sm focus:outline-none focus:ring-2 focus:ring-accent-primary focus:border-transparent"
              />
            </div>

            {/* Category filter */}
            <div className="flex items-center gap-1 bg-separator-light rounded-ios-md p-1">
              <button
                onClick={() => setFilterCategory('all')}
                className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                  filterCategory === 'all'
                    ? 'bg-white shadow-sm text-label-primary font-medium'
                    : 'text-label-secondary hover:text-label-primary'
                }`}
              >
                All ({runbooks.length})
              </button>
              {categories.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setFilterCategory(cat)}
                  className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                    filterCategory === cat
                      ? 'bg-white shadow-sm text-label-primary font-medium'
                      : 'text-label-secondary hover:text-label-primary'
                  }`}
                >
                  {cat.replace(/_/g, ' ')} ({categoryCounts[cat] || 0})
                </button>
              ))}
            </div>
          </div>

          {/* Bulk Actions */}
          {filterCategory !== 'all' && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-label-tertiary">Bulk actions for {filterCategory}:</span>
              <button
                onClick={() => handleBulkToggle(filterCategory, true)}
                disabled={setRunbook.isPending}
                className="px-3 py-1.5 text-xs rounded-ios bg-health-healthy/10 text-health-healthy hover:bg-health-healthy/20 disabled:opacity-50 transition-colors"
              >
                Enable All
              </button>
              <button
                onClick={() => handleBulkToggle(filterCategory, false)}
                disabled={setRunbook.isPending}
                className="px-3 py-1.5 text-xs rounded-ios bg-blue-50 text-blue-600 hover:bg-blue-100 disabled:opacity-50 transition-colors"
              >
                Disable All
              </button>
            </div>
          )}

          {/* Runbook Table */}
          <GlassCard className="overflow-hidden">
            {filteredRunbooks.length === 0 ? (
              <div className="text-center py-8">
                <p className="text-label-secondary">No runbooks match your filters</p>
                <button
                  onClick={() => {
                    setFilterCategory('all');
                    setSearchQuery('');
                  }}
                  className="text-accent-primary text-sm mt-2 hover:underline"
                >
                  Clear filters
                </button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-fill-secondary text-left text-xs uppercase text-label-tertiary tracking-wide">
                      <th className="py-3 px-4 w-20">Enabled</th>
                      <th className="py-3 px-4">Runbook</th>
                      <th className="py-3 px-4">Category</th>
                      <th className="py-3 px-4">Severity</th>
                      <th className="py-3 px-4">Flags</th>
                      <th className="py-3 px-4">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRunbooks.map((runbook) => (
                      <RunbookRow
                        key={runbook.runbook_id}
                        runbook={runbook}
                        onToggle={handleToggle}
                        isLoading={setRunbook.isPending}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </GlassCard>

          {/* Legend */}
          <GlassCard padding="sm">
            <div className="flex items-center justify-center gap-6 text-xs text-label-tertiary">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-health-healthy" />
                <span>Enabled - Runbook will auto-execute when triggered</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-slate-300" />
                <span>Disabled - Runbook skipped, drift still detected</span>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="warning">Disruptive</Badge>
                <span>May cause service interruption</span>
              </div>
            </div>
          </GlassCard>
        </>
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

export default RunbookConfig;
