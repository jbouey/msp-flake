import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner } from '../components/shared';
import { StatusLight, AuditReadiness } from '../components/composed';
import { OPS_LABELS } from '../constants/copy';
import { formatTimeAgo } from '../constants/status';
import type { OpsStatus } from '../constants/status';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SubsystemStatus {
  status: OpsStatus;
  label: string;
  [key: string]: unknown;
}

interface EvidenceChainStatus extends SubsystemStatus {
  total_bundles: number;
  last_submission_minutes_ago: number;
  chain_gaps: number;
  signing_rate: number;
}

interface SigningStatus extends SubsystemStatus {
  signing_rate: number;
  key_mismatches_24h: number;
  unsigned_legacy: number;
  signature_failures: number;
}

interface OtsAnchoringStatus extends SubsystemStatus {
  anchored: number;
  pending: number;
  batching: number;
  latest_batch_age_hours: number;
}

interface HealingPipelineStatus extends SubsystemStatus {
  l1_heal_rate: number;
  exhausted_count: number;
  stuck_count: number;
}

interface FleetStatus extends SubsystemStatus {
  total_appliances: number;
  online_count: number;
  max_offline_minutes: number;
}

interface OpsHealthResponse {
  checked_at: string;
  evidence_chain: EvidenceChainStatus;
  signing: SigningStatus;
  ots_anchoring: OtsAnchoringStatus;
  healing_pipeline: HealingPipelineStatus;
  fleet: FleetStatus;
}

// ---------------------------------------------------------------------------
// Fetch helper (cookie auth — matches SystemHealth / PipelineHealth pattern)
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`/api/dashboard${path}`, { credentials: 'same-origin' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Subsystem keys in display order
// ---------------------------------------------------------------------------

const SUBSYSTEM_KEYS: (keyof Pick<
  OpsHealthResponse,
  'evidence_chain' | 'signing' | 'ots_anchoring' | 'healing_pipeline' | 'fleet'
>)[] = ['evidence_chain', 'signing', 'ots_anchoring', 'healing_pipeline', 'fleet'];

// ---------------------------------------------------------------------------
// Metric summaries shown on each status card
// ---------------------------------------------------------------------------

function cardStats(key: string, data: OpsHealthResponse): Record<string, string | number> {
  switch (key) {
    case 'evidence_chain': {
      const d = data.evidence_chain;
      return {
        Bundles: d.total_bundles.toLocaleString(),
        'Signing Rate': `${d.signing_rate.toFixed(1)}%`,
        Gaps: d.chain_gaps,
      };
    }
    case 'signing': {
      const d = data.signing;
      return {
        'Rate': `${d.signing_rate.toFixed(1)}%`,
        'Mismatches 24h': d.key_mismatches_24h,
      };
    }
    case 'ots_anchoring': {
      const d = data.ots_anchoring;
      return {
        Anchored: d.anchored.toLocaleString(),
        Pending: d.pending.toLocaleString(),
      };
    }
    case 'healing_pipeline': {
      const d = data.healing_pipeline;
      return {
        'L1 Rate': `${d.l1_heal_rate.toFixed(1)}%`,
        Stuck: d.stuck_count,
      };
    }
    case 'fleet': {
      const d = data.fleet;
      return {
        Online: `${d.online_count}/${d.total_appliances}`,
      };
    }
    default:
      return {};
  }
}

// ---------------------------------------------------------------------------
// Detail panel — expanded view beneath the grid
// ---------------------------------------------------------------------------

function DetailPanel({ subsystem, data }: { subsystem: string; data: OpsHealthResponse }) {
  const rows: { label: string; value: string }[] = [];

  switch (subsystem) {
    case 'evidence_chain': {
      const d = data.evidence_chain;
      rows.push(
        { label: 'Total Bundles', value: d.total_bundles.toLocaleString() },
        { label: 'Last Submission', value: d.last_submission_minutes_ago < 1 ? 'Just now' : `${d.last_submission_minutes_ago}m ago` },
        { label: 'Signing Rate', value: `${d.signing_rate.toFixed(1)}%` },
        { label: 'Chain Gaps', value: d.chain_gaps.toLocaleString() },
      );
      break;
    }
    case 'signing': {
      const d = data.signing;
      rows.push(
        { label: 'Signing Rate', value: `${d.signing_rate.toFixed(1)}%` },
        { label: 'Key Mismatches (24h)', value: d.key_mismatches_24h.toLocaleString() },
        { label: 'Unsigned Legacy', value: d.unsigned_legacy.toLocaleString() },
        { label: 'Signature Failures', value: d.signature_failures.toLocaleString() },
      );
      break;
    }
    case 'ots_anchoring': {
      const d = data.ots_anchoring;
      rows.push(
        { label: 'Anchored', value: d.anchored.toLocaleString() },
        { label: 'Pending', value: d.pending.toLocaleString() },
        { label: 'Batching', value: d.batching.toLocaleString() },
        { label: 'Latest Batch Age', value: `${d.latest_batch_age_hours.toFixed(1)}h` },
      );
      break;
    }
    case 'healing_pipeline': {
      const d = data.healing_pipeline;
      rows.push(
        { label: 'L1 Heal Rate', value: `${d.l1_heal_rate.toFixed(1)}%` },
        { label: 'Exhausted Count', value: d.exhausted_count.toLocaleString() },
        { label: 'Stuck Incidents', value: d.stuck_count.toLocaleString() },
      );
      break;
    }
    case 'fleet': {
      const d = data.fleet;
      rows.push(
        { label: 'Total Appliances', value: d.total_appliances.toLocaleString() },
        { label: 'Online', value: d.online_count.toLocaleString() },
        { label: 'Max Offline', value: d.max_offline_minutes < 60 ? `${d.max_offline_minutes}m` : `${(d.max_offline_minutes / 60).toFixed(1)}h` },
      );
      break;
    }
  }

  const meta = OPS_LABELS[subsystem];

  return (
    <GlassCard>
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-label-primary">
          {meta?.title ?? subsystem} — Detail
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
          {rows.map(r => (
            <div key={r.label}>
              <div className="text-xs text-label-tertiary">{r.label}</div>
              <div className="text-sm font-semibold text-label-primary">{r.value}</div>
            </div>
          ))}
        </div>
      </div>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Refresh icon
// ---------------------------------------------------------------------------

const RefreshIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M2.985 19.644l3.181-3.183" />
  </svg>
);

// ---------------------------------------------------------------------------
// Audit Readiness Section — fetches orgs, renders one card per org
// ---------------------------------------------------------------------------

interface OrgSummary {
  id: string;
  name: string;
}

function AuditReadinessSection() {
  const { data: orgs, isLoading: orgsLoading } = useQuery<OrgSummary[]>({
    queryKey: ['organizations-list'],
    queryFn: async () => {
      const res = await fetch('/api/dashboard/organizations', { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Failed to fetch organizations');
      return res.json();
    },
  });

  return (
    <div className="mt-8">
      <h2 className="text-lg font-semibold text-label-primary mb-4">Audit Readiness</h2>
      {orgsLoading ? (
        <div className="flex items-center justify-center py-8">
          <Spinner size="md" />
        </div>
      ) : orgs && orgs.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {orgs.map(org => (
            <AuditReadiness key={org.id} orgId={org.id} />
          ))}
        </div>
      ) : (
        <GlassCard>
          <p className="text-sm text-label-tertiary">
            No organizations found.
          </p>
        </GlassCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function OpsCenter() {
  const [expandedPanel, setExpandedPanel] = useState<string | null>(null);

  const {
    data,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery<OpsHealthResponse>({
    queryKey: ['ops-health'],
    queryFn: () => apiFetch<OpsHealthResponse>('/ops/health'),
    refetchInterval: 30_000,
  });

  // -- Loading state --
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Spinner size="lg" />
      </div>
    );
  }

  // -- Error state --
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
        <p className="text-sm text-health-critical">
          {error instanceof Error ? error.message : 'Failed to load operations health'}
        </p>
        <button
          onClick={() => refetch()}
          className="px-4 py-2 text-sm font-medium rounded-ios-md bg-accent-primary text-white hover:bg-accent-primary/90 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  const togglePanel = (key: string) => {
    setExpandedPanel(prev => (prev === key ? null : key));
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold text-label-primary">Operations Center</h1>
          <p className="text-sm text-label-secondary">Platform health and audit readiness</p>
        </div>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-xs text-label-tertiary">
              Last checked: {formatTimeAgo(data.checked_at)}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-ios-md border border-white/10 bg-white/5 text-label-secondary hover:bg-white/10 transition-colors disabled:opacity-50"
          >
            <RefreshIcon className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* 5 Status Cards Grid */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {SUBSYSTEM_KEYS.map(key => {
            const sub = data[key] as SubsystemStatus;
            const meta = OPS_LABELS[key];
            return (
              <StatusLight
                key={key}
                status={sub.status}
                title={meta?.title ?? key}
                label={sub.label}
                tooltip={meta?.tooltip}
                docsAnchor={meta?.docsAnchor}
                stats={cardStats(key, data)}
                onClick={() => togglePanel(key)}
                expanded={expandedPanel === key}
              />
            );
          })}
        </div>
      )}

      {/* Expandable Detail Panel */}
      {expandedPanel && data && (
        <DetailPanel subsystem={expandedPanel} data={data} />
      )}

      {/* Audit Readiness */}
      <AuditReadinessSection />
    </div>
  );
}

export default OpsCenter;
