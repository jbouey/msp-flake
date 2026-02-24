/**
 * Companion Portal Design Tokens
 *
 * Warm, professional, paper-notebook aesthetic.
 * Distinct from the admin dashboard's iOS glassmorphism.
 */

export const companionColors = {
  // Backgrounds
  pageBg: '#FAFAF8',
  cardBg: '#FFFFFF',
  cardBorder: '#E8E5E1',
  cardBorderHover: '#D4D0CB',
  sidebarBg: '#F5F3F0',

  // Brand accents
  primary: '#0D7377',
  primaryLight: '#E8F5F5',
  primaryDark: '#095456',

  // Progress / amber
  amber: '#D4A017',
  amberLight: '#FDF6E3',
  amberDark: '#9A7412',

  // Status
  complete: '#2D8A4E',
  completeLight: '#E8F5EC',
  inProgress: '#2563EB',
  inProgressLight: '#EFF6FF',
  notStarted: '#9CA3AF',
  notStartedLight: '#F3F4F6',
  actionNeeded: '#DC2626',
  actionNeededLight: '#FEF2F2',

  // Text
  textPrimary: '#1A1A18',
  textSecondary: '#6B6B66',
  textTertiary: '#9E9E96',
  textInverse: '#FFFFFF',

  // Misc
  divider: '#E8E5E1',
  focusRing: '#0D7377',
} as const;

export const companionShadows = {
  sm: '0 1px 2px rgba(0,0,0,0.04)',
  md: '0 2px 8px rgba(0,0,0,0.06)',
  lg: '0 4px 16px rgba(0,0,0,0.08)',
} as const;

// Module metadata for the journey stepper
export const MODULE_DEFS = [
  { key: 'sra', label: 'Security Risk Assessment', icon: 'shield', shortLabel: 'SRA' },
  { key: 'policies', label: 'Policy Library', icon: 'book', shortLabel: 'Policies' },
  { key: 'training', label: 'Training Tracker', icon: 'users', shortLabel: 'Training' },
  { key: 'baas', label: 'BAA Inventory', icon: 'handshake', shortLabel: 'BAAs' },
  { key: 'ir-plan', label: 'Incident Response Plan', icon: 'alert', shortLabel: 'IR Plan' },
  { key: 'contingency', label: 'Contingency / DR Plans', icon: 'refresh', shortLabel: 'DR Plans' },
  { key: 'workforce', label: 'Workforce Access', icon: 'key', shortLabel: 'Workforce' },
  { key: 'physical', label: 'Physical Safeguards', icon: 'building', shortLabel: 'Physical' },
  { key: 'officers', label: 'Officer Designation', icon: 'badge', shortLabel: 'Officers' },
  { key: 'gap-analysis', label: 'Gap Analysis', icon: 'chart', shortLabel: 'Gap Analysis' },
] as const;

export type ModuleKey = typeof MODULE_DEFS[number]['key'];
