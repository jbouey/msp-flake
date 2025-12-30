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
  getPipeline: () => fetchApi<OnboardingClient[]>('/onboarding'),

  getMetrics: () => fetchApi<OnboardingMetrics>('/onboarding/metrics'),

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

export { ApiError };
