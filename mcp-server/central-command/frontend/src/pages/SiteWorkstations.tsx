import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge, OrgBanner } from '../components/shared';
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
import { formatTimeAgo } from '../constants';

const formatRelativeTime = formatTimeAgo;

/**
 * Compute pass/fail stats from individual checks
 */
function getCheckStats(ws: Workstation): { passed: number; failed: number; total: number } {
  if (!ws.checks || Object.keys(ws.checks).length === 0) {
    return { passed: 0, failed: 0, total: 0 };
  }
  const checks = Object.values(ws.checks);
  const total = checks.length;
  const passed = checks.filter(c => c.compliant).length;
  return { passed, failed: total - passed, total };
}

/**
 * Detect stale scans — checked > 24h ago or never
 */
function getStaleness(lastCheck: string | undefined): { isStale: boolean; label: string } {
  if (!lastCheck) return { isStale: true, label: 'Never scanned' };
  const hoursAgo = (Date.now() - new Date(lastCheck).getTime()) / (1000 * 60 * 60);
  if (hoursAgo > 24) {
    const days = Math.floor(hoursAgo / 24);
    return { isStale: true, label: `${days}d since scan` };
  }
  return { isStale: false, label: '' };
}

/**
 * Sorting weight: most failing first, then stale, then compliant
 */
function getSortWeight(ws: Workstation): number {
  const { failed } = getCheckStats(ws);
  const stale = getStaleness(ws.last_compliance_check);
  if (ws.compliance_status === 'error') return 1000 + failed;
  if (ws.compliance_status === 'drifted') return 500 + failed * 10 + (stale.isStale ? 50 : 0);
  if (ws.compliance_status === 'unknown') return 100;
  if (stale.isStale) return 50;
  return 0; // compliant
}

/**
 * Status badge colors (fallback for non-check statuses)
 */
const statusColors: Record<WorkstationComplianceStatus, string> = {
  compliant: 'bg-health-healthy text-white',
  warning: 'bg-amber-500 text-white',
  drifted: 'bg-health-warning text-white',
  error: 'bg-health-critical text-white',
  unknown: 'bg-slate-400 text-white',
  offline: 'bg-slate-500 text-white',
};

const statusLabels: Record<WorkstationComplianceStatus, string> = {
  compliant: 'Passing',
  warning: 'Warning',
  drifted: 'Failing',
  error: 'Error',
  unknown: 'No Data',
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

        {/* Failing */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-warning">
            {summary.drifted_workstations}
          </div>
          <div className="text-sm text-label-secondary">Failing</div>
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
                  (data.rate ?? 0) >= 80 ? 'text-health-healthy' :
                  (data.rate ?? 0) >= 50 ? 'text-health-warning' : 'text-health-critical'
                }`}>
                  {(data.rate ?? 0).toFixed(0)}%
                </div>
                <div className="text-xs text-label-tertiary">
                  {data.compliant ?? 0}/{(data.compliant ?? 0) + (data.drifted ?? 0) + (data.error ?? 0)}
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
  const { passed, failed, total } = getCheckStats(workstation);
  const stale = getStaleness(workstation.last_compliance_check);
  const hasChecks = total > 0;

  // Build the status badge based on actual check data
  let badgeLabel: string;
  let badgeColor: string;
  if (workstation.compliance_status === 'compliant') {
    badgeLabel = hasChecks ? `${passed}/${total} Passing` : 'Compliant';
    badgeColor = 'bg-health-healthy text-white';
  } else if (workstation.compliance_status === 'drifted' || workstation.compliance_status === 'error') {
    badgeLabel = hasChecks ? `${failed}/${total} Failing` : (statusLabels[workstation.compliance_status] || 'Failing');
    badgeColor = failed >= 4 ? 'bg-red-600 text-white'
               : failed >= 2 ? 'bg-orange-500 text-white'
               : 'bg-amber-500 text-white';
  } else {
    badgeLabel = statusLabels[workstation.compliance_status] || 'Unknown';
    badgeColor = statusColors[workstation.compliance_status] || statusColors.unknown;
  }

  return (
    <>
      <tr
        className="hover:bg-glass-bg/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${workstation.online ? 'bg-health-healthy' : 'bg-slate-500'}`} />
            <div>
              <span className="font-medium text-label-primary">{workstation.hostname}</span>
              {workstation.hostname === workstation.ip_address && (
                <div className="text-xs text-amber-400">hostname not resolved</div>
              )}
            </div>
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary">
          {workstation.ip_address || '-'}
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {workstation.os_name || '-'}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`px-2 py-1 rounded-full text-xs font-medium ${badgeColor}`}>
              {badgeLabel}
            </span>
            {stale.isStale && workstation.compliance_status !== 'unknown' && (
              <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-amber-500/20 text-amber-400 border border-amber-500/30">
                {stale.label}
              </span>
            )}
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          <span className={stale.isStale ? 'text-amber-400' : ''}>
            {workstation.last_compliance_check
              ? formatRelativeTime(workstation.last_compliance_check)
              : workstation.compliance_status === 'compliant'
                ? 'Status inherited'
                : 'Pending scan'}
          </span>
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
  const [filter, setFilter] = useState<'all' | 'online' | 'compliant' | 'failing'>('all');
  const [wsPage, setWsPage] = useState(0);
  const wsPageSize = 25;

  const failingCount = workstations.filter(ws => ws.compliance_status === 'drifted' || ws.compliance_status === 'error').length;
  const staleCount = workstations.filter(ws => getStaleness(ws.last_compliance_check).isStale && ws.compliance_status !== 'unknown').length;

  const filteredWorkstations = workstations
    .filter(ws => {
      switch (filter) {
        case 'online': return ws.online;
        case 'compliant': return ws.compliance_status === 'compliant';
        case 'failing': return ws.compliance_status === 'drifted' || ws.compliance_status === 'error';
        default: return true;
      }
    })
    .sort((a, b) => getSortWeight(b) - getSortWeight(a));

  const totalFiltered = filteredWorkstations.length;
  const totalWsPages = Math.ceil(totalFiltered / wsPageSize);
  const paginatedWorkstations = filteredWorkstations.slice(wsPage * wsPageSize, (wsPage + 1) * wsPageSize);
  const wsStart = wsPage * wsPageSize + 1;
  const wsEnd = Math.min((wsPage + 1) * wsPageSize, totalFiltered);

  const filterTabs: { key: typeof filter; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: workstations.length },
    { key: 'failing', label: 'Failing', count: failingCount },
    { key: 'online', label: 'Online', count: workstations.filter(ws => ws.online).length },
    { key: 'compliant', label: 'Passing', count: workstations.filter(ws => ws.compliance_status === 'compliant').length },
  ];

  return (
    <GlassCard className="overflow-hidden">
      {/* Filter tabs */}
      <div className="flex items-center gap-2 p-4 border-b border-glass-border">
        {filterTabs.map((f) => (
          <button
            key={f.key}
            onClick={() => { setFilter(f.key); setWsPage(0); }}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              filter === f.key
                ? 'bg-accent-primary text-white'
                : 'text-label-secondary hover:bg-glass-bg/50'
            }`}
          >
            {f.label}
            <span className="ml-1 text-xs opacity-70">({f.count})</span>
          </button>
        ))}
        {staleCount > 0 && (
          <span className="ml-auto px-2 py-1 rounded text-xs font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30">
            {staleCount} stale scan{staleCount !== 1 ? 's' : ''}
          </span>
        )}
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
            {paginatedWorkstations.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-label-tertiary">
                  No workstations found
                </td>
              </tr>
            ) : (
              paginatedWorkstations.map((ws) => (
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

      {/* Pagination */}
      {totalFiltered > wsPageSize && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-glass-border">
          <span className="text-sm text-label-tertiary">
            Showing {wsStart}-{wsEnd} of {totalFiltered}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setWsPage(p => Math.max(0, p - 1))}
              disabled={wsPage === 0}
              className="px-3 py-1.5 text-sm rounded-lg bg-glass-bg text-label-secondary hover:bg-glass-bg/80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setWsPage(p => Math.min(totalWsPages - 1, p + 1))}
              disabled={wsPage >= totalWsPages - 1}
              className="px-3 py-1.5 text-sm rounded-lg bg-glass-bg text-label-secondary hover:bg-glass-bg/80 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}
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
      {/* Org context banner */}
      {siteId && <OrgBanner siteId={siteId} />}

      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/sites" className="text-label-secondary hover:text-label-primary">
          Sites
        </Link>
        <span className="text-label-tertiary">/</span>
        <Link to={`/sites/${siteId}`} className="text-label-secondary hover:text-label-primary">
          {siteId?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || siteId}
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
