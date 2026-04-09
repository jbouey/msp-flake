import React from 'react';
import { useBrowserVerifyFull } from './useBrowserVerifyFull';

interface Props {
  siteId: string;
  /** When true, kicks off automatically on mount. Default: false (the user
   *  must click the button to consent to the verification load). */
  autoStart?: boolean;
}

/**
 * FullChainVerifyPanel — Tier 2.1 follow-up to BrowserVerifiedBadge.
 *
 * The badge proves *some* bundles can be verified in the browser. This
 * panel proves *all* bundles can be verified in the browser by walking the
 * complete chain in a Web Worker and showing live progress. An auditor
 * watching the page sees a counter like "Verified 4,213/5,012 bundles…"
 * tick upward — measurable independent verification.
 *
 * Why a button (not auto-start)?
 *   - For sites with 100K+ bundles the verification takes meaningful CPU
 *     and bandwidth. Surprising the user with a few minutes of fans
 *     spinning is poor UX.
 *   - The act of clicking "verify the entire chain" creates an audit
 *     trail in the user's own session log if they care to keep one. It
 *     also gives the auditor a clear "I requested this" moment.
 */
export const FullChainVerifyPanel: React.FC<Props> = ({ siteId, autoStart }) => {
  const r = useBrowserVerifyFull(siteId, { autoStart });

  const progressPct =
    r.totalBundles > 0
      ? Math.min(100, Math.round((r.bundlesProcessed / r.totalBundles) * 100))
      : 0;

  const tone: 'idle' | 'running' | 'good' | 'bad' = (() => {
    if (r.status === 'error') return 'bad';
    if (r.status === 'done' && r.summary) {
      if (r.summary.signaturesFailed > 0 || r.summary.chainLinksFailed > 0) {
        return 'bad';
      }
      return 'good';
    }
    if (r.status === 'verifying' || r.status === 'loading') return 'running';
    return 'idle';
  })();

  const containerClass = {
    idle: 'border-slate-200 bg-white',
    running: 'border-blue-200 bg-blue-50',
    good: 'border-emerald-200 bg-emerald-50',
    bad: 'border-red-200 bg-red-50',
  }[tone];

  const titleColor = {
    idle: 'text-slate-900',
    running: 'text-blue-900',
    good: 'text-emerald-900',
    bad: 'text-red-900',
  }[tone];

  const barColor = {
    idle: 'bg-slate-300',
    running: 'bg-blue-500',
    good: 'bg-emerald-500',
    bad: 'bg-red-500',
  }[tone];

  return (
    <div className={`rounded-xl border p-5 ${containerClass}`}>
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex-1 min-w-0">
          <h3 className={`text-base font-semibold ${titleColor}`}>
            Full chain browser verification
          </h3>
          <p className="text-xs text-slate-600 mt-0.5">
            Walks every bundle in the site&apos;s evidence chain, checks Ed25519
            signatures and SHA-256 hash linkage in a Web Worker — entirely
            on this device, with no trust in the OsirisCare backend.
          </p>
        </div>
        {r.status === 'idle' && (
          <button
            type="button"
            onClick={r.start}
            className="px-3 py-1.5 rounded-md bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 flex-shrink-0"
          >
            Verify entire chain
          </button>
        )}
        {(r.status === 'verifying' || r.status === 'loading') && (
          <button
            type="button"
            onClick={r.cancel}
            className="px-3 py-1.5 rounded-md border border-slate-300 bg-white text-slate-700 text-sm font-medium hover:bg-slate-50 flex-shrink-0"
          >
            Cancel
          </button>
        )}
      </div>

      {r.status !== 'idle' && (
        <>
          <div className="w-full h-2 rounded-full bg-slate-200 overflow-hidden mb-3">
            <div
              className={`h-full ${barColor} transition-all duration-150`}
              style={{ width: `${progressPct}%` }}
            />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <Metric label="Bundles" value={`${r.bundlesProcessed.toLocaleString()} / ${r.totalBundles.toLocaleString()}`} />
            <Metric
              label="Signatures verified"
              value={r.signaturesVerified.toLocaleString()}
              tone={r.signaturesFailed > 0 ? 'bad' : 'good'}
            />
            <Metric
              label="Chain links verified"
              value={r.chainLinksVerified.toLocaleString()}
              tone={r.chainLinksFailed > 0 ? 'bad' : 'good'}
            />
            <Metric
              label="Public keys"
              value={r.publicKeysLoaded.toLocaleString()}
            />
          </div>

          {(r.signaturesFailed > 0 || r.chainLinksFailed > 0) && (
            <div className="mt-3 text-xs text-red-800 bg-red-100 rounded-md p-2 border border-red-200">
              <strong>{r.signaturesFailed}</strong> signature failure
              {r.signaturesFailed === 1 ? '' : 's'} ·{' '}
              <strong>{r.chainLinksFailed}</strong> chain-link failure
              {r.chainLinksFailed === 1 ? '' : 's'}. Contact{' '}
              <a href="mailto:security@osiriscare.net" className="underline">
                security@osiriscare.net
              </a>{' '}
              with your site ID and the bundle position above.
            </div>
          )}

          {r.signaturesMissing > 0 && (
            <div className="mt-2 text-[11px] text-slate-600">
              {r.signaturesMissing.toLocaleString()} bundle
              {r.signaturesMissing === 1 ? '' : 's'} unsigned (legacy) — these
              are pre-Ed25519 entries and are honestly counted as
              <code className="mx-1 px-1 bg-slate-100 rounded">unsigned</code>
              rather than verified. See the public Merkle disclosure at{' '}
              <a
                href="/docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE.md"
                className="underline"
              >
                OSIRIS-2026-04-09-MERKLE-COLLISION
              </a>{' '}
              for context.
            </div>
          )}

          {r.status === 'done' && r.summary && (
            <p className="mt-3 text-[11px] text-slate-700">
              Completed at{' '}
              {r.completedAt ? new Date(r.completedAt).toLocaleTimeString() : '—'}.
              Verified locally in your browser using @noble/ed25519 — no
              backend trust required.
            </p>
          )}

          {r.status === 'error' && (
            <p className="mt-3 text-xs text-red-700">{r.error || 'Unknown error'}</p>
          )}
        </>
      )}
    </div>
  );
};

interface MetricProps {
  label: string;
  value: string;
  tone?: 'good' | 'bad' | 'neutral';
}

const Metric: React.FC<MetricProps> = ({ label, value, tone = 'neutral' }) => {
  const valueColor = {
    good: 'text-emerald-700',
    bad: 'text-red-700',
    neutral: 'text-slate-900',
  }[tone];
  return (
    <div className="rounded-md bg-white/70 border border-white/60 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`text-sm font-semibold ${valueColor}`}>{value}</p>
    </div>
  );
};

export default FullChainVerifyPanel;
