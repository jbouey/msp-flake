import React, { useState, useMemo } from 'react';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { GlassCard } from '../shared';
import { useIncidentTrends } from '../../hooks';
import { colors } from '../../tokens/style-tokens';

type Window = '24h' | '7d' | '30d';

const windowLabels: Record<Window, string> = {
  '24h': '24 Hours',
  '7d': '7 Days',
  '30d': '30 Days',
};

function formatTick(value: string, bucketType: string): string {
  if (!value) return '';
  const d = new Date(value);
  if (bucketType === 'hour') {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

const CustomTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string; dataKey: string }>;
  label?: string;
  bucketType: string;
}> = ({ active, payload, label, bucketType }) => {
  if (!active || !payload?.length || !label) return null;

  const d = new Date(label);
  const timeStr = bucketType === 'hour'
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

  // Only show L1/L2/L3 bars, not the total line
  const barEntries = payload.filter((e) => e.dataKey !== 'total');
  const total = barEntries.reduce((sum, e) => sum + (e.value || 0), 0);

  return (
    <div className="bg-white/95 backdrop-blur-xl rounded-ios-md border border-separator-light px-3 py-2.5 shadow-lg min-w-[140px]">
      <p className="text-xs text-label-tertiary mb-1.5 font-medium">{timeStr}</p>
      {barEntries.map((entry) => (
        <div key={entry.name} className="flex items-center justify-between gap-4 text-xs py-0.5">
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-sm"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-label-secondary">{entry.name}</span>
          </span>
          <span className="font-semibold tabular-nums text-label-primary">{entry.value}</span>
        </div>
      ))}
      <div className="border-t border-separator-light mt-1.5 pt-1.5 flex items-center justify-between text-xs">
        <span className="text-label-tertiary">Total</span>
        <span className="font-bold tabular-nums text-label-primary">{total}</span>
      </div>
    </div>
  );
};

export const IncidentTrendChart: React.FC<{
  siteId?: string;
  className?: string;
}> = ({ siteId, className = '' }) => {
  const [window, setWindow] = useState<Window>('24h');
  const { data, isLoading } = useIncidentTrends(window, siteId);

  const chartData = data?.data ?? [];
  const bucketType = data?.bucket_type ?? 'hour';
  const totalIncidents = chartData.reduce((sum, d) => sum + d.total, 0);

  // Compute tier totals for the header summary
  const tierTotals = useMemo(() => {
    const t = { l1: 0, l2: 0, l3: 0 };
    for (const d of chartData) {
      t.l1 += d.l1;
      t.l2 += d.l2;
      t.l3 += d.l3;
    }
    return t;
  }, [chartData]);

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-1">
        <div>
          <h3 className="text-base font-semibold text-label-primary">Incident Volume</h3>
          <p className="text-xs text-label-tertiary mt-0.5">
            {totalIncidents} incident{totalIncidents !== 1 ? 's' : ''} in {windowLabels[window].toLowerCase()}
          </p>
        </div>
        <div className="flex items-center bg-fill-secondary rounded-ios-md p-0.5">
          {(['24h', '7d', '30d'] as Window[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
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

      {/* Tier summary pills */}
      {!isLoading && totalIncidents > 0 && (
        <div className="flex items-center gap-3 mb-3">
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
            <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: colors.levels.l1 }} />
            <span className="text-label-secondary">L1</span>
            <span className="font-bold tabular-nums" style={{ color: colors.levels.l1 }}>{tierTotals.l1}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
            <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: colors.levels.l2 }} />
            <span className="text-label-secondary">L2</span>
            <span className="font-bold tabular-nums" style={{ color: colors.levels.l2 }}>{tierTotals.l2}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium">
            <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: colors.levels.l3 }} />
            <span className="text-label-secondary">L3</span>
            <span className="font-bold tabular-nums" style={{ color: colors.levels.l3 }}>{tierTotals.l3}</span>
          </span>
        </div>
      )}

      <div style={{ height: 220 }}>
        {isLoading ? (
          <div className="w-full h-full flex items-center justify-center">
            <div className="w-5 h-5 border-2 border-accent-primary/30 border-t-accent-primary rounded-full animate-spin" />
          </div>
        ) : chartData.length === 0 ? (
          <div className="w-full h-full flex items-center justify-center text-sm text-label-tertiary">
            No incident data for this period
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="rgba(60,60,67,0.06)"
                vertical={false}
              />
              <XAxis
                dataKey="time"
                tickFormatter={(v) => formatTick(v, bucketType)}
                tick={{ fontSize: 10, fill: '#8E8E93' }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#8E8E93' }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip content={<CustomTooltip bucketType={bucketType} />} />
              <Legend
                iconType="square"
                iconSize={8}
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
              />
              {/* Stacked bars — each tier clearly visible as a segment */}
              <Bar
                dataKey="l1"
                name="L1 Auto"
                fill={colors.levels.l1}
                stackId="tier"
                radius={[0, 0, 0, 0]}
                maxBarSize={bucketType === 'hour' ? 16 : 28}
                opacity={0.85}
              />
              <Bar
                dataKey="l2"
                name="L2 LLM"
                fill={colors.levels.l2}
                stackId="tier"
                radius={[0, 0, 0, 0]}
                maxBarSize={bucketType === 'hour' ? 16 : 28}
                opacity={0.85}
              />
              <Bar
                dataKey="l3"
                name="L3 Human"
                fill={colors.levels.l3}
                stackId="tier"
                radius={[2, 2, 0, 0]}
                maxBarSize={bucketType === 'hour' ? 16 : 28}
                opacity={0.85}
              />
              {/* Smooth total trend line overlaid */}
              <Line
                type="monotone"
                dataKey="total"
                name="Total"
                stroke="rgba(60,60,67,0.25)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                dot={false}
                activeDot={false}
                legendType="none"
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </GlassCard>
  );
};

export default IncidentTrendChart;
