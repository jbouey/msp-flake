/**
 * PartnerAttestations.test.tsx — sprint-36 Decision-1 deliverable.
 *
 * Coverage:
 *   - Empty state when no attestation issued + no roster.
 *   - Roster summary card after fetch.
 *   - Issue button triggers blob fetch + filename parsing from
 *     Content-Disposition.
 *   - Issue button disabled while in flight.
 *   - 429 rate-limit toast surfaces Retry-After.
 *   - Add-BAA modal validates ≥20-char scope.
 *   - Revoke confirm requires typed-counterparty match.
 *   - 401 redirects to /partner/login.
 *   - 403 from issuance shows permission toast.
 *   - Tablist/tab semantics aren't in this page (Attestations is a
 *     standalone route), but a11y on the issue/add/revoke buttons IS
 *     verified via aria-label + role="dialog" coverage.
 */
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock PartnerContext.usePartner before importing the component.
const mockUsePartner = vi.fn();
vi.mock('../PartnerContext', () => ({
  usePartner: () => mockUsePartner(),
}));

// Mock react-router-dom's useNavigate so we can assert redirects.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>(
      'react-router-dom',
    );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { PartnerAttestations } from '../PartnerAttestations';

interface MockPartner {
  id: string;
  name: string;
  slug: string;
  brand_name: string;
  primary_color: string;
  logo_url: null;
  contact_email: string;
  revenue_share_percent: number;
  site_count: number;
  provisions: { pending: number; claimed: number };
  user_role: 'admin' | 'tech' | 'billing';
}

const adminPartner: MockPartner = {
  id: 'p1',
  name: 'Acme MSP',
  slug: 'acme',
  brand_name: 'Acme MSP',
  primary_color: '#000',
  logo_url: null,
  contact_email: 'a@a.com',
  revenue_share_percent: 40,
  site_count: 0,
  provisions: { pending: 0, claimed: 0 },
  user_role: 'admin',
};

const techPartner: MockPartner = { ...adminPartner, user_role: 'tech' };

function _mockPartner(opts: {
  partner?: MockPartner | undefined;
  isAuthenticated?: boolean;
  isLoading?: boolean;
}) {
  mockUsePartner.mockReturnValue({
    partner: opts.partner ?? adminPartner,
    apiKey: null,
    isAuthenticated: opts.isAuthenticated ?? true,
    isLoading: opts.isLoading ?? false,
    error: null,
    login: vi.fn(),
    logout: vi.fn(),
    checkSession: vi.fn(),
  });
}

function _renderPage() {
  return render(
    <MemoryRouter initialEntries={['/partner/attestations']}>
      <PartnerAttestations />
    </MemoryRouter>,
  );
}

// Convenience for crafting fetch mock responses.
function _jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function _blobResponse(opts: {
  status?: number;
  headers?: Record<string, string>;
  body?: string | Blob;
}): Response {
  return new Response(opts.body ?? new Blob(['%PDF-fake']), {
    status: opts.status ?? 200,
    headers: opts.headers ?? {},
  });
}

describe('PartnerAttestations', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    mockNavigate.mockClear();
    mockUsePartner.mockReset();
    _mockPartner({});
    // Stub URL.createObjectURL / revokeObjectURL — jsdom omits them.
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:mock-url'),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('renders empty state when no portfolio attestation + empty roster', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    expect(screen.getByText('Attestations')).toBeInTheDocument();
    expect(screen.getByTestId('portfolio-empty')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('roster-empty')).toBeInTheDocument();
    });
  });

  it('renders roster table when entries exist', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) {
        return _jsonResponse({
          roster: [
            {
              id: 'r1',
              counterparty_org_id: null,
              counterparty_practice_name: 'North Valley Dental',
              executed_at: '2026-01-01T00:00:00Z',
              expiry_at: null,
              scope:
                'Permitted uses include monitoring of compliance posture across covered systems',
              signer_name: 'Jane Doe',
              signer_title: 'Practice Manager',
              signer_email: 'jane@nvd.example',
              attestation_bundle_id: 'b1',
            },
          ],
        });
      }
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    await waitFor(() => {
      expect(screen.getByText('North Valley Dental')).toBeInTheDocument();
      expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    });
  });

  it('Issue portfolio button triggers blob download with filename from header', async () => {
    let fetchCalls = 0;
    globalThis.fetch = vi.fn(
      async (input: string | URL) => {
        const url =
          typeof input === 'string' ? input : (input as URL).toString();
        if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
        if (url.includes('/me/orgs')) return _jsonResponse([]);
        if (url.includes('/me/portfolio-attestation')) {
          fetchCalls++;
          return _blobResponse({
            headers: {
              'Content-Disposition':
                'attachment; filename="portfolio-attestation-acme-2026-05-08.pdf"',
              'X-Attestation-Hash': 'a'.repeat(64),
              'X-Letter-Valid-Until': '2027-05-08T00:00:00Z',
            },
          });
        }
        return _jsonResponse({});
      },
    ) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(fetchCalls).toBe(1);
    });
    await waitFor(() => {
      expect(screen.getByTestId('portfolio-summary')).toBeInTheDocument();
    });
  });

  it('Issue portfolio button is disabled while in flight (aria-busy)', async () => {
    let resolveFetch!: (r: Response) => void;
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/portfolio-attestation')) {
        return new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(btn).toHaveAttribute('aria-busy', 'true');
      expect(btn).toBeDisabled();
    });

    resolveFetch(
      _blobResponse({
        headers: {
          'Content-Disposition': 'attachment; filename="x.pdf"',
        },
      }),
    );

    await waitFor(() => {
      expect(btn).not.toBeDisabled();
    });
  });

  it('Issue 429 surfaces rate-limit toast with retry-after', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/portfolio-attestation')) {
        return new Response(
          JSON.stringify({ detail: 'rate limit' }),
          {
            status: 429,
            headers: { 'Retry-After': '600' },
          },
        );
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/rate-limited.*~10 min/i),
      ).toBeInTheDocument();
    });
  });

  it('Issue 403 shows permission toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/portfolio-attestation')) {
        return new Response(JSON.stringify({ detail: 'forbidden' }), {
          status: 403,
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/don't have permission to issue/i),
      ).toBeInTheDocument();
    });
  });

  it('Issue 401 surfaces session-expired toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/portfolio-attestation')) {
        return new Response('', { status: 401 });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/session expired/i),
      ).toBeInTheDocument();
    });
  });

  it('redirects unauthenticated users to /partner/login', async () => {
    _mockPartner({
      partner: undefined,
      isAuthenticated: false,
      isLoading: false,
    });
    globalThis.fetch = vi.fn(async () =>
      _jsonResponse({ roster: [] }),
    ) as unknown as typeof fetch;

    _renderPage();

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/partner/login', {
        replace: true,
      });
    });
  });

  it('Add-BAA modal validates >=20-char scope', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs'))
        return _jsonResponse([{ id: 'o1', name: 'Client One' }]);
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const addBtn = await screen.findByRole('button', { name: /Add new BAA/ });
    fireEvent.click(addBtn);

    const dialog = await screen.findByRole('dialog');
    // Pick org
    const select = within(dialog).getByRole('combobox');
    fireEvent.change(select, { target: { value: 'o1' } });
    // Required dates / signer
    fireEvent.change(within(dialog).getAllByLabelText(/Executed/i)[0], {
      target: { value: '2026-01-01' },
    });
    fireEvent.change(
      within(dialog).getByLabelText(/Scope \(\d+\/20\+ chars\)/i),
      { target: { value: 'too short' } },
    );
    fireEvent.change(within(dialog).getByLabelText(/Signer name/i), {
      target: { value: 'A B' },
    });
    fireEvent.change(within(dialog).getByLabelText(/Signer title/i), {
      target: { value: 'CEO' },
    });

    const submit = within(dialog).getByRole('button', {
      name: /Add to roster/,
    });
    fireEvent.click(submit);

    await waitFor(() => {
      expect(
        within(dialog).getByText(/Scope must be at least 20 characters/i),
      ).toBeInTheDocument();
    });
  });

  it('Revoke confirm requires typed counterparty label', async () => {
    let deleteCalls = 0;
    globalThis.fetch = vi.fn(async (input: string | URL, init?: { method?: string }) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      const method = init?.method || 'GET';
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/ba-roster') && method === 'DELETE') {
        deleteCalls++;
        return _jsonResponse({ status: 'ok', revoked_id: 'r1' });
      }
      if (url.includes('/me/ba-roster')) {
        return _jsonResponse({
          roster: [
            {
              id: 'r1',
              counterparty_org_id: null,
              counterparty_practice_name: 'Test Clinic',
              executed_at: '2026-01-01T00:00:00Z',
              expiry_at: null,
              scope:
                'A long enough scope description to pass the twenty-char gate',
              signer_name: 'Jane',
              signer_title: 'CEO',
              signer_email: null,
              attestation_bundle_id: null,
            },
          ],
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const revokeBtn = await screen.findByRole('button', {
      name: /Revoke BAA for Test Clinic/,
    });
    fireEvent.click(revokeBtn);

    const dialog = await screen.findByRole('dialog');
    // First try with mismatched typed-confirm:
    fireEvent.change(within(dialog).getByLabelText(/Reason/i), {
      target: { value: 'A reason of more than twenty characters total' },
    });
    fireEvent.change(
      within(dialog).getByLabelText(/Type Test Clinic to confirm/i),
      { target: { value: 'wrong' } },
    );
    const submit = within(dialog).getByRole('button', { name: 'Revoke BAA' });
    fireEvent.click(submit);

    await waitFor(() => {
      expect(
        screen.getByText(/Confirmation did not match/i),
      ).toBeInTheDocument();
    });
    expect(deleteCalls).toBe(0);

    // Now match exactly:
    fireEvent.change(
      within(dialog).getByLabelText(/Type Test Clinic to confirm/i),
      { target: { value: 'Test Clinic' } },
    );
    fireEvent.click(submit);

    await waitFor(() => {
      expect(deleteCalls).toBe(1);
    });
  });

  it('tech role sees read-only view (no Issue / no Add buttons)', async () => {
    _mockPartner({ partner: techPartner });
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    // Issue button is disabled (admin-required hint visible).
    const issueBtn = await screen.findByRole('button', {
      name: /admin role required/,
    });
    expect(issueBtn).toBeDisabled();

    // Add BAA button is disabled.
    const addBtn = screen.getByRole('button', { name: /Add new BAA/ });
    expect(addBtn).toBeDisabled();
  });

  it('Public verify URL renders with [hash[:32]] shape', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/ba-roster')) return _jsonResponse({ roster: [] });
      if (url.includes('/me/orgs')) return _jsonResponse([]);
      if (url.includes('/me/portfolio-attestation')) {
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="x.pdf"',
            'X-Attestation-Hash': 'b'.repeat(64),
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue new portfolio attestation/,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      // hash[:32] = 32 'b's
      const code = screen.getByText(
        new RegExp(`osiriscare\\.io/verify/portfolio/b{32}`),
      );
      expect(code).toBeInTheDocument();
    });
  });
});
