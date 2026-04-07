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
  auth_failed: 'Auth Failed',

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
  incidents_24h: 'Configuration issues detected in the last 24 hours.',
  l1_rate: 'Percentage of incidents resolved automatically by deterministic rules, without human intervention.',
  compliance_checks: 'Number of active security configuration checks running across all sites.',
  clients: 'Total active sites being monitored.',
  healing_rate: 'Percentage of incidents successfully auto-remediated.',
  order_rate: 'Percentage of fleet orders completed successfully.',
  connectivity: 'Appliance connection status based on last check-in time.',
  mfa_coverage: 'Multi-factor authentication is not yet tracked by this platform.',
  backup_rate: 'Percentage of monitored systems with verified recent backups.',
  promotion_success: 'Success rate of promoted L2 patterns running as L1 rules.',
  control_coverage: 'Percentage of security checks passing across all sites.',
  appliances_online: 'Appliances currently reporting in vs. total deployed.',
  incidents_7d: 'Total configuration issues in the past 7 days.',
  incidents_30d: 'Total configuration issues in the past 30 days.',
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
  firewall: 'Is your network firewall active and properly configured? Helps restrict unauthorized access.',
  encryption: 'Is data encrypted on your devices? Reduces exposure if a device is lost or stolen.',
  backup: 'Are your systems being backed up? Required for disaster recovery.',
  logging: 'Are security events being recorded? Required for audit trails.',
  access_control: 'Are user accounts and passwords properly managed? Monitors for unauthorized access to patient data.',
  antivirus: 'Is antivirus software active with current definitions? Helps detect malware.',
  services: 'Are critical system services running? Monitors infrastructure availability.',
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
  footer: 'OsirisCare provides automated compliance monitoring and does not constitute legal advice, compliance certification, or a guarantee of regulatory compliance. All metrics represent point-in-time observations. Consult qualified compliance professionals for formal assessments.',
  score: 'This score measures automated check pass rates and does not constitute compliance certification.',
  evidence: 'Cryptographically signed records of system configuration state. Supports compliance documentation but does not replace formal assessments.',
  blockchain: 'Bitcoin blockchain anchoring provides immutable timestamp verification that evidence records have not been altered since creation.',
  portal_detailed: 'This system monitors configuration states and provides automated compliance observations. OsirisCare does not certify regulatory compliance. Compliance determinations require qualified assessment by authorized personnel. Contact your compliance officer for official guidance. All metrics represent point-in-time observations, not guarantees of security or compliance status.',
  evidence_chain: 'Evidence bundles are cryptographically signed and anchored to the Bitcoin blockchain for immutable timestamp verification. This provides independent proof that evidence records have not been altered since creation.',
  landing_legal: 'OsirisCare provides automated compliance monitoring tools for healthcare organizations. Organizations remain solely responsible for their compliance programs, policies, and regulatory obligations.',
  portal_login: 'This portal provides monitoring data only. OsirisCare does not certify compliance.',
} as const;

// =============================================================================
// PLATFORM BRANDING
// =============================================================================

export const BRANDING = {
  name: 'OsirisCare',
  tagline: 'Compliance Monitoring Platform',
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

// =============================================================================
// SRA CATEGORY LABELS
// =============================================================================

export const SRA_CATEGORY_LABELS = {
  administrative: 'Administrative Safeguards',
  physical: 'Physical Safeguards',
  technical: 'Technical Safeguards',
} as const;

// =============================================================================
// PHYSICAL SAFEGUARD LABELS
// =============================================================================

export const PHYSICAL_SAFEGUARD_LABELS = {
  facility_access: 'Facility Access Controls',
  workstation_use: 'Workstation Use',
  workstation_security: 'Workstation Security',
  device_controls: 'Device and Media Controls',
} as const;

// =============================================================================
// SSO LABELS
// =============================================================================

// =============================================================================
// WHITE-LABEL BRANDING DEFAULTS
// =============================================================================

export const WHITE_LABEL = {
  POWERED_BY: 'Powered by OsirisCare',
  DEFAULT_BRAND: 'OsirisCare',
  DEFAULT_TAGLINE: 'Compliance Simplified',
  DEFAULT_PRIMARY: '#0D9488',
  DEFAULT_SECONDARY: '#6366F1',
} as const;

// =============================================================================
// SSO LABELS
// =============================================================================

export const SSO_LABELS = {
  sign_in_with_sso: 'Sign in with SSO',
  sso_not_configured: 'SSO is not configured for this organization.',
  sso_enforced_message: 'Your organization requires SSO sign-in.',
  sso_config_title: 'SSO Configuration',
  sso_config_description: 'Configure SAML/OIDC single sign-on for your client organizations.',
  sso_issuer_url: 'Issuer URL',
  sso_client_id: 'Client ID',
  sso_client_secret: 'Client Secret',
  sso_allowed_domains: 'Allowed Domains',
  sso_enforced: 'Enforce SSO',
  sso_enforced_help: 'When enabled, users in this organization must use SSO. Password and magic link sign-in will be disabled.',
  sso_saved: 'SSO configuration saved.',
  sso_deleted: 'SSO configuration removed.',
  sso_delete_confirm: 'Remove SSO configuration? Users will need to sign in with email/password or magic link.',
} as const;

// =============================================================================
// OPS CENTER
// =============================================================================

export const OPS_LABELS: Record<string, { title: string; tooltip: string; docsAnchor: string }> = {
  evidence_chain:   { title: 'Evidence Chain',   tooltip: 'Compliance bundle submission pipeline — Ed25519 signed, hash-chained',               docsAnchor: '#evidence-chain' },
  signing:          { title: 'Signing',          tooltip: 'Ed25519 signature coverage and key health across all appliances',                    docsAnchor: '#signing' },
  ots_anchoring:    { title: 'OTS Anchoring',    tooltip: 'OpenTimestamps Bitcoin proof pipeline — Merkle batched hourly',                      docsAnchor: '#ots' },
  healing_pipeline: { title: 'Healing Pipeline', tooltip: 'L1/L2/L3 auto-remediation success rates and stuck incident detection',               docsAnchor: '#healing' },
  fleet:            { title: 'Fleet',            tooltip: 'Appliance connectivity — online/offline status and version currency',                 docsAnchor: '#fleet' },
};

export const AUDIT_BADGE_LABELS: Record<string, string> = {
  green: 'Audit Supportive',
  yellow: 'Issues Found',
  red: 'Not Ready',
};
