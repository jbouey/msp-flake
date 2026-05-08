/**
 * ClientAttestations.test.tsx — owner-side mirror of
 * partner/__tests__/PartnerAttestations.test.tsx (sprint 2026-05-08).
 *
 * Coverage (25 cases):
 *   - Page renders empty states for all 3 cards.
 *   - F1: issue triggers blob fetch + filename parsing from
 *     Content-Disposition + summary populated from
 *     X-Attestation-Hash + X-Letter-Valid-Until.
 *   - F1: aria-busy in flight.
 *   - F1: 401/403/429/409/5xx error toasts.
 *   - F3: quarter selector posts the right body shape (year, quarter
 *     integers — not the friendly current/previous string).
 *   - F3: summary populates with X-Summary-Valid-Until parity.
 *   - F3: 429 + 409 toasts.
 *   - F5: button disabled when no F1 summary.
 *   - F5: button enabled after F1 issuance + downloads blob.
 *   - F5: 404 toast directs back to Card A.
 *   - Public verify URL hash slug shape: 32-char floor
 *     (osiriscare.io/verify/<32> AND osiriscare.io/verify/quarterly/<32>).
 *   - 401 redirects to /client/login.
 *   - aria-busy + aria-label semantics on buttons.
 *   - Banned-words sweep on rendered output.
 *   - Sibling-parity: extract a partner-specific copy fragment + assert
 *     it does NOT appear (different artifact set; copy MUST diverge in
 *     content) AND the hash[:32] verify-URL shape DOES appear.
 *   - viewer role gates wall cert with admin-required hint.
 */
import {
  render,
  screen,
  waitFor,
  fireEvent,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock ClientContext.useClient before importing the component.
const mockUseClient = vi.fn();
vi.mock('../ClientContext', () => ({
  useClient: () => mockUseClient(),
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

import { ClientAttestations } from '../ClientAttestations';

interface MockClientUser {
  id: string;
  email: string;
  name: string | null;
  role: 'owner' | 'admin' | 'viewer';
  org: { id: string; name: string };
}

const ownerUser: MockClientUser = {
  id: 'u1',
  email: 'maria@nvd.example',
  name: 'Maria Owner',
  role: 'owner',
  org: { id: 'o1', name: 'North Valley Dental' },
};

const adminUser: MockClientUser = { ...ownerUser, role: 'admin' };
const viewerUser: MockClientUser = { ...ownerUser, role: 'viewer' };

function _mockClient(opts: {
  user?: MockClientUser | null;
  isAuthenticated?: boolean;
  isLoading?: boolean;
}) {
  mockUseClient.mockReturnValue({
    user: opts.user === undefined ? ownerUser : opts.user,
    isAuthenticated: opts.isAuthenticated ?? true,
    isLoading: opts.isLoading ?? false,
    error: null,
    logout: vi.fn(),
    checkSession: vi.fn(),
  });
}

function _renderPage() {
  return render(
    <MemoryRouter initialEntries={['/client/attestations']}>
      <ClientAttestations />
    </MemoryRouter>,
  );
}

function _jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function _blobResponse(opts: {
  status?: number;
  headers?: Record<string, string>;
  body?: string;
}): Response {
  // Use a string body — undici's Response.blob() under node implements
  // stream conversion on text but choked on Blob input on CI's node
  // version. String body works on both jsdom + node-undici.
  return new Response(opts.body ?? '%PDF-fake', {
    status: opts.status ?? 200,
    headers: opts.headers ?? {},
  });
}

describe('ClientAttestations', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    mockNavigate.mockClear();
    mockUseClient.mockReset();
    _mockClient({});
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

  // ---------------------------------------------------------------
  // Page render + empty states
  // ---------------------------------------------------------------

  it('renders heading + all three empty-state cards on first paint', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;

    _renderPage();

    expect(screen.getByText('Attestations')).toBeInTheDocument();
    expect(screen.getByTestId('letter-empty')).toBeInTheDocument();
    expect(screen.getByTestId('quarterly-empty')).toBeInTheDocument();
    expect(screen.getByTestId('wall-cert-prereq-missing')).toBeInTheDocument();
  });

  it('letter-empty copy directs the owner to issue first', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    expect(
      screen.getByText(/No attestation letter issued in this session yet/i),
    ).toBeInTheDocument();
  });

  it('quarterly-empty copy mentions §164.530\\(j\\) framing somewhere on the card', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    // Card B body copy explicitly cites §164.530(j) so Maria sees the
    // retention-archive framing without reading the issued PDF first.
    expect(
      screen.getByText(/§164\.530\(j\)/i),
    ).toBeInTheDocument();
  });

  // ---------------------------------------------------------------
  // F1 — Compliance Attestation Letter
  // ---------------------------------------------------------------

  it('F1 issue triggers blob download + populates summary from headers', async () => {
    let fetchCalls = 0;
    let lastMethod = '';
    globalThis.fetch = vi.fn(
      async (input: string | URL, init?: RequestInit) => {
        const url =
          typeof input === 'string' ? input : (input as URL).toString();
        if (url.includes('/api/client/attestation-letter') && !url.includes('wall-cert')) {
          fetchCalls++;
          lastMethod = init?.method || 'GET';
          return _blobResponse({
            headers: {
              'Content-Disposition':
                'attachment; filename="compliance-attestation-nvd-2026-05-08.pdf"',
              'X-Attestation-Hash': 'a'.repeat(64),
              'X-Letter-Valid-Until': '2026-08-06T00:00:00Z',
            },
          });
        }
        return _jsonResponse({});
      },
    ) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(fetchCalls).toBe(1);
    });
    // F1 endpoint is GET on the backend (issuance + stream in one call).
    expect(lastMethod).toBe('GET');
    await waitFor(() => {
      expect(screen.getByTestId('letter-summary')).toBeInTheDocument();
    });
    // Header values surfaced in summary block.
    expect(
      screen.getByText(/compliance-attestation-nvd-2026-05-08\.pdf/),
    ).toBeInTheDocument();
  });

  it('F1 issue button is disabled while in flight (aria-busy true)', async () => {
    let resolveFetch!: (r: Response) => void;
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();

    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(btn).toHaveAttribute('aria-busy', 'true');
      expect(btn).toBeDisabled();
    });

    resolveFetch(
      _blobResponse({
        headers: { 'Content-Disposition': 'attachment; filename="x.pdf"' },
      }),
    );

    await waitFor(() => {
      expect(btn).not.toBeDisabled();
    });
  });

  it('F1 401 surfaces session-expired toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Response('', { status: 401 });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/session expired/i)).toBeInTheDocument();
    });
  });

  it('F1 403 shows permission toast (banned-words clean)', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Response(JSON.stringify({ detail: 'forbidden' }), {
          status: 403,
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/don't have permission to issue/i),
      ).toBeInTheDocument();
    });
  });

  it('F1 429 surfaces rate-limit toast with retry-after minutes', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Response(JSON.stringify({ detail: 'rate limit' }), {
          status: 429,
          headers: { 'Retry-After': '600' },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/rate-limited.*~10 min/i)).toBeInTheDocument();
    });
  });

  it('F1 409 surfaces precondition-missing toast (PO / BAA)', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Response(
          JSON.stringify({
            detail: 'No active Privacy Officer designation on file.',
          }),
          { status: 409 },
        );
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/No active Privacy Officer designation/i),
      ).toBeInTheDocument();
    });
  });

  it('F1 500 surfaces generic failure toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
        return new Response('boom', { status: 500 });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/Issuance failed.*support|500/i),
      ).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------
  // F3 — Quarterly Practice Compliance Summary
  // ---------------------------------------------------------------

  it('F3 issue posts year+quarter integer body (NOT current/previous string)', async () => {
    let bodyJson: unknown = null;
    let methodSeen = '';
    globalThis.fetch = vi.fn(
      async (input: string | URL, init?: RequestInit) => {
        const url =
          typeof input === 'string' ? input : (input as URL).toString();
        if (url.includes('/api/client/quarterly-summary')) {
          methodSeen = init?.method || 'GET';
          if (init?.body && typeof init.body === 'string') {
            bodyJson = JSON.parse(init.body);
          }
          return _blobResponse({
            headers: {
              'Content-Disposition':
                'attachment; filename="quarterly-summary-nvd-Q1-2026.pdf"',
              'X-Attestation-Hash': 'c'.repeat(64),
              'X-Summary-Valid-Until': '2027-04-01T00:00:00Z',
            },
          });
        }
        return _jsonResponse({});
      },
    ) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(methodSeen).toBe('POST');
    });
    expect(bodyJson).not.toBeNull();
    const body = bodyJson as { year: unknown; quarter: unknown };
    expect(typeof body.year).toBe('number');
    expect(typeof body.quarter).toBe('number');
    expect((body.quarter as number) >= 1 && (body.quarter as number) <= 4).toBe(
      true,
    );
  });

  it('F3 summary block reads X-Summary-Valid-Until (sibling-divergent header)', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/quarterly-summary')) {
        return _blobResponse({
          headers: {
            'Content-Disposition':
              'attachment; filename="quarterly-summary-nvd-Q1-2026.pdf"',
            'X-Attestation-Hash': 'd'.repeat(64),
            // CRITICAL: this is X-Summary-Valid-Until, NOT
            // X-Letter-Valid-Until. The component MUST read the F3-
            // distinct header per the multi-endpoint header parity
            // memo.
            'X-Summary-Valid-Until': '2027-04-01T00:00:00Z',
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId('quarterly-summary')).toBeInTheDocument();
    });
    // The summary block surfaces the parsed valid-until date.
    expect(screen.getByText(/Valid until/i)).toBeInTheDocument();
  });

  it('F3 includes CSRF + Content-Type headers on POST (mutation CSRF rule)', async () => {
    // Stub document.cookie so csrfHeaders() returns the X-CSRF-Token
    // entry. Without this jsdom returns no cookie and csrfHeaders()
    // legitimately returns {} — which is the production behavior
    // pre-session-cookie-bootstrap, not what we want to assert here.
    const originalCookieDescriptor = Object.getOwnPropertyDescriptor(
      document.constructor.prototype,
      'cookie',
    );
    Object.defineProperty(document, 'cookie', {
      value: 'csrf_token=test-token-abc',
      writable: true,
      configurable: true,
    });

    try {
      let headersSeen: Record<string, string> = {};
      // RequestCredentials is the lib.dom.d.ts string literal type
      // 'omit'|'same-origin'|'include' — eslint globals don't carry
      // it so we widen to string for the runtime check.
      let credentialsSeen: string | undefined;
      globalThis.fetch = vi.fn(
        async (input: string | URL, init?: RequestInit) => {
          const url =
            typeof input === 'string' ? input : (input as URL).toString();
          if (url.includes('/api/client/quarterly-summary')) {
            const h = init?.headers as Record<string, string> | undefined;
            if (h) headersSeen = h;
            credentialsSeen = init?.credentials;
            return _blobResponse({
              headers: { 'Content-Disposition': 'attachment; filename="x.pdf"' },
            });
          }
          return _jsonResponse({});
        },
      ) as unknown as typeof fetch;

      _renderPage();
      const btn = await screen.findByRole('button', {
        name: /Issue and download Quarterly Practice Compliance Summary/i,
      });
      fireEvent.click(btn);

      await waitFor(() => {
        // X-CSRF-Token must be in the spread (CSRF middleware gate).
        expect(
          Object.keys(headersSeen).some(
            (k) => k.toLowerCase() === 'x-csrf-token',
          ),
        ).toBe(true);
        // Content-Type set so FastAPI parses request.json().
        expect(
          Object.keys(headersSeen).some(
            (k) => k.toLowerCase() === 'content-type',
          ),
        ).toBe(true);
        // credentials:'include' so the session cookie travels.
        expect(credentialsSeen).toBe('include');
      });
    } finally {
      // Restore — other tests rely on the stock document.cookie shape.
      if (originalCookieDescriptor) {
        Object.defineProperty(
          document.constructor.prototype,
          'cookie',
          originalCookieDescriptor,
        );
      }
    }
  });

  it('F3 quarter selector default is "previous" (auditor preference)', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    const select = screen.getByLabelText(/Select quarter to issue/i) as HTMLInputElement;
    // HTMLSelectElement.value semantics — HTMLInputElement is a
    // shape-compatible cast acceptable to the eslint globals list.
    expect(select.value).toBe('previous');
  });

  it('F3 changing quarter selector to "current" posts that resolution', async () => {
    let bodyJson: unknown = null;
    globalThis.fetch = vi.fn(
      async (input: string | URL, init?: RequestInit) => {
        const url =
          typeof input === 'string' ? input : (input as URL).toString();
        if (url.includes('/api/client/quarterly-summary')) {
          if (init?.body && typeof init.body === 'string') {
            bodyJson = JSON.parse(init.body);
          }
          return _blobResponse({
            headers: { 'Content-Disposition': 'attachment; filename="x.pdf"' },
          });
        }
        return _jsonResponse({});
      },
    ) as unknown as typeof fetch;

    _renderPage();
    const select = screen.getByLabelText(/Select quarter to issue/i);
    fireEvent.change(select, { target: { value: 'current' } });
    const btn = screen.getByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(bodyJson).not.toBeNull();
    });
    // current quarter must resolve to the current calendar year.
    const body = bodyJson as { year: number; quarter: number };
    const now = new Date();
    expect(body.year).toBe(now.getUTCFullYear());
  });

  it('F3 429 surfaces rate-limit toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/quarterly-summary')) {
        return new Response(JSON.stringify({ detail: 'rate limit' }), {
          status: 429,
          headers: { 'Retry-After': '1800' },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/rate-limited.*~30 min/i)).toBeInTheDocument();
    });
  });

  it('F3 409 surfaces precondition-missing toast', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/quarterly-summary')) {
        return new Response(
          JSON.stringify({ detail: 'Quarter is not yet completed.' }),
          { status: 409 },
        );
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(
        screen.getByText(/Quarter is not yet completed/i),
      ).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------
  // F5 — Wall Certificate
  // ---------------------------------------------------------------

  it('F5 button is disabled when no F1 summary exists', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();

    const btn = screen.getByRole('button', { name: /Download Wall Certificate/i });
    expect(btn).toBeDisabled();
  });

  it('F5 button enables after F1 issuance + clicking downloads blob', async () => {
    let wallCertCalls = 0;
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/wall-cert.pdf')) {
        wallCertCalls++;
        return _blobResponse({
          headers: {
            'Content-Disposition':
              'attachment; filename="wall-cert-nvd-2026-05-08.pdf"',
            'X-Attestation-Hash': 'e'.repeat(64),
          },
        });
      }
      if (url.includes('/api/client/attestation-letter')) {
        return _blobResponse({
          headers: {
            'Content-Disposition':
              'attachment; filename="compliance-attestation-nvd.pdf"',
            'X-Attestation-Hash': 'e'.repeat(64),
            'X-Letter-Valid-Until': '2026-08-06T00:00:00Z',
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    // 1. Issue F1 first.
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(issueBtn);
    await waitFor(() => {
      expect(screen.getByTestId('letter-summary')).toBeInTheDocument();
    });
    // 2. Wall cert button now enabled.
    const wallBtn = screen.getByRole('button', { name: /Download Wall Certificate/i });
    expect(wallBtn).not.toBeDisabled();
    fireEvent.click(wallBtn);
    await waitFor(() => {
      expect(wallCertCalls).toBe(1);
    });
  });

  it('F5 404 toast directs back to Card A', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/wall-cert.pdf')) {
        return new Response(JSON.stringify({ detail: 'not found' }), {
          status: 404,
        });
      }
      if (url.includes('/api/client/attestation-letter')) {
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="x.pdf"',
            'X-Attestation-Hash': 'f'.repeat(64),
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(issueBtn);
    await waitFor(() => {
      expect(screen.getByTestId('letter-summary')).toBeInTheDocument();
    });
    const wallBtn = screen.getByRole('button', { name: /Download Wall Certificate/i });
    fireEvent.click(wallBtn);
    await waitFor(() => {
      expect(
        screen.getByText(/Issue a new Compliance Attestation Letter/i),
      ).toBeInTheDocument();
    });
  });

  it('F5 viewer role gets disabled button + admin-required hint', () => {
    _mockClient({ user: viewerUser });
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    const wallBtn = screen.getByRole('button', {
      name: /Download Wall Certificate.*owner or admin role required/i,
    });
    expect(wallBtn).toBeDisabled();
    expect(
      screen.getByText(/Owner or admin role required to download/i),
    ).toBeInTheDocument();
  });

  it('F5 admin role can download (parity with owner)', async () => {
    _mockClient({ user: adminUser });
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter') && !url.includes('wall-cert')) {
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="x.pdf"',
            'X-Attestation-Hash': '1'.repeat(64),
            'X-Letter-Valid-Until': '2026-08-06T00:00:00Z',
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(issueBtn);
    await waitFor(() => {
      expect(screen.getByTestId('letter-summary')).toBeInTheDocument();
    });
    const wallBtn = screen.getByRole('button', { name: /Download Wall Certificate/i });
    expect(wallBtn).not.toBeDisabled();
  });

  // ---------------------------------------------------------------
  // Public verify URL hash-slug shape (sibling-parity contract)
  // ---------------------------------------------------------------

  it('F1 verify URL renders with osiriscare.io/verify/<hash[:32]> shape', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/attestation-letter')) {
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
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(btn);
    await waitFor(() => {
      // hash[:32] = 32 'b's, NOT 64. The verify URL MUST be the
      // 32-char floor (Brian-the-agent + sibling-parity rule).
      expect(
        screen.getByText(new RegExp(`osiriscare\\.io/verify/b{32}(?!b)`)),
      ).toBeInTheDocument();
    });
  });

  it('F3 verify URL renders with osiriscare.io/verify/quarterly/<hash[:32]> shape', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/quarterly-summary')) {
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="x.pdf"',
            'X-Attestation-Hash': 'c'.repeat(64),
            'X-Summary-Valid-Until': '2027-04-01T00:00:00Z',
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const btn = await screen.findByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(
        screen.getByText(new RegExp(`osiriscare\\.io/verify/quarterly/c{32}(?!c)`)),
      ).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------
  // Auth + a11y
  // ---------------------------------------------------------------

  it('redirects unauthenticated users to /client/login', async () => {
    _mockClient({ user: null, isAuthenticated: false });
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;

    _renderPage();

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/client/login', {
        replace: true,
      });
    });
  });

  it('issue buttons carry aria-label semantics + role=button', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    const f1 = screen.getByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    const f3 = screen.getByRole('button', {
      name: /Issue and download Quarterly Practice Compliance Summary/i,
    });
    const f5 = screen.getByRole('button', { name: /Download Wall Certificate/i });
    expect(f1).toBeInTheDocument();
    expect(f3).toBeInTheDocument();
    expect(f5).toBeInTheDocument();
  });

  // ---------------------------------------------------------------
  // Banned-words sweep + sibling-parity copy divergence
  // ---------------------------------------------------------------

  it('rendered output contains no banned legal-language words', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();

    const text = document.body.textContent || '';
    const banned = [
      'ensures',
      'prevents',
      'protects',
      'guarantees',
      'audit-ready',
      'PHI never leaves',
      '100%',
    ];
    for (const w of banned) {
      expect(
        text.toLowerCase().includes(w.toLowerCase()),
        `Banned word found in render: ${w}`,
      ).toBe(false);
    }
  });

  it('uses the canonical "monitored on a continuous automated schedule" cadence verb', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    const text = document.body.textContent || '';
    expect(
      text.includes('monitored on a continuous automated schedule'),
    ).toBe(true);
    // Sibling-parity check: NOT the partner-specific cadence shape
    // ("aggregate substrate posture across all clinics") — that's
    // partner copy (P-F5 portfolio attestation) and must NOT leak
    // into the owner-side page.
    expect(text).not.toMatch(/aggregate substrate posture across all clinics/i);
  });

  it('sibling-parity: page does NOT use partner-specific framing words', () => {
    globalThis.fetch = vi.fn(async () => _jsonResponse({})) as unknown as typeof fetch;
    _renderPage();
    const text = document.body.textContent || '';
    // These are partner-portal-specific framing strings that must
    // NOT appear on the owner-side page (the artifact set diverges
    // by design).
    expect(text).not.toMatch(/portfolio attestation/i);
    expect(text).not.toMatch(/downstream BAA/i);
    expect(text).not.toMatch(/BA Compliance Letter/i);
  });

  // -----------------------------------------------------------------
  // Plan-38 D4 + D6 fix-up: verify caption + PO pre-flight gate
  // -----------------------------------------------------------------

  it('D4: F1 + F3 verify URLs carry the auditor-handoff caption', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/privacy-officer')) {
        return _jsonResponse({
          designation: { id: 'po-uid', name: 'Smoke', title: 'Officer' },
        });
      }
      if (url.includes('/api/client/attestation-letter')) {
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="letter.pdf"',
            'X-Attestation-Hash': 'a'.repeat(64),
            'X-Letter-Valid-Until': '2026-12-31T00:00:00Z',
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    fireEvent.click(issueBtn);
    await waitFor(() => {
      expect(
        screen.getByText(
          /Send this URL alongside the PDF.*verify cryptographically without contacting OsirisCare/i,
        ),
      ).toBeInTheDocument();
    });
  });

  it('D6: PO-missing intercepts F1 issuance with modal routing to /client/compliance', async () => {
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/privacy-officer')) {
        // Missing-PO shape per backend client_portal.py:5219.
        return _jsonResponse({ designation: null });
      }
      // F1 endpoint should NEVER be called when PO missing — return
      // an error so the test fails loudly if the gate doesn't fire.
      if (url.includes('/api/client/attestation-letter')) {
        return _jsonResponse({ detail: 'should-not-be-called' }, 500);
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    // Wait for the PO-fetch effect to settle (poStatus → 'missing')
    // before clicking. A small tick is enough.
    await new Promise((r) => setTimeout(r, 0));
    fireEvent.click(issueBtn);
    // Modal renders synchronously after the click.
    expect(
      await screen.findByRole('dialog', { name: /Designate a Privacy Officer first/i }),
    ).toBeInTheDocument();
    // Click "Designate now" → navigate('/client/compliance').
    fireEvent.click(screen.getByRole('button', { name: /Designate now/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/client/compliance');
  });

  it('D6: PO-designated lets F1 issuance flow through normally', async () => {
    let f1Called = false;
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/privacy-officer')) {
        return _jsonResponse({
          designation: { id: 'po-uid', name: 'Smoke', title: 'Officer' },
        });
      }
      if (url.includes('/api/client/attestation-letter')) {
        f1Called = true;
        return _blobResponse({
          headers: {
            'Content-Disposition': 'attachment; filename="letter.pdf"',
            'X-Attestation-Hash': 'a'.repeat(64),
          },
        });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;

    _renderPage();
    const issueBtn = await screen.findByRole('button', {
      name: /Issue and download Compliance Attestation Letter/i,
    });
    await new Promise((r) => setTimeout(r, 0));
    fireEvent.click(issueBtn);
    await waitFor(() => {
      expect(f1Called).toBe(true);
    });
    // Modal MUST NOT render in the designated-PO path.
    expect(
      screen.queryByRole('dialog', { name: /Designate a Privacy Officer first/i }),
    ).not.toBeInTheDocument();
  });
});
