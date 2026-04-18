import React from 'react';
import { GlassCard, Spinner, Tooltip } from '../shared';
import { useDashboardSLA } from '../../hooks';

/**
 * DashboardSLAStrip — platform-wide SLA posture strip pinned under the
 * hero KPI row. Shows three pills:
 *
 *   1. Healing SLA (hourly healing rate ≥ target across the fleet)
 *   2. Evidence SLA (OTS anchoring delay ≤ 2h)
 *   3. Fleet SLA (≥ 95% of appliances online)
 *
 * Data comes from `useDashboardSLA` — the same hook powers the System
 * Health card on the main dashboard so both surfaces render from the
 * same React Query cache entry with identical fetch semantics.
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
  const { data, isLoading, error } = useDashboardSLA();

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
