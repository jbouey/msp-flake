import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SiteSLAIndicator } from './SiteSLAIndicator';

/**
 * SiteSLAIndicator unit tests.
 *
 * These tests stub global fetch so they don't hit the network. They cover:
 *   - loading state
 *   - healthy SLA (met=true)
 *   - breached SLA (met=false)
 *   - no-data (null current_rate)
 *   - execution_telemetry fallback badge
 *   - fetch failure
 */

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const baseResponse = {
  site_id: 'test-site',
  sla_target: 90,
  current_rate: 87.5,
  sla_met: false,
  periods_last_7d: 168,
  periods_met_last_7d: 140,
  met_pct_7d: 83.3,
  trend: [
    { period_start: '2026-04-08T00:00:00Z', healing_rate: 92, sla_met: true },
    { period_start: '2026-04-08T01:00:00Z', healing_rate: 87.5, sla_met: false },
    { period_start: '2026-04-08T02:00:00Z', healing_rate: 85, sla_met: false },
  ],
  source: 'site_healing_sla' as const,
};

describe('SiteSLAIndicator', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it('shows a loading spinner before data arrives', async () => {
    globalThis.fetch = vi.fn().mockImplementation(
      () =>
        new Promise(() => {
          /* never resolves */
        }),
    );
    const { container } = renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(container.querySelector('svg')).toBeTruthy(); // spinner svg
  });

  it('renders the SLA header', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => baseResponse,
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('Healing SLA')).toBeInTheDocument();
  });

  it('renders current rate vs target when SLA is breached', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...baseResponse, sla_met: false, current_rate: 87.5 }),
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('87.5%')).toBeInTheDocument();
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('Missing')).toBeInTheDocument();
  });

  it('shows "Meeting" pill when SLA is met', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...baseResponse,
        current_rate: 95,
        sla_met: true,
        met_pct_7d: 96,
      }),
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('Meeting')).toBeInTheDocument();
    expect(screen.getByText('95.0%')).toBeInTheDocument();
  });

  it('shows "No Data" pill when current_rate is null', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...baseResponse,
        current_rate: null,
        sla_met: null,
        met_pct_7d: null,
      }),
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('No Data')).toBeInTheDocument();
  });

  it('renders "Live" badge when source is execution_telemetry fallback', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...baseResponse, source: 'execution_telemetry' }),
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('Live')).toBeInTheDocument();
  });

  it('renders the 7-day met count', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => baseResponse,
    });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    expect(await screen.findByText('140')).toBeInTheDocument();
    expect(screen.getByText(/168/)).toBeInTheDocument();
  });

  it('shows an error message on fetch failure', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });
    renderWithClient(<SiteSLAIndicator siteId="test-site" />);
    await waitFor(() => {
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    });
  });

  it('does not fetch when siteId is empty', () => {
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy;
    renderWithClient(<SiteSLAIndicator siteId="" />);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
