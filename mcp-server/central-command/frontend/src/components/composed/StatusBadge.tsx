import React from 'react';
import { getStatusConfig } from '../../constants';

interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
  showDot?: boolean;
  className?: string;
  label?: string;
}

/**
 * StatusBadge -- replaces all scattered badge implementations.
 *
 * Uses getStatusConfig() internally -- one component, one mapping, everywhere.
 * Pass any status string and get consistent styling.
 */
export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = 'sm',
  showDot = true,
  className = '',
  label,
}) => {
  const config = getStatusConfig(status);
  const displayLabel = label || config.label;

  const sizeClasses = size === 'sm'
    ? 'px-2 py-0.5 text-xs'
    : 'px-2.5 py-1 text-sm';

  const dotSize = size === 'sm' ? 'w-1.5 h-1.5' : 'w-2 h-2';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-medium ${sizeClasses} ${config.bgColor} ${config.color} ${className}`}
    >
      {showDot && <span className={`${dotSize} rounded-full ${config.dotColor}`} />}
      {displayLabel}
    </span>
  );
};

export default StatusBadge;
