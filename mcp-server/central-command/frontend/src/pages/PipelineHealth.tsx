import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, StatCard, Spinner } from '../components/shared';
import { formatTimeAgo, successRateToColor } from '../constants/status';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentEntry {
  agent_id: string;
  hostname: string;
  os_version: string | null;
  agent_version: string | null;
  derived_status: 'active' | 'stale' | 'offline' | 'never';
  last_heartbeat: string | null;
  compliance_percentage: number;
  clinic_name: string | null;
  site_id: string | null;
}

interface AgentHealthResponse {
  checked_at: string;
  total_agents: number;
  summary: { active: number; stale: number; offline: number; never: number };
  agents: AgentEntry[];
}

interface TelemetryEntry {
  incident_type: string;
  runbook_id: string | null;
  success: boolean;
  attempts: number;
  latest: string | null;
}

interface HealingTelemetryResponse {
  hours: number;
  checked_at: string;
  totals: { total: number; succeeded: number; failed: number; success_rate: number };
  error_breakdown: { failure_type: string; count: number }[];
  entries: TelemetryEntry[];
}

interface TargetProtocol {
  protocol: string;
  port: number | null;
  status: string;
  error: string | null;
  latency_ms: number | null;
  last_reported_at: string | null;
}

interface TargetHost {
  hostname: string;
  protocols: TargetProtocol[];
  overall_status: string;
}

interface TargetHealthResponse {
  site_id: string;
  targets: TargetHost[];
  summary: { total_targets: number; healthy: number; unhealthy: number };
}

interface WitnessStatus {
  total_attestations: number;
  attestations_24h: number;
  witness_coverage_24h: {
    total_bundles: number;
    witnessed_bundles: number;
    coverage_pct: number;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Fetch wrapper matching SystemHealth.tsx pattern (cookie auth). */
async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`/api/dashboard${path}`, { credentials: 'include' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/** Group telemetry entries by incident_type and compute per-type success rate. */
function groupTelemetry(entries: TelemetryEntry[]) {
  const map = new Map<string, { incident_type: string; runbook_id: string | null; attempts: number; successes: number; latest: string | null }>();
  for (const e of entries) {
    const key = e.incident_type;
    const existing = map.get(key);
    if (!existing) {
      map.set(key, {
        incident_type: e.incident_type,
        runbook_id: e.runbook_id,
        attempts: e.attempts,
        successes: e.success ? e.attempts : 0,
        latest: e.latest,
      });
    } else {
      existing.attempts += e.attempts;
      if (e.success) existing.successes += e.attempts;
      if (e.latest && (!existing.latest || e.latest > existing.latest)) existing.latest = e.latest;
      if (!existing.runbook_id && e.runbook_id) existing.runbook_id = e.runbook_id;
    }
  }
  return Array.from(map.values())
    .map(g => ({ ...g, success_rate: g.attempts > 0 ? Math.round(100 * g.successes / g.attempts) : 0 }))
    .sort((a, b) => b.attempts - a.attempts);
}

// #43 closure 2026-05-02: delegated to canon helper.
const rateColor = successRateToColor;

// ---------------------------------------------------------------------------
// Badge components
// ---------------------------------------------------------------------------

const AgentStatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const cls: Record<string, string> = {
    active: 'bg-health-healthy/10 text-health-healthy',
    stale: 'bg-health-warning/10 text-health-warning',
    offline: 'bg-health-critical/10 text-health-critical',
    never: 'bg-fill-secondary text-label-tertiary',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cls[status] || cls.never}`}>
      {status === 'active' && <span className="w-1.5 h-1.5 rounded-full bg-health-healthy animate-pulse" />}
      {status}
    </span>
  );
};

const TargetStatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const isOk = status === 'ok';
  const cls = isOk
    ? 'bg-health-healthy/10 text-health-healthy'
    : 'bg-health-critical/10 text-health-critical';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {isOk && <span className="w-1.5 h-1.5 rounded-full bg-health-healthy animate-pulse" />}
      {isOk ? 'ok' : 'unreachable'}
    </span>
  );
};

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

const HeartPulseIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M21 8.25c0-2.485-2.099-4.5-4.688-4.5-1.935 0-3.597 1.126-4.312 2.733-.715-1.607-2.377-2.733-4.313-2.733C5.1 3.75 3 5.765 3 8.25c0 7.22 9 12 9 12s9-4.78 9-12z" />
  </svg>
);

const CpuIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25zm.75-12h9v9h-9v-9z" />
  </svg>
);

const ShieldCheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
  </svg>
);

const ClockIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export const PipelineHealth: React.FC = () => {
  // Agent health (fleet-wide)
  const {
    data: agentData,
    isLoading: agentLoading,
  } = useQuery<AgentHealthResponse>({
    queryKey: ['pipeline-agent-health'],
    queryFn: () => apiFetch<AgentHealthResponse>('/admin/agent-health'),
    refetchInterval: 30_000,
  });

  // Healing telemetry (24h)
  const {
    data: healingData,
    isLoading: healingLoading,
  } = useQuery<HealingTelemetryResponse>({
    queryKey: ['pipeline-healing-telemetry'],
    queryFn: () => apiFetch<HealingTelemetryResponse>('/admin/healing-telemetry?hours=24'),
    refetchInterval: 30_000,
  });

  const { data: witnessData } = useQuery<WitnessStatus>({
    queryKey: ['pipeline-witness-status'],
    queryFn: () => apiFetch<WitnessStatus>('/admin/evidence-witness'),
    refetchInterval: 60_000,
  });

  // Target health — fetch per-site for each known site then merge.
  // We derive site list from agent data to avoid an extra round-trip.
  const siteIds = React.useMemo(() => {
    if (!agentData) return [];
    const ids = new Set<string>();
    for (const a of agentData.agents) {
      if (a.site_id) ids.add(a.site_id);
    }
    return Array.from(ids);
  }, [agentData]);

  const {
    data: targetData,
    isLoading: targetLoading,
  } = useQuery<TargetHealthResponse[]>({
    queryKey: ['pipeline-target-health', siteIds],
    queryFn: async () => {
      if (siteIds.length === 0) return [];
      const results = await Promise.all(
        siteIds.map(id =>
          apiFetch<TargetHealthResponse>(`/sites/${id}/target-health`).catch(() => null),
        ),
      );
      return results.filter((r): r is TargetHealthResponse => r !== null);
    },
    enabled: siteIds.length > 0,
    refetchInterval: 60_000,
  });

  // Derived metrics
  const healingRate = healingData?.totals.success_rate ?? null;
  const activeAgents = agentData?.summary.active ?? 0;
  const totalAgents = agentData?.total_agents ?? 0;

  const targetSummary = React.useMemo(() => {
    if (!targetData) return { healthy: 0, unhealthy: 0 };
    let healthy = 0, unhealthy = 0;
    for (const site of targetData) {
      healthy += site.summary.healthy;
      unhealthy += site.summary.unhealthy;
    }
    return { healthy, unhealthy };
  }, [targetData]);

  // Latest incident timestamp from healing telemetry
  const lastScanCycle = React.useMemo(() => {
    if (!healingData?.entries.length) return null;
    let latest: string | null = null;
    for (const e of healingData.entries) {
      if (e.latest && (!latest || e.latest > latest)) latest = e.latest;
    }
    return latest;
  }, [healingData]);

  const groupedTelemetry = React.useMemo(
    () => groupTelemetry(healingData?.entries ?? []),
    [healingData],
  );

  // Flatten all target hosts for the connectivity table
  const allTargets = React.useMemo(() => {
    if (!targetData) return [];
    const flat: (TargetProtocol & { hostname: string; overall_status: string })[] = [];
    for (const site of targetData) {
      for (const host of site.targets) {
        for (const proto of host.protocols) {
          flat.push({ ...proto, hostname: host.hostname, overall_status: host.overall_status });
        }
      }
    }
    return flat;
  }, [targetData]);

  // -----------------------------------------------------------------------
  // Loading state
  // -----------------------------------------------------------------------

  if (agentLoading && healingLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <Spinner size="lg" />
        <p className="text-label-tertiary text-sm">Loading pipeline health...</p>
      </div>
    );
  }

  return (
    <div className="space-y-5 page-enter">

      {/* ================================================================= */}
      {/* Hero Stats Row                                                     */}
      {/* ================================================================= */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 stagger-list">
        <StatCard
          label="Healing Rate"
          value={healingLoading ? '--' : `${healingRate ?? 0}%`}
          icon={<HeartPulseIcon className="w-[18px] h-[18px] text-health-healthy" />}
          color="#14A89E"
          trend={healingRate !== null ? {
            // 80/50 thresholds match successRateToColor canon — these
            // map to trend-direction tokens, not color. Distinct
            // abstraction (direction vs color) so no helper.
            direction: healingRate >= 80 ? 'up' : healingRate >= 50 ? 'flat' : 'down',  // noqa: score-threshold-gate — trend direction not color
            value: `${healingData?.totals.succeeded ?? 0}/${healingData?.totals.total ?? 0}`,
            positive: healingRate >= 80 ? true : healingRate >= 50 ? undefined : false,  // noqa: score-threshold-gate — trend polarity not color
          } : undefined}
        />
        <StatCard
          label="Active Agents"
          value={agentLoading ? '--' : `${activeAgents}/${totalAgents}`}
          icon={<CpuIcon className="w-[18px] h-[18px] text-ios-blue" />}
          color="#007AFF"
          trend={totalAgents > 0 ? {
            direction: activeAgents === totalAgents ? 'up' : activeAgents > 0 ? 'flat' : 'down',
            value: `${agentData?.summary.stale ?? 0} stale`,
            positive: activeAgents === totalAgents ? true : activeAgents > 0 ? undefined : false,
          } : undefined}
        />
        <StatCard
          label="Target Health"
          value={targetLoading ? '--' : `${targetSummary.healthy} ok`}
          icon={<ShieldCheckIcon className="w-[18px] h-[18px] text-ios-purple" />}
          color="#AF52DE"
          trend={targetSummary.unhealthy > 0 ? {
            direction: 'down',
            value: `${targetSummary.unhealthy} unhealthy`,
            positive: false,
          } : targetSummary.healthy > 0 ? {
            direction: 'up',
            value: 'all reachable',
            positive: true,
          } : undefined}
        />
        <StatCard
          label="Last Scan Cycle"
          value={lastScanCycle ? formatTimeAgo(lastScanCycle) : '--'}
          icon={<ClockIcon className="w-[18px] h-[18px] text-ios-orange" />}
          color="#FF9500"
        />
        <StatCard
          label="Evidence Witnesses"
          value={witnessData ? `${witnessData.witness_coverage_24h.coverage_pct}%` : '--'}
          icon={<ShieldCheckIcon className="w-[18px] h-[18px] text-health-healthy" />}
          color="#30D158"
          trend={witnessData ? {
            direction: witnessData.attestations_24h > 0 ? 'up' : 'flat',
            value: `${witnessData.attestations_24h} attestations`,
            positive: witnessData.attestations_24h > 0 ? true : undefined,
          } : undefined}
        />
      </div>

      {/* ================================================================= */}
      {/* Agent Status Grid                                                  */}
      {/* ================================================================= */}
      <GlassCard>
        <h2 className="text-base font-semibold text-label-primary mb-4">Agent Status</h2>
        {agentLoading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : !agentData?.agents.length ? (
          <p className="text-sm text-label-tertiary py-4">No agents registered.</p>
        ) : (
          <div className="overflow-x-auto -mx-6 px-6">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-separator-primary">
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Hostname</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">OS</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Version</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Status</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Heartbeat</th>
                  <th className="text-right py-2 text-xs font-semibold text-label-secondary uppercase tracking-wider">Compliance</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-primary">
                {agentData.agents.map(agent => (
                  <tr key={agent.agent_id} className="hover:bg-fill-secondary/50 transition-colors">
                    <td className="py-2.5 pr-4 font-medium text-label-primary">{agent.hostname}</td>
                    <td className="py-2.5 pr-4 text-label-secondary">{agent.os_version || '--'}</td>
                    <td className="py-2.5 pr-4 text-label-secondary tabular-nums">{agent.agent_version || '--'}</td>
                    <td className="py-2.5 pr-4"><AgentStatusBadge status={agent.derived_status} /></td>
                    <td className="py-2.5 pr-4 text-label-secondary tabular-nums">{formatTimeAgo(agent.last_heartbeat)}</td>
                    <td className={`py-2.5 text-right font-semibold tabular-nums ${rateColor(agent.compliance_percentage)}`}>
                      {agent.compliance_percentage.toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* ================================================================= */}
      {/* Healing Telemetry Table                                            */}
      {/* ================================================================= */}
      <GlassCard>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-label-primary">Healing Telemetry (24h)</h2>
          {healingData && (
            <span className="text-xs text-label-tertiary tabular-nums">
              {healingData.totals.total} total executions
            </span>
          )}
        </div>
        {healingLoading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : groupedTelemetry.length === 0 ? (
          <p className="text-sm text-label-tertiary py-4">No healing activity in the last 24 hours.</p>
        ) : (
          <div className="overflow-x-auto -mx-6 px-6">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-separator-primary">
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Incident Type</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Runbook</th>
                  <th className="text-right py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Attempts</th>
                  <th className="text-right py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Success Rate</th>
                  <th className="text-right py-2 text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Attempt</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-primary">
                {groupedTelemetry.map(row => (
                  <tr key={row.incident_type} className="hover:bg-fill-secondary/50 transition-colors">
                    <td className="py-2.5 pr-4 font-medium text-label-primary">{row.incident_type}</td>
                    <td className="py-2.5 pr-4 text-label-secondary text-xs font-mono">{row.runbook_id || '--'}</td>
                    <td className="py-2.5 pr-4 text-right text-label-primary tabular-nums">{row.attempts}</td>
                    <td className={`py-2.5 pr-4 text-right font-semibold tabular-nums ${rateColor(row.success_rate)}`}>
                      {row.success_rate}%
                    </td>
                    <td className="py-2.5 text-right text-label-secondary tabular-nums">{formatTimeAgo(row.latest)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* ================================================================= */}
      {/* Target Connectivity                                                */}
      {/* ================================================================= */}
      <GlassCard>
        <h2 className="text-base font-semibold text-label-primary mb-4">Target Connectivity</h2>
        {targetLoading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : allTargets.length === 0 ? (
          <p className="text-sm text-label-tertiary py-4">No target connectivity data available.</p>
        ) : (
          <div className="overflow-x-auto -mx-6 px-6">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-separator-primary">
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Hostname</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Protocol</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Port</th>
                  <th className="text-left py-2 pr-4 text-xs font-semibold text-label-secondary uppercase tracking-wider">Status</th>
                  <th className="text-right py-2 text-xs font-semibold text-label-secondary uppercase tracking-wider">Last Checked</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-separator-primary">
                {allTargets.map((t, i) => (
                  <tr key={`${t.hostname}-${t.protocol}-${i}`} className="hover:bg-fill-secondary/50 transition-colors">
                    <td className="py-2.5 pr-4 font-medium text-label-primary">{t.hostname}</td>
                    <td className="py-2.5 pr-4 text-label-secondary uppercase text-xs">{t.protocol}</td>
                    <td className="py-2.5 pr-4 text-label-secondary tabular-nums">{t.port ?? '--'}</td>
                    <td className="py-2.5 pr-4"><TargetStatusBadge status={t.status} /></td>
                    <td className="py-2.5 text-right text-label-secondary tabular-nums">{formatTimeAgo(t.last_reported_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>
    </div>
  );
};

export default PipelineHealth;
