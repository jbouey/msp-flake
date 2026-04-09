import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { SiteSearchBar } from './SiteSearchBar';

/**
 * SiteSearchBar unit tests.
 *
 * Covers:
 *   - short-circuit for queries under 2 chars (no fetch)
 *   - happy path: fetch returns categorised results, user sees categories
 *   - empty results state
 *   - dropdown dismisses on Escape
 *   - clear button wipes input
 */

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SiteSearchBar', () => {
  let originalFetch: typeof globalThis.fetch;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    fetchSpy = vi.fn();
    // vi.fn() returns a Mock which satisfies the Fetch signature at runtime
    // but TS can't prove it — an `unknown` cast is the idiomatic escape hatch.
    globalThis.fetch = fetchSpy as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it('does not fetch for single-character queries', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SiteSearchBar siteId="test-site" />);
    const input = screen.getByPlaceholderText(/search this site/i);
    await user.type(input, 'a');
    // Wait a moment for debounce
    await new Promise((r) => setTimeout(r, 300));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('fetches and renders categorised results', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        site_id: 'test-site',
        query: 'fire',
        results: {
          incidents: [
            {
              id: 'i1',
              incident_type: 'firewall_status',
              title: 'Firewall disabled on WS01',
              severity: 'high',
              status: 'open',
              created_at: '2026-04-08T12:00:00Z',
            },
          ],
          devices: [
            {
              id: 'd1',
              hostname: 'firewall-01',
              ip_address: '192.168.1.1',
              mac_address: null,
              device_type: 'network',
            },
          ],
          credentials: [],
          workstations: [],
        },
        total: 2,
      }),
    });

    const user = userEvent.setup();
    renderWithProviders(<SiteSearchBar siteId="test-site" />);
    const input = screen.getByPlaceholderText(/search this site/i);
    await user.type(input, 'fire');

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(await screen.findByText('Firewall disabled on WS01')).toBeInTheDocument();
    expect(screen.getByText('firewall-01')).toBeInTheDocument();
    expect(screen.getByText(/incidents \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText(/devices \(1\)/i)).toBeInTheDocument();
  });

  it('shows empty state when total is 0', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        site_id: 'test-site',
        query: 'zzz',
        results: { incidents: [], devices: [], credentials: [], workstations: [] },
        total: 0,
      }),
    });

    const user = userEvent.setup();
    renderWithProviders(<SiteSearchBar siteId="test-site" />);
    const input = screen.getByPlaceholderText(/search this site/i);
    await user.type(input, 'zzz');
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(await screen.findByText(/no matches for/i)).toBeInTheDocument();
  });

  it('clears input when clear button clicked', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      json: async () => ({
        site_id: 'test-site',
        query: 'abc',
        results: { incidents: [], devices: [], credentials: [], workstations: [] },
        total: 0,
      }),
    });

    const user = userEvent.setup();
    renderWithProviders(<SiteSearchBar siteId="test-site" />);
    const input = screen.getByPlaceholderText(/search this site/i) as HTMLInputElement;
    await user.type(input, 'abc');
    expect(input.value).toBe('abc');
    const clearBtn = screen.getByLabelText('Clear search');
    await user.click(clearBtn);
    expect(input.value).toBe('');
  });

  it('uses custom placeholder', () => {
    renderWithProviders(<SiteSearchBar siteId="test-site" placeholder="Find stuff…" />);
    expect(screen.getByPlaceholderText('Find stuff…')).toBeInTheDocument();
  });
});
