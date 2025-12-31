import React from 'react';
import type { HealthStatus, ResolutionLevel, Severity } from '../../types';

export type BadgeVariant = 'health' | 'level' | 'severity' | 'default' | 'success' | 'info' | 'warning' | 'error';

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  status?: HealthStatus;
  level?: ResolutionLevel;
  severity?: Severity;
  className?: string;
}

const healthClasses: Record<HealthStatus, string> = {
  critical: 'bg-red-100 text-health-critical',
  warning: 'bg-orange-100 text-health-warning',
  healthy: 'bg-green-100 text-health-healthy',
};

const levelClasses: Record<ResolutionLevel, string> = {
  L1: 'bg-blue-100 text-ios-blue',
  L2: 'bg-purple-100 text-ios-purple',
  L3: 'bg-orange-100 text-ios-orange',
};

const severityClasses: Record<Severity, string> = {
  critical: 'bg-red-100 text-health-critical',
  high: 'bg-orange-100 text-health-warning',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-gray-100 text-label-tertiary',
};

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'default',
  status,
  level,
  severity,
  className = '',
}) => {
  let variantClasses = 'bg-gray-100 text-label-secondary';

  if (variant === 'health' && status) {
    variantClasses = healthClasses[status];
  } else if (variant === 'level' && level) {
    variantClasses = levelClasses[level];
  } else if (variant === 'severity' && severity) {
    variantClasses = severityClasses[severity];
  } else if (variant === 'success') {
    variantClasses = 'bg-green-100 text-health-healthy';
  } else if (variant === 'info') {
    variantClasses = 'bg-blue-100 text-ios-blue';
  } else if (variant === 'warning') {
    variantClasses = 'bg-orange-100 text-health-warning';
  } else if (variant === 'error') {
    variantClasses = 'bg-red-100 text-health-critical';
  }

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${variantClasses} ${className}`}
    >
      {children}
    </span>
  );
};

// Convenience components
export const HealthBadge: React.FC<{ status: HealthStatus; className?: string }> = ({
  status,
  className,
}) => {
  const labels: Record<HealthStatus, string> = {
    critical: 'Critical',
    warning: 'Warning',
    healthy: 'Healthy',
  };
  return (
    <Badge variant="health" status={status} className={className}>
      {labels[status]}
    </Badge>
  );
};

export const LevelBadge: React.FC<{ level: ResolutionLevel; showLabel?: boolean; className?: string }> = ({
  level,
  showLabel = false,
  className,
}) => {
  const labels: Record<ResolutionLevel, string> = {
    L1: 'AUTO',
    L2: 'LLM',
    L3: 'ESC',
  };
  return (
    <Badge variant="level" level={level} className={className}>
      {level} {showLabel && labels[level]}
    </Badge>
  );
};

export const SeverityBadge: React.FC<{ severity: Severity; className?: string }> = ({
  severity,
  className,
}) => {
  return (
    <Badge variant="severity" severity={severity} className={`uppercase ${className}`}>
      {severity}
    </Badge>
  );
};

export default Badge;
