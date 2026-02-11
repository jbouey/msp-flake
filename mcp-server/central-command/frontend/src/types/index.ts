/**
 * TypeScript interfaces for Central Command Dashboard
 *
 * Mirrors the Pydantic models from the backend.
 */

// =============================================================================
// ENUMS
// =============================================================================

export type HealthStatus = 'critical' | 'warning' | 'healthy';
export type ResolutionLevel = 'L1' | 'L2' | 'L3';
export type Severity = 'critical' | 'high' | 'medium' | 'low';
export type CheckType =
  // Core compliance checks
  | 'patching' | 'antivirus' | 'backup' | 'logging' | 'firewall' | 'encryption' | 'network'
  // Extended monitoring checks
  | 'ntp_sync' | 'certificate_expiry' | 'database_corruption' | 'memory_pressure'
  | 'windows_defender' | 'disk_space' | 'service_health' | 'prohibited_port'
  // Workstation checks
  | 'workstation' | 'bitlocker' | 'defender' | 'patches' | 'screen_lock';
export type CheckinStatus = 'pending' | 'connected' | 'failed';

export type OnboardingStage =
  // Phase 1: Acquisition
  | 'lead'
  | 'discovery'
  | 'proposal'
  | 'contract'
  | 'intake'
  | 'creds'
  | 'shipped'
  // Phase 2: Activation
  | 'received'
  | 'connectivity'
  | 'scanning'
  | 'baseline'
  | 'compliant'
  | 'active';

export type DeploymentPhase =
  | 'discovering'
  | 'awaiting_credentials'
  | 'enumerating'
  | 'deploying'
  | 'scanning'
  | 'complete';

export interface DeploymentStatus {
  phase: DeploymentPhase;
  progress: number;
  details?: {
    domain_discovered?: string;
    servers_found?: number;
    workstations_found?: number;
    agents_deployed?: number;
    first_scan_complete?: boolean;
  };
}

// =============================================================================
// HEALTH METRICS
// =============================================================================

export interface ConnectivityMetrics {
  checkin_freshness: number;
  healing_success_rate: number;
  order_execution_rate: number;
  score: number;
}

export interface ComplianceMetrics {
  patching: number;
  antivirus: number;
  backup: number;
  logging: number;
  firewall: number;
  encryption: number;
  network: number;
  score: number;
}

export interface HealthMetrics {
  connectivity: ConnectivityMetrics;
  compliance: ComplianceMetrics;
  overall: number;
  status: HealthStatus;
}

// =============================================================================
// FLEET MODELS
// =============================================================================

export interface Appliance {
  id: number;
  site_id: string;
  hostname: string;
  ip_address?: string;
  agent_version?: string;
  tier: string;
  is_online: boolean;
  last_checkin?: string;
  health?: HealthMetrics;
  created_at: string;
}

export interface ClientOverview {
  site_id: string;
  name: string;
  appliance_count: number;
  online_count: number;
  health: HealthMetrics;
  last_incident?: string;
  incidents_24h: number;
}

export interface ClientDetail {
  site_id: string;
  name: string;
  tier: string;
  appliances: Appliance[];
  health: HealthMetrics;
  recent_incidents: Incident[];
  compliance_breakdown: ComplianceMetrics;
}

// =============================================================================
// INCIDENT MODELS
// =============================================================================

export interface Incident {
  id: number;
  site_id: string;
  hostname: string;
  check_type: CheckType;
  severity: Severity;
  resolution_level?: ResolutionLevel;
  resolved: boolean;
  resolved_at?: string;
  hipaa_controls: string[];
  created_at: string;
}

export interface IncidentDetail extends Incident {
  appliance_id: number;
  drift_data: Record<string, unknown>;
  evidence_bundle_id?: number;
  evidence_hash?: string;
  runbook_executed?: string;
  execution_log?: string;
}

// =============================================================================
// EVENT MODELS (Compliance Bundles)
// =============================================================================

export interface ComplianceEvent {
  id: string;
  site_id: string;
  hostname: string;
  check_type: string;
  check_name?: string;
  outcome: string;
  severity: Severity;
  resolution_level?: string;
  resolved: boolean;
  resolved_at?: string;
  hipaa_controls: string[];
  created_at: string;
  source: 'compliance_bundle';
}

// =============================================================================
// RUNBOOK MODELS
// =============================================================================

export interface Runbook {
  id: string;
  name: string;
  description: string;
  level: ResolutionLevel;
  hipaa_controls: string[];
  is_disruptive: boolean;
  execution_count: number;
  success_rate: number;
  avg_execution_time_ms: number;
}

export interface RunbookDetail extends Runbook {
  steps: Array<Record<string, unknown>>;
  parameters: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface RunbookExecution {
  id: number;
  runbook_id: string;
  site_id: string;
  hostname: string;
  incident_id?: number;
  success: boolean;
  execution_time_ms: number;
  output?: string;
  error?: string;
  executed_at: string;
}

// =============================================================================
// LEARNING LOOP MODELS
// =============================================================================

export interface LearningStatus {
  total_l1_rules: number;
  total_l2_decisions_30d: number;
  patterns_awaiting_promotion: number;
  recently_promoted_count: number;
  promotion_success_rate: number;
  l1_resolution_rate: number;
  l2_resolution_rate: number;
}

export interface PromotionCandidate {
  id: string;
  pattern_signature: string;
  description: string;
  occurrences: number;
  success_rate: number;
  avg_resolution_time_ms: number;
  proposed_rule: string;
  first_seen: string;
  last_seen: string;
}

export interface PromotionHistory {
  id: number;
  pattern_signature: string;
  rule_id: string;
  promoted_at: string;
  post_promotion_success_rate: number;
  executions_since_promotion: number;
}

// =============================================================================
// ONBOARDING MODELS
// =============================================================================

export interface ComplianceChecks {
  patching?: boolean;
  antivirus?: boolean;
  backup?: boolean;
  logging?: boolean;
  firewall?: boolean;
  encryption?: boolean;
}

export interface OnboardingClient {
  id: string;
  name: string;
  contact_name?: string;
  contact_email?: string;
  contact_phone?: string;
  stage: OnboardingStage;
  stage_entered_at: string;
  days_in_stage: number;
  blockers: string[];
  notes?: string;

  // Phase 1 timestamps
  lead_at?: string;
  discovery_at?: string;
  proposal_at?: string;
  contract_at?: string;
  intake_at?: string;
  creds_at?: string;
  shipped_at?: string;

  // Phase 2 timestamps
  received_at?: string;
  connectivity_at?: string;
  scanning_at?: string;
  baseline_at?: string;
  compliant_at?: string;
  active_at?: string;

  // Tracking
  tracking_number?: string;
  tracking_carrier?: string;
  appliance_serial?: string;
  site_id?: string;

  // Activation metrics
  checkin_status?: CheckinStatus;
  last_checkin?: string;
  assets_discovered?: number;
  compliance_checks?: ComplianceChecks;
  compliance_score?: number;

  // Progress
  progress_percent: number;
  phase: number;
  phase_progress: number;

  created_at: string;
}

export interface OnboardingMetrics {
  total_prospects: number;
  acquisition: Record<string, number>;
  activation: Record<string, number>;
  avg_days_to_ship: number;
  avg_days_to_active: number;
  stalled_count: number;
  at_risk_count: number;
  connectivity_issues: number;
}

// =============================================================================
// STATS MODELS
// =============================================================================

export interface GlobalStats {
  total_clients: number;
  total_appliances: number;
  online_appliances: number;
  avg_compliance_score: number;
  avg_connectivity_score: number;
  incidents_24h: number;
  incidents_7d: number;
  incidents_30d: number;
  l1_resolution_rate: number;
  l2_resolution_rate: number;
  l3_escalation_rate: number;
}

export interface ClientStats {
  site_id: string;
  appliance_count: number;
  online_count: number;
  compliance_score: number;
  connectivity_score: number;
  incidents_24h: number;
  incidents_7d: number;
  incidents_30d: number;
  l1_resolution_count: number;
  l2_resolution_count: number;
  l3_escalation_count: number;
}

// =============================================================================
// COMMAND INTERFACE
// =============================================================================

export interface CommandResponse {
  command: string;
  command_type: string;
  success: boolean;
  data?: Record<string, unknown>;
  message?: string;
  error?: string;
}

// =============================================================================
// NOTIFICATIONS
// =============================================================================

export type NotificationSeverity = 'critical' | 'warning' | 'info' | 'success';

export interface Notification {
  id: string;
  site_id?: string;
  appliance_id?: string;
  severity: NotificationSeverity;
  category: string;
  title: string;
  message: string;
  metadata: Record<string, unknown>;
  is_read: boolean;
  is_dismissed: boolean;
  created_at: string;
  read_at?: string;
}

export interface NotificationSummary {
  total: number;
  unread: number;
  critical: number;
  warning: number;
  info: number;
  success: number;
}

// =============================================================================
// MULTI-FRAMEWORK COMPLIANCE
// =============================================================================

export type ComplianceFramework = 'hipaa' | 'soc2' | 'pci_dss' | 'nist_csf' | 'cis';

export interface FrameworkConfig {
  appliance_id: string;
  site_id: string;
  enabled_frameworks: ComplianceFramework[];
  primary_framework: ComplianceFramework;
  industry: string;
  framework_metadata: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface FrameworkScore {
  framework: ComplianceFramework;
  framework_name: string;
  total_controls: number;
  passing_controls: number;
  failing_controls: number;
  unknown_controls: number;
  score_percentage: number;
  is_compliant: boolean;
  at_risk: boolean;
  calculated_at: string;
}

export interface FrameworkMetadata {
  framework: ComplianceFramework;
  name: string;
  version: string;
  description: string;
  regulatory_body: string;
  industry: string;
  categories: string[];
}

export interface IndustryRecommendation {
  industry: string;
  primary: ComplianceFramework;
  recommended: ComplianceFramework[];
  description: string;
}

export const FRAMEWORK_LABELS: Record<ComplianceFramework, string> = {
  hipaa: 'HIPAA',
  soc2: 'SOC 2',
  pci_dss: 'PCI DSS',
  nist_csf: 'NIST CSF',
  cis: 'CIS Controls',
};

export const FRAMEWORK_COLORS: Record<ComplianceFramework, string> = {
  hipaa: 'blue',
  soc2: 'purple',
  pci_dss: 'green',
  nist_csf: 'orange',
  cis: 'teal',
};

// =============================================================================
// WORKSTATION MODELS
// =============================================================================

export type WorkstationComplianceStatus = 'compliant' | 'drifted' | 'error' | 'unknown' | 'offline';

export interface WorkstationCheckResult {
  check_type: 'bitlocker' | 'defender' | 'patches' | 'firewall' | 'screen_lock';
  status: WorkstationComplianceStatus;
  compliant: boolean;
  details: Record<string, unknown>;
  hipaa_controls: string[];
  checked_at: string;
}

export interface Workstation {
  id: string;
  hostname: string;
  ip_address?: string;
  os_name?: string;
  os_version?: string;
  online: boolean;
  last_seen?: string;
  compliance_status: WorkstationComplianceStatus;
  last_compliance_check?: string;
  compliance_percentage: number;
  checks?: Record<string, WorkstationCheckResult>;
}

export interface SiteWorkstationSummary {
  site_id: string;
  total_workstations: number;
  online_workstations: number;
  compliant_workstations: number;
  drifted_workstations: number;
  error_workstations: number;
  unknown_workstations: number;
  overall_compliance_rate: number;
  check_compliance: Record<string, {
    compliant: number;
    drifted: number;
    error: number;
    rate: number;
  }>;
  last_scan?: string;
}

export const WORKSTATION_CHECK_LABELS: Record<string, string> = {
  bitlocker: 'BitLocker',
  defender: 'Defender',
  patches: 'Patches',
  firewall: 'Firewall',
  screen_lock: 'Screen Lock',
};

export const WORKSTATION_CHECK_HIPAA: Record<string, string> = {
  bitlocker: '§164.312(a)(2)(iv)',
  defender: '§164.308(a)(5)(ii)(B)',
  patches: '§164.308(a)(5)(ii)(B)',
  firewall: '§164.312(a)(1)',
  screen_lock: '§164.312(a)(2)(iii)',
};

// =============================================================================
// GO AGENT MODELS
// =============================================================================

export type GoAgentStatus = 'active' | 'offline' | 'error' | 'pending';
export type GoAgentCapabilityTier = 'monitor_only' | 'self_heal' | 'full_remediation';

export interface GoAgentCheckResult {
  check_type: 'bitlocker' | 'defender' | 'firewall' | 'patches' | 'screen_lock' | 'services';
  status: 'pass' | 'fail' | 'error' | 'skipped';
  message?: string;
  details?: Record<string, unknown>;
  hipaa_control: string;
  checked_at: string;
}

export interface GoAgent {
  id: string;
  hostname: string;
  ip_address?: string;
  agent_version?: string;
  capability_tier: GoAgentCapabilityTier;
  last_heartbeat?: string;
  status: GoAgentStatus;
  checks_passed: number;
  checks_total: number;
  compliance_percentage: number;
  rmm_detected?: string;
  rmm_disabled: boolean;
  offline_queue_size: number;
  connected_at?: string;
  checks?: GoAgentCheckResult[];
}

export interface SiteGoAgentSummary {
  site_id: string;
  total_agents: number;
  active_agents: number;
  offline_agents: number;
  error_agents: number;
  pending_agents: number;
  overall_compliance_rate: number;
  agents_by_tier: Record<GoAgentCapabilityTier, number>;
  agents_by_version: Record<string, number>;
  rmm_detected_count: number;
  last_event?: string;
}

export const GO_AGENT_STATUS_LABELS: Record<GoAgentStatus, string> = {
  active: 'Active',
  offline: 'Offline',
  error: 'Error',
  pending: 'Pending',
};

export const GO_AGENT_STATUS_COLORS: Record<GoAgentStatus, string> = {
  active: 'green',
  offline: 'gray',
  error: 'red',
  pending: 'yellow',
};

export const GO_AGENT_TIER_LABELS: Record<GoAgentCapabilityTier, string> = {
  monitor_only: 'Monitor Only',
  self_heal: 'Self-Heal',
  full_remediation: 'Full Remediation',
};

export const GO_AGENT_TIER_COLORS: Record<GoAgentCapabilityTier, string> = {
  monitor_only: 'blue',
  self_heal: 'purple',
  full_remediation: 'green',
};

// =============================================================================
// CVE Watch Types
// =============================================================================

export interface CVESummary {
  total_cves: number;
  by_severity: { critical: number; high: number; medium: number; low: number };
  by_status: { open: number; mitigated: number; accepted_risk: number; not_affected: number };
  coverage_pct: number;
  last_sync: string | null;
  watched_cpes: string[];
}

export interface CVEEntry {
  id: string;
  cve_id: string;
  severity: string;
  cvss_score: number | null;
  published_date: string;
  description: string;
  affected_count: number;
  status: string;
}

export interface CVEDetail extends CVEEntry {
  last_modified: string | null;
  nvd_status: string;
  references: { url: string; source: string }[];
  cwe_ids: string[];
  affected_appliances: { appliance_id: string; site_id: string; status: string; notes: string | null; mitigated_at: string | null; mitigated_by: string | null }[];
}

export interface CVEWatchConfig {
  watched_cpes: string[];
  sync_interval_hours: number;
  min_severity: string;
  enabled: boolean;
  has_api_key: boolean;
  last_sync_at: string | null;
}

export const GO_AGENT_CHECK_LABELS: Record<string, string> = {
  bitlocker: 'BitLocker',
  defender: 'Defender',
  firewall: 'Firewall',
  patches: 'Patches',
  screen_lock: 'Screen Lock',
  services: 'Services',
};

export const GO_AGENT_CHECK_HIPAA: Record<string, string> = {
  bitlocker: '§164.312(a)(2)(iv)',
  defender: '§164.308(a)(5)(ii)(B)',
  firewall: '§164.312(e)(1)',
  patches: '§164.308(a)(1)(ii)(B)',
  screen_lock: '§164.312(a)(2)(i)',
  services: '§164.308(a)(5)(ii)(B)',
};

// =============================================================================
// Framework Sync Types (Compliance Library)
// =============================================================================

export interface FrameworkSyncStatus {
  framework: string;
  display_name: string;
  version: string | null;
  source_type: string;
  source_url: string | null;
  last_sync: string | null;
  sync_status: string | null;
  total_controls: number;
  our_coverage: number;
  coverage_pct: number;
  enabled: boolean;
}

export interface FrameworkControl {
  control_id: string;
  control_name: string;
  description: string;
  category: string;
  subcategory: string | null;
  parent_control_id: string | null;
  severity: string | null;
  required: boolean;
  mapped_check: string | null;
  mapping_source: string | null;
}

export interface FrameworkCrosswalk {
  source_control_id: string;
  target_framework: string;
  target_control_id: string;
  mapping_type: string;
  source_reference: string | null;
}

export interface CoverageAnalysis {
  frameworks: Array<{
    framework: string;
    display_name: string;
    total_controls: number;
    our_coverage: number;
    coverage_pct: number;
    unmapped_controls: number;
  }>;
  check_matrix: Record<string, Record<string, string[]>>;
}

export interface FrameworkCategory {
  category: string;
  count: number;
}
