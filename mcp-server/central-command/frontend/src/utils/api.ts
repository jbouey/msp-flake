/**
 * API client for Central Command Dashboard
 */

const API_BASE = '/api/dashboard';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${endpoint}`;

  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new ApiError(response.status, error.detail || `HTTP ${response.status}`);
  }

  return response.json();
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

import type { Incident, IncidentDetail } from '../types';

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
}

export interface SiteCredential {
  id: string;
  credential_type: string;
  credential_name: string;
  created_at: string | null;
}

async function fetchSitesApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `/api${endpoint}`;

  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new ApiError(response.status, error.detail || `HTTP ${response.status}`);
  }

  return response.json();
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
};

export { ApiError };
