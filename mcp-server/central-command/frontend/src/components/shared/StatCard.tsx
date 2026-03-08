import React from 'react';

interface SparklinePoint {
  value: number;
}

interface StatCardProps {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  trend?: {
    direction: 'up' | 'down' | 'flat';
    value: string;  // e.g. "+12%"
    positive?: boolean;  // green if true, red if false
  };
  sparkline?: SparklinePoint[];
  color?: string;  // accent color for icon background
  onClick?: () => void;
  className?: string;
}

function MiniSparkline({ data, color = '#14A89E' }: { data: SparklinePoint[]; color?: string }) {
  if (!data || data.length < 2) return null;
  const max = Math.max(...data.map(d => d.value));
  const min = Math.min(...data.map(d => d.value));
  const range = max - min || 1;
  const width = 80;
  const height = 28;
  const padding = 2;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * (width - padding * 2);
    const y = height - padding - ((d.value - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="opacity-60">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TrendArrow({ direction }: { direction: 'up' | 'down' | 'flat' }) {
  if (direction === 'flat') return <span className="text-label-tertiary">&rarr;</span>;
  if (direction === 'up') return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M6 2L10 7H2L6 2Z" fill="currentColor"/>
    </svg>
  );
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M6 10L2 5H10L6 10Z" fill="currentColor"/>
    </svg>
  );
}

export const StatCard: React.FC<StatCardProps> = ({ label, value, icon, trend, sparkline, color, onClick, className = '' }) => {
  const isClickable = !!onClick;
  return (
    <div
      className={`glass-card p-4 flex flex-col gap-2 ${isClickable ? 'cursor-pointer' : ''} ${className}`}
      onClick={onClick}
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-label-secondary">
          {label}
        </span>
        {icon && (
          <div
            className="w-8 h-8 rounded-ios-sm flex items-center justify-center"
            style={color ? { background: `${color}18` } : undefined}
          >
            {icon}
          </div>
        )}
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold font-display text-label-primary tabular-nums animate-count-up">
            {value}
          </span>
          {trend && (
            <span className={`flex items-center gap-0.5 text-xs font-medium ${
              trend.positive === true ? 'text-health-healthy' :
              trend.positive === false ? 'text-health-critical' :
              'text-label-tertiary'
            }`}>
              <TrendArrow direction={trend.direction} />
              {trend.value}
            </span>
          )}
        </div>
        {sparkline && <MiniSparkline data={sparkline} color={color || '#14A89E'} />}
      </div>
    </div>
  );
};

export default StatCard;
