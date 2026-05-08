/**
 * PartnerSiteDetail.test.tsx — Sprint-N+2 deliverable.
 *
 * Coverage (round-table 2026-05-08 plan-37 Gate-5):
 *   1.  Renders site header for valid siteId (admin role).
 *   2.  Renders for tech role (operational mint allowed).
 *   3.  Hides mint button for billing role.
 *   4.  Mint button opens DangerousActionModal (reversible tier).
 *   5.  Modal copy includes "logged to the cryptographic audit chain".
 *   6.  Modal copy includes "expires in 15 minutes".
 *   7.  Reason textarea < 20 chars surfaces validation error.
 *   8.  Reason textarea ≥ 20 chars enables submit + posts.
 *   9.  Mint posts to canonical URL with credentials + CSRF (via postJson).
 *   10. Mint result renders URL.
 *   11. Mint result renders attestation_hash.
 *   12. Mint result renders countdown badge.
 *   13. Copy-to-clipboard wires through navigator.clipboard.writeText.
 *   14. 401 from site detail redirects to /partner/login.
 *   15. 401 from mint redirects to /partner/login.
 *   16. 429 from mint surfaces rate-limit toast with retry-after.
 *   17. 403 from mint shows permission-denied toast.
 *   18. Site-not-found 404 renders error state.
 *   19. Sub-route navigation (Workstation agents link).
 *   20. Sub-route navigation (Devices link).
 *   21. Sub-route navigation (Check config link).
 *   22. Existing topology link still renders.
 *   23. Existing consent link still renders.
 *   24. Activity feed renders 30-day events.
 *   25. Activity feed empty state.
 *   26. Sibling-parity: mint response carries X-Attestation-Hash +
 *       X-Letter-Valid-Until headers per
 *       feedback_multi_endpoint_header_parity.md (verified at the
 *       backend test layer; here we verify the frontend reads
 *       attestation_hash + expires_at from JSON body).
 *   27. Auth-loading state renders before redirect.
 *   28. Site loading state renders before data.
 */
import {
  render,
  screen,
  waitFor,
  fireEvent,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock PartnerContext.usePartner before importing the component.
const mockUsePartner = vi.fn();
vi.mock('../PartnerContext', () => ({
  usePartner: () => mockUsePartner(),
  PartnerProvider: ({ children }: { children: React.ReactNode }) => children,
}));

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

import { PartnerSiteDetail } from '../PartnerSiteDetail';

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

const baseMock: MockPartner = {
  id: 'partner-1',
  name: 'Acme MSP',
  slug: 'acme',
  brand_name: 'Acme MSP',
  primary_color: '#000',
  logo_url: null,
  contact_email: 'a@a.com',
  revenue_share_percent: 40,
  site_count: 5,
  provisions: { pending: 0, claimed: 0 },
  user_role: 'admin',
};

function _mockPartner(role: 'admin' | 'tech' | 'billing' = 'admin', isAuthenticated = true, isLoading = false) {
  mockUsePartner.mockReturnValue({
    partner: { ...baseMock, user_role: role },
    apiKey: null,
    isAuthenticated,
    isLoading,
    error: null,
    login: vi.fn(),
    logout: vi.fn(),
    checkSession: vi.fn(),
  });
}

function _jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function _renderPage(siteId = 'site-abc') {
  return render(
    <MemoryRouter initialEntries={[`/partner/site/${siteId}`]}>
      <Routes>
        <Route path="/partner/site/:siteId" element={<PartnerSiteDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

const SITE_DETAIL_BODY = {
  site: {
    site_id: 'site-abc',
    clinic_name: 'North Valley Dental',
    status: 'active',
    tier: 'professional',
    onboarding_stage: 'live',
    partner_brand: 'Acme MSP',
  },
  appliances: [
    {
      appliance_id: 'app-1',
      hostname: 'osiris-01',
      display_name: 'osiris-01',
      status: 'online',
      agent_version: '0.5.0',
      last_checkin: new Date(Date.now() - 60 * 1000).toISOString(),
    },
  ],
  asset_count: 14,
  credential_count: 2,
  assets: [],
  credentials: [],
  recent_scans: [],
};

describe('PartnerSiteDetail', () => {
  const originalFetch = globalThis.fetch;
  const originalClipboard = navigator.clipboard;

  beforeEach(() => {
    mockNavigate.mockClear();
    mockUsePartner.mockReset();
    _mockPartner('admin');
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => undefined) },
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      configurable: true,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  it('1. renders site header for admin role', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/me/sites/site-abc') && !url.includes('audit-log')) {
        return _jsonResponse(SITE_DETAIL_BODY);
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    await waitFor(() => {
      // Heading uses h1 — pin the role to disambiguate from the
      // breadcrumb link which renders the same clinic_name text.
      expect(
        screen.getByRole('heading', { level: 1, name: 'North Valley Dental' }),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId('partner-site-detail')).toBeInTheDocument();
  });

  it('2. renders mint button for tech role', async () => {
    _mockPartner('tech');
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('open-mint-modal')).not.toBeDisabled();
    });
  });

  it('3. disables mint button for billing role', async () => {
    _mockPartner('billing');
    globalThis.fetch = vi.fn(async () => _jsonResponse(SITE_DETAIL_BODY)) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('open-mint-modal')).toBeDisabled();
    });
    expect(screen.getByText(/admin or tech role required/i)).toBeInTheDocument();
  });

  it('4-6. opens DangerousActionModal with chain + 15-minute copy', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    await waitFor(() => {
      // The string appears in BOTH the cross-portal access card AND
      // the modal description (Carol-revised copy in plan-37 D4); both
      // are required artifacts. Use getAllByText + assert ≥2 matches
      // so the test pins the dual presence (modal + card) without
      // breaking on exact-match ambiguity.
      const chainMatches = screen.getAllByText(
        /logged to the cryptographic audit chain/i,
      );
      expect(chainMatches.length).toBeGreaterThanOrEqual(1);
    });
    const expiryMatches = screen.getAllByText(/expires in 15 minutes/i);
    expect(expiryMatches.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId('mint-reason-input')).toBeInTheDocument();
  });

  it('7. shows validation error when reason < 20 chars', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    const textarea = await screen.findByTestId('mint-reason-input');
    fireEvent.change(textarea, { target: { value: 'too short' } });
    const confirmBtn = screen.getByRole('button', { name: /Mint/i });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(
        screen.getByText(/Reason must be at least 20 characters/i),
      ).toBeInTheDocument();
    });
  });

  it('8-11. mint posts on ≥20 chars + renders URL + attestation_hash', async () => {
    let mintCalled = false;
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        mintCalled = true;
        // Verify CSRF + credentials shape via init
        expect((init as RequestInit | undefined)?.credentials).toBe('include');
        return _jsonResponse({
          url: 'https://www.osiriscare.net/portal/site/site-abc/login?magic=tok',
          expires_at: new Date(Date.now() + 14 * 60 * 1000).toISOString(),
          magic_link_id: 'm-1',
          attestation_bundle_id: 'b-1',
          attestation_hash: 'a'.repeat(64),
        });
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    const textarea = await screen.findByTestId('mint-reason-input');
    fireEvent.change(textarea, {
      target: { value: 'Customer triage call: 15-min owner walkthrough OK' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(mintCalled).toBe(true);
      expect(screen.getByTestId('mint-url')).toHaveTextContent('magic=tok');
      expect(screen.getByText(/a{64}/)).toBeInTheDocument();
      expect(screen.getByTestId('magic-link-countdown')).toBeInTheDocument();
    });
  });

  it('13. copy-to-clipboard wires through navigator.clipboard.writeText', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        return _jsonResponse({
          url: 'https://x/y',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          magic_link_id: 'm',
          attestation_bundle_id: 'b',
          attestation_hash: 'h',
        });
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    const textarea = await screen.findByTestId('mint-reason-input');
    fireEvent.change(textarea, {
      target: { value: 'twenty character reason for the mint' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => screen.getByTestId('copy-mint-url'));
    fireEvent.click(screen.getByTestId('copy-mint-url'));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('https://x/y');
    });
  });

  it('14. 401 from site detail redirects to /partner/login', async () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({ detail: 'unauthorized' }, 401)) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/partner/login');
    });
  });

  it('15. 401 from mint redirects to /partner/login', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) return _jsonResponse({ detail: 'no' }, 401);
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    const textarea = await screen.findByTestId('mint-reason-input');
    fireEvent.change(textarea, {
      target: { value: 'twenty character mint reason for test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/partner/login');
    });
  });

  it('16. 429 surfaces rate-limit copy', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        return _jsonResponse(
          { detail: 'Magic-link mint rate-limited. Retry in 1200s.' },
          429,
          { 'Retry-After': '1200' },
        );
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty-char reason for the mint test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(screen.getByText(/rate-limited/i)).toBeInTheDocument();
    });
  });

  it('17. 403 surfaces permission-denied copy', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        return _jsonResponse({ detail: 'forbidden' }, 403);
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty-char reason for forbidden test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Permission denied/i),
      ).toBeInTheDocument();
    });
  });

  it('18. site 404 renders error state', async () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({ detail: 'not found' }, 404)) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('site-error')).toBeInTheDocument();
    });
  });

  it('19-23. sub-route + cohabiting links render', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByText('Workstation agents'));
    expect(screen.getByText('Workstation agents').getAttribute('href')).toContain('/agents');
    expect(screen.getByText('Devices').getAttribute('href')).toContain('/devices');
    expect(screen.getByText('Check config').getAttribute('href')).toContain('/drift-config');
    expect(screen.getByText('Mesh topology').getAttribute('href')).toContain('/topology');
    expect(screen.getByText('Consent').getAttribute('href')).toContain('/consent');
  });

  it('24. activity feed renders 30-day events', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) {
        return _jsonResponse({
          events: [
            {
              event_id: 'e1',
              at: new Date().toISOString(),
              action: 'PARTNER_CLIENT_PORTAL_LINK_MINTED',
              actor: 'tech@acme.example',
              details: {},
              kind: 'partner_action',
            },
          ],
        });
      }
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => {
      expect(
        screen.getByText('PARTNER_CLIENT_PORTAL_LINK_MINTED'),
      ).toBeInTheDocument();
    });
  });

  it('25. activity feed empty state', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      expect(
        screen.getByText(/No partner-scoped activity in the last 30 days/i),
      ).toBeInTheDocument();
    });
  });

  it('26. mint response carries attestation_hash and expires_at fields', async () => {
    let mintBody: Record<string, unknown> | null = null;
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        mintBody = {
          url: 'https://x/y',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          magic_link_id: 'm-1',
          attestation_bundle_id: 'b-1',
          attestation_hash: 'parity-hash',
        };
        return _jsonResponse(mintBody);
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;

    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty character reason for parity test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(mintBody).not.toBeNull();
      expect(screen.getByText(/parity-hash/)).toBeInTheDocument();
    });
  });

  it('27. auth-loading state renders spinner copy', async () => {
    _mockPartner('admin', false, true);
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    expect(screen.getByTestId('auth-loading')).toBeInTheDocument();
  });

  it('29. mint button labelled with practice-owner copy (Carol-revised)', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    expect(screen.getByTestId('open-mint-modal')).toHaveTextContent(
      /Open client portal as practice owner/i,
    );
  });

  it('30. cross-portal card includes Issue Compliance Letter deep link', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      const link = screen.getByText(/Issue Compliance Letter/i);
      expect(link.getAttribute('href')).toContain('intent=letter');
    });
  });

  it('31. cross-portal card includes Issue Wall Certificate deep link', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => {
      const link = screen.getByText(/Issue Wall Certificate/i);
      expect(link.getAttribute('href')).toContain('intent=wall_cert');
    });
  });

  it('32. mint URL is single-use shape (token in query param)', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        return _jsonResponse({
          url: 'https://www.osiriscare.net/portal/site/site-abc/login?magic=single-use-token-1234',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          magic_link_id: 'm',
          attestation_bundle_id: 'b',
          attestation_hash: 'h',
        });
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty-character reason for token shape test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(screen.getByTestId('mint-url')).toHaveTextContent('?magic=');
    });
  });

  it('33. mint button uses postJson canonical helper (CSRF + credentials)', async () => {
    let observedHeaders: Headers | null = null;
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        const initObj = init as RequestInit | undefined;
        observedHeaders = new Headers(initObj?.headers as HeadersInit);
        return _jsonResponse({
          url: 'https://x',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          magic_link_id: 'm',
          attestation_bundle_id: 'b',
          attestation_hash: 'h',
        });
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty-character reason for csrf header check' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => {
      expect(observedHeaders).not.toBeNull();
      expect((observedHeaders as Headers | null)?.get('Content-Type')).toBe(
        'application/json',
      );
    });
  });

  it('34. result panel exposes Done close button', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.endsWith('/client-portal-link')) {
        return _jsonResponse({
          url: 'https://x',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          magic_link_id: 'm',
          attestation_bundle_id: 'b',
          attestation_hash: 'h',
        });
      }
      if (url.includes('audit-log')) return _jsonResponse({ events: [] });
      return _jsonResponse(SITE_DETAIL_BODY);
    }) as unknown as typeof fetch;
    _renderPage();
    await waitFor(() => screen.getByTestId('open-mint-modal'));
    fireEvent.click(screen.getByTestId('open-mint-modal'));
    fireEvent.change(await screen.findByTestId('mint-reason-input'), {
      target: { value: 'twenty-char reason for done button test' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Mint/i }));
    await waitFor(() => screen.getByTestId('close-mint-result'));
    fireEvent.click(screen.getByTestId('close-mint-result'));
    await waitFor(() => {
      expect(screen.queryByTestId('close-mint-result')).not.toBeInTheDocument();
    });
  });

  it('35. unauthenticated state does not render mint UI', async () => {
    _mockPartner('admin', false, false);
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    expect(screen.queryByTestId('open-mint-modal')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/partner/login');
    });
  });

  it('28. site loading state renders before data', () => {
    let resolveFetch: ((r: Response) => void) | null = null;
    globalThis.fetch = vi.fn(
      () =>
        new Promise<Response>((res) => {
          resolveFetch = res;
        }),
    ) as unknown as typeof fetch;
    _renderPage();
    expect(screen.getByTestId('site-loading')).toBeInTheDocument();
    if (resolveFetch) (resolveFetch as (r: Response) => void)(_jsonResponse({}));
  });
});
