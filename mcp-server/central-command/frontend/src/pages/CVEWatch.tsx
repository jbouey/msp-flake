import React, { useState } from 'react';
import { GlassCard } from '../components/shared';
import { useCVESummary, useCVEs, useCVEDetail, useTriggerCVESync, useUpdateCVEStatus } from '../hooks';
import type { CVEEntry } from '../types';

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-health-critical text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-health-warning text-white',
  low: 'bg-blue-500 text-white',
  unknown: 'bg-fill-tertiary text-label-secondary',
};

const STATUS_COLORS: Record<string, string> = {
  open: 'bg-health-critical/20 text-health-critical',
  mitigated: 'bg-health-healthy/20 text-health-healthy',
  accepted_risk: 'bg-health-warning/20 text-health-warning',
  not_affected: 'bg-fill-tertiary text-label-tertiary',
  no_match: 'bg-fill-tertiary text-label-tertiary',
};

const STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  mitigated: 'Mitigated',
  accepted_risk: 'Accepted Risk',
  not_affected: 'Not Affected',
  no_match: 'No Match',
};

export const CVEWatch: React.FC = () => {
  const [severityFilter, setSeverityFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [search, setSearch] = useState('');
  const [selectedCve, setSelectedCve] = useState<string | null>(null);

  const { data: summary, isLoading: summaryLoading } = useCVESummary();
  const { data: cves = [], isLoading: cvesLoading, error } = useCVEs({
    severity: severityFilter || undefined,
    status: statusFilter || undefined,
    search: search || undefined,
  });
  const { data: cveDetail } = useCVEDetail(selectedCve);
  const syncMutation = useTriggerCVESync();
  const updateStatusMutation = useUpdateCVEStatus();

  const isLoading = summaryLoading || cvesLoading;

  return (
    <div className="space-y-6 page-enter">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-label-primary">{summary?.total_cves ?? 0}</div>
            <div className="text-xs text-label-tertiary mt-1">Total CVEs</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-health-critical">{summary?.by_severity.critical ?? 0}</div>
            <div className="text-xs text-label-tertiary mt-1">Critical</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-orange-500">{summary?.by_severity.high ?? 0}</div>
            <div className="text-xs text-label-tertiary mt-1">High</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-health-warning">{summary?.by_severity.medium ?? 0}</div>
            <div className="text-xs text-label-tertiary mt-1">Medium</div>
          </div>
        </GlassCard>
        <GlassCard>
          <div className="text-center">
            <div className="text-2xl font-bold text-health-healthy">{summary?.coverage_pct ?? 0}%</div>
            <div className="text-xs text-label-tertiary mt-1">Coverage</div>
          </div>
        </GlassCard>
      </div>

      {/* CVE List */}
      <GlassCard>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold text-label-primary tracking-tight">CVE Watch</h1>
            <p className="text-sm text-label-tertiary mt-1">
              {cves.length} vulnerabilities tracked
              {summary?.last_sync && (
                <span> &middot; Last sync: {new Date(summary.last_sync).toLocaleString()}</span>
              )}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Search CVEs..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="px-3 py-1.5 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary placeholder-label-tertiary focus:outline-none focus:ring-1 focus:ring-accent-primary w-48"
            />

            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="px-3 py-1.5 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
            >
              <option value="">All Severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-1.5 text-sm bg-fill-tertiary border border-separator rounded-ios-sm text-label-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
            >
              <option value="">All Status</option>
              <option value="open">Open</option>
              <option value="mitigated">Mitigated</option>
              <option value="accepted_risk">Accepted Risk</option>
              <option value="not_affected">Not Affected</option>
            </select>

            <button
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
              className="px-3 py-1.5 text-sm font-medium bg-accent-primary text-white rounded-ios-sm hover:bg-accent-primary/90 disabled:opacity-50 transition-colors"
            >
              {syncMutation.isPending ? 'Syncing...' : 'Sync Now'}
            </button>
          </div>
        </div>

        {error && (
          <div className="text-center py-8">
            <p className="text-health-critical">{(error as Error).message}</p>
          </div>
        )}

        {isLoading && (
          <div className="text-center py-8">
            <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="text-label-tertiary">Loading CVEs...</p>
          </div>
        )}

        {!isLoading && !error && cves.length === 0 && (
          <div className="text-center py-8">
            <div className="text-label-tertiary mb-2">
              <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </div>
            <p className="text-label-secondary text-sm">No CVEs found. Click "Sync Now" to fetch from NVD.</p>
          </div>
        )}

        {!isLoading && !error && cves.length > 0 && (
          <div className="divide-y divide-separator">
            {cves.map((cve: CVEEntry) => (
              <div
                key={cve.id}
                onClick={() => setSelectedCve(selectedCve === cve.cve_id ? null : cve.cve_id)}
                className="flex items-center gap-4 py-3 px-2 hover:bg-fill-tertiary/50 rounded-ios-sm cursor-pointer transition-colors"
              >
                <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${SEVERITY_COLORS[cve.severity] || SEVERITY_COLORS.unknown}`}>
                  {cve.severity.toUpperCase()}
                </span>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium text-accent-primary">{cve.cve_id}</span>
                    {cve.cvss_score && (
                      <span className="text-xs text-label-tertiary">CVSS {cve.cvss_score}</span>
                    )}
                  </div>
                  <p className="text-sm text-label-secondary truncate mt-0.5">{cve.description}</p>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  {cve.affected_count > 0 && (
                    <span className="text-xs text-label-tertiary">
                      {cve.affected_count} affected
                    </span>
                  )}
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[cve.status] || STATUS_COLORS.no_match}`}>
                    {STATUS_LABELS[cve.status] || cve.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      {/* CVE Detail Panel */}
      {selectedCve && cveDetail && (
        <GlassCard>
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 className="text-lg font-semibold text-label-primary font-mono">{cveDetail.cve_id}</h2>
              <div className="flex items-center gap-2 mt-1">
                <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${SEVERITY_COLORS[cveDetail.severity]}`}>
                  {cveDetail.severity.toUpperCase()}
                </span>
                {cveDetail.cvss_score && (
                  <span className="text-sm text-label-tertiary">CVSS {cveDetail.cvss_score}</span>
                )}
                {cveDetail.published_date && (
                  <span className="text-sm text-label-tertiary">
                    Published {new Date(cveDetail.published_date).toLocaleDateString()}
                  </span>
                )}
              </div>
            </div>
            <button
              onClick={() => setSelectedCve(null)}
              className="text-label-tertiary hover:text-label-primary transition-colors"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <p className="text-sm text-label-secondary mb-4">{cveDetail.description}</p>

          {cveDetail.cwe_ids.length > 0 && (
            <div className="mb-4">
              <h3 className="text-xs font-medium text-label-tertiary uppercase mb-1">Weaknesses</h3>
              <div className="flex gap-1">
                {cveDetail.cwe_ids.map((cwe) => (
                  <span key={cwe} className="px-2 py-0.5 text-xs bg-fill-tertiary rounded-full text-label-secondary">{cwe}</span>
                ))}
              </div>
            </div>
          )}

          {cveDetail.references.length > 0 && (
            <div className="mb-4">
              <h3 className="text-xs font-medium text-label-tertiary uppercase mb-1">References</h3>
              <div className="space-y-1">
                {cveDetail.references.slice(0, 5).map((ref, i) => (
                  <a
                    key={i}
                    href={ref.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-sm text-accent-primary hover:underline truncate"
                  >
                    {ref.url}
                  </a>
                ))}
              </div>
            </div>
          )}

          {cveDetail.affected_appliances.length > 0 && (
            <div className="mb-4">
              <h3 className="text-xs font-medium text-label-tertiary uppercase mb-2">Affected Appliances</h3>
              <div className="divide-y divide-separator">
                {cveDetail.affected_appliances.map((a, i) => (
                  <div key={i} className="flex items-center justify-between py-2">
                    <div>
                      <span className="text-sm font-mono text-label-primary">{a.appliance_id.split('-').slice(0, 3).join('-')}</span>
                      <span className="text-xs text-label-tertiary ml-2">{a.site_id}</span>
                    </div>
                    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[a.status]}`}>
                      {STATUS_LABELS[a.status] || a.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Status update actions */}
          <div className="flex items-center gap-2 pt-3 border-t border-separator">
            <span className="text-xs text-label-tertiary mr-2">Set status:</span>
            {['mitigated', 'accepted_risk', 'not_affected', 'open'].map((status) => (
              <button
                key={status}
                onClick={() => updateStatusMutation.mutate({ cveId: cveDetail.cve_id, status })}
                disabled={updateStatusMutation.isPending}
                className={`px-3 py-1 text-xs font-medium rounded-ios-sm transition-colors ${STATUS_COLORS[status]} hover:opacity-80 disabled:opacity-50`}
              >
                {STATUS_LABELS[status]}
              </button>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
};

export default CVEWatch;
