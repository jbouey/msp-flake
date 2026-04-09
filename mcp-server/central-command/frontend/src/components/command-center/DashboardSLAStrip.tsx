import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Tooltip } from '../shared';

/**
 * DashboardSLAStrip — platform-wide SLA posture strip pinned under the
 * hero KPI row. Shows three pills:
 *
 *   1. Healing SLA (hourly healing rate ≥ target across the fleet)
 *   2. Evidence SLA (OTS anchoring delay ≤ 2h)
 *   3. Fleet SLA (≥ 95% of appliances online)
 *
 * Data source: platform metrics aggregated server-side. Falls back to
 * computing from /api/stats if the dedicated endpoint isn't available.
 *
 * This is the "are we meeting our contracts" glance — the dashboard's
 * single most important row for customer success + compliance reporting.
 */

interface SLAEntry {
  label: string;
  current: number | null;
  target: number;
  unit: '%' | 'min' | 'h';
  met: boolean | null;
  /** Informative subtitle shown under the status pill */
  detail: string;
  /** Optional tooltip expanding on the SLA definition */
  tip: string;
}

interface DashboardSLAData {
  healing_rate_24h: number | null;
  healing_target: number;
  ots_anchor_age_minutes: number | null;
  ots_target_minutes: number;
  online_appliances_pct: number | null;
  fleet_target: number;
  computed_at?: string | null;
}

/**
 * Fetch the strip data. Tries `/api/dashboard/sla-strip` first, falls back
 * to computing from `/api/dashboard/stats` + light math. The fallback is
 * intentionally permissive because the strip is a soft signal — if any
 * subpart is missing we show "—" instead of the whole strip.
 */
async function fetchSLAStrip(): Promise<DashboardSLAData> {
  // Try the dedicated endpoint first
  try {
    const res = await fetch('/api/dashboard/sla-strip', { credentials: 'same-origin' });
    if (res.ok) return await res.json();
  } catch {
    // fall through to stats fallback
  }

  // Fallback: derive from /stats
  const statsRes = await fetch('/api/dashboard/stats', { credentials: 'same-origin' });
  if (!statsRes.ok) {
    throw new Error(`stats fallback failed: ${statsRes.status}`);
  }
  const stats = await statsRes.json();
  const onlinePct =
    stats.total_appliances > 0
      ? (stats.online_appliances / stats.total_appliances) * 100
      : null;
  return {
    healing_rate_24h: stats.l1_resolution_rate ?? null,
    healing_target: 85,
    ots_anchor_age_minutes: null, // unknown without the dedicated endpoint
    ots_target_minutes: 120,
    online_appliances_pct: onlinePct,
    fleet_target: 95,
    computed_at: stats.computed_at ?? null,
  };
}

const StatusDot: React.FC<{ met: boolean | null }> = ({ met }) => {
  if (met === null) return <span className="w-2 h-2 rounded-full bg-label-tertiary" />;
  if (met) return <span className="w-2 h-2 rounded-full bg-health-healthy" />;
  return <span className="w-2 h-2 rounded-full bg-health-critical animate-pulse" />;
};

const SLAEntryPill: React.FC<{ entry: SLAEntry }> = ({ entry }) => {
  const displayValue =
    entry.current === null ? '—' : `${entry.current.toFixed(entry.unit === '%' ? 1 : 0)}${entry.unit}`;
  const targetLabel = `target ${entry.target}${entry.unit}`;
  const statusColor =
    entry.met === null
      ? 'text-label-tertiary'
      : entry.met
        ? 'text-health-healthy'
        : 'text-health-critical';

  return (
    <Tooltip text={entry.tip}>
      <div className="flex-1 min-w-[180px] flex items-center gap-3 px-4 py-3 rounded-ios bg-fill-secondary/60 cursor-help">
        <StatusDot met={entry.met} />
        <div className="flex-1 min-w-0">
          <p className="text-[10px] uppercase tracking-wide text-label-tertiary font-medium">
            {entry.label}
          </p>
          <div className="flex items-baseline gap-2">
            <span className={`text-base font-semibold tabular-nums ${statusColor}`}>
              {displayValue}
            </span>
            <span className="text-[10px] text-label-tertiary">{targetLabel}</span>
          </div>
          <p className="text-[10px] text-label-tertiary truncate mt-0.5">{entry.detail}</p>
        </div>
      </div>
    </Tooltip>
  );
};

export const DashboardSLAStrip: React.FC = () => {
  const { data, isLoading, error } = useQuery<DashboardSLAData>({
    queryKey: ['dashboard-sla-strip'],
    queryFn: fetchSLAStrip,
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
    retry: false,
  });

  if (isLoading) {
    return (
      <GlassCard className="py-3">
        <div className="flex justify-center">
          <Spinner size="sm" />
        </div>
      </GlassCard>
    );
  }

  if (error || !data) {
    return null; // soft fail — strip is optional polish, not core data
  }

  const healingMet =
    data.healing_rate_24h === null ? null : data.healing_rate_24h >= data.healing_target;
  const otsMet =
    data.ots_anchor_age_minutes === null
      ? null
      : data.ots_anchor_age_minutes <= data.ots_target_minutes;
  const fleetMet =
    data.online_appliances_pct === null
      ? null
      : data.online_appliances_pct >= data.fleet_target;

  const entries: SLAEntry[] = [
    {
      label: 'Healing SLA',
      current: data.healing_rate_24h,
      target: data.healing_target,
      unit: '%',
      met: healingMet,
      detail:
        healingMet === null
          ? 'no recent telemetry'
          : healingMet
            ? 'meeting auto-heal target'
            : `${(data.healing_target - (data.healing_rate_24h ?? 0)).toFixed(1)}% below target`,
      tip: 'Percentage of incidents resolved by L1/L2 automation in the last 24h. Target: ≥85% for platform baseline.',
    },
    {
      label: 'Evidence SLA',
      current: data.ots_anchor_age_minutes,
      target: data.ots_target_minutes,
      unit: 'min',
      met: otsMet,
      detail:
        otsMet === null
          ? 'anchoring metric unavailable'
          : otsMet
            ? 'bundles anchoring on time'
            : 'anchoring delay above target',
      tip: 'Age of the oldest pending OpenTimestamps anchor. HIPAA evidence integrity requires ≤2h.',
    },
    {
      label: 'Fleet SLA',
      current: data.online_appliances_pct,
      target: data.fleet_target,
      unit: '%',
      met: fleetMet,
      detail:
        fleetMet === null
          ? 'no appliance data'
          : fleetMet
            ? 'fleet availability on target'
            : `${(data.fleet_target - (data.online_appliances_pct ?? 0)).toFixed(1)}% below target`,
      tip: 'Percentage of appliances reporting in the last 5 minutes. Target: ≥95% fleet availability.',
    },
  ];

  return (
    <GlassCard className="py-3">
      <div className="flex flex-wrap items-stretch gap-3">
        {entries.map((entry) => (
          <SLAEntryPill key={entry.label} entry={entry} />
        ))}
      </div>
    </GlassCard>
  );
};

export default DashboardSLAStrip;
