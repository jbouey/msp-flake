import React, { useState, useEffect } from 'react';
import { GlassCard, Spinner, LevelBadge } from '../components/shared';
import { IncidentRow } from '../components/incidents/IncidentRow';
import { useIncidents, useSites } from '../hooks';
import { incidentApi } from '../utils/api';
import type { Incident, IncidentDetail } from '../types';
import { CHECK_TYPE_LABELS } from '../types';

/**
 * Expanded incident detail panel
 */
const IncidentDetailPanel: React.FC<{ incidentId: string; onClose: () => void }> = ({ incidentId, onClose }) => {
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    incidentApi.getIncident(Number(incidentId))
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [incidentId]);

  if (loading) {
    return (
      <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md">
        <div className="flex items-center gap-2 text-label-tertiary">
          <Spinner size="sm" /> Loading incident details...
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md">
        <p className="text-label-tertiary">Failed to load incident details.</p>
      </div>
    );
  }

  const checkLabel = CHECK_TYPE_LABELS[detail.check_type] || detail.check_type;
  const driftData = detail.drift_data || {};
  const hasDriftInfo = Object.keys(driftData).length > 0;

  return (
    <div className="p-6 border-t border-separator-light bg-fill-primary rounded-b-ios-md space-y-4">
      {/* Header with close */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-label-primary">{checkLabel}</h3>
        <button onClick={onClose} className="text-label-tertiary hover:text-label-primary text-sm">
          Close
        </button>
      </div>

      {/* Key info grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <span className="text-label-tertiary">Hostname:</span>
          <div className="text-label-primary font-medium">{detail.hostname || 'Unknown'}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Site:</span>
          <div className="text-label-primary font-medium">{detail.site_id}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Severity:</span>
          <div className="text-label-primary font-medium capitalize">{detail.severity}</div>
        </div>
        <div>
          <span className="text-label-tertiary">Resolution:</span>
          <div>{detail.resolution_level ? <LevelBadge level={detail.resolution_level} showLabel /> : <span className="text-health-warning">Pending</span>}</div>
        </div>
      </div>

      {/* HIPAA Controls */}
      {detail.hipaa_controls.length > 0 && (
        <div>
          <span className="text-xs text-label-tertiary">HIPAA Controls:</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {detail.hipaa_controls.map(ctrl => (
              <span key={ctrl} className="px-2 py-0.5 bg-accent-primary/10 text-accent-primary rounded text-xs font-mono">{ctrl}</span>
            ))}
          </div>
        </div>
      )}

      {/* Drift details */}
      {hasDriftInfo && (
        <div className="rounded-lg bg-glass-bg/30 p-4">
          <h4 className="text-xs font-medium text-label-tertiary uppercase mb-2">Drift Details</h4>
          <div className="space-y-2 text-sm">
            {'message' in driftData && driftData.message !== undefined && driftData.message !== null && (
              <p className="text-label-primary">{String(driftData.message)}</p>
            )}
            {'expected' in driftData && driftData.expected !== undefined && driftData.expected !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Expected:</span>
                <span className="text-health-healthy font-mono text-xs">{String(driftData.expected)}</span>
              </div>
            )}
            {'actual' in driftData && driftData.actual !== undefined && driftData.actual !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Actual:</span>
                <span className="text-health-critical font-mono text-xs">{String(driftData.actual)}</span>
              </div>
            )}
            {'platform' in driftData && driftData.platform !== undefined && driftData.platform !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Platform:</span>
                <span className="text-label-primary">{String(driftData.platform)}</span>
              </div>
            )}
            {'source' in driftData && driftData.source !== undefined && driftData.source !== null && (
              <div className="flex gap-2">
                <span className="text-label-tertiary">Source:</span>
                <span className="text-label-primary">{String(driftData.source)}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Healing/Runbook info */}
      {(detail.runbook_executed || detail.execution_log) && (
        <div className="rounded-lg bg-health-healthy/5 border border-health-healthy/20 p-4">
          <h4 className="text-xs font-medium text-label-tertiary uppercase mb-2">Auto-Healing</h4>
          {detail.runbook_executed && (
            <div className="text-sm">
              <span className="text-label-tertiary">Runbook:</span>{' '}
              <span className="text-label-primary font-mono">{detail.runbook_executed}</span>
            </div>
          )}
          {detail.execution_log && (
            <pre className="mt-2 text-xs text-label-secondary bg-glass-bg/50 rounded p-2 overflow-x-auto max-h-32">
              {detail.execution_log}
            </pre>
          )}
        </div>
      )}

      {/* Status and timestamps */}
      <div className="flex items-center gap-4 text-xs text-label-tertiary pt-2 border-t border-separator-light">
        <span>Created: {new Date(detail.created_at).toLocaleString()}</span>
        {detail.resolved_at && (
          <span>Resolved: {new Date(detail.resolved_at).toLocaleString()}</span>
        )}
        <span className={detail.resolved ? 'text-health-healthy font-medium' : 'text-health-warning font-medium'}>
          {detail.resolved ? 'Resolved' : 'Active'}
        </span>
      </div>
    </div>
  );
};

export const Incidents: React.FC = () => {
  const [filter, setFilter] = useState<'all' | 'active' | 'resolved'>('all');
  const [selectedSiteId, setSelectedSiteId] = useState<string>('');
  const [selectedLevel, setSelectedLevel] = useState<string>('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
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
              <div key={incident.id}>
                <IncidentRow
                  incident={incident}
                  compact={false}
                  onClick={() => setExpandedId(expandedId === String(incident.id) ? null : String(incident.id))}
                />
                {expandedId === String(incident.id) && (
                  <IncidentDetailPanel
                    incidentId={String(incident.id)}
                    onClose={() => setExpandedId(null)}
                  />
                )}
              </div>
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
