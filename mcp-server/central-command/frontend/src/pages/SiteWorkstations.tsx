import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSiteWorkstations } from '../hooks';
import type {
  Workstation,
  SiteWorkstationSummary,
  WorkstationComplianceStatus,
} from '../types';
import {
  WORKSTATION_CHECK_LABELS,
  WORKSTATION_CHECK_HIPAA,
} from '../types';

/**
 * Format relative time
 */
function formatRelativeTime(dateString: string | null | undefined): string {
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
 * Status badge colors
 */
const statusColors: Record<WorkstationComplianceStatus, string> = {
  compliant: 'bg-health-healthy text-white',
  drifted: 'bg-health-warning text-white',
  error: 'bg-health-critical text-white',
  unknown: 'bg-slate-400 text-white',
  offline: 'bg-slate-500 text-white',
};

const statusLabels: Record<WorkstationComplianceStatus, string> = {
  compliant: 'Compliant',
  drifted: 'Drifted',
  error: 'Error',
  unknown: 'Unknown',
  offline: 'Offline',
};

/**
 * Compliance summary card
 */
const SummaryCard: React.FC<{ summary: SiteWorkstationSummary }> = ({ summary }) => {
  const complianceRate = summary.overall_compliance_rate || 0;
  const rateColor = complianceRate >= 80 ? 'text-health-healthy' :
                    complianceRate >= 50 ? 'text-health-warning' : 'text-health-critical';

  return (
    <GlassCard className="p-6 mb-6">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Workstation Fleet Summary</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
        {/* Overall compliance */}
        <div className="text-center">
          <div className={`text-3xl font-bold ${rateColor}`}>
            {complianceRate.toFixed(0)}%
          </div>
          <div className="text-sm text-label-secondary">Compliance</div>
        </div>

        {/* Total workstations */}
        <div className="text-center">
          <div className="text-3xl font-bold text-label-primary">
            {summary.total_workstations}
          </div>
          <div className="text-sm text-label-secondary">Total</div>
        </div>

        {/* Online */}
        <div className="text-center">
          <div className="text-3xl font-bold text-blue-400">
            {summary.online_workstations}
          </div>
          <div className="text-sm text-label-secondary">Online</div>
        </div>

        {/* Compliant */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-healthy">
            {summary.compliant_workstations}
          </div>
          <div className="text-sm text-label-secondary">Compliant</div>
        </div>

        {/* Drifted */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-warning">
            {summary.drifted_workstations}
          </div>
          <div className="text-sm text-label-secondary">Drifted</div>
        </div>

        {/* Error */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-critical">
            {summary.error_workstations}
          </div>
          <div className="text-sm text-label-secondary">Error</div>
        </div>

        {/* Unknown/Offline */}
        <div className="text-center">
          <div className="text-3xl font-bold text-slate-400">
            {summary.unknown_workstations}
          </div>
          <div className="text-sm text-label-secondary">Unknown</div>
        </div>
      </div>

      {/* Per-check compliance rates */}
      {summary.check_compliance && Object.keys(summary.check_compliance).length > 0 && (
        <div className="mt-6 pt-4 border-t border-glass-border">
          <h3 className="text-sm font-medium text-label-secondary mb-3">Check Compliance Rates</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {Object.entries(summary.check_compliance).map(([checkType, data]) => (
              <div key={checkType} className="bg-glass-bg/50 rounded-lg p-3 text-center">
                <div className="text-sm font-medium text-label-primary">
                  {WORKSTATION_CHECK_LABELS[checkType] || checkType}
                </div>
                <div className={`text-xl font-bold ${
                  data.rate >= 80 ? 'text-health-healthy' :
                  data.rate >= 50 ? 'text-health-warning' : 'text-health-critical'
                }`}>
                  {data.rate.toFixed(0)}%
                </div>
                <div className="text-xs text-label-tertiary">
                  {data.compliant}/{data.compliant + data.drifted + data.error}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
};

/**
 * Single workstation row
 */
const WorkstationRow: React.FC<{
  workstation: Workstation;
  expanded: boolean;
  onToggle: () => void;
}> = ({ workstation, expanded, onToggle }) => {
  const statusColor = statusColors[workstation.compliance_status] || statusColors.unknown;
  const statusLabel = statusLabels[workstation.compliance_status] || 'Unknown';

  return (
    <>
      <tr
        className="hover:bg-glass-bg/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${workstation.online ? 'bg-health-healthy' : 'bg-slate-500'}`} />
            <span className="font-medium text-label-primary">{workstation.hostname}</span>
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary">
          {workstation.ip_address || '-'}
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {workstation.os_name || '-'}
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-1 rounded-full text-xs font-medium ${statusColor}`}>
            {statusLabel}
          </span>
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {formatRelativeTime(workstation.last_compliance_check)}
        </td>
        <td className="px-4 py-3 text-right">
          <svg
            className={`w-5 h-5 text-label-tertiary transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </td>
      </tr>

      {/* Expanded check details */}
      {expanded && workstation.checks && (
        <tr>
          <td colSpan={6} className="px-4 py-3 bg-glass-bg/20">
            <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
              {(['bitlocker', 'defender', 'patches', 'firewall', 'screen_lock'] as const).map((checkType) => {
                const check = workstation.checks?.[checkType];
                const isCompliant = check?.compliant ?? false;
                const status = check?.status || 'unknown';

                return (
                  <div
                    key={checkType}
                    className={`p-3 rounded-lg border ${
                      isCompliant
                        ? 'border-health-healthy/30 bg-health-healthy/10'
                        : status === 'error'
                        ? 'border-health-critical/30 bg-health-critical/10'
                        : status === 'drifted'
                        ? 'border-health-warning/30 bg-health-warning/10'
                        : 'border-slate-500/30 bg-slate-500/10'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-label-primary">
                        {WORKSTATION_CHECK_LABELS[checkType]}
                      </span>
                      {isCompliant ? (
                        <svg className="w-4 h-4 text-health-healthy" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4 text-health-critical" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                        </svg>
                      )}
                    </div>
                    <div className="text-xs text-label-tertiary">
                      {WORKSTATION_CHECK_HIPAA[checkType]}
                    </div>
                  </div>
                );
              })}
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

/**
 * Workstation list table
 */
const WorkstationTable: React.FC<{ workstations: Workstation[] }> = ({ workstations }) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'online' | 'compliant' | 'drifted'>('all');

  const filteredWorkstations = workstations.filter(ws => {
    switch (filter) {
      case 'online': return ws.online;
      case 'compliant': return ws.compliance_status === 'compliant';
      case 'drifted': return ws.compliance_status === 'drifted' || ws.compliance_status === 'error';
      default: return true;
    }
  });

  return (
    <GlassCard className="overflow-hidden">
      {/* Filter tabs */}
      <div className="flex gap-2 p-4 border-b border-glass-border">
        {(['all', 'online', 'compliant', 'drifted'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              filter === f
                ? 'bg-accent-primary text-white'
                : 'text-label-secondary hover:bg-glass-bg/50'
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
            <span className="ml-1 text-xs opacity-70">
              ({f === 'all' ? workstations.length :
                f === 'online' ? workstations.filter(ws => ws.online).length :
                f === 'compliant' ? workstations.filter(ws => ws.compliance_status === 'compliant').length :
                workstations.filter(ws => ws.compliance_status === 'drifted' || ws.compliance_status === 'error').length
              })
            </span>
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-glass-border">
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Hostname</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">IP Address</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">OS</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Status</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Last Check</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-label-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {filteredWorkstations.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-label-tertiary">
                  No workstations found
                </td>
              </tr>
            ) : (
              filteredWorkstations.map((ws) => (
                <WorkstationRow
                  key={ws.id}
                  workstation={ws}
                  expanded={expandedId === ws.id}
                  onToggle={() => setExpandedId(expandedId === ws.id ? null : ws.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
};

/**
 * Site Workstations Page
 */
export const SiteWorkstations: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const { data, isLoading, error } = useSiteWorkstations(siteId || '');

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <GlassCard className="p-6">
        <div className="text-center text-health-critical">
          <p>Failed to load workstations</p>
          <p className="text-sm text-label-secondary mt-2">{error.message}</p>
        </div>
      </GlassCard>
    );
  }

  const { summary, workstations } = data || { summary: null, workstations: [] };

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/sites" className="text-label-secondary hover:text-label-primary">
          Sites
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}`} className="text-label-secondary hover:text-label-primary">
          {siteId}
        </Link>
        <span className="text-label-tertiary">/</span>
        <span className="text-label-primary">Workstations</span>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Workstation Compliance</h1>
          <p className="text-label-secondary mt-1">
            Monitor and track compliance status for all workstations at this site
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to={`/sites/${siteId}/workstations/rmm-compare`}
            className="px-4 py-2 bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            RMM Compare
          </Link>
          <Badge variant="info" className="px-3 py-1">
            {workstations.length} Workstations
          </Badge>
        </div>
      </div>

      {/* Summary */}
      {summary && <SummaryCard summary={summary} />}

      {/* Workstation list */}
      <WorkstationTable workstations={workstations} />
    </div>
  );
};

export default SiteWorkstations;
