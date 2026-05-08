/**
 * ClientDashboard.test.tsx — covers plan-38 wave-2 D1 + D8 deltas.
 *
 * Wave-2 deltas under test:
 *   - D1: Compliance Attestation hero card renders above the dashboard
 *     KPI tile grid; primary CTA reads "Issue Compliance Attestation
 *     Letter"; secondary "View all attestations"; helper line "Print
 *     one for your auditor, insurance underwriter, or to display in
 *     the lobby"; routes to /client/attestations.
 *   - D1 sibling-parity: the prior Quick-Links Attestations tile is
 *     REMOVED — only ONE entry surface (the hero card) per Sarah-PM
 *     verdict.
 *   - D8: hero card carries mobile-first responsive classes
 *     (full-width on mobile via w-full, bounded width on desktop via
 *     md:max-w-3xl + md:mx-auto; CTA stack via flex-col + sm:flex-row).
 *
 * Coverage rationale: ClientDashboard is large and integrates many
 * subcomponents (ComplianceHealthInfographic, ClientAppliances, etc.).
 * These tests render the dashboard with a stub for each child + the
 * useClient hook + minimal fetch mocks so the assertions stay
 * focused on the hero-card surface only.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock useClient.
const mockUseClient = vi.fn();
vi.mock('../ClientContext', () => ({
  useClient: () => mockUseClient(),
}));

// Mock react-router-dom navigate (the dashboard calls navigate on
// auth failure; we don't exercise that path here but the hook must
// exist).
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

// Stub out heavy children so the dashboard mounts cleanly in jsdom
// without pulling in chart libs / network dependencies. The hero
// card sits at the top of <main> — these stubs just prevent noise
// below it.
vi.mock('../ComplianceHealthInfographic', () => ({
  ComplianceHealthInfographic: () => <div data-testid="stub-infographic" />,
}));
vi.mock('../DevicesAtRisk', () => ({
  DevicesAtRisk: () => <div data-testid="stub-devices-at-risk" />,
}));
vi.mock('../ClientAppliances', () => ({
  ClientAppliances: () => <div data-testid="stub-client-appliances" />,
}));
vi.mock('../ClientDriftConfig', () => ({
  ClientDriftConfig: () => <div data-testid="stub-drift-config" />,
}));
vi.mock('../../components/shared', () => ({
  OsirisCareLeaf: () => <span data-testid="stub-leaf" />,
  WelcomeModal: () => null,
  InfoTip: () => null,
  DashboardErrorBoundary: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));
vi.mock('../../components/composed', () => ({
  DisclaimerFooter: () => <div data-testid="stub-disclaimer" />,
}));

// IMPORTANT: import AFTER all vi.mock() calls so the stubs are wired
// in before the module graph resolves.
import { ClientDashboard } from '../ClientDashboard';

const ownerUser = {
  id: 'u1',
  email: 'maria@nvd.example',
  name: 'Maria',
  role: 'owner' as const,
  org: { id: 'o1', name: 'North Valley Dental' },
};

function _mockClient(opts: {
  user?: typeof ownerUser | null;
  isAuthenticated?: boolean;
  isLoading?: boolean;
} = {}) {
  mockUseClient.mockReturnValue({
    user: opts.user === undefined ? ownerUser : opts.user,
    isAuthenticated: opts.isAuthenticated ?? true,
    isLoading: opts.isLoading ?? false,
    error: null,
    logout: vi.fn(),
    checkSession: vi.fn(),
  });
}

function _jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function _renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/client/dashboard']}>
      <ClientDashboard />
    </MemoryRouter>,
  );
}

describe('ClientDashboard — plan-38 wave-2 D1 + D8 hero card', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    mockNavigate.mockClear();
    mockUseClient.mockReset();
    _mockClient();
    // Stable dashboard payload — the hero card renders independently
    // of this data, but the dashboard page itself fetches on mount.
    globalThis.fetch = vi.fn(async (input: string | URL) => {
      const url =
        typeof input === 'string' ? input : (input as URL).toString();
      if (url.includes('/api/client/dashboard')) {
        return _jsonResponse({
          org: {
            id: 'o1',
            name: 'North Valley Dental',
            partner_name: null,
            partner_brand: null,
            provider_count: 3,
          },
          sites: [],
          kpis: {
            compliance_score: 92.5,
            score_status: 'healthy',
            score_source: 'bundles',
            total_checks: 100,
            passed: 92,
            failed: 5,
            warnings: 3,
          },
          agent_compliance: null,
          unread_notifications: 0,
        });
      }
      if (url.includes('/api/client/agent/install-info')) {
        // Shape matches AgentInstallInfo — sites array required so
        // the dashboard's agentInfo.sites.length truthy check works.
        return _jsonResponse({ sites: [], agent_version: '1.0.0' });
      }
      if (url.includes('/api/client/notifications')) {
        return _jsonResponse({ notifications: [] });
      }
      return _jsonResponse({});
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('D1: hero card renders with primary "Issue" CTA + canonical heading', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('attestation-hero-card');
    expect(card.textContent).toMatch(/Issue Compliance Attestation Letter/);
    expect(card.textContent).toMatch(/Issue letter/);
    expect(card.textContent).toMatch(/View all attestations/);
  });

  it('D1: hero card helper line uses Maria-voice copy', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    // Plan-38 D1 specific copy: "Print one for your auditor,
    // insurance underwriter, or to display in the lobby."
    expect(
      screen.getByText(
        /Print one for your auditor, insurance underwriter, or to display in the lobby/,
      ),
    ).toBeInTheDocument();
  });

  it('D1: hero card is an <a> linking to /client/attestations', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('attestation-hero-card');
    expect(card.tagName.toLowerCase()).toBe('a');
    expect(card.getAttribute('href')).toBe('/client/attestations');
  });

  it('D1: hero card carries an aria-label for screen readers', async () => {
    _renderDashboard();
    await waitFor(() => {
      const card = screen.getByTestId('attestation-hero-card');
      expect(card.getAttribute('aria-label')).toMatch(
        /Issue Compliance Attestation Letter/i,
      );
    });
  });

  it('D1 sibling-parity: prior Quick-Links Attestations tile is REMOVED (single hero entry surface)', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    // The Quick-Links area still exists for HIPAA Compliance,
    // Healing Activity, etc. — but the prior "Letter, quarterly
    // summary, wall certificate" Attestations TILE must be gone
    // per Sarah-PM verdict (one hero card; one entry surface).
    expect(
      screen.queryByText(/Letter, quarterly summary, wall certificate/i),
    ).not.toBeInTheDocument();
    // Exactly one link to /client/attestations on the page.
    const links = Array.from(
      document.querySelectorAll('a[href="/client/attestations"]'),
    );
    expect(links.length).toBe(1);
    // And that one link IS the hero card.
    expect(links[0].getAttribute('data-testid')).toBe(
      'attestation-hero-card',
    );
  });

  it('D8: hero card uses mobile-first responsive classes (w-full + md:max-w-3xl)', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    // Container <div> wrapping the hero <a>: full-width on mobile,
    // bounded + centered from md: up.
    const card = screen.getByTestId('attestation-hero-card');
    const container = card.parentElement;
    expect(container).not.toBeNull();
    expect(container?.className).toMatch(/w-full/);
    expect(container?.className).toMatch(/md:max-w-3xl/);
    expect(container?.className).toMatch(/md:mx-auto/);
  });

  it('D8: hero card inner layout stacks on mobile, side-by-side from sm: up', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    const card = screen.getByTestId('attestation-hero-card');
    // First child is the inner padded flex row.
    const inner = card.firstElementChild as HTMLElement;
    expect(inner.className).toMatch(/flex-col/);
    expect(inner.className).toMatch(/sm:flex-row/);
  });

  it('D1 carol-gate: hero card copy is banned-words-clean', async () => {
    _renderDashboard();
    await waitFor(() => {
      expect(screen.getByTestId('attestation-hero-card')).toBeInTheDocument();
    });
    const text = screen.getByTestId('attestation-hero-card').textContent || '';
    const banned = [
      'ensures',
      'prevents',
      'protects',
      'guarantees',
      'audit-ready',
      'PHI never leaves',
      '100%',
      'continuously monitored',
    ];
    for (const w of banned) {
      expect(
        text.toLowerCase().includes(w.toLowerCase()),
        `Banned word found in hero card: ${w}`,
      ).toBe(false);
    }
  });
});
