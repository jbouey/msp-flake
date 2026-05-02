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
  auth_failed: {
    label: STATUS_LABELS.auth_failed,
    color: 'text-ios-orange',
    bgColor: 'bg-orange-100 dark:bg-orange-900/20',
    dotColor: 'bg-ios-orange',
    type: 'warning',
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
    label: 'Failing',
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
 * Consistent thresholds used everywhere (90/70/50 canon).
 *
 * For FLEET-HEALTH metrics (uptime, connectivity %), use
 * `getHealthStatus()` from `tokens/style-tokens.ts` (80/40 canon).
 * Different domain, different thresholds — both are canonical for
 * their domain. #50 closure 2026-05-02 documented the boundary.
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


/**
 * Compliance score → Badge variant. Single source of truth for the
 * 90/70/50 canonical thresholds when the consumer is a `<Badge>`.
 *
 * Use this instead of inlining `score >= 80 ? 'success' : ...` —
 * those thresholds drift over time and create demo-path inconsistency.
 *
 * Returns 'default' for null/zero (no data state) so the Badge renders
 * the neutral "N/A" affordance rather than coloring "0%" critical.
 */
export function scoreToBadgeVariant(score: number | null): 'default' | 'success' | 'warning' | 'error' {
  if (score === null || score === 0) return 'default';
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  if (score >= 50) return 'warning';
  return 'error';
}


/**
 * Compliance score → Tailwind bg-* class for a fill bar. Same 90/70/50
 * thresholds as getScoreStatus + scoreToBadgeVariant.
 */
export function scoreToBarColor(score: number): string {
  if (score >= 90) return 'bg-health-healthy';
  if (score >= 70) return 'bg-health-warning';
  if (score >= 50) return 'bg-ios-orange';
  return 'bg-health-critical';
}


/**
 * Risk score → Tailwind text-* class. INVERSE semantic to compliance:
 * higher risk score = worse posture. Used by the SRA wizard
 * (HIPAA Security Risk Analysis §164.308(a)(1)(ii)(A)) where the
 * computed score is on a 0-100 scale and a high score means more risk
 * surfaces are unmitigated.
 *
 * Threshold rationale (50/25 split, inverse of compliance 90/70/50):
 * - > 50: red — substantial unmitigated risk; remediation backlog
 * - > 25: yellow — moderate; targeted remediation
 * - ≤ 25: green — well-controlled
 *
 * If the SRA scoring methodology changes, update HERE — not in
 * call sites. D2 collapse 2026-05-02.
 */
export function riskScoreToColor(score: number): string {
  if (score > 50) return 'text-red-600';
  if (score > 25) return 'text-yellow-600';
  return 'text-green-600';
}


/**
 * Companion chat-progress percentage → palette key for the
 * `companionColors` palette. DISTINCT DOMAIN from compliance score
 * (chat-task completion, not security posture) so it uses a separate
 * palette and 70/40 thresholds. This helper exists so call sites
 * don't inline-fork the threshold logic — same canon-helper philosophy
 * as scoreToBarColor + riskScoreToColor.
 *
 * Returns the palette KEY (not the color value) so call sites can
 * resolve via their imported `companionColors` palette and the helper
 * stays decoupled from the palette object's import path.
 *
 * Followup #51 closure 2026-05-02. Replaces 2 noqa opt-outs in
 * CompanionStats.tsx with a single source-of-truth helper.
 */
export function companionProgressPaletteKey(
  pct: number,
): 'complete' | 'amber' | 'inProgress' {
  if (pct >= 70) return 'complete';
  if (pct >= 40) return 'amber';
  return 'inProgress';
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


// =============================================================================
// OPS CENTER STATUS LIGHTS
// =============================================================================

export type OpsStatus = 'green' | 'yellow' | 'red';

export interface OpsStatusConfig {
  color: string;
  bgColor: string;
  ringColor: string;
  pulseColor: string;
  label: string;
}

// =============================================================================
// ATTENTION TITLE CLEANUP
// =============================================================================

/**
 * Clean backend-generated attention/notification titles for display.
 * Maps raw check_type slugs to human labels after known prefixes.
 *
 * "Repeat drift: net_host_reachability (9x in 24h)" → "Recurring: Host Reach (9x in 24h)"
 * "L3 Escalation: rogue_scheduled_tasks"             → "L3 Escalation: Rogue Tasks"
 *
 * Accepts an optional labels map to avoid circular imports — callers pass
 * CHECK_TYPE_LABELS from types/index.ts.
 */
export function cleanAttentionTitle(title: string, labels: Record<string, string> = {}): string {
  return title
    .replace(/^Repeat drift:\s*/i, 'Recurring: ')
    .replace(/^Repeat failure:\s*/i, 'Recurring: ')
    .replace(/^L3 Escalation:\s*/i, 'L3 Escalation: ')
    .replace(/((?:Recurring|L3 Escalation):\s*)(\S+)/, (_match, prefix, checkType) => {
      return prefix + (labels[checkType] || checkType.replace(/_/g, ' '));
    });
}

export const OPS_STATUS_CONFIG: Record<OpsStatus, OpsStatusConfig> = {
  green:  { color: 'text-emerald-400', bgColor: 'bg-emerald-400', ringColor: 'ring-emerald-400/30', pulseColor: 'bg-emerald-400/20', label: 'Healthy' },
  yellow: { color: 'text-amber-400',   bgColor: 'bg-amber-400',   ringColor: 'ring-amber-400/30',   pulseColor: 'bg-amber-400/20',   label: 'Warning' },
  red:    { color: 'text-red-400',     bgColor: 'bg-red-400',     ringColor: 'ring-red-400/30',     pulseColor: 'bg-red-400/20',     label: 'Critical' },
};
