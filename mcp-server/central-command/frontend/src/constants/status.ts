/**
 * Centralized Status Mapping
 *
 * Status -> color -> label mapping. Replaces all scattered if/else chains.
 * One function to resolve any status string in the app.
 */

import { STATUS_LABELS } from './copy';

// =============================================================================
// TYPES
// =============================================================================

export type StatusType = 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'critical';

export interface StatusConfig {
  label: string;
  color: string;       // Tailwind text color
  bgColor: string;     // Tailwind bg color
  dotColor: string;    // For status dots
  type: StatusType;
}

// =============================================================================
// STATUS CONFIG MAP
// =============================================================================

const STATUS_MAP: Record<string, StatusConfig> = {
  // Site / appliance statuses
  online: {
    label: STATUS_LABELS.online,
    color: 'text-health-healthy',
    bgColor: 'bg-health-healthy/10',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  offline: {
    label: STATUS_LABELS.offline,
    color: 'text-health-critical',
    bgColor: 'bg-health-critical/10',
    dotColor: 'bg-health-critical',
    type: 'error',
  },
  stale: {
    label: STATUS_LABELS.stale,
    color: 'text-health-warning',
    bgColor: 'bg-health-warning/10',
    dotColor: 'bg-health-warning',
    type: 'warning',
  },
  pending: {
    label: STATUS_LABELS.pending,
    color: 'text-label-tertiary',
    bgColor: 'bg-fill-secondary',
    dotColor: 'bg-label-tertiary',
    type: 'neutral',
  },
  inactive: {
    label: STATUS_LABELS.inactive,
    color: 'text-label-tertiary',
    bgColor: 'bg-fill-secondary',
    dotColor: 'bg-label-tertiary',
    type: 'neutral',
  },

  // Compliance statuses
  pass: {
    label: STATUS_LABELS.pass,
    color: 'text-health-healthy',
    bgColor: 'bg-green-100',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  fail: {
    label: STATUS_LABELS.fail,
    color: 'text-health-critical',
    bgColor: 'bg-red-100',
    dotColor: 'bg-health-critical',
    type: 'error',
  },
  warn: {
    label: STATUS_LABELS.warn,
    color: 'text-health-warning',
    bgColor: 'bg-orange-100',
    dotColor: 'bg-health-warning',
    type: 'warning',
  },
  unknown: {
    label: STATUS_LABELS.unknown,
    color: 'text-label-tertiary',
    bgColor: 'bg-fill-secondary',
    dotColor: 'bg-label-tertiary',
    type: 'neutral',
  },

  // Incident statuses
  resolved: {
    label: STATUS_LABELS.resolved,
    color: 'text-health-healthy',
    bgColor: 'bg-green-100',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  escalated: {
    label: STATUS_LABELS.escalated,
    color: 'text-ios-orange',
    bgColor: 'bg-orange-100',
    dotColor: 'bg-ios-orange',
    type: 'warning',
  },
  resolving: {
    label: STATUS_LABELS.resolving,
    color: 'text-ios-blue',
    bgColor: 'bg-blue-100',
    dotColor: 'bg-ios-blue',
    type: 'info',
  },

  // Agent statuses
  connected: {
    label: STATUS_LABELS.connected,
    color: 'text-health-healthy',
    bgColor: 'bg-health-healthy/10',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  disconnected: {
    label: STATUS_LABELS.disconnected,
    color: 'text-health-critical',
    bgColor: 'bg-health-critical/10',
    dotColor: 'bg-health-critical',
    type: 'error',
  },

  // Health statuses
  healthy: {
    label: 'Healthy',
    color: 'text-health-healthy',
    bgColor: 'bg-green-100',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  warning: {
    label: 'Warning',
    color: 'text-health-warning',
    bgColor: 'bg-orange-100',
    dotColor: 'bg-health-warning',
    type: 'warning',
  },
  critical: {
    label: 'Critical',
    color: 'text-health-critical',
    bgColor: 'bg-red-100',
    dotColor: 'bg-health-critical',
    type: 'critical',
  },

  // Workstation statuses
  compliant: {
    label: 'Compliant',
    color: 'text-health-healthy',
    bgColor: 'bg-green-100',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
  drifted: {
    label: 'Drifted',
    color: 'text-health-warning',
    bgColor: 'bg-orange-100',
    dotColor: 'bg-health-warning',
    type: 'warning',
  },
  error: {
    label: 'Error',
    color: 'text-health-critical',
    bgColor: 'bg-red-100',
    dotColor: 'bg-health-critical',
    type: 'error',
  },

  // Go agent statuses
  active: {
    label: STATUS_LABELS.active,
    color: 'text-health-healthy',
    bgColor: 'bg-green-100',
    dotColor: 'bg-health-healthy',
    type: 'success',
  },
};

// Fallback for unknown statuses
const FALLBACK_STATUS: StatusConfig = {
  label: 'Unknown',
  color: 'text-label-tertiary',
  bgColor: 'bg-fill-secondary',
  dotColor: 'bg-label-tertiary',
  type: 'neutral',
};

// =============================================================================
// LOOKUP FUNCTIONS
// =============================================================================

/**
 * Single mapping function -- replaces all scattered if/else chains.
 * Pass any status string and get back consistent colors, labels, and types.
 */
export function getStatusConfig(status: string): StatusConfig {
  const normalized = status.toLowerCase().replace(/[\s-]/g, '_');
  return STATUS_MAP[normalized] || { ...FALLBACK_STATUS, label: status };
}

/**
 * Compliance score -> status config.
 * Consistent thresholds used everywhere.
 */
export function getScoreStatus(score: number | null): StatusConfig {
  if (score === null) {
    return {
      label: 'No Data',
      color: 'text-label-tertiary',
      bgColor: 'bg-fill-secondary',
      dotColor: 'bg-label-tertiary',
      type: 'neutral',
    };
  }
  if (score >= 90) {
    return {
      label: 'Healthy',
      color: 'text-health-healthy',
      bgColor: 'bg-health-healthy/10',
      dotColor: 'bg-health-healthy',
      type: 'success',
    };
  }
  if (score >= 70) {
    return {
      label: 'Needs Attention',
      color: 'text-health-warning',
      bgColor: 'bg-health-warning/10',
      dotColor: 'bg-health-warning',
      type: 'warning',
    };
  }
  if (score >= 50) {
    return {
      label: 'At Risk',
      color: 'text-ios-orange',
      bgColor: 'bg-ios-orange/10',
      dotColor: 'bg-ios-orange',
      type: 'warning',
    };
  }
  return {
    label: 'Critical',
    color: 'text-health-critical',
    bgColor: 'bg-health-critical/10',
    dotColor: 'bg-health-critical',
    type: 'critical',
  };
}

// =============================================================================
// FORMATTERS
// =============================================================================

/**
 * Null-safe metric formatter.
 * Handles null/undefined values uniformly.
 */
export function formatMetric(
  value: number | null | undefined,
  suffix?: string,
  placeholder?: string,
): string {
  if (value === null || value === undefined) return placeholder || 'N/A';
  if (suffix === '%') return `${value.toFixed(1)}%`;
  return `${value}${suffix || ''}`;
}

/**
 * Relative time formatter (used everywhere).
 * Replaces the 14+ duplicate implementations across the codebase.
 */
export function formatTimeAgo(dateString: string | null | undefined): string {
  if (!dateString || dateString === '-infinity' || dateString === 'infinity') return 'Never';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return 'Never';
  const diffMs = Date.now() - date.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString();
}

/**
 * Bytes formatter.
 * Replaces duplicate implementations in VPNManagement, DocumentUpload, etc.
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
