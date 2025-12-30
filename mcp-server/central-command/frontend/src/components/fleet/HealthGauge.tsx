import React from 'react';
import type { HealthStatus } from '../../types';
import { getHealthColor, getHealthStatus, getHealthLabel } from '../../tokens/style-tokens';

interface HealthGaugeProps {
  score: number;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showLabel?: boolean;
  showPercentage?: boolean;
  thickness?: number;
  className?: string;
}

const sizeConfig = {
  sm: { size: 40, fontSize: 'text-xs', labelSize: 'text-[8px]', strokeWidth: 3 },
  md: { size: 64, fontSize: 'text-base', labelSize: 'text-[10px]', strokeWidth: 4 },
  lg: { size: 96, fontSize: 'text-xl', labelSize: 'text-xs', strokeWidth: 5 },
  xl: { size: 128, fontSize: 'text-2xl', labelSize: 'text-sm', strokeWidth: 6 },
};

export const HealthGauge: React.FC<HealthGaugeProps> = ({
  score,
  size = 'md',
  showLabel = true,
  showPercentage = true,
  thickness,
  className = '',
}) => {
  const config = sizeConfig[size];
  const strokeWidth = thickness ?? config.strokeWidth;
  const radius = (config.size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(Math.max(score, 0), 100);
  const strokeDashoffset = circumference - (progress / 100) * circumference;

  const status: HealthStatus = getHealthStatus(score);
  const color = getHealthColor(status);
  const label = getHealthLabel(status);

  return (
    <div
      className={`relative inline-flex items-center justify-center ${className}`}
      style={{ width: config.size, height: config.size }}
    >
      {/* Background circle */}
      <svg
        className="absolute inset-0 -rotate-90"
        width={config.size}
        height={config.size}
      >
        <circle
          cx={config.size / 2}
          cy={config.size / 2}
          r={radius}
          fill="none"
          stroke="rgba(60, 60, 67, 0.1)"
          strokeWidth={strokeWidth}
        />
        {/* Progress arc */}
        <circle
          cx={config.size / 2}
          cy={config.size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          strokeLinecap="round"
          className="transition-all duration-500 ease-out"
        />
      </svg>

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {showPercentage && (
          <span
            className={`font-semibold ${config.fontSize}`}
            style={{ color }}
          >
            {Math.round(score)}%
          </span>
        )}
        {showLabel && (
          <span className={`text-label-tertiary ${config.labelSize}`}>
            {label}
          </span>
        )}
      </div>
    </div>
  );
};

// Mini health indicator (just the colored ring, no text)
export const HealthRing: React.FC<{
  score: number;
  size?: number;
  className?: string;
}> = ({ score, size = 24, className = '' }) => {
  const strokeWidth = 3;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(Math.max(score, 0), 100);
  const strokeDashoffset = circumference - (progress / 100) * circumference;
  const color = getHealthColor(getHealthStatus(score));

  return (
    <svg
      className={`-rotate-90 ${className}`}
      width={size}
      height={size}
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(60, 60, 67, 0.1)"
        strokeWidth={strokeWidth}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={circumference}
        strokeDashoffset={strokeDashoffset}
        strokeLinecap="round"
        className="transition-all duration-500 ease-out"
      />
    </svg>
  );
};

// Progress bar variant
export const HealthBar: React.FC<{
  score: number;
  height?: number;
  showLabel?: boolean;
  className?: string;
}> = ({ score, height = 8, showLabel = false, className = '' }) => {
  const progress = Math.min(Math.max(score, 0), 100);
  const status = getHealthStatus(score);
  const color = getHealthColor(status);

  return (
    <div className={className}>
      {showLabel && (
        <div className="flex justify-between mb-1">
          <span className="text-xs text-label-secondary">Health</span>
          <span className="text-xs font-medium" style={{ color }}>
            {Math.round(score)}%
          </span>
        </div>
      )}
      <div
        className="w-full bg-separator-light rounded-full overflow-hidden"
        style={{ height }}
      >
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{ width: `${progress}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
};

export default HealthGauge;
