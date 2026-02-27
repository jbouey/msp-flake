import React, { useState } from 'react';
import {
  AreaChart,
  Area,
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
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
  bucketType: string;
}> = ({ active, payload, label, bucketType }) => {
  if (!active || !payload?.length || !label) return null;

  const d = new Date(label);
  const timeStr = bucketType === 'hour'
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

  return (
    <div className="bg-white/95 backdrop-blur-xl rounded-ios-md border border-separator-light px-3 py-2 shadow-lg">
      <p className="text-xs text-label-tertiary mb-1.5 font-medium">{timeStr}</p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center justify-between gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            {entry.name}
          </span>
          <span className="font-semibold tabular-nums">{entry.value}</span>
        </div>
      ))}
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

  return (
    <GlassCard className={className}>
      <div className="flex items-center justify-between mb-4">
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
            <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <defs>
                <linearGradient id="gradL1" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors.levels.l1} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={colors.levels.l1} stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="gradL2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors.levels.l2} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={colors.levels.l2} stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="gradL3" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors.levels.l3} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={colors.levels.l3} stopOpacity={0.02} />
                </linearGradient>
              </defs>
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
                iconType="circle"
                iconSize={6}
                wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
              />
              <Area
                type="monotone"
                dataKey="l1"
                name="L1 Auto"
                stroke={colors.levels.l1}
                fill="url(#gradL1)"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 0 }}
                stackId="1"
              />
              <Area
                type="monotone"
                dataKey="l2"
                name="L2 LLM"
                stroke={colors.levels.l2}
                fill="url(#gradL2)"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 0 }}
                stackId="1"
              />
              <Area
                type="monotone"
                dataKey="l3"
                name="L3 Human"
                stroke={colors.levels.l3}
                fill="url(#gradL3)"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3, strokeWidth: 0 }}
                stackId="1"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </GlassCard>
  );
};

export default IncidentTrendChart;
