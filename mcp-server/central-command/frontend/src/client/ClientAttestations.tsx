/**
 * ClientAttestations — owner-side mirror of partner/PartnerAttestations
 * (sprint 2026-05-08). Closes the F1 + F3 + F5 portal-UI gap: backend
 * endpoints have shipped on `client_portal.py` since the round-table
 * 2026-05-06, but Maria-the-practice-owner had no in-portal surface to
 * issue or download a Compliance Attestation Letter, a Quarterly
 * Practice Compliance Summary, or a Wall Certificate. This page is
 * that surface.
 *
 * Three stacked cards (mirrors partner sibling shape):
 *   A. Compliance Attestation Letter (F1)
 *   B. Quarterly Practice Compliance Summary (F3)
 *   C. Wall Certificate (F5) — alternate render of the latest F1 row
 *
 * Backend contract DRIFT (verified against client_portal.py 2026-05-08
 * before coding — flagged at gate 0):
 *   - F1 endpoint is GET /api/client/attestation-letter (NOT POST as
 *     the build spec assumed). Backend issues + streams in one call.
 *     Headers emitted: X-Attestation-Hash + X-Letter-Valid-Until.
 *     Auth: require_client_user (any role).
 *   - F3 endpoint IS POST /api/client/quarterly-summary, body
 *     `{year: int, quarter: 1..4}` (NOT {quarter: 'current'|'previous'}
 *     as the spec said). Resolved by translating the friendly
 *     `current`/`previous` selector into (year, quarter) integers
 *     client-side. Headers: X-Attestation-Hash + X-Summary-Valid-Until.
 *     Auth: require_client_user.
 *   - F5 endpoint is GET /api/client/attestation-letter/{hash}/wall-
 *     cert.pdf — pure re-render of the F1 row, no new state machine.
 *     Auth: require_client_admin (owner OR admin only).
 *   - Public verify: F1 → /verify/{hash}, F3 → /verify/quarterly/{hash}.
 *     Both 32-char hash-prefix. Posted to the customer as
 *     osiriscare.io/verify/<hash[:32]> + osiriscare.io/verify/quarterly
 *     /<hash[:32]> per Brian-the-agent rule (no QR).
 *
 * CSRF posture:
 *   - F1 + F5 are GET — fetchBlob handles credentials:'include'.
 *   - F3 is a POST that returns binary; portalFetch only exposes
 *     postJson + fetchBlob (GET-only). We inline a `_postBlob` helper
 *     here that auto-injects credentials + X-CSRF-Token + Content-Type
 *     via csrfHeaders. test_frontend_mutation_csrf.py recognizes the
 *     credentials+csrf pair. Baseline stays at 0.
 *
 * Role-gate (cosmetic; backend authoritative):
 *   - F1 + F3: any role can issue (matches backend require_client_user).
 *   - F5: owner OR admin (matches backend require_client_admin).
 *
 * Banned-words sweep: no "ensures"/"prevents"/"protects"/"guarantees"/
 * "audit-ready"/"PHI never leaves"/"100%"/"continuously monitored".
 * Cadence verb is "monitored on a continuous automated schedule".
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useClient } from './ClientContext';
import { fetchBlob, type PortalFetchError } from '../utils/portalFetch';
import { csrfHeaders } from '../utils/csrf';

// ---------- Types ----------

interface AttestationSummary {
  attestation_hash: string;
  valid_until: string | null;
  filename: string;
  fetched_at: string;
}

interface QuarterlySummary extends AttestationSummary {
  period_label: string;
}

type QuarterChoice = 'current' | 'previous';

// ---------- Helpers ----------

const PUBLIC_VERIFY_LETTER_BASE = 'osiriscare.io/verify/';
const PUBLIC_VERIFY_QUARTERLY_BASE = 'osiriscare.io/verify/quarterly/';

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
  if (!brand) return 'practice';
  return brand.replace(/[^a-zA-Z0-9_-]+/g, '-').toLowerCase();
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

/**
 * Resolve the friendly QuarterChoice (current | previous) into the
 * backend's required (year, quarter) integer pair. "current" maps to
 * the calendar quarter we're inside; "previous" maps to the most
 * recent FULLY-COMPLETED quarter (which is what auditors usually
 * file under §164.530(j)).
 */
function _resolveQuarterChoice(
  choice: QuarterChoice,
  now: Date = new Date(),
): { year: number; quarter: number; label: string } {
  const month = now.getUTCMonth(); // 0-11
  const currentQuarter = Math.floor(month / 3) + 1; // 1-4
  const currentYear = now.getUTCFullYear();
  if (choice === 'current') {
    return {
      year: currentYear,
      quarter: currentQuarter,
      label: `Q${currentQuarter} ${currentYear}`,
    };
  }
  // previous: walk one quarter back, wrap year if needed.
  let prevQuarter = currentQuarter - 1;
  let prevYear = currentYear;
  if (prevQuarter < 1) {
    prevQuarter = 4;
    prevYear -= 1;
  }
  return {
    year: prevYear,
    quarter: prevQuarter,
    label: `Q${prevQuarter} ${prevYear}`,
  };
}

/**
 * POST helper that returns a Response so the caller can `.blob()` the
 * binary body. Mirrors fetchBlob for non-GET shapes — auto-injects
 * credentials:'include' + Content-Type + X-CSRF-Token (via
 * csrfHeaders). On non-2xx, throws a PortalFetchError-shaped object
 * with .status + .detail + .retryAfter so the caller branches on
 * 401/403/429/etc the same way as the partner sibling.
 *
 * Kept inline (not promoted to portalFetch.ts yet) because F3 is
 * currently the only POST-blob shape in the portal. If a second
 * POST-blob endpoint ships, lift this to portalFetch.ts.
 */
async function _postBlob(url: string, body: unknown): Promise<Response> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...csrfHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let parsed: { detail?: string } | undefined;
    try {
      parsed = JSON.parse(text);
    } catch {
      // not JSON
    }
    const err = new Error(
      parsed?.detail || `${res.status} ${text || 'request failed'}`,
    ) as PortalFetchError & { retryAfter?: string };
    err.status = res.status;
    err.detail = parsed?.detail;
    const retry = res.headers.get('Retry-After');
    if (retry) {
      err.retryAfter = retry;
    }
    throw err;
  }
  return res;
}

// ---------- Toast helper (inline; matches partner sibling shape) ----------

interface Toast {
  id: number;
  kind: 'success' | 'error' | 'info';
  message: string;
}

let _toastCounter = 0;

// ---------- Component ----------

export const ClientAttestations: React.FC = () => {
  const navigate = useNavigate();
  const { user, isAuthenticated, isLoading } = useClient();

  // Auth redirect
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      navigate('/client/login', { replace: true });
    }
  }, [isAuthenticated, isLoading, navigate]);

  // Cosmetic role gates (backend is authoritative).
  const canIssueLetter = !!user; // F1 — any role
  const canIssueQuarterly = !!user; // F3 — any role
  const canIssueWallCert = useMemo(
    () => user?.role === 'owner' || user?.role === 'admin',
    [user],
  );

  // ---------- State ----------
  const [toasts, setToasts] = useState<Toast[]>([]);
  const pushToast = (kind: Toast['kind'], message: string) => {
    const id = ++_toastCounter;
    setToasts((prev) => [...prev, { id, kind, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 6000);
  };

  // Card A — F1 letter
  const [letterSummary, setLetterSummary] =
    useState<AttestationSummary | null>(null);
  const [letterBusy, setLetterBusy] = useState<'idle' | 'issuing'>('idle');

  // Card B — F3 quarterly
  const [quarterChoice, setQuarterChoice] = useState<QuarterChoice>('previous');
  const [quarterlySummary, setQuarterlySummary] =
    useState<QuarterlySummary | null>(null);
  const [quarterlyBusy, setQuarterlyBusy] = useState<'idle' | 'issuing'>('idle');

  // Card C — F5 wall cert (re-render of latest F1 row)
  const [wallCertBusy, setWallCertBusy] = useState<'idle' | 'downloading'>('idle');

  // Plan-38 D6: Privacy Officer pre-flight gate. Pre-fix, F1 issuance
  // would 409 with "A precondition is missing (Privacy Officer
  // designation or BAA on file). Resolve the gap and retry." — but
  // Maria has to CLICK Issue + read the toast + figure out where to
  // designate. Pre-flight: fetch the PO designation on mount; if
  // none, intercept Issue clicks with an explanatory modal that
  // routes to /client/compliance.
  const [poStatus, setPoStatus] = useState<'unknown' | 'designated' | 'missing'>(
    'unknown',
  );
  const [showPoGate, setShowPoGate] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/client/privacy-officer', {
          credentials: 'include',
        });
        if (cancelled) return;
        if (res.ok) {
          const data = await res.json();
          // Real backend shape (client_portal.py:5203):
          //   { "designation": null }                — none designated
          //   { "designation": { id, name, ... } }   — designated
          // Older mock-only shape `{}` (no `designation` key) is
          // treated as 'unknown' so dev/test environments don't
          // accidentally trigger the gate.
          if (data && data.designation === null) {
            setPoStatus('missing');
          } else if (data && data.designation && data.designation.id) {
            setPoStatus('designated');
          } else {
            setPoStatus('unknown');
          }
        } else if (res.status === 404) {
          setPoStatus('missing');
        } else {
          // 401/403/5xx — leave as 'unknown' so the issuance path
          // falls through to the existing 409-toast handling. Don't
          // block on uncertainty.
          setPoStatus('unknown');
        }
      } catch {
        if (!cancelled) setPoStatus('unknown');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated]);

  // ---------- F1 actions ----------

  const handleLetterIssue = async () => {
    if (!canIssueLetter) {
      pushToast('error', 'Sign in to issue an attestation letter.');
      return;
    }
    // Plan-38 D6: pre-flight intercept. If we KNOW the PO is missing,
    // surface the modal instead of POSTing + getting 409. If status
    // is 'unknown' (auth flake or transient error), fall through to
    // the issuance path; backend's 409 toast still catches the gap.
    if (poStatus === 'missing') {
      setShowPoGate(true);
      return;
    }
    setLetterBusy('issuing');
    try {
      const res = await fetchBlob('/api/client/attestation-letter');
      const blob = await res.blob();
      const fallback = `compliance-attestation-${_safeBrand(
        user?.org.name,
      )}.pdf`;
      const filename = _filenameFromHeaders(res, fallback);
      _saveBlob(blob, filename);
      const hash = res.headers.get('X-Attestation-Hash') || '';
      const validUntil = res.headers.get('X-Letter-Valid-Until');
      setLetterSummary({
        attestation_hash: hash,
        valid_until: validUntil,
        filename,
        fetched_at: new Date().toISOString(),
      });
      pushToast('success', 'Compliance Attestation Letter issued and downloaded.');
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
            'A precondition is missing (Privacy Officer designation or BAA on file). Resolve the gap and retry.',
        );
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Issuance failed. Contact support.',
        );
      }
    } finally {
      setLetterBusy('idle');
    }
  };

  // ---------- F3 actions ----------

  const handleQuarterlyIssue = async () => {
    if (!canIssueQuarterly) {
      pushToast('error', 'Sign in to issue a quarterly summary.');
      return;
    }
    // Plan-38 D6: F3 quarterly summary also embeds the PO sign-off
    // line — same pre-flight as F1.
    if (poStatus === 'missing') {
      setShowPoGate(true);
      return;
    }
    const resolved = _resolveQuarterChoice(quarterChoice);
    setQuarterlyBusy('issuing');
    try {
      const res = await _postBlob('/api/client/quarterly-summary', {
        year: resolved.year,
        quarter: resolved.quarter,
      });
      const blob = await res.blob();
      const fallback = `quarterly-summary-${_safeBrand(
        user?.org.name,
      )}-Q${resolved.quarter}-${resolved.year}.pdf`;
      const filename = _filenameFromHeaders(res, fallback);
      _saveBlob(blob, filename);
      const hash = res.headers.get('X-Attestation-Hash') || '';
      // X-Summary-Valid-Until is the F3-distinct header (sibling-
      // divergent from F1's X-Letter-Valid-Until per coach D-8 memo
      // + multi-endpoint header parity rule).
      const validUntil = res.headers.get('X-Summary-Valid-Until');
      setQuarterlySummary({
        attestation_hash: hash,
        valid_until: validUntil,
        filename,
        fetched_at: new Date().toISOString(),
        period_label: resolved.label,
      });
      pushToast(
        'success',
        `Quarterly Practice Compliance Summary issued for ${resolved.label}.`,
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
            'Quarterly summary cannot be issued (Privacy Officer designation missing or quarter is not yet completed).',
        );
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Issuance failed. Contact support.',
        );
      }
    } finally {
      setQuarterlyBusy('idle');
    }
  };

  // ---------- F5 actions ----------

  const handleWallCertDownload = async () => {
    if (!canIssueWallCert) {
      pushToast('error', 'Owner or admin role required to download the wall certificate.');
      return;
    }
    if (!letterSummary || !letterSummary.attestation_hash) {
      pushToast(
        'error',
        'Issue a Compliance Attestation Letter first; the wall certificate is an alternate render of that signed payload.',
      );
      return;
    }
    setWallCertBusy('downloading');
    try {
      const url = `/api/client/attestation-letter/${encodeURIComponent(
        letterSummary.attestation_hash,
      )}/wall-cert.pdf`;
      const res = await fetchBlob(url);
      const blob = await res.blob();
      const fallback = `wall-cert-${_safeBrand(user?.org.name)}.pdf`;
      const filename = _filenameFromHeaders(res, fallback);
      _saveBlob(blob, filename);
      pushToast('success', 'Wall Certificate downloaded.');
    } catch (e) {
      const err = e as PortalFetchError & { retryAfter?: string };
      if (err.status === 401) {
        pushToast('error', 'Your session expired. Sign in again.');
      } else if (err.status === 403) {
        pushToast(
          'error',
          'Owner or admin role required to download the wall certificate.',
        );
      } else if (err.status === 404) {
        pushToast(
          'error',
          'No attestation letter found for this hash. Issue a new Compliance Attestation Letter (Card A) first.',
        );
      } else if (err.status === 429) {
        const retry = err.retryAfter || '3600';
        const mins = Math.ceil(Number(retry) / 60);
        pushToast(
          'error',
          `Wall certificate rendering is rate-limited (10/hr). Try again in ~${mins} min.`,
        );
      } else {
        pushToast(
          'error',
          err.detail || err.message || 'Wall certificate render failed.',
        );
      }
    } finally {
      setWallCertBusy('idle');
    }
  };

  // ---------- Copy-to-clipboard ----------

  const handleCopyVerify = (base: string, hashHex: string) => {
    const url = `${base}${hashHex.slice(0, 32)}`;
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
          className="w-8 h-8 border-4 border-teal-500 border-t-transparent rounded-full animate-spin"
          aria-label="Loading"
          role="status"
        />
      </div>
    );
  }

  if (!user) return null;

  const presenterPractice = user.org.name || 'your practice';

  return (
    <div className="min-h-screen bg-slate-50/80">
      {/* Header */}
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
              to="/client/dashboard"
              className="p-2 text-slate-500 hover:text-teal-600 rounded-lg hover:bg-teal-50 transition"
              aria-label="Back to client dashboard"
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
                Printable, hash-bound attestations of {presenterPractice}'s
                compliance posture, monitored on a continuous automated
                schedule.
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
        {/* ---------- Card A: Compliance Attestation Letter (F1) ---------- */}
        <section
          data-testid="letter-card"
          className="bg-white rounded-2xl shadow-sm border border-slate-200"
          aria-labelledby="letter-heading"
        >
          <div className="px-6 py-4 border-b border-slate-100">
            <h2
              id="letter-heading"
              className="text-base font-semibold text-slate-900"
            >
              Compliance Attestation Letter
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Practice-owner-grade attestation summarizing your practice's
              compliance posture. Hand to insurance underwriters, board
              chairs, or counsel. Each issuance is Ed25519-signed and
              hash-chained; auditors can independently verify at the public
              verify URL.
            </p>
          </div>
          <div className="px-6 py-5 space-y-4">
            {letterSummary ? (
              <div
                data-testid="letter-summary"
                className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm"
              >
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  <div>
                    <div className="text-xs text-slate-500">
                      Latest filename
                    </div>
                    <div className="font-mono text-xs break-all text-slate-800">
                      {letterSummary.filename}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">
                      Issued at (this session)
                    </div>
                    <div className="text-slate-800 tabular-nums">
                      {_formatDate(letterSummary.fetched_at)}
                    </div>
                  </div>
                  {letterSummary.valid_until && (
                    <div>
                      <div className="text-xs text-slate-500">Valid until</div>
                      <div className="text-slate-800 tabular-nums">
                        {_formatDate(letterSummary.valid_until)}
                      </div>
                    </div>
                  )}
                  <div>
                    <div className="text-xs text-slate-500">
                      Attestation hash
                    </div>
                    <div className="font-mono text-xs break-all text-slate-800">
                      {letterSummary.attestation_hash || '—'}
                    </div>
                  </div>
                </div>
                {letterSummary.attestation_hash && (
                  <div className="mt-3 space-y-1.5">
                    <div className="flex items-center gap-2 text-xs flex-wrap">
                      <span className="text-slate-500">Public verify URL:</span>
                      <code className="bg-white border border-slate-200 px-2 py-1 rounded font-mono text-[11px]">
                        {PUBLIC_VERIFY_LETTER_BASE}
                        {letterSummary.attestation_hash.slice(0, 32)}
                      </code>
                      <button
                        type="button"
                        onClick={() =>
                          handleCopyVerify(
                            PUBLIC_VERIFY_LETTER_BASE,
                            letterSummary.attestation_hash,
                          )
                        }
                        className="text-teal-600 hover:underline"
                        aria-label="Copy attestation letter verify URL"
                      >
                        Copy
                      </button>
                    </div>
                    {/* Plan-38 D4: explanatory caption for the
                        owner audience. Maria forwards this URL in
                        a cover email; auditor pastes into a
                        browser to verify the letter without
                        contacting OsirisCare. */}
                    <p className="text-[11px] text-slate-500 italic">
                      Send this URL alongside the PDF — recipient can
                      verify cryptographically without contacting
                      OsirisCare.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div
                data-testid="letter-empty"
                className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-600"
              >
                No attestation letter issued in this session yet. Issue your
                first letter to share with auditors and counsel.
              </div>
            )}

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleLetterIssue}
                disabled={letterBusy !== 'idle'}
                aria-busy={letterBusy === 'issuing'}
                aria-label="Issue and download Compliance Attestation Letter"
                className="px-4 py-2 text-sm rounded-xl text-white font-medium hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                style={{
                  background:
                    'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
                }}
              >
                {letterBusy === 'issuing' && (
                  <span
                    className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
                    aria-hidden="true"
                  />
                )}
                {letterBusy === 'issuing'
                  ? 'Issuing…'
                  : letterSummary
                    ? 'Issue + download new letter'
                    : 'Issue + download letter'}
              </button>
            </div>
          </div>
        </section>

        {/* ---------- Card B: Quarterly Practice Compliance Summary (F3) ---------- */}
        <section
          data-testid="quarterly-card"
          className="bg-white rounded-2xl shadow-sm border border-slate-200"
          aria-labelledby="quarterly-heading"
        >
          <div className="px-6 py-4 border-b border-slate-100">
            <h2
              id="quarterly-heading"
              className="text-base font-semibold text-slate-900"
            >
              Quarterly Practice Compliance Summary
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Quarterly aggregate of your practice's substrate evidence and
              compliance posture. File for §164.530(j) records retention or
              hand to your annual auditor. Each summary is hash-bound to the
              canonical evidence chain.
            </p>
          </div>
          <div className="px-6 py-5 space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <label className="block text-sm">
                <span className="text-slate-700 font-medium">Quarter</span>
                <select
                  value={quarterChoice}
                  onChange={(e) =>
                    setQuarterChoice(e.target.value as QuarterChoice)
                  }
                  disabled={quarterlyBusy !== 'idle'}
                  aria-label="Select quarter to issue"
                  className="mt-1 block w-56 rounded-md border border-slate-300 px-3 py-2 text-sm bg-white"
                >
                  <option value="previous">
                    Previous quarter ({_resolveQuarterChoice('previous').label})
                  </option>
                  <option value="current">
                    Current quarter to-date ({_resolveQuarterChoice('current').label})
                  </option>
                </select>
              </label>
            </div>

            {quarterlySummary ? (
              <div
                data-testid="quarterly-summary"
                className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm"
              >
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  <div>
                    <div className="text-xs text-slate-500">Period</div>
                    <div className="text-slate-800 tabular-nums font-medium">
                      {quarterlySummary.period_label}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">
                      Issued at (this session)
                    </div>
                    <div className="text-slate-800 tabular-nums">
                      {_formatDate(quarterlySummary.fetched_at)}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">
                      Latest filename
                    </div>
                    <div className="font-mono text-xs break-all text-slate-800">
                      {quarterlySummary.filename}
                    </div>
                  </div>
                  {quarterlySummary.valid_until && (
                    <div>
                      <div className="text-xs text-slate-500">Valid until</div>
                      <div className="text-slate-800 tabular-nums">
                        {_formatDate(quarterlySummary.valid_until)}
                      </div>
                    </div>
                  )}
                  <div className="col-span-2">
                    <div className="text-xs text-slate-500">
                      Attestation hash
                    </div>
                    <div className="font-mono text-xs break-all text-slate-800">
                      {quarterlySummary.attestation_hash || '—'}
                    </div>
                  </div>
                </div>
                {quarterlySummary.attestation_hash && (
                  <div className="mt-3 space-y-1.5">
                    <div className="flex items-center gap-2 text-xs flex-wrap">
                      <span className="text-slate-500">Public verify URL:</span>
                      <code className="bg-white border border-slate-200 px-2 py-1 rounded font-mono text-[11px]">
                        {PUBLIC_VERIFY_QUARTERLY_BASE}
                        {quarterlySummary.attestation_hash.slice(0, 32)}
                      </code>
                      <button
                        type="button"
                        onClick={() =>
                          handleCopyVerify(
                            PUBLIC_VERIFY_QUARTERLY_BASE,
                            quarterlySummary.attestation_hash,
                          )
                        }
                        className="text-teal-600 hover:underline"
                        aria-label="Copy quarterly summary verify URL"
                      >
                        Copy
                      </button>
                    </div>
                    {/* Plan-38 D4: caption mirrors the F1 card —
                        auditor receiving the quarterly summary can
                        verify cryptographically. */}
                    <p className="text-[11px] text-slate-500 italic">
                      Send this URL alongside the PDF — recipient can
                      verify cryptographically without contacting
                      OsirisCare.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div
                data-testid="quarterly-empty"
                className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm text-slate-600"
              >
                No quarterly summary issued in this session yet.
              </div>
            )}

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleQuarterlyIssue}
                disabled={quarterlyBusy !== 'idle'}
                aria-busy={quarterlyBusy === 'issuing'}
                aria-label="Issue and download Quarterly Practice Compliance Summary"
                className="px-4 py-2 text-sm rounded-xl text-white font-medium hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                style={{
                  background:
                    'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
                }}
              >
                {quarterlyBusy === 'issuing' && (
                  <span
                    className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
                    aria-hidden="true"
                  />
                )}
                {quarterlyBusy === 'issuing'
                  ? 'Issuing…'
                  : quarterlySummary
                    ? 'Issue + download new summary'
                    : 'Issue + download summary'}
              </button>
            </div>
          </div>
        </section>

        {/* ---------- Card C: Wall Certificate (F5) ---------- */}
        <section
          data-testid="wall-cert-card"
          className="bg-white rounded-2xl shadow-sm border border-slate-200"
          aria-labelledby="wall-cert-heading"
        >
          <div className="px-6 py-4 border-b border-slate-100">
            <h2
              id="wall-cert-heading"
              className="text-base font-semibold text-slate-900"
            >
              Wall Certificate
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              One-page landscape display certificate. Re-renders the most
              recent Compliance Attestation Letter into a wall-frame
              format — same Ed25519 signature, same evidence chain,
              formatted for hanging in the practice.
            </p>
          </div>
          <div className="px-6 py-5 space-y-4">
            {!letterSummary ? (
              <div
                data-testid="wall-cert-prereq-missing"
                className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600"
              >
                Issue a Compliance Attestation Letter (Card A above)
                first; the wall certificate is an alternate render of
                that signed payload.
              </div>
            ) : null}

            <div className="flex flex-wrap gap-3 items-center">
              <button
                type="button"
                onClick={handleWallCertDownload}
                disabled={
                  wallCertBusy !== 'idle' ||
                  !letterSummary ||
                  !canIssueWallCert
                }
                aria-busy={wallCertBusy === 'downloading'}
                aria-label={
                  canIssueWallCert
                    ? 'Download Wall Certificate'
                    : 'Download Wall Certificate (owner or admin role required)'
                }
                className="px-4 py-2 text-sm rounded-xl text-white font-medium hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2"
                style={{
                  background:
                    'linear-gradient(135deg, #14A89E 0%, #3CBCB4 100%)',
                }}
              >
                {wallCertBusy === 'downloading' && (
                  <span
                    className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
                    aria-hidden="true"
                  />
                )}
                {wallCertBusy === 'downloading'
                  ? 'Preparing…'
                  : 'Download Wall Certificate'}
              </button>
              {!canIssueWallCert && (
                <span className="text-xs text-slate-500 self-center">
                  Owner or admin role required to download.
                </span>
              )}
            </div>
          </div>
        </section>

        <p className="text-xs text-slate-500 italic px-1">
          Audit-supportive technical evidence. Not a substitute for your
          §164.528 disclosure accounting, designated record set, or
          §164.530(d) complaint log.
        </p>
      </main>

      {/* Plan-38 D6: Privacy Officer pre-flight modal. Triggered when
          Maria clicks Issue F1 / F3 without a designated Privacy
          Officer. Routes to /client/compliance where the F2
          designation flow lives. role=dialog + aria-modal + focus
          first button on open + ESC closes. */}
      {showPoGate && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="po-gate-title"
          aria-describedby="po-gate-desc"
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowPoGate(false);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setShowPoGate(false);
          }}
        >
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4">
            <h2
              id="po-gate-title"
              className="text-lg font-semibold text-slate-900"
            >
              Designate a Privacy Officer first
            </h2>
            <p id="po-gate-desc" className="text-sm text-slate-700">
              HIPAA §164.530(a)(1) requires every covered entity to
              formally designate a Privacy Officer. Each Compliance
              Attestation Letter and Quarterly Summary embeds that
              designation as part of its signed payload — it can't be
              issued without one. The same person can serve as both
              Privacy Officer and Security Officer for small practices.
            </p>
            <p className="text-xs text-slate-500">
              The compliance settings page walks you through the
              designation; takes about 2 minutes.
            </p>
            <div className="flex flex-wrap gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowPoGate(false)}
                className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900"
              >
                Not now
              </button>
              <button
                type="button"
                autoFocus
                onClick={() => {
                  setShowPoGate(false);
                  navigate('/client/compliance');
                }}
                className="px-4 py-2 text-sm rounded-lg bg-teal-600 text-white font-medium hover:bg-teal-700"
              >
                Designate now
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ClientAttestations;
