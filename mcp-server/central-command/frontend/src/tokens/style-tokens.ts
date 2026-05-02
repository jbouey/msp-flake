/**
 * iOS Glassmorphism Design System
 *
 * Design tokens for Central Command Dashboard.
 * Based on Apple's Human Interface Guidelines.
 */

// =============================================================================
// COLORS
// =============================================================================

export const colors = {
  // Backgrounds
  background: {
    primary: '#F2F2F7',           // iOS light gray
    secondary: '#FFFFFF',          // Pure white
    tertiary: 'rgba(255, 255, 255, 0.72)',  // Frosted glass
  },

  // Text
  text: {
    primary: '#1C1C1E',           // iOS label
    secondary: 'rgba(60, 60, 67, 0.6)',  // iOS secondary label
    tertiary: '#8E8E93',          // iOS tertiary label
    inverted: '#FFFFFF',
  },

  // Health status
  health: {
    critical: '#FF3B30',          // iOS red
    warning: '#FF9500',           // iOS orange
    healthy: '#34C759',           // iOS green
    neutral: '#8E8E93',           // iOS gray
  },

  // Resolution levels
  levels: {
    l1: '#14A89E',                // OsirisCare teal - Deterministic
    l2: '#5856D6',                // iOS purple - LLM
    l3: '#FF9500',                // iOS orange - Human
  },

  // Accents
  accent: {
    primary: '#14A89E',           // OsirisCare teal
    secondary: '#5856D6',         // iOS purple
    tint: 'rgba(20, 168, 158, 0.1)',
  },

  // iOS System Colors
  system: {
    red: '#FF3B30',
    orange: '#FF9500',
    yellow: '#FFCC00',
    green: '#34C759',
    mint: '#00C7BE',
    teal: '#30B0C7',
    cyan: '#32ADE6',
    blue: '#007AFF',
    indigo: '#5856D6',
    purple: '#AF52DE',
    pink: '#FF2D55',
    gray: '#8E8E93',
  },

  // Borders & separators
  border: {
    light: 'rgba(60, 60, 67, 0.1)',
    medium: 'rgba(60, 60, 67, 0.18)',
    glass: 'rgba(255, 255, 255, 0.18)',
  },
} as const;

// =============================================================================
// EFFECTS
// =============================================================================

export const effects = {
  glass: {
    background: 'rgba(255, 255, 255, 0.72)',
    backdropFilter: 'blur(20px) saturate(180%)',
    border: '1px solid rgba(255, 255, 255, 0.18)',
    boxShadow: '0 4px 30px rgba(0, 0, 0, 0.1)',
  },
  glassDark: {
    background: 'rgba(255, 255, 255, 0.85)',
    backdropFilter: 'blur(20px) saturate(180%)',
    border: '1px solid rgba(60, 60, 67, 0.1)',
    boxShadow: '0 4px 30px rgba(0, 0, 0, 0.1)',
  },
  cardShadow: '0 2px 8px rgba(0, 0, 0, 0.04), 0 4px 24px rgba(0, 0, 0, 0.08)',
  hoverShadow: '0 4px 12px rgba(0, 0, 0, 0.08), 0 8px 32px rgba(0, 0, 0, 0.12)',
  focusRing: '0 0 0 4px rgba(0, 122, 255, 0.3)',
} as const;

// =============================================================================
// SPACING
// =============================================================================

export const spacing = {
  xs: '4px',
  sm: '8px',
  md: '16px',
  lg: '24px',
  xl: '32px',
  xxl: '48px',
} as const;

// =============================================================================
// BORDER RADIUS
// =============================================================================

export const borderRadius = {
  sm: '8px',
  md: '12px',
  lg: '16px',
  xl: '24px',
  full: '9999px',
} as const;

// =============================================================================
// TYPOGRAPHY
// =============================================================================

export const typography = {
  fontFamily: {
    sans: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif',
    mono: '"SF Mono", "Menlo", "Monaco", monospace',
  },
  fontSize: {
    xs: '11px',
    sm: '13px',
    base: '15px',
    lg: '17px',
    xl: '20px',
    '2xl': '24px',
    '3xl': '34px',
  },
  lineHeight: {
    xs: '13px',
    sm: '18px',
    base: '20px',
    lg: '22px',
    xl: '25px',
    '2xl': '29px',
    '3xl': '41px',
  },
  fontWeight: {
    regular: '400',
    medium: '500',
    semibold: '600',
    bold: '700',
  },
} as const;

// =============================================================================
// HEALTH THRESHOLDS
// =============================================================================

/**
 * Fleet-health thresholds — DOMAIN-DISTINCT from compliance score.
 *
 * These 80/40 thresholds are for FLEET HEALTH metrics (uptime ratio,
 * agent connectivity %, gauge dashboards) — NOT compliance score.
 *
 * Compliance score uses 90/70/50 thresholds via
 * `getScoreStatus()` in constants/status.ts. The two domains are
 * semantically distinct (fleet uptime tolerance is looser than
 * compliance posture tolerance) so different thresholds are correct.
 *
 * #50 closure 2026-05-02: documented + linked. CI gate for inline
 * threshold drift (`test_no_inline_score_thresholds`) treats this
 * file as canon-exempt for the same-canon reason.
 *
 * If a NEW domain needs threshold mapping, add a NEW helper here or
 * in constants/status.ts with a docstring explaining the domain. Do
 * NOT inline in component files.
 */
export const HEALTH_THRESHOLDS = {
  critical: { max: 39, color: colors.health.critical, label: 'Critical' },
  warning: { min: 40, max: 79, color: colors.health.warning, label: 'Warning' },
  healthy: { min: 80, color: colors.health.healthy, label: 'Healthy' },
} as const;

export type HealthStatus = 'critical' | 'warning' | 'healthy';

/**
 * Fleet-health status from a 0-100 score (uptime/connectivity domain).
 *
 * NOT for compliance score — use `getScoreStatus()` from
 * `constants/status.ts` (90/70/50) for compliance.
 *
 * Today's call sites:
 *   - components/fleet/HealthGauge.tsx (gauge dashboard)
 *   - components/command-center/FleetHealthMatrix.tsx (matrix viz)
 *
 * Both are operational-health metrics where 80% uptime is "healthy"
 * and 40% is "critical." Compliance posture uses tighter thresholds.
 */
export function getHealthStatus(score: number): HealthStatus {
  if (score < 40) return 'critical';
  if (score < 80) return 'warning';
  return 'healthy';
}

export function getHealthColor(status: HealthStatus): string {
  return colors.health[status];
}

export function getHealthLabel(status: HealthStatus): string {
  return HEALTH_THRESHOLDS[status].label;
}

// =============================================================================
// RESOLUTION LEVEL HELPERS
// =============================================================================

export type ResolutionLevel = 'L1' | 'L2' | 'L3';

export function getLevelColor(level: ResolutionLevel): string {
  const map: Record<ResolutionLevel, string> = {
    L1: colors.levels.l1,
    L2: colors.levels.l2,
    L3: colors.levels.l3,
  };
  return map[level];
}

export function getLevelLabel(level: ResolutionLevel): string {
  const map: Record<ResolutionLevel, string> = {
    L1: 'Deterministic',
    L2: 'LLM',
    L3: 'Human',
  };
  return map[level];
}

// =============================================================================
// ANIMATION
// =============================================================================

export const animation = {
  fast: '150ms',
  normal: '200ms',
  slow: '300ms',
  easing: {
    default: 'ease',
    easeOut: 'cubic-bezier(0, 0, 0.2, 1)',
    easeIn: 'cubic-bezier(0.4, 0, 1, 1)',
    easeInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
  },
} as const;

// =============================================================================
// Z-INDEX
// =============================================================================

export const zIndex = {
  base: 0,
  dropdown: 1000,
  sticky: 1100,
  modal: 1200,
  popover: 1300,
  tooltip: 1400,
} as const;
