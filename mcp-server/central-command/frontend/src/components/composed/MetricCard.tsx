import React from 'react';
import { GlassCard, InfoTip } from '../shared';
import { METRIC_TOOLTIPS, type MetricKey } from '../../constants';

interface MetricCardProps {
  metric: MetricKey;
  value: number | string | null;
  label?: string;
  suffix?: string;
  delta?: number;
  deltaSuffix?: string;
  deltaInvert?: boolean;
  icon?: React.ReactNode;
  iconColor?: string;
  iconBgColor?: string;
  valueColor?: string;
  onClick?: () => void;
  loading?: boolean;
  children?: React.ReactNode;
  /** Optional sub-row rendered under the delta — e.g. "target 85%". */
  footer?: React.ReactNode;
}

/**
 * Delta indicator with directional arrow.
 * positive = good for compliance/L1 rate, bad for incidents (inverted).
 */
const DeltaIndicator: React.FC<{
  value: number;
  suffix?: string;
  invertColor?: boolean;
}> = ({ value, suffix = '', invertColor = false }) => {
  if (value === 0) {
    return (
      <span className="text-xs font-medium text-label-tertiary tabular-nums">
        0{suffix} vs last week
      </span>
    );
  }

  const isPositive = value > 0;
  const isGood = invertColor ? !isPositive : isPositive;

  return (
    <span className={`text-xs font-medium tabular-nums ${isGood ? 'text-health-healthy' : 'text-health-critical'}`}>
      <svg
        className="w-3 h-3 inline-block mr-0.5 -mt-px"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2.5}
      >
        {isPositive ? (
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
        ) : (
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        )}
      </svg>
      {isPositive ? '+' : ''}{typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(1) : value}{suffix} vs last week
    </span>
  );
};

/**
 * MetricCard -- replaces all manual KPI GlassCard construction.
 *
 * Auto-resolves tooltip from METRIC_TOOLTIPS.
 * Handles null/loading states uniformly.
 * Shows delta with correct colors.
 */
export const MetricCard: React.FC<MetricCardProps> = ({
  metric,
  value,
  label,
  suffix = '',
  delta,
  deltaSuffix = '',
  deltaInvert = false,
  icon,
  iconColor = 'text-ios-blue',
  iconBgColor,
  valueColor = '',
  onClick,
  loading = false,
  children,
  footer,
}) => {
  const tooltip = METRIC_TOOLTIPS[metric];
  const displayLabel = label || metric.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const resolvedBgColor = iconBgColor || `${iconColor.replace('text-', 'bg-')}/15`;

  const formattedValue = (() => {
    if (loading) return <span className="skeleton inline-block w-10 h-7" />;
    if (value === null || value === undefined) {
      return <span className="text-label-tertiary">N/A</span>;
    }
    return `${value}${suffix}`;
  })();

  return (
    <GlassCard padding="md" onClick={onClick} hover={!!onClick}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-label-tertiary text-[10px] font-semibold uppercase tracking-wider">
            {displayLabel}
            {tooltip && <InfoTip text={tooltip} />}
          </p>
          <p className={`text-2xl font-bold mt-1 tabular-nums animate-count-up ${valueColor}`}>
            {formattedValue}
          </p>
          {delta !== undefined && <DeltaIndicator value={delta} suffix={deltaSuffix} invertColor={deltaInvert} />}
          {footer && <div className="mt-1">{footer}</div>}
        </div>
        {icon && (
          <div
            className={`w-9 h-9 rounded-ios-md flex items-center justify-center ${resolvedBgColor} ${iconColor}`}
          >
            {icon}
          </div>
        )}
        {children}
      </div>
    </GlassCard>
  );
};

export default MetricCard;
