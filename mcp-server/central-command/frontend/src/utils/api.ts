/**
 * API client for Central Command Dashboard
 */

const API_BASE = '/api/dashboard';

// ETag cache for conditional requests
const etagCache = new Map<string, string>();
// Response cache for 304 Not Modified fallback
const responseCache = new Map<string, unknown>();

class ApiError extends Error {
  constructor(public status: number, message: string, public isAborted: boolean = false) {
    super(message);
    this.name = 'ApiError';
  }
}

// Request timeout in milliseconds (30 seconds)
const REQUEST_TIMEOUT = 30000;

// Read CSRF token from cookie for state-changing requests
function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Extended fetch options with AbortSignal support
 */
interface FetchApiOptions extends RequestInit {
  /** AbortSignal for request cancellation */
  signal?: AbortSignal;
  /** Custom timeout in milliseconds (default: 30000) */
  timeout?: number;
}

/**
 * Creates an AbortController with timeout
 */
function createTimeoutController(timeoutMs: number, existingSignal?: AbortSignal): {
  controller: AbortController;
  cleanup: () => void;
} {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  // If an external signal is provided, abort when it aborts
  const abortHandler = () => controller.abort();
  existingSignal?.addEventListener('abort', abortHandler);

  const cleanup = () => {
    clearTimeout(timeoutId);
    existingSignal?.removeEventListener('abort', abortHandler);
  };

  return { controller, cleanup };
}

async function _fetchWithBase<T>(
  baseUrl: string,
  endpoint: string,
  options?: FetchApiOptions,
): Promise<T> {
  const url = `${baseUrl}${endpoint}`;
  const timeout = options?.timeout ?? REQUEST_TIMEOUT;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  // Add CSRF token for state-changing requests
  const method = options?.method?.toUpperCase() || 'GET';
  if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
  }

  // Add ETag for conditional GET requests
  if (method === 'GET') {
    const cachedEtag = etagCache.get(url);
    if (cachedEtag) {
      headers['If-None-Match'] = cachedEtag;
    }
  }

  // Create timeout controller
  const { controller, cleanup } = createTimeoutController(timeout, options?.signal);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: {
        ...headers,
        ...(options?.headers as Record<string, string>),
      },
    });

    // 304 Not Modified - return cached response data
    if (response.status === 304) {
      const cached = responseCache.get(url);
      if (cached !== undefined) {
        return cached as T;
      }
      etagCache.delete(url);
      return _fetchWithBase<T>(baseUrl, endpoint, options);
    }

    if (!response.ok) {
      if (response.status === 401 && !endpoint.includes('/auth/')) {
        window.location.href = '/login';
        throw new ApiError(401, 'Session expired');
      }
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new ApiError(response.status, error.detail || `HTTP ${response.status}`);
    }

    const etag = response.headers.get('etag');
    if (etag) {
      etagCache.set(url, etag);
    }

    const data: T = await response.json();
    if (method === 'GET') {
      responseCache.set(url, data);
    }
    return data;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiError(0, 'Request was cancelled or timed out', true);
    }
    throw error;
  } finally {
    cleanup();
  }
}

async function fetchApi<T>(endpoint: string, options?: FetchApiOptions): Promise<T> {
  return _fetchWithBase<T>(API_BASE, endpoint, options);
}

// =============================================================================
// FLEET API
// =============================================================================

import type {
  ClientOverview,
  ClientDetail,
  Appliance,
} from '../types';

export const fleetApi = {
  getFleet: () => fetchApi<ClientOverview[]>('/fleet'),

  getClient: (siteId: string) => fetchApi<ClientDetail>(`/fleet/${siteId}`),

  getAppliances: (siteId: string) => fetchApi<Appliance[]>(`/fleet/${siteId}/appliances`),
};

// =============================================================================
// INCIDENT API
// =============================================================================

import type { Incident, IncidentDetail, ComplianceEvent } from '../types';

export const incidentApi = {
  getIncidents: (params?: {
    site_id?: string;
    limit?: number;
    level?: string;
    resolved?: boolean;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.site_id) searchParams.set('site_id', params.site_id);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.level) searchParams.set('level', params.level);
    if (params?.resolved !== undefined) searchParams.set('resolved', String(params.resolved));

    const query = searchParams.toString();
    return fetchApi<Incident[]>(`/incidents${query ? `?${query}` : ''}`);
  },

  getIncident: (id: number) => fetchApi<IncidentDetail>(`/incidents/${id}`),

  getEvents: (params?: {
    site_id?: string;
    limit?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.site_id) searchParams.set('site_id', params.site_id);
    if (params?.limit) searchParams.set('limit', String(params.limit));

    const query = searchParams.toString();
    return fetchApi<ComplianceEvent[]>(`/events${query ? `?${query}` : ''}`);
  },
};

// =============================================================================
// RUNBOOK API
// =============================================================================

import type { Runbook, RunbookDetail, RunbookExecution } from '../types';

export const runbookApi = {
  getRunbooks: () => fetchApi<Runbook[]>('/runbooks'),

  getRunbook: (id: string) => fetchApi<RunbookDetail>(`/runbooks/${id}`),

  getExecutions: (id: string, limit?: number) => {
    const query = limit ? `?limit=${limit}` : '';
    return fetchApi<RunbookExecution[]>(`/runbooks/${id}/executions${query}`);
  },
};

// =============================================================================
// LEARNING API
// =============================================================================

import type { LearningStatus, PromotionCandidate, PromotionHistory } from '../types';

export const learningApi = {
  getStatus: () => fetchApi<LearningStatus>('/learning/status'),

  getCandidates: () => fetchApi<PromotionCandidate[]>('/learning/candidates'),

  getHistory: (limit?: number) => {
    const query = limit ? `?limit=${limit}` : '';
    return fetchApi<PromotionHistory[]>(`/learning/history${query}`);
  },

  promote: (patternId: string) =>
    fetchApi<{ status: string; pattern_id: string; new_rule_id: string }>(
      `/learning/promote/${patternId}`,
      { method: 'POST' }
    ),

  reject: (patternId: string) =>
    fetchApi<{ status: string; pattern_id: string }>(
      `/learning/reject/${patternId}`,
      { method: 'POST' }
    ),
};

// =============================================================================
// ONBOARDING API
// =============================================================================

import type { OnboardingClient, OnboardingMetrics } from '../types';

export const onboardingApi = {
  // Use real Sites data for pipeline (via /api/sites/onboarding/pipeline)
  getPipeline: () => fetchSitesApi<OnboardingClient[]>('/onboarding/pipeline'),

  getMetrics: () => fetchSitesApi<OnboardingMetrics>('/onboarding/metrics'),

  getClient: (id: number) => fetchApi<OnboardingClient>(`/onboarding/${id}`),

  createProspect: (data: {
    name: string;
    contact_name?: string;
    contact_email?: string;
    contact_phone?: string;
    notes?: string;
  }) =>
    fetchApi<OnboardingClient>('/onboarding', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  advanceStage: (id: number, newStage: string, notes?: string) =>
    fetchApi<{ status: string }>(`/onboarding/${id}/stage`, {
      method: 'PATCH',
      body: JSON.stringify({ new_stage: newStage, notes }),
    }),

  updateBlockers: (id: number, blockers: string[]) =>
    fetchApi<{ status: string }>(`/onboarding/${id}/blockers`, {
      method: 'PATCH',
      body: JSON.stringify({ blockers }),
    }),

  addNote: (id: number, note: string) =>
    fetchApi<{ status: string }>(`/onboarding/${id}/note`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
};

// =============================================================================
// STATS API
// =============================================================================

import type { GlobalStats, ClientStats } from '../types';

export const statsApi = {
  getGlobalStats: () => fetchApi<GlobalStats>('/stats'),

  getClientStats: (siteId: string) => fetchApi<ClientStats>(`/stats/${siteId}`),
};

// =============================================================================
// COMMAND CENTER API
// =============================================================================

import type {
  FleetPostureSite,
  IncidentTrendsResponse,
  AttentionRequiredResponse,
} from '../types';

export const commandCenterApi = {
  getFleetPosture: () =>
    fetchApi<FleetPostureSite[]>('/fleet-posture'),

  getIncidentTrends: (window: '24h' | '7d' | '30d' = '24h', siteId?: string) => {
    const params = new URLSearchParams({ window });
    if (siteId) params.set('site_id', siteId);
    return fetchApi<IncidentTrendsResponse>(`/incident-trends?${params}`);
  },

  getAttentionRequired: () =>
    fetchApi<AttentionRequiredResponse>('/attention-required'),
};

// =============================================================================
// COMMAND API
// =============================================================================

import type { CommandResponse } from '../types';

export const commandApi = {
  execute: (command: string) =>
    fetchApi<CommandResponse>('/command', {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
};

// =============================================================================
// SITES API (Real appliance onboarding data)
// =============================================================================

export interface Site {
  site_id: string;
  clinic_name: string;
  contact_name: string | null;
  contact_email: string | null;
  tier: string;
  healing_tier: 'standard' | 'full_coverage';
  status: string;
  live_status: 'online' | 'stale' | 'offline' | 'pending';
  onboarding_stage: string;
  created_at: string | null;
  updated_at: string | null;
  last_checkin: string | null;
  appliance_count: number;
}

export interface SiteDetail extends Site {
  contact_phone: string | null;
  address: string | null;
  provider_count: string | null;
  ehr_system: string | null;
  notes: string | null;
  blockers: string[];
  tracking_number: string | null;
  tracking_carrier: string | null;
  timestamps: {
    lead_at: string | null;
    discovery_at: string | null;
    proposal_at: string | null;
    contract_at: string | null;
    intake_at: string | null;
    creds_at: string | null;
    shipped_at: string | null;
    received_at: string | null;
    connectivity_at: string | null;
    scanning_at: string | null;
    baseline_at: string | null;
    active_at: string | null;
  };
  appliances: SiteAppliance[];
  credentials: SiteCredential[];
}

export interface SiteAppliance {
  appliance_id: string;
  hostname: string | null;
  mac_address: string | null;
  ip_addresses: string[];
  agent_version: string | null;
  nixos_version: string | null;
  status: string;
  live_status: 'online' | 'stale' | 'offline' | 'pending';
  first_checkin: string | null;
  last_checkin: string | null;
  uptime_seconds: number | null;
  l2_mode: 'auto' | 'manual' | 'disabled';
}

export interface SiteCredential {
  id: string;
  credential_type: string;
  credential_name: string;
  created_at: string | null;
}

async function fetchSitesApi<T>(endpoint: string, options?: FetchApiOptions): Promise<T> {
  return _fetchWithBase<T>('/api', endpoint, options);
}

export const sitesApi = {
  getSites: (status?: string) => {
    const query = status ? `?status=${status}` : '';
    return fetchSitesApi<{ sites: Site[]; count: number }>(`/sites${query}`);
  },

  getSite: (siteId: string) => fetchSitesApi<SiteDetail>(`/sites/${siteId}`),

  createSite: (data: {
    clinic_name: string;
    site_id?: string;
    contact_name?: string;
    contact_email?: string;
    contact_phone?: string;
    address?: string;
    tier?: string;
  }) =>
    fetchSitesApi<{ status: string; site_id: string; clinic_name: string }>('/sites', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateSite: (siteId: string, data: Partial<{
    clinic_name: string;
    contact_name: string;
    contact_email: string;
    contact_phone: string;
    address: string;
    tier: string;
    onboarding_stage: string;
    notes: string;
    blockers: string[];
  }>) =>
    fetchSitesApi<{ status: string; site_id: string }>(`/sites/${siteId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteSite: (siteId: string) =>
    fetchSitesApi<{ status: string; site_id: string }>(`/sites/${siteId}`, {
      method: 'DELETE',
    }),

  getAppliances: (siteId: string) =>
    fetchSitesApi<{ site_id: string; appliances: SiteAppliance[]; count: number }>(
      `/sites/${siteId}/appliances`
    ),

  addCredential: (siteId: string, data: {
    credential_type: string;
    credential_name: string;
    username?: string;
    password?: string;
    host?: string;
    port?: number;
  }) =>
    fetchSitesApi<{ status: string }>(`/sites/${siteId}/credentials`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  deleteCredential: (siteId: string, credentialId: string) =>
    fetchSitesApi<{ status: string }>(`/sites/${siteId}/credentials/${credentialId}`, {
      method: 'DELETE',
    }),

  updateHealingTier: (siteId: string, healingTier: 'standard' | 'full_coverage') =>
    fetchSitesApi<{ status: string; site_id: string; healing_tier: string }>(`/sites/${siteId}/healing-tier`, {
      method: 'PUT',
      body: JSON.stringify({ healing_tier: healingTier }),
    }),
};

// =============================================================================
// ORDERS API
// =============================================================================

export type OrderType =
  | 'force_checkin'
  | 'run_drift'
  | 'sync_rules'
  | 'restart_agent'
  | 'view_logs'
  | 'collect_evidence'
  | 'update_agent';

export type OrderStatus =
  | 'pending'
  | 'acknowledged'
  | 'executing'
  | 'completed'
  | 'failed'
  | 'expired';

export interface OrderCreate {
  order_type: OrderType;
  parameters?: Record<string, unknown>;
  priority?: number;
}

export interface OrderResponse {
  order_id: string;
  appliance_id: string | null;
  site_id: string;
  order_type: OrderType;
  parameters: Record<string, unknown>;
  priority: number;
  status: OrderStatus;
  created_by: string;
  created_at: string;
  expires_at: string;
  acknowledged_at: string | null;
  completed_at: string | null;
  result: Record<string, unknown> | null;
  error_message: string | null;
}

export interface ClearStaleRequest {
  stale_hours?: number;
}

export interface ClearStaleResponse {
  deleted_count: number;
  deleted_appliances: string[];
}

export const ordersApi = {
  // Create an order for a specific appliance
  createApplianceOrder: (siteId: string, applianceId: string, order: OrderCreate) =>
    fetchSitesApi<OrderResponse>(`/sites/${siteId}/appliances/${applianceId}/orders`, {
      method: 'POST',
      body: JSON.stringify(order),
    }),

  // Broadcast an order to all appliances in a site
  broadcastOrder: (siteId: string, order: { order_type: OrderType; parameters?: Record<string, unknown> }) =>
    fetchSitesApi<OrderResponse[]>(`/sites/${siteId}/orders/broadcast`, {
      method: 'POST',
      body: JSON.stringify(order),
    }),

  // Get orders for a site
  getOrders: (siteId: string, status?: OrderStatus) => {
    const query = status ? `?status=${status}` : '';
    return fetchSitesApi<OrderResponse[]>(`/sites/${siteId}/orders${query}`);
  },

  // Delete an appliance
  deleteAppliance: (siteId: string, applianceId: string) =>
    fetchSitesApi<{ status: string; appliance_id: string; site_id: string }>(
      `/sites/${siteId}/appliances/${applianceId}`,
      { method: 'DELETE' }
    ),

  // Clear stale appliances
  clearStaleAppliances: (siteId: string, staleHours: number = 24) =>
    fetchSitesApi<ClearStaleResponse>(`/sites/${siteId}/appliances/clear-stale`, {
      method: 'POST',
      body: JSON.stringify({ stale_hours: staleHours }),
    }),

  // Update L2 healing mode for an appliance
  updateL2Mode: (siteId: string, applianceId: string, l2Mode: string) =>
    fetchSitesApi<{ status: string; appliance_id: string; l2_mode: string }>(
      `/sites/${siteId}/appliances/${applianceId}/l2-mode`,
      { method: 'PATCH', body: JSON.stringify({ l2_mode: l2Mode }) }
    ),
};

// =============================================================================
// NOTIFICATIONS API
// =============================================================================

import type { Notification, NotificationSummary } from '../types';

export const notificationsApi = {
  getNotifications: (params?: {
    site_id?: string;
    severity?: string;
    unread_only?: boolean;
    limit?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.site_id) searchParams.set('site_id', params.site_id);
    if (params?.severity) searchParams.set('severity', params.severity);
    if (params?.unread_only) searchParams.set('unread_only', 'true');
    if (params?.limit) searchParams.set('limit', String(params.limit));

    const query = searchParams.toString();
    return fetchApi<Notification[]>(`/notifications${query ? `?${query}` : ''}`);
  },

  getSummary: () => fetchApi<NotificationSummary>('/notifications/summary'),

  markRead: (notificationId: string) =>
    fetchApi<{ status: string }>(`/notifications/${notificationId}/read`, {
      method: 'POST',
    }),

  markAllRead: () =>
    fetchApi<{ status: string; marked_count: number }>('/notifications/read-all', {
      method: 'POST',
    }),

  dismiss: (notificationId: string) =>
    fetchApi<{ status: string }>(`/notifications/${notificationId}/dismiss`, {
      method: 'POST',
    }),

  create: (notification: {
    severity: 'critical' | 'warning' | 'info' | 'success';
    category: string;
    title: string;
    message: string;
    site_id?: string;
    appliance_id?: string;
    metadata?: Record<string, unknown>;
  }) =>
    fetchApi<Notification>('/notifications', {
      method: 'POST',
      body: JSON.stringify(notification),
    }),
};

// =============================================================================
// RUNBOOK CONFIG API (Partner-configurable runbook enable/disable)
// =============================================================================

export interface RunbookCatalogItem {
  id: string;
  name: string;
  description: string | null;
  category: string;
  check_type: string;
  severity: string;
  is_disruptive: boolean;
  requires_maintenance_window: boolean;
  hipaa_controls: string[];
  version: string;
}

export interface SiteRunbookConfig {
  runbook_id: string;
  name: string;
  description: string | null;
  category: string;
  severity: string;
  is_disruptive: boolean;
  enabled: boolean;
  modified_by: string | null;
  modified_at: string | null;
}

export const runbookConfigApi = {
  // Get all runbooks in the catalog
  getRunbooks: () => fetchSitesApi<RunbookCatalogItem[]>('/runbooks'),

  // Get runbook categories - returns [{category, count}, ...], extract just categories
  getCategories: async () => {
    const data = await fetchSitesApi<Array<{ category: string; count: number }>>('/runbooks/categories');
    return data.map((item) => item.category);
  },

  // Get site's runbook configuration
  getSiteRunbooks: (siteId: string) =>
    fetchSitesApi<SiteRunbookConfig[]>(`/runbooks/sites/${siteId}`),

  // Enable/disable a runbook for a site
  setSiteRunbook: (siteId: string, runbookId: string, enabled: boolean) =>
    fetchSitesApi<{ status: string }>(`/runbooks/sites/${siteId}/${runbookId}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),

  // Get effective enabled runbooks for an appliance
  getApplianceEffective: (applianceId: string) =>
    fetchSitesApi<string[]>(`/runbooks/appliances/${encodeURIComponent(applianceId)}/effective`),
};

// =============================================================================
// USERS API (RBAC User Management)
// =============================================================================

export interface AdminUser {
  id: string;
  username: string;
  email: string | null;
  display_name: string | null;
  role: 'admin' | 'operator' | 'readonly';
  status: 'active' | 'disabled';
  last_login: string | null;
  created_at: string;
}

export interface UserInvite {
  id: string;
  email: string;
  role: string;
  display_name: string | null;
  status: 'pending' | 'accepted' | 'expired' | 'revoked';
  invited_by: string | null;
  invited_by_name: string | null;
  expires_at: string;
  created_at: string;
}

export interface InviteValidation {
  valid: boolean;
  email?: string;
  role?: string;
  display_name?: string;
  error?: string;
}

export const usersApi = {
  // Get all users (admin only)
  getUsers: () => fetchSitesApi<AdminUser[]>('/users'),

  // Get pending invites (admin only)
  getInvites: () => fetchSitesApi<UserInvite[]>('/users/invites'),

  // Invite a new user (admin only)
  inviteUser: (data: { email: string; role: string; display_name?: string }) =>
    fetchSitesApi<UserInvite>('/users/invite', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Resend invite email (admin only)
  resendInvite: (inviteId: string) =>
    fetchSitesApi<{ status: string }>(`/users/invite/${inviteId}/resend`, {
      method: 'POST',
    }),

  // Revoke pending invite (admin only)
  revokeInvite: (inviteId: string) =>
    fetchSitesApi<{ status: string }>(`/users/invite/${inviteId}`, {
      method: 'DELETE',
    }),

  // Update user (admin only)
  updateUser: (userId: string, data: { role?: string; status?: string; display_name?: string }) =>
    fetchSitesApi<AdminUser>(`/users/${userId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Delete user (admin only)
  deleteUser: (userId: string) =>
    fetchSitesApi<{ status: string }>(`/users/${userId}`, {
      method: 'DELETE',
    }),

  // Admin reset user password
  adminResetPassword: (userId: string, newPassword: string) =>
    fetchSitesApi<{ status: string }>(`/users/${userId}/password`, {
      method: 'PUT',
      body: JSON.stringify({ new_password: newPassword }),
    }),

  // Get current user profile
  getProfile: () => fetchSitesApi<AdminUser>('/users/me'),

  // Change own password
  changePassword: (data: { current_password: string; new_password: string; confirm_password: string }) =>
    fetchSitesApi<{ status: string }>('/users/me/password', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Validate invite token (public - no auth)
  validateInvite: async (token: string): Promise<InviteValidation> => {
    const response = await fetch(`/api/users/invite/validate/${token}`);
    if (!response.ok) {
      return { valid: false, error: 'Failed to validate invite' };
    }
    return response.json();
  },

  // Accept invite and set password (public - no auth)
  acceptInvite: async (data: { token: string; password: string; confirm_password: string }) => {
    const response = await fetch('/api/users/invite/accept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new ApiError(response.status, error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
};

// =============================================================================
// FRAMEWORKS API (Multi-Framework Compliance)
// =============================================================================

import type {
  ComplianceFramework,
  FrameworkConfig,
  FrameworkScore,
  FrameworkMetadata,
  IndustryRecommendation,
} from '../types';

export const frameworksApi = {
  // Get framework configuration for an appliance
  getConfig: (applianceId: string) =>
    fetchSitesApi<FrameworkConfig>(`/frameworks/appliances/${encodeURIComponent(applianceId)}/config`),

  // Update framework configuration
  updateConfig: (applianceId: string, config: {
    enabled_frameworks: ComplianceFramework[];
    primary_framework: ComplianceFramework;
    industry?: string;
    framework_metadata?: Record<string, Record<string, unknown>>;
  }) =>
    fetchSitesApi<FrameworkConfig>(`/frameworks/appliances/${encodeURIComponent(applianceId)}/config`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),

  // Get compliance scores for all enabled frameworks
  getScores: (applianceId: string) =>
    fetchSitesApi<FrameworkScore[]>(`/frameworks/appliances/${encodeURIComponent(applianceId)}/scores`),

  // Get control status for a specific framework
  getControls: (applianceId: string, framework: ComplianceFramework) =>
    fetchSitesApi<Record<string, string>>(
      `/frameworks/appliances/${encodeURIComponent(applianceId)}/controls/${framework}`
    ),

  // Refresh compliance scores
  refreshScores: (applianceId: string) =>
    fetchSitesApi<{ status: string; scores: FrameworkScore[] }>(
      `/frameworks/appliances/${encodeURIComponent(applianceId)}/scores/refresh`,
      { method: 'POST' }
    ),

  // Get metadata for all frameworks
  getMetadata: () => fetchSitesApi<Record<ComplianceFramework, FrameworkMetadata>>('/frameworks/metadata'),

  // Get industry recommendations
  getIndustries: () => fetchSitesApi<Record<string, IndustryRecommendation>>('/frameworks/industries'),
};

// =============================================================================
// WORKSTATIONS API (Site workstation compliance monitoring)
// =============================================================================

import type {
  Workstation,
  SiteWorkstationSummary,
} from '../types';

export interface SiteWorkstationsResponse {
  summary: SiteWorkstationSummary | null;
  workstations: Workstation[];
}

export const workstationsApi = {
  // Get all workstations for a site with summary
  getSiteWorkstations: (siteId: string) =>
    fetchSitesApi<SiteWorkstationsResponse>(`/sites/${siteId}/workstations`),

  // Get a single workstation's details
  getWorkstation: (siteId: string, workstationId: string) =>
    fetchSitesApi<Workstation>(`/sites/${siteId}/workstations/${workstationId}`),

  // Trigger a workstation scan
  triggerScan: (siteId: string) =>
    fetchSitesApi<{ status: string; message: string }>(`/sites/${siteId}/workstations/scan`, {
      method: 'POST',
    }),
};

// =============================================================================
// RMM COMPARISON API
// =============================================================================

export interface RMMDevice {
  hostname: string;
  device_id?: string;
  ip_address?: string;
  mac_address?: string;
  os_name?: string;
  serial_number?: string;
}

export interface RMMCompareRequest {
  provider: 'connectwise' | 'datto' | 'ninja' | 'syncro' | 'manual';
  devices: RMMDevice[];
}

export interface RMMMatch {
  our_hostname: string;
  rmm_device: RMMDevice | null;
  confidence: 'exact' | 'high' | 'medium' | 'low' | 'no_match';
  confidence_score: number;
  matching_fields: string[];
}

export interface RMMGap {
  gap_type: 'missing_from_rmm' | 'missing_from_ad' | 'stale_rmm' | 'stale_ad';
  device: Record<string, unknown>;
  recommendation: string;
  severity: 'high' | 'medium' | 'low';
}

export interface RMMComparisonReport {
  summary: {
    our_device_count: number;
    rmm_device_count: number;
    matched_count: number;
    exact_match_count: number;
    coverage_rate: number;
  };
  matches: RMMMatch[];
  gaps: RMMGap[];
  metadata: {
    provider: string;
    comparison_timestamp: string;
  };
}

export interface RMMComparisonResponse {
  site_id: string;
  provider: string;
  summary: RMMComparisonReport['summary'];
  report: RMMComparisonReport;
  created_at: string | null;
  error?: string;
  message?: string;
}

export const rmmComparisonApi = {
  // Get the latest RMM comparison report for a site
  getReport: (siteId: string) =>
    fetchSitesApi<RMMComparisonResponse>(`/sites/${siteId}/workstations/rmm-compare`),

  // Upload RMM data and compare with workstations
  compare: (siteId: string, data: RMMCompareRequest) =>
    fetchSitesApi<RMMComparisonReport>(`/sites/${siteId}/workstations/rmm-compare`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

// =============================================================================
// GO AGENTS API (Workstation-scale gRPC agents)
// =============================================================================

import type {
  GoAgent,
  SiteGoAgentSummary,
} from '../types';

export interface SiteGoAgentsResponse {
  summary: SiteGoAgentSummary | null;
  agents: GoAgent[];
}

export const goAgentsApi = {
  // Get all Go agents for a site with summary
  getSiteAgents: (siteId: string) =>
    fetchSitesApi<SiteGoAgentsResponse>(`/sites/${siteId}/agents`),

  // Get a single Go agent's details
  getAgent: (siteId: string, agentId: string) =>
    fetchSitesApi<GoAgent>(`/sites/${siteId}/agents/${agentId}`),

  // Get Go agent summary for a site
  getSummary: (siteId: string) =>
    fetchSitesApi<SiteGoAgentSummary>(`/sites/${siteId}/agents/summary`),

  // Update Go agent capability tier
  updateTier: (siteId: string, agentId: string, tier: 'monitor_only' | 'self_heal' | 'full_remediation') =>
    fetchSitesApi<{ status: string }>(`/sites/${siteId}/agents/${agentId}/tier`, {
      method: 'PUT',
      body: JSON.stringify({ capability_tier: tier }),
    }),

  // Trigger drift check on a Go agent
  triggerCheck: (siteId: string, agentId: string) =>
    fetchSitesApi<{ status: string; message: string }>(`/sites/${siteId}/agents/${agentId}/check`, {
      method: 'POST',
    }),

  // Remove a Go agent from registry
  removeAgent: (siteId: string, agentId: string) =>
    fetchSitesApi<{ status: string }>(`/sites/${siteId}/agents/${agentId}`, {
      method: 'DELETE',
    }),
};

// =============================================================================
// DEPLOYMENT API
// =============================================================================

import type { DeploymentStatus } from '../types';

export const deploymentApi = {
  getStatus: (siteId: string) => fetchSitesApi<DeploymentStatus>(`/sites/${siteId}/deployment-status`),
};

// =============================================================================
// FLEET UPDATES API
// =============================================================================

export interface FleetRelease {
  id: string;
  version: string;
  iso_url: string;
  sha256: string;
  size_bytes: number | null;
  release_notes: string | null;
  agent_version: string | null;
  created_at: string;
  is_active: boolean;
  is_latest: boolean;
}

export interface FleetRollout {
  id: string;
  release_id: string;
  version: string;
  name: string | null;
  strategy: string;
  current_stage: number;
  stages: Array<{ percent: number; delay_hours: number }>;
  maintenance_window: {
    start: string;
    end: string;
    timezone: string;
    days: string[];
  };
  status: string;
  started_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  failure_threshold_percent: number;
  auto_rollback: boolean;
  progress: {
    total: number;
    succeeded: number;
    failed: number;
    rolled_back: number;
    pending: number;
    in_progress: number;
    success_rate: number;
  } | null;
}

export interface FleetStats {
  releases: {
    total: number;
    active: number;
    latest_version: string | null;
  };
  fleet: {
    deployed_version: string | null;
    active_appliances: number;
  };
  rollouts: {
    total: number;
    in_progress: number;
    paused: number;
    completed: number;
  };
  appliance_updates_30d: {
    total: number;
    succeeded: number;
    failed: number;
    rolled_back: number;
    success_rate: number;
  };
}

export const fleetUpdatesApi = {
  // Stats
  getStats: () => fetchSitesApi<FleetStats>('/fleet/stats'),

  // Releases
  getReleases: (activeOnly = true) =>
    fetchSitesApi<FleetRelease[]>(`/fleet/releases?active_only=${activeOnly}`),

  createRelease: (release: {
    version: string;
    iso_url: string;
    sha256: string;
    release_notes?: string;
    agent_version?: string;
  }) =>
    fetchSitesApi<FleetRelease>('/fleet/releases', {
      method: 'POST',
      body: JSON.stringify(release),
    }),

  setLatest: (version: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/releases/${version}/latest`, {
      method: 'PUT',
    }),

  // Rollouts
  getRollouts: (status?: string) =>
    fetchSitesApi<FleetRollout[]>(`/fleet/rollouts${status ? `?status=${status}` : ''}`),

  createRollout: (data: {
    release_id: string;
    strategy?: string;
    stages?: Array<{ percent: number; delay_hours: number }>;
  }) =>
    fetchSitesApi<FleetRollout>('/fleet/rollouts', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  pauseRollout: (rolloutId: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/rollouts/${rolloutId}/pause`, {
      method: 'POST',
    }),

  resumeRollout: (rolloutId: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/rollouts/${rolloutId}/resume`, {
      method: 'POST',
    }),

  advanceRollout: (rolloutId: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/rollouts/${rolloutId}/advance`, {
      method: 'POST',
    }),

  cancelRollout: (rolloutId: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/rollouts/${rolloutId}/cancel`, {
      method: 'POST',
    }),

  deleteRelease: (version: string) =>
    fetchSitesApi<{ status: string }>(`/fleet/releases/${version}`, {
      method: 'DELETE',
    }),
};

// =============================================================================
// DEVICES API (Network scanner device inventory)
// =============================================================================

export interface DiscoveredDevice {
  id: number;
  appliance_id: number;
  local_device_id: string;
  hostname: string | null;
  ip_address: string;
  mac_address: string | null;
  device_type: string;
  os_name: string | null;
  os_version: string | null;
  medical_device: boolean;
  scan_policy: string;
  manually_opted_in: boolean;
  compliance_status: string;
  open_ports: number[];
  discovery_source: string;
  first_seen_at: string;
  last_seen_at: string;
  last_scan_at: string | null;
  sync_created_at: string;
  sync_updated_at: string;
  appliance_hostname: string;
  site_id: string;
}

export interface DeviceCounts {
  total: number;
  compliant: number;
  drifted: number;
  unknown: number;
  medical: number;
  workstations: number;
  servers: number;
  network_devices: number;
  printers: number;
}

export interface SiteDevicesResponse {
  site_id: string;
  devices: DiscoveredDevice[];
  counts: DeviceCounts;
  pagination: {
    limit: number;
    offset: number;
    total: number;
  };
}

export interface SiteDeviceSummary {
  site_id: string;
  total_devices: number;
  compliance_rate: number;
  by_compliance: {
    compliant: number;
    drifted: number;
    unknown: number;
  };
  by_type: {
    workstations: number;
    servers: number;
    network: number;
    printers: number;
  };
  medical_devices: {
    total: number;
    excluded_by_default: boolean;
  };
}

export const devicesApi = {
  // Get all devices for a site with filters
  getSiteDevices: (
    siteId: string,
    params?: {
      device_type?: string;
      compliance_status?: string;
      include_medical?: boolean;
      limit?: number;
      offset?: number;
    }
  ) => {
    const query = new URLSearchParams();
    if (params?.device_type) query.set('device_type', params.device_type);
    if (params?.compliance_status) query.set('compliance_status', params.compliance_status);
    if (params?.include_medical !== undefined) query.set('include_medical', String(params.include_medical));
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    const queryString = query.toString();
    return fetchSitesApi<SiteDevicesResponse>(`/devices/sites/${siteId}${queryString ? `?${queryString}` : ''}`);
  },

  // Get device summary for a site
  getSiteSummary: (siteId: string) =>
    fetchSitesApi<SiteDeviceSummary>(`/devices/sites/${siteId}/summary`),

  // Get medical devices for a site
  getMedicalDevices: (siteId: string, limit?: number, offset?: number) => {
    const query = new URLSearchParams();
    if (limit) query.set('limit', String(limit));
    if (offset) query.set('offset', String(offset));
    const queryString = query.toString();
    return fetchSitesApi<{
      site_id: string;
      medical_devices: DiscoveredDevice[];
      total: number;
      note: string;
    }>(`/devices/sites/${siteId}/medical${queryString ? `?${queryString}` : ''}`);
  },
};

// =============================================================================
// CVE WATCH API
// =============================================================================

import type { CVESummary, CVEEntry, CVEDetail, CVEWatchConfig } from '../types';

const CVE_BASE = '/api/cve-watch';

async function fetchCveApi<T>(endpoint: string, options?: FetchApiOptions): Promise<T> {
  return _fetchWithBase<T>(CVE_BASE, endpoint, options);
}

export const cveApi = {
  getSummary: () => fetchCveApi<CVESummary>('/summary'),

  getCVEs: (params?: { severity?: string; status?: string; search?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.severity) query.set('severity', params.severity);
    if (params?.status) query.set('status', params.status);
    if (params?.search) query.set('search', params.search);
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    const queryString = query.toString();
    return fetchCveApi<CVEEntry[]>(`/cves${queryString ? `?${queryString}` : ''}`);
  },

  getCVE: (cveId: string) => fetchCveApi<CVEDetail>(`/cves/${cveId}`),

  triggerSync: () => fetchCveApi<{ status: string }>('/sync', { method: 'POST' }),

  updateStatus: (cveId: string, status: string, notes?: string) =>
    fetchCveApi<{ status: string }>(`/cves/${cveId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status, notes }),
      headers: { 'Content-Type': 'application/json' },
    }),

  getConfig: () => fetchCveApi<CVEWatchConfig>('/config'),

  updateConfig: (config: Partial<CVEWatchConfig>) =>
    fetchCveApi<CVEWatchConfig>('/config', {
      method: 'PUT',
      body: JSON.stringify(config),
      headers: { 'Content-Type': 'application/json' },
    }),
};

// =============================================================================
// FRAMEWORK SYNC API (Compliance Library)
// =============================================================================

import type { FrameworkSyncStatus, FrameworkControl, FrameworkCrosswalk, CoverageAnalysis, FrameworkCategory } from '../types';

const FRAMEWORK_SYNC_BASE = '/api/framework-sync';

async function fetchFrameworkSyncApi<T>(endpoint: string, options?: FetchApiOptions): Promise<T> {
  return _fetchWithBase<T>(FRAMEWORK_SYNC_BASE, endpoint, options);
}

export const frameworkSyncApi = {
  getStatus: () => fetchFrameworkSyncApi<FrameworkSyncStatus[]>('/status'),

  getControls: (framework: string, params?: { category?: string; search?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params?.category) query.set('category', params.category);
    if (params?.search) query.set('search', params.search);
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    const queryString = query.toString();
    return fetchFrameworkSyncApi<FrameworkControl[]>(`/controls/${framework}${queryString ? `?${queryString}` : ''}`);
  },

  getCrosswalks: (framework: string) =>
    fetchFrameworkSyncApi<FrameworkCrosswalk[]>(`/crosswalks/${framework}`),

  getCoverage: () => fetchFrameworkSyncApi<CoverageAnalysis>('/coverage'),

  getCategories: (framework: string) =>
    fetchFrameworkSyncApi<FrameworkCategory[]>(`/categories/${framework}`),

  triggerSync: () => fetchFrameworkSyncApi<{ status: string }>('/sync', { method: 'POST' }),

  syncFramework: (framework: string) =>
    fetchFrameworkSyncApi<{ status: string }>(`/sync/${framework}`, { method: 'POST' }),
};

export { ApiError };
