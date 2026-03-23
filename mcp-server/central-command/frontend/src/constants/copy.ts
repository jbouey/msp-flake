/**
 * Centralized Copy/Text Constants
 *
 * THE single source of truth for all user-facing text in the OsirisCare dashboard.
 * Every label, tooltip, status name, disclaimer, and description lives here.
 * Change once, updates everywhere.
 */

// =============================================================================
// STATUS LABELS
// =============================================================================

export const STATUS_LABELS = {
  // Compliance statuses
  pass: 'Passing',
  fail: 'Failing',
  warn: 'Needs Attention',
  unknown: 'Not Yet Monitored',

  // Site statuses
  online: 'Online',
  offline: 'Offline',
  stale: 'Stale',
  pending: 'Pending',
  inactive: 'Decommissioned',

  // Onboarding stages
  lead: 'Lead',
  discovery: 'Discovery',
  proposal: 'Proposal',
  contract: 'Contract',
  intake: 'Intake',
  creds: 'Credentials',
  shipped: 'Shipped',
  received: 'Received',
  provisioning: 'Provisioning',
  connectivity: 'Connected',
  scanning: 'Scanning',
  baseline: 'Baseline',
  compliant: 'Baseline Complete',  // NOT "Compliant" -- legal
  active: 'Active',

  // Incident statuses
  resolved: 'Resolved',
  escalated: 'Escalated',
  resolving: 'In Progress',

  // Agent statuses
  connected: 'Connected',
  disconnected: 'Disconnected',
} as const;

export type StatusKey = keyof typeof STATUS_LABELS;

// =============================================================================
// METRIC TOOLTIPS
// =============================================================================

export const METRIC_TOOLTIPS = {
  compliance_score: 'Average configuration check pass rate across all sites. Not a compliance certification.',
  incidents_24h: 'Configuration drift events detected in the last 24 hours.',
  l1_rate: 'Percentage of incidents resolved automatically by deterministic rules, without human intervention.',
  drift_checks: 'Number of active security configuration checks running across all sites.',
  clients: 'Total active sites being monitored.',
  healing_rate: 'Percentage of incidents successfully auto-remediated.',
  order_rate: 'Percentage of fleet orders completed successfully.',
  connectivity: 'Appliance connection status based on last check-in time.',
  mfa_coverage: 'Multi-factor authentication is not yet tracked by this platform.',
  backup_rate: 'Percentage of monitored systems with verified recent backups.',
  promotion_success: 'Success rate of promoted L2 patterns running as L1 rules.',
  control_coverage: 'Percentage of security checks passing across all sites.',
  appliances_online: 'Appliances currently reporting in vs. total deployed.',
  incidents_7d: 'Total configuration drift events in the past 7 days.',
  incidents_30d: 'Total configuration drift events in the past 30 days.',
  l1_rules: 'Instant automatic fixes. No human needed, resolves in under a second.',
  l2_decisions: 'AI-assisted resolutions from the past 30 days.',
  awaiting_promotion: 'AI fixes ready to become instant automatic rules after review.',
  learning_loop: 'The system learns from AI-assisted fixes and promotes them to instant automatic rules.',
} as const;

export type MetricKey = keyof typeof METRIC_TOOLTIPS;

// =============================================================================
// CATEGORY TOOLTIPS
// =============================================================================

export const CATEGORY_TOOLTIPS = {
  patching: 'Are your systems receiving security updates? Unpatched systems are vulnerable to known exploits.',
  firewall: 'Is your network firewall active and properly configured? Prevents unauthorized access.',
  encryption: 'Is data encrypted on your devices? Protects patient data if a device is lost or stolen.',
  backup: 'Are your systems being backed up? Required for disaster recovery.',
  logging: 'Are security events being recorded? Required for audit trails.',
  access_control: 'Are user accounts and passwords properly managed? Prevents unauthorized access to patient data.',
  antivirus: 'Is antivirus software active with current definitions? Protects against malware.',
  services: 'Are critical system services running? Ensures infrastructure availability.',
} as const;

// =============================================================================
// HEALING TIER LABELS
// =============================================================================

export const TIER_LABELS = {
  L1: 'Automatic fix',
  L2: 'AI-assisted resolution',
  L3: 'Escalated to your IT provider',
  manual: 'Manually resolved',
} as const;

// =============================================================================
// LEGAL DISCLAIMERS
// =============================================================================

export const DISCLAIMERS = {
  footer: 'OsirisCare provides automated compliance monitoring and does not constitute legal advice, HIPAA certification, or a guarantee of regulatory compliance. All metrics represent point-in-time observations. Consult qualified compliance professionals for formal assessments.',
  score: 'This score measures automated check pass rates and does not constitute compliance certification.',
  evidence: 'Cryptographically signed records of system configuration state. Supports compliance documentation but does not replace formal assessments.',
  blockchain: 'Bitcoin blockchain anchoring provides immutable timestamp verification that evidence records have not been altered since creation.',
} as const;

// =============================================================================
// PLATFORM BRANDING
// =============================================================================

export const BRANDING = {
  name: 'OsirisCare',
  tagline: 'HIPAA Compliance Monitoring Platform',
  support_email: 'support@osiriscare.net',
  dashboard_url: 'https://dashboard.osiriscare.net',
} as const;

// =============================================================================
// CATEGORY LABELS
// =============================================================================

export const CATEGORY_LABELS: Record<string, string> = {
  patching: 'Patching',
  antivirus: 'Antivirus',
  backup: 'Backup',
  logging: 'Logging',
  firewall: 'Firewall',
  encryption: 'Encryption',
  access_control: 'Access Control',
  services: 'Services',
};
