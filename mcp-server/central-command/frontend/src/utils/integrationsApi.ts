/**
 * API client for Cloud Integrations
 */

const API_BASE = '/api/integrations';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// Get auth token from localStorage
function getAuthToken(): string | null {
  return localStorage.getItem('auth_token');
}

async function fetchIntegrationsApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const token = getAuthToken();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...headers,
      ...(options?.headers as Record<string, string>),
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new ApiError(response.status, error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// =============================================================================
// TYPES
// =============================================================================

export type IntegrationProvider = 'aws' | 'google_workspace' | 'okta' | 'azure_ad';

export type IntegrationStatus = 'active' | 'pending_oauth' | 'error' | 'paused' | 'disconnected';

export type RiskLevel = 'critical' | 'high' | 'medium' | 'low' | 'unknown';

export interface IntegrationHealth {
  status: 'healthy' | 'warning' | 'critical' | 'error';
  critical_count: number;
  high_count: number;
  last_error: string | null;
}

export interface Integration {
  id: string;
  site_id: string;
  provider: IntegrationProvider;
  name: string;
  status: IntegrationStatus;
  last_sync: string | null;
  next_sync: string | null;
  resource_count: number;
  health: IntegrationHealth;
  created_at: string;
}

export interface IntegrationResource {
  id: string;
  resource_type: string;
  resource_id: string;
  name: string | null;
  compliance_checks: ComplianceCheck[];
  risk_level: RiskLevel | null;
  last_synced: string | null;
}

export interface ComplianceCheck {
  check: string;
  status: 'pass' | 'fail' | 'warning' | 'info' | 'critical';
  control: string;
  description: string;
  details?: string;
}

export interface IntegrationCreateRequest {
  provider: IntegrationProvider;
  name: string;
  // AWS-specific
  aws_role_arn?: string;
  aws_external_id?: string;
  aws_regions?: string[];
  // OAuth-specific
  oauth_client_id?: string;
  oauth_client_secret?: string;
  oauth_tenant_id?: string;
  okta_domain?: string;
  google_customer_id?: string;
}

export interface IntegrationCreateResponse {
  id: string;
  provider: IntegrationProvider;
  status: IntegrationStatus;
  name?: string;
  message: string;
  auth_url?: string;
  aws_account_id?: string;
}

export interface SyncJob {
  job_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'timeout';
  started_at: string | null;
  completed_at: string | null;
  resources_synced: number | null;
  error_message: string | null;
}

export interface SiteIntegrationsHealth {
  site_id: string;
  overall_status: 'healthy' | 'warning' | 'critical';
  total_integrations: number;
  total_critical: number;
  total_high: number;
  integrations: Array<{
    integration_id: string;
    provider: IntegrationProvider;
    status: IntegrationStatus;
    health: 'healthy' | 'warning' | 'critical';
    critical_count: number;
    high_count: number;
    last_sync: string | null;
    resource_count: number;
  }>;
}

export interface ResourcesResponse {
  total: number;
  limit: number;
  offset: number;
  resources: IntegrationResource[];
}

export interface AWSSetupInstructions {
  instructions: string;
  cloudformation_template: string;
  terraform_template: string;
}

// =============================================================================
// API FUNCTIONS
// =============================================================================

export const integrationsApi = {
  // List integrations for a site
  listIntegrations: (siteId: string, params?: { status?: string; provider?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.provider) searchParams.set('provider', params.provider);
    const query = searchParams.toString();
    return fetchIntegrationsApi<Integration[]>(`/sites/${siteId}${query ? `?${query}` : ''}`);
  },

  // Get single integration
  getIntegration: (siteId: string, integrationId: string) =>
    fetchIntegrationsApi<Integration>(`/sites/${siteId}/${integrationId}`),

  // Create integration (returns auth_url for OAuth, or creates directly for AWS)
  createIntegration: (siteId: string, data: IntegrationCreateRequest) =>
    fetchIntegrationsApi<IntegrationCreateResponse>(`/sites/${siteId}`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Delete integration
  deleteIntegration: (siteId: string, integrationId: string) =>
    fetchIntegrationsApi<{ message: string }>(`/sites/${siteId}/${integrationId}`, {
      method: 'DELETE',
    }),

  // List resources for an integration
  listResources: (
    siteId: string,
    integrationId: string,
    params?: { resource_type?: string; risk_level?: string; limit?: number; offset?: number }
  ) => {
    const searchParams = new URLSearchParams();
    if (params?.resource_type) searchParams.set('resource_type', params.resource_type);
    if (params?.risk_level) searchParams.set('risk_level', params.risk_level);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    return fetchIntegrationsApi<ResourcesResponse>(
      `/sites/${siteId}/${integrationId}/resources${query ? `?${query}` : ''}`
    );
  },

  // Trigger manual sync
  triggerSync: (siteId: string, integrationId: string) =>
    fetchIntegrationsApi<{ job_id: string; status: string; message: string }>(
      `/sites/${siteId}/${integrationId}/sync`,
      { method: 'POST' }
    ),

  // Get sync job status
  getSyncStatus: (siteId: string, integrationId: string, jobId: string) =>
    fetchIntegrationsApi<SyncJob>(`/sites/${siteId}/${integrationId}/sync/${jobId}`),

  // Get health status for all integrations in a site
  getSiteHealth: (siteId: string) =>
    fetchIntegrationsApi<SiteIntegrationsHealth>(`/sites/${siteId}/health`),

  // Get AWS setup instructions
  getAWSSetupInstructions: () =>
    fetchIntegrationsApi<AWSSetupInstructions>('/aws/setup-instructions'),

  // Generate AWS external ID
  generateAWSExternalId: () =>
    fetchIntegrationsApi<{ external_id: string }>('/aws/generate-external-id', {
      method: 'POST',
    }),
};

// Provider display information
export const PROVIDER_INFO: Record<IntegrationProvider, {
  name: string;
  description: string;
  icon: string;
  color: string;
  setupType: 'oauth' | 'iam_role';
}> = {
  aws: {
    name: 'Amazon Web Services',
    description: 'IAM users, S3 buckets, EC2 instances, RDS, CloudTrail, Security Groups',
    icon: 'aws',
    color: '#FF9900',
    setupType: 'iam_role',
  },
  google_workspace: {
    name: 'Google Workspace',
    description: 'Users, Groups, MFA status, Organizational Units',
    icon: 'google',
    color: '#4285F4',
    setupType: 'oauth',
  },
  okta: {
    name: 'Okta',
    description: 'Users, Groups, Applications, Policies',
    icon: 'okta',
    color: '#007DC1',
    setupType: 'oauth',
  },
  azure_ad: {
    name: 'Azure AD (Entra ID)',
    description: 'Users, Groups, Conditional Access Policies, Directory Roles',
    icon: 'microsoft',
    color: '#0078D4',
    setupType: 'oauth',
  },
};

// Risk level colors and labels
export const RISK_LEVEL_CONFIG: Record<RiskLevel, { label: string; color: string; bgColor: string }> = {
  critical: { label: 'Critical', color: '#DC2626', bgColor: '#FEE2E2' },
  high: { label: 'High', color: '#EA580C', bgColor: '#FFEDD5' },
  medium: { label: 'Medium', color: '#CA8A04', bgColor: '#FEF3C7' },
  low: { label: 'Low', color: '#16A34A', bgColor: '#DCFCE7' },
  unknown: { label: 'Unknown', color: '#6B7280', bgColor: '#F3F4F6' },
};

// Resource type labels
export const RESOURCE_TYPE_LABELS: Record<string, string> = {
  // AWS
  iam_user: 'IAM User',
  s3_bucket: 'S3 Bucket',
  ec2_instance: 'EC2 Instance',
  rds_instance: 'RDS Instance',
  cloudtrail: 'CloudTrail',
  security_group: 'Security Group',
  // Google
  user: 'User',
  group: 'Group',
  org_unit: 'Organizational Unit',
  // Okta
  application: 'Application',
  policy_password: 'Password Policy',
  policy_mfa_enroll: 'MFA Enrollment Policy',
  policy_okta_sign_on: 'Sign-On Policy',
  // Azure
  conditional_access_policy: 'Conditional Access Policy',
  directory_role: 'Directory Role',
};

export { ApiError };
