import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WebSocketContext } from './useWebSocket';

// Mock the API module
vi.mock('../utils/api', () => ({
  fleetApi: {
    getFleet: vi.fn(),
    getClient: vi.fn(),
  },
  incidentApi: { getIncidents: vi.fn(), getEvents: vi.fn() },
  statsApi: { getGlobalStats: vi.fn() },
  learningApi: { getStatus: vi.fn(), getCandidates: vi.fn(), getCoverageGaps: vi.fn(), getHistory: vi.fn(), promote: vi.fn(), reject: vi.fn() },
  runbookApi: { getRunbooks: vi.fn(), getRunbook: vi.fn(), getExecutions: vi.fn() },
  onboardingApi: { getPipeline: vi.fn(), getMetrics: vi.fn() },
  sitesApi: { getSites: vi.fn(), getSite: vi.fn(), createSite: vi.fn(), updateSite: vi.fn(), addCredential: vi.fn(), deleteSite: vi.fn(), deleteCredential: vi.fn(), updateHealingTier: vi.fn() },
  ordersApi: { getOrders: vi.fn(), createApplianceOrder: vi.fn(), broadcastOrder: vi.fn(), deleteAppliance: vi.fn(), clearStaleAppliances: vi.fn(), updateL2Mode: vi.fn() },
  notificationsApi: { getNotifications: vi.fn(), getSummary: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn(), dismiss: vi.fn(), create: vi.fn() },
  runbookConfigApi: { getRunbooks: vi.fn(), getCategories: vi.fn(), getSiteRunbooks: vi.fn(), setSiteRunbook: vi.fn() },
  workstationsApi: { getSiteWorkstations: vi.fn(), triggerScan: vi.fn() },
  goAgentsApi: { getSiteAgents: vi.fn(), updateTier: vi.fn(), triggerCheck: vi.fn(), removeAgent: vi.fn() },
  devicesApi: { getSiteDevices: vi.fn(), getSiteSummary: vi.fn(), getMedicalDevices: vi.fn() },
  cveApi: { getSummary: vi.fn(), getCVEs: vi.fn(), getCVE: vi.fn(), triggerSync: vi.fn(), updateStatus: vi.fn(), getConfig: vi.fn() },
  frameworkSyncApi: { getStatus: vi.fn(), getControls: vi.fn(), getCategories: vi.fn(), getCoverage: vi.fn(), triggerSync: vi.fn(), syncFramework: vi.fn() },
  commandCenterApi: { getFleetPosture: vi.fn(), getIncidentTrends: vi.fn(), getIncidentBreakdown: vi.fn(), getAttentionRequired: vi.fn() },
}));

import { useFleet, useClient } from './useFleet';
import { fleetApi } from '../utils/api';
import type { ClientOverview, ClientDetail } from '../types';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(
      WebSocketContext.Provider,
      { value: { connected: false } },
      React.createElement(QueryClientProvider, { client: queryClient }, children)
    );
}

describe('useFleet', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls fleetApi.getFleet and returns data', async () => {
    const mockData = [
      { site_id: 'site-1', name: 'North Valley', health: 'healthy' },
      { site_id: 'site-2', name: 'South Valley', health: 'warning' },
    ];
    vi.mocked(fleetApi.getFleet).mockResolvedValue(mockData as unknown as ClientOverview[]);

    const { result } = renderHook(() => useFleet(), { wrapper: createWrapper() });

    // Initially loading
    expect(result.current.isLoading).toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(mockData);
    expect(fleetApi.getFleet).toHaveBeenCalledOnce();
  });

  it('returns error state when API fails', async () => {
    vi.mocked(fleetApi.getFleet).mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useFleet(), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(result.current.error).toBeDefined();
    expect(result.current.error!.message).toBe('Network error');
  });

  it('starts in loading state', () => {
    vi.mocked(fleetApi.getFleet).mockReturnValue(new Promise(() => {})); // never resolves

    const { result } = renderHook(() => useFleet(), { wrapper: createWrapper() });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });
});

describe('useClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not fetch when siteId is null (disabled)', () => {
    vi.mocked(fleetApi.getClient).mockResolvedValue({} as unknown as ClientDetail);

    const { result } = renderHook(() => useClient(null), { wrapper: createWrapper() });

    expect(result.current.fetchStatus).toBe('idle');
    expect(fleetApi.getClient).not.toHaveBeenCalled();
  });

  it('fetches when siteId is provided', async () => {
    const mockClient = { site_id: 'site-1', name: 'Test', appliances: [] };
    vi.mocked(fleetApi.getClient).mockResolvedValue(mockClient as unknown as ClientDetail);

    const { result } = renderHook(() => useClient('site-1'), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(mockClient);
    expect(fleetApi.getClient).toHaveBeenCalledWith('site-1');
  });
});
