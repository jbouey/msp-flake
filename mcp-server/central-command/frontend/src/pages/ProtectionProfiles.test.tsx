import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// Track navigate calls
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock the full API module
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
  protectionProfilesApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    discover: vi.fn(),
    lockBaseline: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    toggleAsset: vi.fn(),
    createFromTemplate: vi.fn(),
    listTemplates: vi.fn(),
  },
}));

// Mock Spinner for simplicity
vi.mock('../components/shared', () => ({
  GlassCard: ({ children, padding }: { children: React.ReactNode; padding?: string }) =>
    React.createElement('div', { 'data-testid': 'glass-card', 'data-padding': padding }, children),
  Spinner: () => React.createElement('div', { 'data-testid': 'spinner' }, 'Loading...'),
}));

import { protectionProfilesApi } from '../utils/api';
import { ProtectionProfiles } from './ProtectionProfiles';

function createWrapper(siteId = 'site-123') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(
      MemoryRouter,
      { initialEntries: [`/sites/${siteId}/protection`] },
      React.createElement(
        QueryClientProvider,
        { client: queryClient },
        React.createElement(
          Routes,
          null,
          React.createElement(Route, {
            path: '/sites/:siteId/protection',
            element: children,
          })
        )
      )
    );
}

const sampleProfiles = [
  {
    id: 'prof-1',
    site_id: 'site-123',
    name: 'Epic EHR',
    description: 'Electronic health records system',
    status: 'active',
    created_by: 'admin',
    created_at: '2026-03-01T00:00:00Z',
    updated_at: '2026-03-01T00:00:00Z',
    asset_count: 12,
    rule_count: 8,
    enabled_asset_count: 10,
  },
  {
    id: 'prof-2',
    site_id: 'site-123',
    name: 'Dentrix',
    description: null,
    status: 'draft',
    created_by: 'admin',
    created_at: '2026-03-02T00:00:00Z',
    updated_at: '2026-03-02T00:00:00Z',
    asset_count: 0,
    rule_count: 0,
    enabled_asset_count: 0,
  },
];

describe('ProtectionProfiles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(protectionProfilesApi.listTemplates).mockResolvedValue([]);
  });

  it('shows loading spinner initially', () => {
    vi.mocked(protectionProfilesApi.list).mockReturnValue(new Promise(() => {}));

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    expect(screen.getByTestId('spinner')).toBeInTheDocument();
  });

  it('renders profile cards after loading', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('Epic EHR')).toBeInTheDocument();
      expect(screen.getByText('Dentrix')).toBeInTheDocument();
    });
  });

  it('shows page heading and description', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('App Protection Profiles')).toBeInTheDocument();
      expect(screen.getByText(/protect proprietary applications/i)).toBeInTheDocument();
    });
  });

  it('shows status badge on profile cards', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('active')).toBeInTheDocument();
      expect(screen.getByText('draft')).toBeInTheDocument();
    });
  });

  it('shows asset and rule counts on profile cards', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('10/12 assets')).toBeInTheDocument();
      expect(screen.getByText('8 rules')).toBeInTheDocument();
    });
  });

  it('shows profile description when present', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('Electronic health records system')).toBeInTheDocument();
    });
  });

  it('shows empty state when no profiles exist', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue([]);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('No Protection Profiles')).toBeInTheDocument();
      expect(screen.getByText(/create a profile to protect/i)).toBeInTheDocument();
    });
  });

  it('shows "Create First Profile" button in empty state', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue([]);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/create first profile/i)).toBeInTheDocument();
    });
  });

  it('has a "+ New Profile" button in header', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('+ New Profile')).toBeInTheDocument();
    });
  });

  it('shows create form when "+ New Profile" is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('+ New Profile')).toBeInTheDocument();
    });

    await user.click(screen.getByText('+ New Profile'));

    expect(screen.getByText('Create Protection Profile')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/e\.g\. Epic EHR/i)).toBeInTheDocument();
    expect(screen.getByText('Create')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('hides create form when Cancel is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('+ New Profile')).toBeInTheDocument();
    });

    await user.click(screen.getByText('+ New Profile'));
    expect(screen.getByText('Create Protection Profile')).toBeInTheDocument();

    await user.click(screen.getByText('Cancel'));
    expect(screen.queryByText('Create Protection Profile')).not.toBeInTheDocument();
  });

  it('Create button is disabled when name is empty', async () => {
    const user = userEvent.setup();
    vi.mocked(protectionProfilesApi.list).mockResolvedValue([]);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/create first profile/i)).toBeInTheDocument();
    });

    await user.click(screen.getByText(/create first profile/i));

    // The Create button should be disabled since name is empty
    const createBtn = screen.getByRole('button', { name: 'Create' });
    expect(createBtn).toBeDisabled();
  });

  it('has a back-to-site link', async () => {
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/back to site/i)).toBeInTheDocument();
    });
  });

  it('navigates to profile detail on card click', async () => {
    const user = userEvent.setup();
    vi.mocked(protectionProfilesApi.list).mockResolvedValue(sampleProfiles as any);

    render(<ProtectionProfiles />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('Epic EHR')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Epic EHR'));

    expect(mockNavigate).toHaveBeenCalledWith('/sites/site-123/protection/prof-1');
  });
});
