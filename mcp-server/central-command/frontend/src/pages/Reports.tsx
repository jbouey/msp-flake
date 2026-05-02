import React, { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, StatCard, Spinner } from '../components/shared';
import { useSites } from '../hooks';
import { getScoreStatus } from '../constants';

// -- Types --

interface ReportEvidence {
  total_bundles: number;
  signed: number;
  blockchain_anchored: number;
  avg_checks_per_bundle: number;
}

interface ReportIncidents {
  total: number;
  l1_auto_resolved: number;
  l2_llm_resolved: number;
  l3_escalated: number;
  resolution_rate: number;
}

interface ReportCategory {
  check_type: string;
  checks: number;
  passed: number;
  failed: number;
}

interface ComplianceReport {
  site_id: string;
  clinic_name: string;
  period: string;
  generated_at: string;
  compliance_score: number | null;
  evidence: ReportEvidence;
  incidents: ReportIncidents;
  categories: ReportCategory[];
}

// -- Icons --

const DocumentIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
  </svg>
);

const PrinterIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M6.72 13.829c-.24.03-.48.062-.72.096m.72-.096a42.415 42.415 0 0110.56 0m-10.56 0L6.34 18m10.94-4.171c.24.03.48.062.72.096m-.72-.096L17.66 18m0 0l.229 2.523a1.125 1.125 0 01-1.12 1.227H7.231c-.662 0-1.18-.568-1.12-1.227L6.34 18m11.318 0h1.091A2.25 2.25 0 0021 15.75V9.456c0-1.081-.768-2.015-1.837-2.175a48.055 48.055 0 00-1.913-.247M6.34 18H5.25A2.25 2.25 0 013 15.75V9.456c0-1.081.768-2.015 1.837-2.175a48.041 48.041 0 011.913-.247m10.5 0a48.536 48.536 0 00-10.5 0m10.5 0V3.375c0-.621-.504-1.125-1.125-1.125h-8.25c-.621 0-1.125.504-1.125 1.125v3.659M18 10.5h.008v.008H18V10.5zm-3 0h.008v.008H15V10.5z" />
  </svg>
);

const ShieldIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
  </svg>
);

// -- Helper --

function formatPeriod(period: string): string {
  const [year, month] = period.split('-');
  const date = new Date(Number(year), Number(month) - 1);
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'long' });
}

// -- Score Gauge --

const ScoreGauge: React.FC<{ score: number | null }> = ({ score }) => {
  const status = getScoreStatus(score);
  if (score === null) {
    return (
      <div className="flex flex-col items-center opacity-40">
        <span className="text-4xl font-bold font-display text-label-tertiary">N/A</span>
        <span className="text-sm text-label-tertiary">Insufficient Data</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center">
      <span className={`text-5xl font-bold font-display ${status.color}`}>{Math.round(score)}</span>
      <span className={`text-sm font-medium ${status.color}`}>{status.label}</span>
    </div>
  );
};

// -- Main --

export const Reports: React.FC = () => {
  const [selectedSiteId, setSelectedSiteId] = useState<string>('');
  const [selectedMonth, setSelectedMonth] = useState<string>(
    new Date().toISOString().slice(0, 7)
  );

  const { data: sitesData } = useSites();
  const sites = sitesData?.sites || [];

  const shouldFetch = !!selectedSiteId;

  const {
    data: report,
    isLoading,
    error,
    refetch,
  } = useQuery<ComplianceReport>({
    queryKey: ['admin-report', selectedSiteId, selectedMonth],
    queryFn: async () => {
      const params = new URLSearchParams({ site_id: selectedSiteId });
      if (selectedMonth) params.set('month', selectedMonth);
      const res = await fetch(`/api/dashboard/admin/reports/generate?${params}`, {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      return res.json();
    },
    enabled: shouldFetch,
    staleTime: 60_000,
  });

  const handleGenerate = useCallback(() => {
    if (selectedSiteId) refetch();
  }, [selectedSiteId, refetch]);

  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  return (
    <div className="space-y-6 print:space-y-4">
      {/* Config Form */}
      <GlassCard className="print:hidden">
        <div className="p-5">
          <div className="flex items-center gap-3 mb-4">
            <DocumentIcon className="w-5 h-5 text-accent-primary" />
            <h2 className="text-lg font-semibold text-label-primary">Generate Compliance Report</h2>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-sm font-medium text-label-secondary mb-1">Site</label>
              <select
                value={selectedSiteId}
                onChange={(e) => setSelectedSiteId(e.target.value)}
                className="w-full rounded-ios-md border border-border-primary bg-background-primary px-3 py-2 text-sm text-label-primary focus:outline-none focus:ring-2 focus:ring-accent-primary/30"
              >
                <option value="">Select a site...</option>
                {sites.map((s) => (
                  <option key={s.site_id} value={s.site_id}>{s.clinic_name || s.site_id}</option>
                ))}
              </select>
            </div>
            <div className="min-w-[180px]">
              <label className="block text-sm font-medium text-label-secondary mb-1">Month</label>
              <input
                type="month"
                value={selectedMonth}
                onChange={(e) => setSelectedMonth(e.target.value)}
                className="w-full rounded-ios-md border border-border-primary bg-background-primary px-3 py-2 text-sm text-label-primary focus:outline-none focus:ring-2 focus:ring-accent-primary/30"
              />
            </div>
            <button
              onClick={handleGenerate}
              disabled={!selectedSiteId || isLoading}
              className="px-5 py-2 rounded-ios-md bg-accent-primary text-white text-sm font-medium hover:bg-accent-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? 'Generating...' : 'Generate'}
            </button>
          </div>
        </div>
      </GlassCard>

      {/* Loading */}
      {isLoading && (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <Spinner size="lg" />
          <p className="text-sm text-label-tertiary">Generating report...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <GlassCard>
          <div className="p-5 text-center">
            <p className="text-health-critical font-medium">Failed to generate report</p>
            <p className="text-sm text-label-tertiary mt-1">{(error as Error).message}</p>
          </div>
        </GlassCard>
      )}

      {/* Report Display */}
      {report && !isLoading && (
        <div className="space-y-6 print:space-y-4" id="report-content">
          {/* Header */}
          <GlassCard>
            <div className="p-6">
              <div className="flex items-start justify-between">
                <div>
                  <h1 className="text-2xl font-bold font-display text-label-primary">
                    {report.clinic_name}
                  </h1>
                  <p className="text-label-secondary mt-1">
                    Compliance Report &mdash; {formatPeriod(report.period)}
                  </p>
                  <p className="text-xs text-label-tertiary mt-1">
                    Generated {new Date(report.generated_at).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <ScoreGauge score={report.compliance_score} />
                  <button
                    onClick={handlePrint}
                    className="print:hidden p-2 rounded-ios-sm hover:bg-fill-secondary text-label-secondary transition-colors"
                    title="Print / Download PDF"
                  >
                    <PrinterIcon className="w-5 h-5" />
                  </button>
                </div>
              </div>
            </div>
          </GlassCard>

          {/* Evidence Section */}
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary mb-3 flex items-center gap-2">
              <ShieldIcon className="w-4 h-4" />
              Evidence Collection
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCard
                label="Total Bundles"
                value={report.evidence.total_bundles}
                color="#14A89E"
              />
              <StatCard
                label="Signed"
                value={report.evidence.signed}
                color="#3B82F6"
              />
              <StatCard
                label="Blockchain Anchored"
                value={report.evidence.blockchain_anchored}
                color="#8B5CF6"
              />
              <StatCard
                label="Avg Checks / Bundle"
                value={report.evidence.avg_checks_per_bundle}
                color="#F59E0B"
              />
            </div>
          </div>

          {/* Incidents Section */}
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary mb-3">
              Incident Response
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
              <StatCard label="Total Incidents" value={report.incidents.total} />
              <StatCard
                label="L1 Auto-Resolved"
                value={report.incidents.l1_auto_resolved}
                color="#14A89E"
              />
              <StatCard
                label="L2 LLM Resolved"
                value={report.incidents.l2_llm_resolved}
                color="#3B82F6"
              />
              <StatCard
                label="L3 Escalated"
                value={report.incidents.l3_escalated}
                color="#EF4444"
              />
              <StatCard
                label="Resolution Rate"
                value={`${report.incidents.resolution_rate}%`}
                color="#14A89E"
              />
            </div>
          </div>

          {/* Category Breakdown */}
          {report.categories.length > 0 && (
            <GlassCard>
              <div className="p-5">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-label-secondary mb-4">
                  Category Breakdown
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border-primary">
                        <th className="text-left py-2 pr-4 font-medium text-label-secondary">Check Type</th>
                        <th className="text-right py-2 px-4 font-medium text-label-secondary">Total</th>
                        <th className="text-right py-2 px-4 font-medium text-label-secondary">Passed</th>
                        <th className="text-right py-2 px-4 font-medium text-label-secondary">Failed</th>
                        <th className="text-right py-2 pl-4 font-medium text-label-secondary">Pass Rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.categories.map((cat) => {
                        const total = cat.passed + cat.failed;
                        const rate = total > 0 ? Math.round((cat.passed / total) * 100) : 0;
                        // #43 closure 2026-05-02: getScoreStatus canon.
                        const rateColor = getScoreStatus(rate).color;
                        return (
                          <tr key={cat.check_type} className="border-b border-border-primary/50">
                            <td className="py-2 pr-4 text-label-primary font-medium">{cat.check_type}</td>
                            <td className="py-2 px-4 text-right text-label-secondary tabular-nums">{cat.checks}</td>
                            <td className="py-2 px-4 text-right text-health-healthy tabular-nums">{cat.passed}</td>
                            <td className="py-2 px-4 text-right text-health-critical tabular-nums">{cat.failed}</td>
                            <td className={`py-2 pl-4 text-right font-medium tabular-nums ${rateColor}`}>{rate}%</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </GlassCard>
          )}
        </div>
      )}

      {/* Empty state when no report generated yet */}
      {!report && !isLoading && !error && (
        <div className="flex flex-col items-center justify-center py-16 text-center print:hidden">
          <DocumentIcon className="w-12 h-12 text-label-tertiary mb-4" />
          <h2 className="text-lg font-semibold text-label-primary mb-1">Generate a Compliance Report</h2>
          <p className="text-label-tertiary text-sm max-w-md">
            {sites.length > 0
              ? `Select a site from the ${sites.length} available above, choose a reporting month, and click Generate.`
              : 'No sites available. Add a site first to generate compliance reports.'}
          </p>
        </div>
      )}
    </div>
  );
};

export default Reports;
