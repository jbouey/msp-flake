import React, { useState, useEffect } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useIncidents, useSites } from '../hooks';
import type { Incident } from '../types';

export const Incidents: React.FC = () => {
  const [filter, setFilter] = useState<'all' | 'active' | 'resolved'>('all');
  const [selectedSiteId, setSelectedSiteId] = useState<string>('');
  const [selectedLevel, setSelectedLevel] = useState<string>('');
  const [page, setPage] = useState(0);
  const limit = 50;

  // Fetch sites for the selector
  const { data: sitesData } = useSites({ limit: 200, sort_by: 'clinic_name', sort_dir: 'asc' });
  const sites = sitesData?.sites || [];

  // Build query params
  const resolvedParam = filter === 'all' ? undefined : filter === 'resolved';
  const { data: incidents = [], isLoading, error } = useIncidents({
    site_id: selectedSiteId || undefined,
    limit,
    offset: page * limit,
    level: selectedLevel || undefined,
    resolved: resolvedParam,
  });

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [filter, selectedSiteId, selectedLevel]);

  const activeCount = incidents.filter((i: Incident) => !i.resolved).length;
  const resolvedCount = incidents.filter((i: Incident) => i.resolved).length;
  const hasMore = incidents.length === limit;

  return (
    <div className="space-y-6 page-enter">
      <GlassCard>
        <div className="flex flex-col gap-4 mb-6">
          {/* Header row */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-xl font-semibold text-label-primary tracking-tight">Incidents</h1>
              <p className="text-sm text-label-tertiary mt-1">
                {incidents.length} incidents{selectedSiteId ? ` for ${sites.find(s => s.site_id === selectedSiteId)?.clinic_name || selectedSiteId}` : ''}
                {hasMore ? '+' : ''}
              </p>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setFilter('all')}
                className={`px-3 py-1.5 text-sm font-medium rounded-ios-sm transition-colors ${
                  filter === 'all'
                    ? 'bg-accent-primary text-white shadow-glow-teal'
                    : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
                }`}
              >
                All
              </button>
              <button
                onClick={() => setFilter('active')}
                className={`px-3 py-1.5 text-sm font-medium rounded-ios-sm transition-colors ${
                  filter === 'active'
                    ? 'bg-health-warning text-white shadow-glow-orange'
                    : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
                }`}
              >
                Active ({activeCount})
              </button>
              <button
                onClick={() => setFilter('resolved')}
                className={`px-3 py-1.5 text-sm font-medium rounded-ios-sm transition-colors ${
                  filter === 'resolved'
                    ? 'bg-health-healthy text-white shadow-glow-green'
                    : 'bg-fill-tertiary text-label-secondary hover:bg-fill-secondary'
                }`}
              >
                Resolved ({resolvedCount})
              </button>
            </div>
          </div>

          {/* Filter row */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
            {/* Site selector */}
            <div className="flex items-center gap-2 flex-1 max-w-md">
              <svg className="w-4 h-4 text-label-tertiary shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
              <select
                value={selectedSiteId}
                onChange={e => setSelectedSiteId(e.target.value)}
                className="flex-1 px-3 py-2 text-sm border border-separator-light rounded-ios bg-fill-primary focus:ring-2 focus:ring-accent-primary focus:border-transparent"
              >
                <option value="">All Sites</option>
                {sites.map(site => (
                  <option key={site.site_id} value={site.site_id}>
                    {site.clinic_name} ({site.site_id})
                  </option>
                ))}
              </select>
            </div>

            {/* Level filter */}
            <div className="flex gap-1">
              {[
                { value: '', label: 'All Levels' },
                { value: 'L1', label: 'L1' },
                { value: 'L2', label: 'L2' },
                { value: 'L3', label: 'L3' },
              ].map(option => (
                <button
                  key={option.value || 'all-levels'}
                  onClick={() => setSelectedLevel(option.value)}
                  className={`px-3 py-1.5 text-sm rounded-ios-sm transition-colors ${
                    selectedLevel === option.value
                      ? 'bg-accent-primary text-white'
                      : 'bg-separator-light text-label-secondary hover:bg-separator-light/80'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && (
          <div className="text-center py-8">
            <p className="text-health-critical">{error.message}</p>
          </div>
        )}

        {isLoading && (
          <div className="text-center py-8">
            <Spinner size="lg" />
            <p className="text-label-tertiary mt-4">Loading incidents...</p>
          </div>
        )}

        {!isLoading && !error && incidents.length === 0 && (
          <div className="text-center py-8">
            <div className="text-health-healthy mb-2">
              <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-label-secondary">No incidents to display</p>
            <p className="text-label-tertiary text-sm mt-1">
              {filter !== 'all' || selectedSiteId ? 'Try adjusting your filters' : 'All systems operating normally'}
            </p>
          </div>
        )}

        {!isLoading && !error && incidents.length > 0 && (
          <div className="space-y-2 stagger-list">
            {incidents.map((incident: Incident) => (
              <IncidentRow
                key={incident.id}
                incident={incident}
                compact={false}
                onClick={() => console.log('View incident', incident.id)}
              />
            ))}
          </div>
        )}

        {/* Pagination */}
        {!isLoading && incidents.length > 0 && (page > 0 || hasMore) && (
          <div className="flex items-center justify-between pt-4 mt-4 border-t border-separator-light">
            <p className="text-sm text-label-tertiary">
              Page {page + 1}{hasMore ? '' : ' (last)'}
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="px-3 py-1.5 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={!hasMore}
                className="px-3 py-1.5 text-sm rounded-ios bg-fill-tertiary text-label-secondary hover:bg-fill-secondary disabled:opacity-30 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </GlassCard>
    </div>
  );
};

export default Incidents;
