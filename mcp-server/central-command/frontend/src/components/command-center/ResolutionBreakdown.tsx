import React, { useState } from 'react';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { GlassCard } from '../shared';
import { useIncidentBreakdown } from '../../hooks';
import { colors } from '../../tokens/style-tokens';

type Window = '24h' | '7d' | '30d';

const TIER_CONFIG = [
  { key: 'l1', label: 'L1 Auto', color: colors.levels.l1, description: 'Deterministic' },
  { key: 'l2', label: 'L2 LLM', color: colors.levels.l2, description: 'AI-planned' },
  { key: 'l3', label: 'L3 Human', color: colors.levels.l3, description: 'Escalated' },
] as const;

function formatMinutes(mins: number): string {
  if (mins < 1) return '<1m';
  if (mins < 60) return `${Math.round(mins)}m`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

const CustomTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ name: string; value: number; payload: { color: string; pct: number } }>;
}> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  return (
    <div className="bg-white/95 backdrop-blur-xl rounded-ios-md border border-separator-light px-3 py-2 shadow-lg">
      <div className="flex items-center gap-1.5 text-xs">
        <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: entry.payload.color }} />
        <span className="font-medium text-label-primary">{entry.name}</span>
      </div>
      <p className="text-xs text-label-secondary mt-1">
        {entry.value} incident{entry.value !== 1 ? 's' : ''} ({entry.payload.pct}%)
      </p>
    </div>
  );
};

export const ResolutionBreakdown: React.FC<{
  siteId?: string;
  className?: string;
}> = ({ siteId, className = '' }) => {
  const [window, setWindow] = useState<Window>('24h');
  const { data, isLoading } = useIncidentBreakdown(window, siteId);

  const tierCounts = data?.tier_counts;
  const mttr = data?.mttr;
  const total = tierCounts?.total ?? 0;

  const pieData = TIER_CONFIG.map((t) => {
    const count = tierCounts?.[t.key] ?? 0;
    return {
      name: t.label,
      value: count,
      color: t.color,
      pct: total > 0 ? Math.round((count / total) * 100) : 0,
    };
  }).filter((d) => d.value > 0);

  // If no resolved incidents, show empty ring
  const hasData = pieData.length > 0;

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-base font-semibold text-label-primary">Resolution Breakdown</h3>
        <div className="flex items-center bg-fill-secondary rounded-ios-md p-0.5">
          {(['24h', '7d', '30d'] as Window[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`px-2 py-0.5 text-[10px] font-medium rounded-md transition-all ${
                window === w
                  ? 'bg-white text-label-primary shadow-sm'
                  : 'text-label-tertiary hover:text-label-secondary'
              }`}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="h-40 flex items-center justify-center">
          <div className="w-5 h-5 border-2 border-accent-primary/30 border-t-accent-primary rounded-full animate-spin" />
        </div>
      ) : !hasData ? (
        <div className="h-40 flex items-center justify-center text-sm text-label-tertiary">
          No incidents in this period
        </div>
      ) : (
        <div className="flex items-start gap-3">
          {/* Donut chart */}
          <div className="relative flex-shrink-0" style={{ width: 120, height: 120 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={32}
                  outerRadius={52}
                  paddingAngle={2}
                  dataKey="value"
                  strokeWidth={0}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip content={<CustomTooltip />} />
              </PieChart>
            </ResponsiveContainer>
            {/* Center label */}
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <span className="text-lg font-bold tabular-nums text-label-primary">{total}</span>
              <span className="text-[9px] text-label-tertiary uppercase tracking-wider">Total</span>
            </div>
          </div>

          {/* Legend + MTTR */}
          <div className="flex-1 space-y-2 pt-1">
            {TIER_CONFIG.map((t) => {
              const count = tierCounts?.[t.key] ?? 0;
              const pct = total > 0 ? Math.round((count / total) * 100) : 0;
              return (
                <div key={t.key} className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: t.color }} />
                    <div>
                      <span className="text-xs font-medium text-label-primary">{t.label}</span>
                      <span className="text-[10px] text-label-tertiary ml-1">({t.description})</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-xs font-bold tabular-nums" style={{ color: t.color }}>{pct}%</span>
                    <span className="text-[10px] text-label-tertiary ml-1">({count})</span>
                  </div>
                </div>
              );
            })}
            {/* MTTR row */}
            <div className="border-t border-separator-light pt-2 mt-2">
              <p className="text-[10px] text-label-tertiary uppercase tracking-wider font-semibold mb-1">Avg Resolution Time</p>
              <div className="flex items-center gap-3">
                {TIER_CONFIG.map((t) => {
                  const tierMttr = mttr?.[t.key];
                  if (!tierMttr || tierMttr.resolved_count === 0) return null;
                  return (
                    <span key={t.key} className="text-[11px] font-medium" style={{ color: t.color }}>
                      {t.label.split(' ')[0]}: {formatMinutes(tierMttr.avg_minutes)}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </GlassCard>
  );
};

export default ResolutionBreakdown;
