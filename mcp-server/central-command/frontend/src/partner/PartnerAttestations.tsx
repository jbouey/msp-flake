/**
 * PartnerAttestations — top-level partner-portal surface for the
 * P-F5 (Portfolio Attestation) + P-F6 (BA Compliance + downstream-BAA
 * roster) artifacts. Closes the #1 CRITICAL UI gap from the
 * 2026-05-08 partner-portal adversarial audit (.agent/plans/35) +
 * Decision 1 from .agent/plans/36-next-sprint-ui-queue.
 *
 * Design notes:
 *   - Two stacked cards: Portfolio (top) + BA Compliance (bottom).
 *   - Backend has NO read-only summary endpoint — `GET /me/portfolio-
 *     attestation` and `GET /me/ba-attestation` issue + return PDFs
 *     directly. So this page treats issuance + download as one action,
 *     and surfaces summary metadata captured from the most recent
 *     download response headers (X-Attestation-Hash, X-Letter-Valid-
 *     Until). Local cache survives a rerender; refresh wipes it.
 *   - Roster (P-F6) IS a read-only listing — `GET /me/ba-roster` —
 *     and is the primary persistence surface on this page.
 *   - All mutations go through portalFetch (postJson/deleteJson),
 *     which auto-injects credentials:'include' + X-CSRF-Token. CSRF
 *     baseline test stays at 0.
 *   - Role gate: read = admin OR tech; mutate = admin only. Backend
 *     remains authoritative; this is client-side cosmetic gating.
 *   - Add-BAA modal + Revoke confirm are temporary inline shapes
 *     with TODO markers to migrate to <DangerousActionModal> once
 *     it lands (Decision 2 of plan 36).
 *   - Banned-words sweep: no "ensures"/"prevents"/"protects"/
 *     "guarantees"/"audit-ready"/"PHI never leaves"/"100%". Cadence
 *     verb is "monitored on a continuous automated schedule".
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { usePartner } from './PartnerContext';
import {
  fetchBlob,
  getJson,
  postJson,
  deleteJson,
  type PortalFetchError,
} from '../utils/portalFetch';

// ---------- Types ----------

interface RosterEntry {
  id: string;
  counterparty_org_id: string | null;
  counterparty_practice_name: string | null;
  executed_at: string | null;
  expiry_at: string | null;
  scope: string;
  signer_name: string;
  signer_title: string;
  signer_email: string | null;
  attestation_bundle_id: string | null;
}

interface RosterResponse {
  roster: RosterEntry[];
}

interface OrgPickerEntry {
  id: string;
  name: string;
}

interface OrgsResponse {
  // /me/orgs returns an array (top-level), or could be wrapped — we
  // accept both shapes defensively below.
  orgs?: OrgPickerEntry[];
}

interface AttestationSummary {
  attestation_hash: string;
  valid_until: string | null;
  filename: string;
  fetched_at: string;
}

// ---------- Helpers ----------

const PUBLIC_VERIFY_PORTFOLIO_BASE = 'osiriscare.io/verify/portfolio/';

function _filenameFromHeaders(res: Response, fallback: string): string {
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="?([^";]+)"?/i);
  return m?.[1] || fallback;
}

function _formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

function _safeBrand(brand: string | undefined | null): string {
  if (!brand) return 'partner';
  return brand.replace(/[^a-zA-Z0-9_-]+/g, '-').toLowerCase();
}

function _truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + '…';
}

// Trigger browser save-dialog for a blob.
function _saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------- Small toast helper (inline; we don't import a global toast lib) ----------

interface Toast {
  id: number;
  kind: 'success' | 'error' | 'info';
  message: string;
}

let _toastCounter = 0;

// ---------- Component ----------

export const PartnerAttestations: React.FC = () => {
  const navigate = useNavigate();
  const { partner, isAuthenticated, isLoading } = usePartner();

  // Auth redirect
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/partner/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  const isAdmin = partner?.user_role === 'admin';
  const canRead =
    partner?.user_role === 'admin' || partner?.user_role === 'tech';

  // ---------- State ----------
  const [toasts, setToasts] = useState<Toast[]>([]);
  const pushToast = (kind: Toast['kind'], message: string) => {
    const id = ++_toastCounter;
    setToasts((prev) => [...prev, { id, kind, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  };

  // Portfolio (Card A)
  const [portfolioSummary, setPortfolioSummary] =
    useState<AttestationSummary | null>(null);
  const [portfolioBusy, setPortfolioBusy] = useState<
    'idle' | 'issuing' | 'downloading'
  >('idle');

  // BA Compliance (Card B)
  const [baSummary, setBaSummary] = useState<AttestationSummary | null>(null);
  const [baBusy, setBaBusy] = useState<'idle' | 'issuing' | 'downloading'>(
    'idle',
  );

  // Roster
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [rosterLoading, setRosterLoading] = useState<boolean>(true);
  const [rosterError, setRosterError] = useState<string | null>(null);

  // Org picker (for AddBAA modal)
  const [orgs, setOrgs] = useState<OrgPickerEntry[]>([]);

  // AddBAA modal
  const [addOpen, setAddOpen] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addCounterpartyMode, setAddCounterpartyMode] = useState<
    'org' | 'external'
  >('org');
  const [addOrgId, setAddOrgId] = useState<string>('');
  const [addPracticeName, setAddPracticeName] = useState<string>('');
  const [addExecutedAt, setAddExecutedAt] = useState<string>('');
  const [addExpiryAt, setAddExpiryAt] = useState<string>('');
  const [addScope, setAddScope] = useState<string>('');
  const [addSignerName, setAddSignerName] = useState<string>('');
  const [addSignerTitle, setAddSignerTitle] = useState<string>('');
  const [addSignerEmail, setAddSignerEmail] = useState<string>('');
  const [addDocSha256, setAddDocSha256] = useState<string>('');

  // Revoke confirm (per-row inline)
  const [revokeRowId, setRevokeRowId] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState<string>('');
  const [revokeTypedConfirm, setRevokeTypedConfirm] = useState<string>('');
  const [revokeBusy, setRevokeBusy] = useState(false);

  // ---------- Data fetchers ----------

  const fetchRoster = async () => {
    setRosterLoading(true);
    setRosterError(null);
    try {
      const r = await getJson<RosterResponse>('/api/partners/me/ba-roster');
      setRoster(r?.roster ?? []);
    } catch (e) {
      const err = e as PortalFetchError;
      setRosterError(err.detail || err.message || 'Failed to load roster');
    } finally {
      setRosterLoading(false);
    }
  };

  const fetchOrgs = async () => {
    try {
      // /me/orgs returns an array under top-level (older shape) OR an
      // object with `orgs` key (some endpoints). Accept both.
      const res = await fetch('/api/partners/me/orgs', {
        credentials: 'include',
      });
      if (!res.ok) return;
      const data = (await res.json()) as
        | OrgPickerEntry[]
        | OrgsResponse
        | { organizations?: OrgPickerEntry[] };
      let list: OrgPickerEntry[] = [];
      if (Array.isArray(data)) {
        list = data;
      } else if (Array.isArray((data as OrgsResponse).orgs)) {
        list = (data as OrgsResponse).orgs ?? [];
      } else if (
        Array.isArray(
          (data as { organizations?: OrgPickerEntry[] }).organizations,
        )
      ) {
        list = (data as { organizations: OrgPickerEntry[] }).organizations;
      }
      setOrgs(list.map((o) => ({ id: o.id, name: o.name })));
    } catch {
      // Best-effort — modal will fall back to free-text mode.
    }
  };

  useEffect(() => {
    if (isAuthenticated && canRead) {
      void fetchRoster();
      void fetchOrgs();
    }
  }, [isAuthenticated, canRead]);

  // ---------- Portfolio actions (Card A) ----------

  const handlePortfolioIssue = async () => {
    if (!isAdmin) {
      pushToast('error', "You don't have permission to issue attestations.");
      return;
    }
    setPortfolioBusy('issuing');
    try {
      const res = await fetchBlob('/api/partners/me/portfolio-attestation');
      const blob = await res.blob();
      const fallback = `portfolio-attestation-${_safeBrand(
        partner?.brand_name,
      )}.pdf`;
      const filename = _filenameFromHeaders(res, fallback);
      _saveBlob(blob, filename);
      const hash = res.headers.get('X-Attestation-Hash') || '';
      const validUntil = res.headers.get('X-Letter-Valid-Until');
      setPortfolioSummary({
        attestation_hash: hash,
        valid_until: validUntil,
        filename,
        fetched_at: new Date().toISOString(),
      });
      pushToast(
        'success',
        'Portfolio attestation issued and downloaded.',
      );
    } catch (e) {
      const err = e as PortalFetchError & { retryAfter?: string };
      if (err.status === 401) {
        pushToast('error', 'Your session expired. Sign in again.');
      } else if (err.status === 403) {
        pushToast(
          'error',
          "You don't have permission to issue attestations.",
        );
      } else if (err.status === 429) {
        const retry = err.retryAfter || '3600';
        const mins = Math.ceil(Number(retry) / 60);
        pushToast(
          'error',
          `Issuance is rate-limited (5/hr). Try again in ~${mins} min.`,
        );
      } else if (err.status === 409) {
        pushToast(
          'error',
          err.detail ||
            'Another attestation is already in flight. Try again shortly.',
        );
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Issuance failed. Contact support.',
        );
      }
    } finally {
      setPortfolioBusy('idle');
    }
  };

  // ---------- BA Compliance actions (Card B) ----------

  const handleBaIssue = async () => {
    if (!isAdmin) {
      pushToast('error', "You don't have permission to issue attestations.");
      return;
    }
    setBaBusy('issuing');
    try {
      const res = await fetchBlob('/api/partners/me/ba-attestation');
      const blob = await res.blob();
      const fallback = `ba-compliance-${_safeBrand(
        partner?.brand_name,
      )}.pdf`;
      const filename = _filenameFromHeaders(res, fallback);
      _saveBlob(blob, filename);
      const hash = res.headers.get('X-Attestation-Hash') || '';
      setBaSummary({
        attestation_hash: hash,
        valid_until: null,
        filename,
        fetched_at: new Date().toISOString(),
      });
      pushToast(
        'success',
        'BA Compliance Letter issued and downloaded.',
      );
    } catch (e) {
      const err = e as PortalFetchError & { retryAfter?: string };
      if (err.status === 401) {
        pushToast('error', 'Your session expired. Sign in again.');
      } else if (err.status === 403) {
        pushToast(
          'error',
          "You don't have permission to issue attestations.",
        );
      } else if (err.status === 429) {
        const retry = err.retryAfter || '3600';
        const mins = Math.ceil(Number(retry) / 60);
        pushToast(
          'error',
          `Issuance is rate-limited (5/hr). Try again in ~${mins} min.`,
        );
      } else if (err.status === 409) {
        pushToast(
          'error',
          err.detail ||
            'No active downstream BAAs on roster yet — add at least one before issuing.',
        );
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Issuance failed. Contact support.',
        );
      }
    } finally {
      setBaBusy('idle');
    }
  };

  // Add-BAA modal
  const _resetAddForm = () => {
    setAddCounterpartyMode('org');
    setAddOrgId('');
    setAddPracticeName('');
    setAddExecutedAt('');
    setAddExpiryAt('');
    setAddScope('');
    setAddSignerName('');
    setAddSignerTitle('');
    setAddSignerEmail('');
    setAddDocSha256('');
    setAddError(null);
  };

  const handleAddSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddError(null);
    if (addCounterpartyMode === 'org' && !addOrgId) {
      setAddError('Pick a counterparty client OR switch to external mode.');
      return;
    }
    if (addCounterpartyMode === 'external' && !addPracticeName.trim()) {
      setAddError('Counterparty practice name is required in external mode.');
      return;
    }
    if (!addExecutedAt) {
      setAddError('Executed date is required.');
      return;
    }
    if (addScope.trim().length < 20) {
      setAddError('Scope must be at least 20 characters.');
      return;
    }
    if (!addSignerName.trim() || !addSignerTitle.trim()) {
      setAddError('Signer name and title are required.');
      return;
    }
    setAddBusy(true);
    try {
      const body: Record<string, unknown> = {
        executed_at: addExecutedAt,
        expiry_at: addExpiryAt || null,
        scope: addScope.trim(),
        signer_name: addSignerName.trim(),
        signer_title: addSignerTitle.trim(),
        signer_email: addSignerEmail.trim() || null,
        doc_sha256: addDocSha256.trim() || null,
      };
      if (addCounterpartyMode === 'org') {
        body.counterparty_org_id = addOrgId;
      } else {
        body.counterparty_practice_name = addPracticeName.trim();
      }
      await postJson('/api/partners/me/ba-roster', body);
      pushToast('success', 'BAA added to roster.');
      setAddOpen(false);
      _resetAddForm();
      await fetchRoster();
    } catch (e) {
      const err = e as PortalFetchError;
      if (err.status === 409) {
        setAddError(
          err.detail ||
            'An active BAA exists for this counterparty — revoke first.',
        );
      } else if (err.status === 403) {
        setAddError("You don't have permission to add a BAA.");
      } else {
        setAddError(err.detail || err.message || 'Add BAA failed.');
      }
    } finally {
      setAddBusy(false);
    }
  };

  // Revoke confirm
  const _activeRevokeRow = useMemo(
    () => roster.find((r) => r.id === revokeRowId) || null,
    [roster, revokeRowId],
  );

  const _revokeLabel = (row: RosterEntry | null): string => {
    if (!row) return '';
    return (
      row.counterparty_practice_name ||
      orgs.find((o) => o.id === row.counterparty_org_id)?.name ||
      'this counterparty'
    );
  };

  const handleRevokeSubmit = async () => {
    if (!_activeRevokeRow) return;
    if (revokeReason.trim().length < 20) {
      pushToast('error', 'Reason must be at least 20 characters.');
      return;
    }
    const expectedLabel = _revokeLabel(_activeRevokeRow);
    if (revokeTypedConfirm.trim() !== expectedLabel) {
      pushToast(
        'error',
        `Confirmation did not match. Type "${expectedLabel}" exactly.`,
      );
      return;
    }
    setRevokeBusy(true);
    try {
      await deleteJson(
        `/api/partners/me/ba-roster/${encodeURIComponent(_activeRevokeRow.id)}`,
        { reason: revokeReason.trim() },
      );
      pushToast('success', 'BAA revoked.');
      setRevokeRowId(null);
      setRevokeReason('');
      setRevokeTypedConfirm('');
      await fetchRoster();
    } catch (e) {
      const err = e as PortalFetchError;
      if (err.status === 403) {
        pushToast('error', "You don't have permission to revoke this BAA.");
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Revoke failed.',
        );
      }
    } finally {
      setRevokeBusy(false);
    }
  };

  // Copy-to-clipboard for verify URL
  const handleCopyVerify = (hashHex: string) => {
    const url = `${PUBLIC_VERIFY_PORTFOLIO_BASE}${hashHex.slice(0, 32)}`;
    navigator.clipboard
      .writeText(url)
      .then(() => pushToast('success', 'Verify URL copied.'))
      .catch(() => pushToast('error', 'Copy failed. Select text manually.'));
  };

  // ---------- Render ----------

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50/80 flex items-center justify-center">
        <div
          className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"
          aria-label="Loading"
          role="status"
        />
      </div>
    );
  }

  if (!partner) return null;

  const presenterBrand = partner.brand_name || partner.name || 'your team';

  return (
    <div className="min-h-screen bg-slate-50/80">
      {/* Header + tab strip */}
      <header
        className="sticky top-0 z-30 border-b border-slate-200/60"
        style={{
          background: 'rgba(255,255,255,0.82)',
          backdropFilter: 'blur(20px) saturate(180%)',
          WebkitBackdropFilter: 'blur(20px) saturate(180%)',
        }}
      >
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              to="/partner/dashboard"
              className="p-2 text-slate-500 hover:text-indigo-600 rounded-lg hover:bg-indigo-50 transition"
              aria-label="Back to partner dashboard"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M15 19l-7-7 7-7"
                />
              </svg>
            </Link>
            <div>
              <h1 className="text-lg font-semibold text-slate-900 tracking-tight">
                Attestations
              </h1>
              <p className="text-xs text-slate-500">
                Printable, hash-bound attestations of {presenterBrand}'s
                substrate posture and downstream BAA chain.
              </p>
            </div>
          </div>
        </div>
      </header>

      {/* Toasts */}
      <div
        className="fixed top-4 right-4 z-50 flex flex-col gap-2"
        aria-live="polite"
        aria-atomic="true"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`px-4 py-2 rounded-lg shadow-lg text-sm font-medium ${
              t.kind === 'success'
                ? 'bg-emerald-600 text-white'
                : t.kind === 'error'
                  ? 'bg-rose-600 text-white'
                  : 'bg-slate-700 text-white'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {!canRead && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-amber-900 text-sm">
            Your role does not have access to attestation artifacts.
            Contact your partner administrator to request access.
          </div>
        )}

        {canRead && (
          <>
            {/* ---------- Card A: Portfolio Attestation ---------- */}
            <section
              data-testid="portfolio-card"
              className="bg-white rounded-2xl shadow-sm border border-slate-200"
              aria-labelledby="portfolio-heading"
            >
              <div className="px-6 py-4 border-b border-slate-100">
                <h2
                  id="portfolio-heading"
                  className="text-base font-semibold text-slate-900"
                >
                  Portfolio Attestation
                </h2>
                <p className="mt-1 text-sm text-slate-600">
                  Aggregate substrate posture across all clinics monitored on a
                  continuous automated schedule. Hand to prospects + auditors as
                  proof of operational scale + Bitcoin-anchored evidence chain.
                </p>
              </div>
              <div className="px-6 py-5 space-y-4">
                {portfolioSummary ? (
                  <div
                    data-testid="portfolio-summary"
                    className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm"
                  >
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                      <div>
                        <div className="text-xs text-slate-500">
                          Latest filename
                        </div>
                        <div className="font-mono text-xs break-all text-slate-800">
                          {portfolioSummary.filename}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-500">
                          Issued at (this session)
                        </div>
                        <div className="text-slate-800 tabular-nums">
                          {_formatDate(portfolioSummary.fetched_at)}
                        </div>
                      </div>
                      {portfolioSummary.valid_until && (
                        <div>
                          <div className="text-xs text-slate-500">
                            Valid until
                          </div>
                          <div className="text-slate-800 tabular-nums">
                            {_formatDate(portfolioSummary.valid_until)}
                          </div>
                        </div>
                      )}
                      <div>
                        <div className="text-xs text-slate-500">
                          Attestation hash
                        </div>
                        <div className="font-mono text-xs break-all text-slate-800">
                          {portfolioSummary.attestation_hash || '—'}
                        </div>
                      </div>
                    </div>
                    {portfolioSummary.attestation_hash && (
                      <div className="mt-3 flex items-center gap-2 text-xs">
                        <span className="text-slate-500">Public verify URL:</span>
                        <code className="bg-white border border-slate-200 px-2 py-1 rounded font-mono text-[11px]">
                          {PUBLIC_VERIFY_PORTFOLIO_BASE}
                          {portfolioSummary.attestation_hash.slice(0, 32)}
                        </code>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopyVerify(portfolioSummary.attestation_hash)
                          }
                          className="text-indigo-600 hover:underline"
                          aria-label="Copy public verify URL"
                        >
                          Copy
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div
                    data-testid="portfolio-empty"
                    className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-600"
                  >
                    No portfolio attestation downloaded in this session yet.
                    Issue your first attestation to share with prospects.
                  </div>
                )}

                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={handlePortfolioIssue}
                    disabled={
                      portfolioBusy !== 'idle' || !isAdmin
                    }
                    aria-busy={portfolioBusy === 'issuing'}
                    aria-label={
                      isAdmin
                        ? 'Issue new portfolio attestation'
                        : 'Issue new portfolio attestation (admin role required)'
                    }
                    className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                  >
                    {portfolioBusy === 'issuing' && (
                      <span
                        className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
                        aria-hidden="true"
                      />
                    )}
                    {portfolioBusy === 'issuing'
                      ? 'Issuing…'
                      : portfolioSummary
                        ? 'Issue + download new attestation'
                        : 'Issue + download attestation'}
                  </button>
                  {!isAdmin && (
                    <span className="text-xs text-slate-500 self-center">
                      Read-only view — admin role required to issue.
                    </span>
                  )}
                </div>
              </div>
            </section>

            {/* ---------- Card B: BA Compliance + Roster ---------- */}
            <section
              data-testid="ba-compliance-card"
              className="bg-white rounded-2xl shadow-sm border border-slate-200"
              aria-labelledby="ba-heading"
            >
              <div className="px-6 py-4 border-b border-slate-100">
                <h2
                  id="ba-heading"
                  className="text-base font-semibold text-slate-900"
                >
                  BA Compliance + Downstream BAA Roster
                </h2>
                <p className="mt-1 text-sm text-slate-600">
                  Three-party BAA chain: OsirisCare→{presenterBrand}{' '}
                  Subcontractor BAA + {presenterBrand}→clinic downstream BAAs.
                  Auditor-supportive evidence; not a §164.528 disclosure
                  accounting.
                </p>
              </div>

              <div className="px-6 py-5 space-y-5">
                {/* BA letter summary + download */}
                {baSummary ? (
                  <div
                    data-testid="ba-summary"
                    className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm"
                  >
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                      <div>
                        <div className="text-xs text-slate-500">
                          Latest filename
                        </div>
                        <div className="font-mono text-xs break-all text-slate-800">
                          {baSummary.filename}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-500">
                          Issued at (this session)
                        </div>
                        <div className="text-slate-800 tabular-nums">
                          {_formatDate(baSummary.fetched_at)}
                        </div>
                      </div>
                      <div className="col-span-2">
                        <div className="text-xs text-slate-500">
                          Attestation hash
                        </div>
                        <div className="font-mono text-xs break-all text-slate-800">
                          {baSummary.attestation_hash || '—'}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : null}

                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={handleBaIssue}
                    disabled={baBusy !== 'idle' || !isAdmin}
                    aria-busy={baBusy === 'issuing'}
                    aria-label="Issue and download BA Compliance Letter"
                    className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                  >
                    {baBusy === 'issuing' && (
                      <span
                        className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
                        aria-hidden="true"
                      />
                    )}
                    {baBusy === 'issuing'
                      ? 'Issuing…'
                      : 'Issue + download BA Compliance Letter'}
                  </button>
                </div>

                {/* Roster table */}
                <div className="rounded-lg border border-slate-200 overflow-hidden">
                  <div className="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">
                      Downstream BAA roster ({roster.length})
                    </h3>
                    <button
                      type="button"
                      onClick={() => {
                        if (!isAdmin) {
                          pushToast(
                            'error',
                            "You don't have permission to add a BAA.",
                          );
                          return;
                        }
                        _resetAddForm();
                        setAddOpen(true);
                      }}
                      disabled={!isAdmin}
                      className="px-3 py-1.5 text-sm rounded-md bg-white border border-slate-300 text-slate-700 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed"
                      aria-label="Add new BAA to roster"
                    >
                      Add BAA
                    </button>
                  </div>

                  {rosterError && (
                    <div className="px-4 py-3 bg-rose-50 border-b border-rose-200 text-sm text-rose-700">
                      {rosterError}
                    </div>
                  )}

                  {rosterLoading ? (
                    <div className="px-4 py-8 text-center">
                      <div
                        className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mx-auto"
                        aria-label="Loading roster"
                        role="status"
                      />
                    </div>
                  ) : roster.length === 0 ? (
                    <div
                      data-testid="roster-empty"
                      className="px-4 py-8 text-center text-sm text-slate-500"
                    >
                      No downstream BAAs on roster yet. Add one before issuing
                      your BA Compliance Letter.
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
                          <tr>
                            <th className="px-4 py-2 text-left">Counterparty</th>
                            <th className="px-4 py-2 text-left">Executed</th>
                            <th className="px-4 py-2 text-left">Expires</th>
                            <th className="px-4 py-2 text-left">Scope</th>
                            <th className="px-4 py-2 text-left">Signer</th>
                            {isAdmin && (
                              <th className="px-4 py-2 text-right">Actions</th>
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {roster.map((row) => {
                            const orgName = orgs.find(
                              (o) => o.id === row.counterparty_org_id,
                            )?.name;
                            const cpLabel =
                              row.counterparty_practice_name ||
                              orgName ||
                              row.counterparty_org_id ||
                              '—';
                            return (
                              <tr
                                key={row.id}
                                className="border-t border-slate-100 align-top"
                              >
                                <td className="px-4 py-3 font-medium text-slate-900">
                                  {cpLabel}
                                </td>
                                <td className="px-4 py-3 text-slate-700 tabular-nums">
                                  {_formatDate(row.executed_at)}
                                </td>
                                <td className="px-4 py-3 text-slate-700 tabular-nums">
                                  {_formatDate(row.expiry_at)}
                                </td>
                                <td
                                  className="px-4 py-3 text-slate-700 max-w-md"
                                  title={row.scope}
                                >
                                  {_truncate(row.scope, 80)}
                                </td>
                                <td className="px-4 py-3 text-slate-700">
                                  <div className="font-medium">
                                    {row.signer_name}
                                  </div>
                                  <div className="text-xs text-slate-500">
                                    {row.signer_title}
                                  </div>
                                  {row.signer_email && (
                                    <div className="text-xs text-slate-500">
                                      {row.signer_email}
                                    </div>
                                  )}
                                </td>
                                {isAdmin && (
                                  <td className="px-4 py-3 text-right">
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setRevokeRowId(row.id);
                                        setRevokeReason('');
                                        setRevokeTypedConfirm('');
                                      }}
                                      className="px-3 py-1 text-xs rounded-md bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100"
                                      aria-label={`Revoke BAA for ${cpLabel}`}
                                    >
                                      Revoke
                                    </button>
                                  </td>
                                )}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            </section>
          </>
        )}
      </main>

      {/* ---------- Add-BAA modal (TODO: migrate to <DangerousActionModal>) ---------- */}
      {addOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-baa-title"
          onClick={(e) => {
            if (e.target === e.currentTarget && !addBusy) setAddOpen(false);
          }}
        >
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <div className="px-5 py-4 border-b border-slate-100">
              <h3
                id="add-baa-title"
                className="text-base font-semibold text-slate-900"
              >
                Add downstream BAA
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                Adds a per-clinic BAA to your roster. The BA Compliance Letter
                draws from these entries on each issuance.
              </p>
            </div>

            <form onSubmit={handleAddSubmit} className="px-5 py-4 space-y-4">
              {/* Counterparty mode toggle */}
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium text-slate-800">
                  Counterparty
                </legend>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="addCounterpartyMode"
                    value="org"
                    checked={addCounterpartyMode === 'org'}
                    onChange={() => setAddCounterpartyMode('org')}
                  />
                  Onboarded clinic (pick from your client list)
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="addCounterpartyMode"
                    value="external"
                    checked={addCounterpartyMode === 'external'}
                    onChange={() => setAddCounterpartyMode('external')}
                  />
                  External clinic (not onboarded on OsirisCare)
                </label>
              </fieldset>

              {addCounterpartyMode === 'org' ? (
                <label className="block text-sm">
                  <span className="text-slate-700">Client</span>
                  <select
                    value={addOrgId}
                    onChange={(e) => setAddOrgId(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    required
                  >
                    <option value="">Pick a client…</option>
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                  {orgs.length === 0 && (
                    <span className="mt-1 block text-xs text-amber-700">
                      No onboarded clients found. Use external mode if this
                      clinic is not on OsirisCare yet.
                    </span>
                  )}
                </label>
              ) : (
                <label className="block text-sm">
                  <span className="text-slate-700">Practice name</span>
                  <input
                    type="text"
                    value={addPracticeName}
                    onChange={(e) => setAddPracticeName(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    placeholder="e.g. North Valley Dental, PC"
                    required
                  />
                </label>
              )}

              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  <span className="text-slate-700">Executed</span>
                  <input
                    type="date"
                    value={addExecutedAt}
                    onChange={(e) => setAddExecutedAt(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    required
                  />
                </label>
                <label className="block text-sm">
                  <span className="text-slate-700">Expires (optional)</span>
                  <input
                    type="date"
                    value={addExpiryAt}
                    onChange={(e) => setAddExpiryAt(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
              </div>

              <label className="block text-sm">
                <span className="text-slate-700">
                  Scope ({addScope.trim().length}/20+ chars)
                </span>
                <textarea
                  value={addScope}
                  onChange={(e) => setAddScope(e.target.value)}
                  rows={3}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  placeholder="Permitted uses + disclosures of PHI under this BAA"
                  required
                  minLength={20}
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  <span className="text-slate-700">Signer name</span>
                  <input
                    type="text"
                    value={addSignerName}
                    onChange={(e) => setAddSignerName(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    required
                  />
                </label>
                <label className="block text-sm">
                  <span className="text-slate-700">Signer title</span>
                  <input
                    type="text"
                    value={addSignerTitle}
                    onChange={(e) => setAddSignerTitle(e.target.value)}
                    className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                    required
                  />
                </label>
              </div>

              <label className="block text-sm">
                <span className="text-slate-700">Signer email (optional)</span>
                <input
                  type="email"
                  value={addSignerEmail}
                  onChange={(e) => setAddSignerEmail(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
              </label>

              <label className="block text-sm">
                <span className="text-slate-700">
                  Document SHA-256 (optional)
                </span>
                <input
                  type="text"
                  value={addDocSha256}
                  onChange={(e) => setAddDocSha256(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-mono text-xs"
                  placeholder="hex digest of the signed PDF"
                />
              </label>

              {addError && (
                <div
                  className="p-3 bg-rose-50 border border-rose-200 rounded-md text-sm text-rose-700"
                  role="alert"
                >
                  {addError}
                </div>
              )}

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    if (!addBusy) {
                      setAddOpen(false);
                      _resetAddForm();
                    }
                  }}
                  className="px-4 py-2 text-sm rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
                  disabled={addBusy}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm rounded-md bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50"
                  disabled={addBusy}
                >
                  {addBusy ? 'Adding…' : 'Add to roster'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ---------- Revoke confirm (TODO: migrate to <DangerousActionModal> tier-1) ---------- */}
      {revokeRowId && _activeRevokeRow && (
        <div
          className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="revoke-baa-title"
          onKeyDown={(e) => {
            if (e.key === 'Escape' && !revokeBusy) {
              setRevokeRowId(null);
              setRevokeReason('');
              setRevokeTypedConfirm('');
            }
          }}
        >
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full">
            <div className="px-5 py-4 border-b border-slate-100">
              <h3
                id="revoke-baa-title"
                className="text-base font-semibold text-slate-900"
              >
                Revoke BAA
              </h3>
              <p className="mt-1 text-sm text-slate-600">
                This will remove the active BAA for{' '}
                <strong>{_revokeLabel(_activeRevokeRow)}</strong> from your
                roster. Existing attestation chain entries are preserved
                (immutable). This action cannot be undone.
              </p>
            </div>
            <div className="px-5 py-4 space-y-3">
              <label className="block text-sm">
                <span className="text-slate-700">
                  Reason ({revokeReason.trim().length}/20+ chars)
                </span>
                <textarea
                  value={revokeReason}
                  onChange={(e) => setRevokeReason(e.target.value)}
                  rows={2}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  placeholder="Why are you revoking this BAA?"
                  minLength={20}
                />
              </label>
              <label className="block text-sm">
                <span className="text-slate-700">
                  Type{' '}
                  <code className="bg-slate-100 px-1 rounded text-xs">
                    {_revokeLabel(_activeRevokeRow)}
                  </code>{' '}
                  to confirm
                </span>
                <input
                  type="text"
                  value={revokeTypedConfirm}
                  onChange={(e) => setRevokeTypedConfirm(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  autoFocus
                />
              </label>
            </div>
            <div className="px-5 py-4 border-t border-slate-100 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  if (!revokeBusy) {
                    setRevokeRowId(null);
                    setRevokeReason('');
                    setRevokeTypedConfirm('');
                  }
                }}
                className="px-4 py-2 text-sm rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50"
                disabled={revokeBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRevokeSubmit}
                className="px-4 py-2 text-sm rounded-md bg-rose-600 text-white font-medium hover:bg-rose-700 disabled:opacity-50"
                disabled={revokeBusy}
              >
                {revokeBusy ? 'Revoking…' : 'Revoke BAA'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerAttestations;
