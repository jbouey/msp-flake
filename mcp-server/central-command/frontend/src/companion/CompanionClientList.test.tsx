import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Track navigate calls
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock companion API hooks
const mockUseCompanionClients = vi.fn();
const mockUseCompanionAlertSummary = vi.fn();

vi.mock('./useCompanionApi', () => ({
  useCompanionClients: () => mockUseCompanionClients(),
  useCompanionAlertSummary: () => mockUseCompanionAlertSummary(),
}));

// Mock Spinner to simplify loading detection
vi.mock('../components/shared', () => ({
  Spinner: ({ size }: { size?: string }) =>
    React.createElement('div', { 'data-testid': 'spinner', 'data-size': size }, 'Loading...'),
}));

import { CompanionClientList } from './CompanionClientList';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(QueryClientProvider, { client: queryClient }, children)
    );
}

const sampleClients = [
  {
    id: 'org-1',
    name: 'North Valley Medical',
    practice_type: 'Dental',
    provider_count: 3,
    overview: {
      overall_readiness: 72,
      sra: { status: 'completed' },
      policies: { active: 5, total: 10, review_due: 0 },
      training: { overdue: 0, compliant: 4, total_employees: 4 },
      baas: { expiring_soon: 0, active: 2, total: 2 },
      ir_plan: { status: 'active' },
      contingency: { plans: 1, all_tested: true },
      workforce: { pending_termination: 0, active: 4 },
      physical: { gaps: 0, assessed: 3 },
      officers: { privacy_officer: 'Dr. Smith', security_officer: 'Jane' },
      gap_analysis: { completion: 92 },
    },
  },
  {
    id: 'org-2',
    name: 'South Valley Ortho',
    practice_type: 'Orthopedics',
    provider_count: 8,
    overview: {
      overall_readiness: 35,
      sra: { status: 'not_started' },
      policies: { active: 0, total: 0, review_due: 0 },
      training: { overdue: 2, compliant: 0, total_employees: 5 },
    },
  },
];

describe('CompanionClientList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseCompanionAlertSummary.mockReturnValue({ data: { summary: [] } });
  });

  it('shows loading spinner when data is loading', () => {
    mockUseCompanionClients.mockReturnValue({ data: undefined, isLoading: true });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByTestId('spinner')).toBeInTheDocument();
  });

  it('renders client cards after loading', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText('North Valley Medical')).toBeInTheDocument();
    expect(screen.getByText('South Valley Ortho')).toBeInTheDocument();
  });

  it('shows correct client count in subtitle', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText('2 active clients')).toBeInTheDocument();
  });

  it('shows singular "client" for single client', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: [sampleClients[0]] },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText('1 active client')).toBeInTheDocument();
  });

  it('displays practice type and provider count', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    // Check practice type text is shown (contains unicode middle dot)
    expect(screen.getByText(/Dental/)).toBeInTheDocument();
    expect(screen.getByText(/3 providers/)).toBeInTheDocument();
  });

  it('displays readiness percentage', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText('72%')).toBeInTheDocument();
    expect(screen.getByText('35%')).toBeInTheDocument();
  });

  it('shows empty state when no clients exist', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: [] },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText(/no active client organizations/i)).toBeInTheDocument();
  });

  it('filters clients by search input', async () => {
    const user = userEvent.setup();
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    const searchInput = screen.getByPlaceholderText(/search clients/i);
    await user.type(searchInput, 'North');

    expect(screen.getByText('North Valley Medical')).toBeInTheDocument();
    expect(screen.queryByText('South Valley Ortho')).not.toBeInTheDocument();
  });

  it('shows search empty state when no clients match', async () => {
    const user = userEvent.setup();
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    const searchInput = screen.getByPlaceholderText(/search clients/i);
    await user.type(searchInput, 'zzzzz');

    expect(screen.getByText(/no clients match your search/i)).toBeInTheDocument();
  });

  it('navigates to client detail on card click', async () => {
    const user = userEvent.setup();
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    await user.click(screen.getByText('North Valley Medical'));

    expect(mockNavigate).toHaveBeenCalledWith('/companion/clients/org-1');
  });

  it('shows overdue alert badge when triggered alerts exist', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });
    mockUseCompanionAlertSummary.mockReturnValue({
      data: {
        summary: [
          { org_id: 'org-1', active_count: 2, triggered_count: 3 },
        ],
      },
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.getByText('3 overdue')).toBeInTheDocument();
  });

  it('does not show overdue badge when triggered count is zero', () => {
    mockUseCompanionClients.mockReturnValue({
      data: { clients: sampleClients },
      isLoading: false,
    });
    mockUseCompanionAlertSummary.mockReturnValue({
      data: {
        summary: [
          { org_id: 'org-1', active_count: 2, triggered_count: 0 },
        ],
      },
    });

    render(<CompanionClientList />, { wrapper: createWrapper() });

    expect(screen.queryByText(/overdue/)).not.toBeInTheDocument();
  });
});
