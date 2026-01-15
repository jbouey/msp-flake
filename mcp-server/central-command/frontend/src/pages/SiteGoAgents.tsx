import React, { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { GlassCard, Spinner, Badge } from '../components/shared';
import { useSiteGoAgents, useUpdateGoAgentTier, useTriggerGoAgentCheck, useRemoveGoAgent } from '../hooks';
import type {
  GoAgent,
  SiteGoAgentSummary,
  GoAgentStatus,
  GoAgentCapabilityTier,
} from '../types';
import {
  GO_AGENT_STATUS_LABELS,
  GO_AGENT_TIER_LABELS,
  GO_AGENT_CHECK_LABELS,
  GO_AGENT_CHECK_HIPAA,
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
const statusColors: Record<GoAgentStatus, string> = {
  active: 'bg-health-healthy text-white',
  offline: 'bg-gray-500 text-white',
  error: 'bg-health-critical text-white',
  pending: 'bg-yellow-500 text-white',
};

const tierColors: Record<GoAgentCapabilityTier, string> = {
  monitor_only: 'bg-blue-500/20 text-blue-400 border border-blue-500/30',
  self_heal: 'bg-purple-500/20 text-purple-400 border border-purple-500/30',
  full_remediation: 'bg-health-healthy/20 text-health-healthy border border-health-healthy/30',
};

/**
 * Go Agent summary card
 */
const SummaryCard: React.FC<{ summary: SiteGoAgentSummary }> = ({ summary }) => {
  const complianceRate = summary.overall_compliance_rate || 0;
  const rateColor = complianceRate >= 80 ? 'text-health-healthy' :
                    complianceRate >= 50 ? 'text-health-warning' : 'text-health-critical';

  return (
    <GlassCard className="p-6 mb-6">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Go Agent Fleet Summary</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
        {/* Overall compliance */}
        <div className="text-center">
          <div className={`text-3xl font-bold ${rateColor}`}>
            {complianceRate.toFixed(0)}%
          </div>
          <div className="text-sm text-label-secondary">Compliance</div>
        </div>

        {/* Total agents */}
        <div className="text-center">
          <div className="text-3xl font-bold text-label-primary">
            {summary.total_agents}
          </div>
          <div className="text-sm text-label-secondary">Total</div>
        </div>

        {/* Active */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-healthy">
            {summary.active_agents}
          </div>
          <div className="text-sm text-label-secondary">Active</div>
        </div>

        {/* Offline */}
        <div className="text-center">
          <div className="text-3xl font-bold text-gray-400">
            {summary.offline_agents}
          </div>
          <div className="text-sm text-label-secondary">Offline</div>
        </div>

        {/* Error */}
        <div className="text-center">
          <div className="text-3xl font-bold text-health-critical">
            {summary.error_agents}
          </div>
          <div className="text-sm text-label-secondary">Error</div>
        </div>

        {/* Pending */}
        <div className="text-center">
          <div className="text-3xl font-bold text-yellow-400">
            {summary.pending_agents}
          </div>
          <div className="text-sm text-label-secondary">Pending</div>
        </div>

        {/* RMM Detected */}
        <div className="text-center">
          <div className="text-3xl font-bold text-orange-400">
            {summary.rmm_detected_count}
          </div>
          <div className="text-sm text-label-secondary">RMM Found</div>
        </div>
      </div>

      {/* Agents by tier */}
      {summary.agents_by_tier && Object.keys(summary.agents_by_tier).length > 0 && (
        <div className="mt-6 pt-4 border-t border-glass-border">
          <h3 className="text-sm font-medium text-label-secondary mb-3">Agents by Capability Tier</h3>
          <div className="grid grid-cols-3 gap-3">
            {(['monitor_only', 'self_heal', 'full_remediation'] as GoAgentCapabilityTier[]).map((tier) => (
              <div key={tier} className={`rounded-lg p-3 text-center ${tierColors[tier]}`}>
                <div className="text-sm font-medium">
                  {GO_AGENT_TIER_LABELS[tier]}
                </div>
                <div className="text-2xl font-bold">
                  {summary.agents_by_tier[tier] || 0}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agents by version */}
      {summary.agents_by_version && Object.keys(summary.agents_by_version).length > 0 && (
        <div className="mt-4 pt-4 border-t border-glass-border">
          <h3 className="text-sm font-medium text-label-secondary mb-3">Agent Versions</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.agents_by_version).map(([version, count]) => (
              <span key={version} className="px-2 py-1 bg-glass-bg/50 rounded text-sm text-label-secondary">
                v{version}: <span className="font-medium text-label-primary">{count}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
};

/**
 * Single Go agent row
 */
const GoAgentRow: React.FC<{
  agent: GoAgent;
  siteId: string;
  expanded: boolean;
  onToggle: () => void;
}> = ({ agent, siteId, expanded, onToggle }) => {
  const statusColor = statusColors[agent.status] || statusColors.pending;
  const statusLabel = GO_AGENT_STATUS_LABELS[agent.status] || 'Unknown';
  const tierColor = tierColors[agent.capability_tier] || tierColors.monitor_only;
  const tierLabel = GO_AGENT_TIER_LABELS[agent.capability_tier] || 'Unknown';

  const updateTier = useUpdateGoAgentTier();
  const triggerCheck = useTriggerGoAgentCheck();
  const removeAgent = useRemoveGoAgent();

  const [showTierMenu, setShowTierMenu] = useState(false);

  const handleTierChange = (newTier: GoAgentCapabilityTier) => {
    updateTier.mutate({ siteId, agentId: agent.id, tier: newTier });
    setShowTierMenu(false);
  };

  return (
    <>
      <tr
        className="hover:bg-glass-bg/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${agent.status === 'active' ? 'bg-health-healthy' : 'bg-gray-500'}`} />
            <span className="font-medium text-label-primary">{agent.hostname}</span>
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary">
          {agent.ip_address || '-'}
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          v{agent.agent_version || '?'}
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-1 rounded-full text-xs font-medium ${statusColor}`}>
            {statusLabel}
          </span>
        </td>
        <td className="px-4 py-3">
          <div className="relative">
            <button
              onClick={(e) => { e.stopPropagation(); setShowTierMenu(!showTierMenu); }}
              className={`px-2 py-1 rounded text-xs font-medium ${tierColor}`}
            >
              {tierLabel}
            </button>
            {showTierMenu && (
              <div className="absolute z-50 mt-1 bg-glass-bg border border-glass-border rounded-lg shadow-lg py-1 w-40">
                {(['monitor_only', 'self_heal', 'full_remediation'] as GoAgentCapabilityTier[]).map((tier) => (
                  <button
                    key={tier}
                    onClick={(e) => { e.stopPropagation(); handleTierChange(tier); }}
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-glass-bg/50 ${
                      agent.capability_tier === tier ? 'text-accent-primary' : 'text-label-secondary'
                    }`}
                  >
                    {GO_AGENT_TIER_LABELS[tier]}
                  </button>
                ))}
              </div>
            )}
          </div>
        </td>
        <td className="px-4 py-3 text-label-secondary text-sm">
          {formatRelativeTime(agent.last_heartbeat)}
        </td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-health-healthy">{agent.checks_passed}</span>
            <span className="text-label-tertiary">/</span>
            <span className="text-label-secondary">{agent.checks_total}</span>
          </div>
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
      {expanded && (
        <tr>
          <td colSpan={8} className="px-4 py-3 bg-glass-bg/20">
            {/* Agent info */}
            <div className="flex items-center gap-4 mb-4 text-sm">
              {agent.rmm_detected && (
                <div className="flex items-center gap-2">
                  <span className="text-label-tertiary">RMM Detected:</span>
                  <span className={`font-medium ${agent.rmm_disabled ? 'text-health-healthy' : 'text-orange-400'}`}>
                    {agent.rmm_detected} {agent.rmm_disabled ? '(Disabled)' : '(Active)'}
                  </span>
                </div>
              )}
              {agent.offline_queue_size > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-label-tertiary">Queued Events:</span>
                  <span className="font-medium text-yellow-400">{agent.offline_queue_size}</span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <span className="text-label-tertiary">Connected:</span>
                <span className="text-label-secondary">{formatRelativeTime(agent.connected_at)}</span>
              </div>
            </div>

            {/* Check results */}
            {agent.checks && agent.checks.length > 0 && (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
                {agent.checks.map((check) => {
                  const isPass = check.status === 'pass';
                  const isError = check.status === 'error';
                  const isSkipped = check.status === 'skipped';

                  return (
                    <div
                      key={check.check_type}
                      className={`p-3 rounded-lg border ${
                        isPass
                          ? 'border-health-healthy/30 bg-health-healthy/10'
                          : isError
                          ? 'border-health-critical/30 bg-health-critical/10'
                          : isSkipped
                          ? 'border-gray-500/30 bg-gray-500/10'
                          : 'border-health-warning/30 bg-health-warning/10'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium text-label-primary">
                          {GO_AGENT_CHECK_LABELS[check.check_type] || check.check_type}
                        </span>
                        {isPass ? (
                          <svg className="w-4 h-4 text-health-healthy" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                          </svg>
                        ) : isSkipped ? (
                          <svg className="w-4 h-4 text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-11a1 1 0 10-2 0v3.586L7.707 9.293a1 1 0 00-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L11 10.586V7z" clipRule="evenodd" />
                          </svg>
                        ) : (
                          <svg className="w-4 h-4 text-health-critical" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                          </svg>
                        )}
                      </div>
                      <div className="text-xs text-label-tertiary">
                        {GO_AGENT_CHECK_HIPAA[check.check_type] || 'N/A'}
                      </div>
                      {check.message && (
                        <div className="text-xs text-label-secondary mt-1 truncate" title={check.message}>
                          {check.message}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2">
              <button
                onClick={(e) => { e.stopPropagation(); triggerCheck.mutate({ siteId, agentId: agent.id }); }}
                disabled={triggerCheck.isPending}
                className="px-3 py-1.5 bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20 rounded text-sm font-medium transition-colors disabled:opacity-50"
              >
                {triggerCheck.isPending ? 'Running...' : 'Run Check'}
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`Remove agent ${agent.hostname} from registry?`)) {
                    removeAgent.mutate({ siteId, agentId: agent.id });
                  }
                }}
                disabled={removeAgent.isPending}
                className="px-3 py-1.5 bg-health-critical/10 text-health-critical hover:bg-health-critical/20 rounded text-sm font-medium transition-colors disabled:opacity-50"
              >
                {removeAgent.isPending ? 'Removing...' : 'Remove'}
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

/**
 * Go agent list table
 */
const GoAgentTable: React.FC<{ agents: GoAgent[]; siteId: string }> = ({ agents, siteId }) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'active' | 'healthy' | 'drifted'>('all');

  const filteredAgents = agents.filter(agent => {
    switch (filter) {
      case 'active': return agent.status === 'active';
      case 'healthy': return agent.checks_passed === agent.checks_total && agent.checks_total > 0;
      case 'drifted': return agent.checks_passed < agent.checks_total;
      default: return true;
    }
  });

  return (
    <GlassCard className="overflow-hidden">
      {/* Filter tabs */}
      <div className="flex gap-2 p-4 border-b border-glass-border">
        {(['all', 'active', 'healthy', 'drifted'] as const).map((f) => (
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
              ({f === 'all' ? agents.length :
                f === 'active' ? agents.filter(a => a.status === 'active').length :
                f === 'healthy' ? agents.filter(a => a.checks_passed === a.checks_total && a.checks_total > 0).length :
                agents.filter(a => a.checks_passed < a.checks_total).length
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
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Version</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Status</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Tier</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Last Heartbeat</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-label-secondary">Checks</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-label-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {filteredAgents.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-label-tertiary">
                  No Go agents connected
                </td>
              </tr>
            ) : (
              filteredAgents.map((agent) => (
                <GoAgentRow
                  key={agent.id}
                  agent={agent}
                  siteId={siteId}
                  expanded={expandedId === agent.id}
                  onToggle={() => setExpandedId(expandedId === agent.id ? null : agent.id)}
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
 * Site Go Agents Page
 */
export const SiteGoAgents: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const { data, isLoading, error } = useSiteGoAgents(siteId || '');

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
          <p>Failed to load Go agents</p>
          <p className="text-sm text-label-secondary mt-2">{error.message}</p>
        </div>
      </GlassCard>
    );
  }

  const { summary, agents } = data || { summary: null, agents: [] };

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
        <span className="text-label-primary">Go Agents</span>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Go Agents</h1>
          <p className="text-label-secondary mt-1">
            Lightweight workstation agents pushing drift events via gRPC
          </p>
        </div>
        <Badge variant="info" className="px-3 py-1">
          {agents.length} Agents
        </Badge>
      </div>

      {/* Info banner */}
      <GlassCard className="p-4 bg-accent-primary/5 border-accent-primary/20">
        <div className="flex items-start gap-3">
          <svg className="w-5 h-5 text-accent-primary mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div>
            <h3 className="text-sm font-medium text-label-primary">Push-Based Architecture</h3>
            <p className="text-sm text-label-secondary mt-1">
              Go agents run on Windows workstations and push compliance events to the appliance via gRPC (port 50051).
              This replaces WinRM polling for better scalability (25-50+ workstations per site).
            </p>
          </div>
        </div>
      </GlassCard>

      {/* Summary */}
      {summary && <SummaryCard summary={summary} />}

      {/* Agent list */}
      <GoAgentTable agents={agents} siteId={siteId || ''} />
    </div>
  );
};

export default SiteGoAgents;
