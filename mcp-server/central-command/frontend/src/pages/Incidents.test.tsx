import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WebSocketContext } from '../hooks/useWebSocket';

// Mock the API module — must match the full shape used by hooks
vi.mock('../utils/api', () => ({
  fleetApi: { getFleet: vi.fn(), getClient: vi.fn() },
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

// Mock IncidentRow since it has its own internal complexity
vi.mock('../components/incidents/IncidentRow', () => ({
  IncidentRow: ({ incident }: { incident: { id: number; hostname: string; check_type: string } }) =>
    React.createElement('div', { 'data-testid': `incident-${incident.id}` },
      `${incident.hostname} - ${incident.check_type}`
    ),
}));

import { incidentApi, sitesApi } from '../utils/api';
import { Incidents } from './Incidents';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });

  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(
      WebSocketContext.Provider,
      { value: { connected: false } },
      React.createElement(QueryClientProvider, { client: queryClient }, children)
    );
}

describe('Incidents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Sites selector needs data
    vi.mocked(sitesApi.getSites).mockResolvedValue({
      sites: [], count: 0, total: 0, limit: 200, offset: 0, stats: {},
    });
  });

  it('shows loading state initially', () => {
    vi.mocked(incidentApi.getIncidents).mockReturnValue(new Promise(() => {}));

    render(<Incidents />, { wrapper: createWrapper() });

    expect(screen.getByText(/loading incidents/i)).toBeInTheDocument();
  });

  it('renders incidents after data loads', async () => {
    const mockIncidents = [
      { id: 1, site_id: 's1', hostname: 'dc01', check_type: 'service_stopped', severity: 'high', resolved: false, hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
      { id: 2, site_id: 's1', hostname: 'ws01', check_type: 'backup_failed', severity: 'medium', resolved: true, resolved_at: '2026-03-01T01:00:00Z', hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
    ];
    vi.mocked(incidentApi.getIncidents).mockResolvedValue(mockIncidents as any);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId('incident-1')).toBeInTheDocument();
      expect(screen.getByTestId('incident-2')).toBeInTheDocument();
    });
  });

  it('shows incident count in subtitle', async () => {
    const mockIncidents = [
      { id: 1, site_id: 's1', hostname: 'dc01', check_type: 'svc', severity: 'high', resolved: false, hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
      { id: 2, site_id: 's1', hostname: 'ws01', check_type: 'bak', severity: 'low', resolved: true, resolved_at: '2026-03-01T01:00:00Z', hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
    ];
    vi.mocked(incidentApi.getIncidents).mockResolvedValue(mockIncidents as any);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/2 incidents/)).toBeInTheDocument();
    });
  });

  it('shows error state when API fails', async () => {
    vi.mocked(incidentApi.getIncidents).mockRejectedValue(new Error('Server unavailable'));

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('Server unavailable')).toBeInTheDocument();
    });
  });

  it('renders filter buttons', async () => {
    const mockIncidents = [
      { id: 1, site_id: 's1', hostname: 'dc01', check_type: 'svc', severity: 'high', resolved: false, hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
      { id: 2, site_id: 's1', hostname: 'ws01', check_type: 'bak', severity: 'low', resolved: true, resolved_at: '2026-03-01T01:00:00Z', hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
      { id: 3, site_id: 's1', hostname: 'ws02', check_type: 'fw', severity: 'high', resolved: false, hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
    ];
    vi.mocked(incidentApi.getIncidents).mockResolvedValue(mockIncidents as any);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('All')).toBeInTheDocument();
      // Active/Resolved counts are computed from returned data
      expect(screen.getByText(/Active/)).toBeInTheDocument();
      expect(screen.getByText(/Resolved/)).toBeInTheDocument();
    });
  });

  it('calls API with resolved=false when Active filter clicked', async () => {
    const user = userEvent.setup();
    const mockIncidents = [
      { id: 1, site_id: 's1', hostname: 'dc01', check_type: 'svc', severity: 'high', resolved: false, hipaa_controls: [], created_at: '2026-03-01T00:00:00Z' },
    ];
    vi.mocked(incidentApi.getIncidents).mockResolvedValue(mockIncidents as any);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId('incident-1')).toBeInTheDocument();
    });

    await user.click(screen.getByText(/Active/));

    // Server-side filtering: API called with resolved=false
    await waitFor(() => {
      const calls = vi.mocked(incidentApi.getIncidents).mock.calls;
      const lastCall = calls[calls.length - 1]?.[0];
      expect(lastCall).toMatchObject({ resolved: false });
    });
  });

  it('shows empty state message when no incidents exist', async () => {
    vi.mocked(incidentApi.getIncidents).mockResolvedValue([]);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/no incidents to display/i)).toBeInTheDocument();
      expect(screen.getByText(/all systems operating normally/i)).toBeInTheDocument();
    });
  });

  it('shows site selector dropdown', async () => {
    vi.mocked(incidentApi.getIncidents).mockResolvedValue([]);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('All Sites')).toBeInTheDocument();
    });
  });

  it('shows level filter buttons', async () => {
    vi.mocked(incidentApi.getIncidents).mockResolvedValue([]);

    render(<Incidents />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('All Levels')).toBeInTheDocument();
      expect(screen.getByText('L1')).toBeInTheDocument();
      expect(screen.getByText('L2')).toBeInTheDocument();
      expect(screen.getByText('L3')).toBeInTheDocument();
    });
  });
});
