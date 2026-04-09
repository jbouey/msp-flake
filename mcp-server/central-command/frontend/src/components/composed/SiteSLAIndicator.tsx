import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, Spinner, Tooltip } from '../shared';

interface SLATrendPoint {
  period_start: string;
  healing_rate: number;
  sla_met: boolean;
}

interface SiteSLAResponse {
  site_id: string;
  sla_target: number;
  current_rate: number | null;
  sla_met: boolean | null;
  periods_last_7d: number;
  periods_met_last_7d: number;
  met_pct_7d: number | null;
  trend: SLATrendPoint[];
  source: 'site_healing_sla' | 'execution_telemetry' | 'none';
}

interface Props {
  siteId: string;
}

/**
 * Tiny inline sparkline built from <svg>. Accepts 0..100 rates and draws
 * them against a fixed 0..100 scale so the visual always compares to the
 * same ceiling (a 40% rate is always ~40% of the sparkline height).
 *
 * No external lib — we only ever render ~24-48 points so a hand-rolled
 * polyline is both faster and one less dependency to audit.
 */
const Sparkline: React.FC<{ points: number[]; targetLine: number; width?: number; height?: number }> = ({
  points,
  targetLine,
  width = 120,
  height = 36,
}) => {
  if (points.length < 2) {
    return (
      <div
        className="flex items-center justify-center text-[10px] text-label-tertiary"
        style={{ width, height }}
      >
        insufficient data
      </div>
    );
  }
  const pad = 2;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;
  const step = innerW / (points.length - 1);
  const y = (v: number) => pad + innerH - (Math.max(0, Math.min(100, v)) / 100) * innerH;
  const pts = points.map((v, i) => `${pad + i * step},${y(v)}`).join(' ');
  const targetY = y(targetLine);

  return (
    <svg width={width} height={height} aria-hidden="true">
      <line
        x1={pad}
        x2={pad + innerW}
        y1={targetY}
        y2={targetY}
        stroke="currentColor"
        strokeDasharray="2 2"
        className="text-label-tertiary/40"
      />
      <polyline
        points={pts}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-accent-primary"
      />
    </svg>
  );
};

/**
 * SiteSLAIndicator — compact SLA-at-a-glance panel for the Site Detail
 * sidebar. Reports:
 *   - current healing rate vs the site's SLA target
 *   - a coloured status pill ("Meeting", "Missing", "No Data")
 *   - 7-day met % (how often the hourly SLA was hit over the last week)
 *   - inline sparkline of the most recent trend
 *
 * Data source: GET /api/sites/{site_id}/sla (backend unions
 * site_healing_sla + execution_telemetry fallback).
 *
 * Refreshes every 5 minutes — faster than that is noise, slower hides
 * a freshly-breached SLA from the operator watching the page live.
 */
export const SiteSLAIndicator: React.FC<Props> = ({ siteId }) => {
  const { data, isLoading, error } = useQuery<SiteSLAResponse>({
    queryKey: ['site-sla', siteId],
    queryFn: async () => {
      const res = await fetch(`/api/sites/${siteId}/sla`, {
        credentials: 'same-origin',
      });
      if (!res.ok) {
        throw new Error(`SLA fetch failed: ${res.status}`);
      }
      return res.json();
    },
    refetchInterval: 5 * 60_000,
    staleTime: 60_000,
    enabled: !!siteId,
    retry: false,
  });

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Healing SLA</h2>
        {data?.source === 'execution_telemetry' && (
          <Tooltip text="SLA roll-up table has no entries yet. Computing from raw telemetry as a fallback.">
            <span className="text-[10px] uppercase tracking-wide text-label-tertiary cursor-help">
              Live
            </span>
          </Tooltip>
        )}
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-4">
          <Spinner size="sm" />
        </div>
      )}

      {error && !isLoading && (
        <div className="text-xs text-health-critical">Failed to load SLA data</div>
      )}

      {!isLoading && !error && data && (
        <>
          {/* Rate vs target */}
          <div className="flex items-end gap-3 mb-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-label-tertiary font-medium">
                Current
              </p>
              <p
                className={`text-3xl font-semibold tabular-nums ${
                  data.current_rate === null
                    ? 'text-label-tertiary'
                    : data.sla_met
                      ? 'text-health-healthy'
                      : 'text-health-critical'
                }`}
              >
                {data.current_rate === null ? '—' : `${data.current_rate.toFixed(1)}%`}
              </p>
            </div>
            <div className="text-xs text-label-tertiary pb-1">
              <p>Target</p>
              <p className="text-label-secondary font-medium">{data.sla_target.toFixed(0)}%</p>
            </div>
            <div className="ml-auto pb-1">
              <StatusPill met={data.sla_met} />
            </div>
          </div>

          {/* Sparkline */}
          <div className="mb-3">
            <Sparkline
              points={(data.trend ?? []).map((p) => Number(p.healing_rate))}
              targetLine={data.sla_target}
              width={220}
              height={44}
            />
          </div>

          {/* 7-day roll-up */}
          <div className="grid grid-cols-2 gap-3 text-xs pt-3 border-t border-glass-border">
            <div>
              <p className="text-label-tertiary uppercase tracking-wide">Periods met (7d)</p>
              <p className="text-label-primary font-semibold mt-0.5">
                {data.periods_met_last_7d}
                <span className="text-label-tertiary font-normal">
                  {' / '}
                  {data.periods_last_7d}
                </span>
              </p>
            </div>
            <div>
              <p className="text-label-tertiary uppercase tracking-wide">Met %</p>
              <p
                className={`font-semibold mt-0.5 ${
                  data.met_pct_7d === null
                    ? 'text-label-tertiary'
                    : data.met_pct_7d >= data.sla_target
                      ? 'text-health-healthy'
                      : 'text-amber-500'
                }`}
              >
                {data.met_pct_7d === null ? '—' : `${data.met_pct_7d.toFixed(1)}%`}
              </p>
            </div>
          </div>
        </>
      )}
    </GlassCard>
  );
};

const StatusPill: React.FC<{ met: boolean | null }> = ({ met }) => {
  if (met === null) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium bg-fill-secondary text-label-tertiary">
        <span className="w-1.5 h-1.5 rounded-full bg-label-tertiary" />
        No Data
      </span>
    );
  }
  if (met) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium bg-health-healthy/10 text-health-healthy">
        <span className="w-1.5 h-1.5 rounded-full bg-health-healthy" />
        Meeting
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium bg-health-critical/10 text-health-critical">
      <span className="w-1.5 h-1.5 rounded-full bg-health-critical" />
      Missing
    </span>
  );
};

export default SiteSLAIndicator;
