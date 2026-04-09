import React from 'react';
import { useBrowserVerify } from './useBrowserVerify';

interface Props {
  siteId: string;
}

/**
 * BrowserVerifiedBadge — independent client-side verification badge.
 *
 * SESSION 203 C4 FIX: sits next to the server-returned "Chain Valid"
 * badge on the portal Verify page and shows a SEPARATE result computed
 * in the browser. An auditor can open devtools, see the @noble/ed25519
 * library loaded, and watch the signature verifications fire locally.
 * This is how "independent verification" actually works — the backend's
 * self-reported verdict doesn't count for compliance defensibility.
 *
 * Displays 4 states:
 *   idle/loading — neutral spinner
 *   verified     — green "✓ Verified in your browser (N/M)"
 *   partial      — amber "⚠ Partial browser verification"
 *   failed/error — red "✗ Browser verification FAILED"
 */
export const BrowserVerifiedBadge: React.FC<Props> = ({ siteId }) => {
  const r = useBrowserVerify(siteId);

  const signatureLine =
    r.signatures_verified + r.signatures_failed > 0
      ? `${r.signatures_verified}/${r.signatures_verified + r.signatures_failed} signatures`
      : r.signatures_missing > 0
        ? `${r.signatures_missing} unsigned (legacy)`
        : 'no signatures in sample';

  const chainLine =
    r.chain_links_verified + r.chain_links_failed > 0
      ? `${r.chain_links_verified}/${r.chain_links_verified + r.chain_links_failed} chain links`
      : 'chain_hash not present';

  if (r.status === 'loading' || r.status === 'idle') {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 flex items-center gap-3">
        <div className="w-8 h-8 rounded-full bg-slate-200 animate-pulse" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-700">Verifying in your browser…</p>
          <p className="text-xs text-slate-500">
            Loading public keys and recomputing signatures locally.
          </p>
        </div>
      </div>
    );
  }

  if (r.status === 'error') {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 flex items-center gap-3">
        <div className="w-8 h-8 rounded-full bg-red-100 flex items-center justify-center">
          <span className="text-red-700 text-lg">✗</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-red-900">
            Browser verification error
          </p>
          <p className="text-xs text-red-700 truncate" title={r.error || ''}>
            {r.error || 'Unknown error'}
          </p>
        </div>
      </div>
    );
  }

  const tone: 'healthy' | 'warning' | 'critical' =
    r.status === 'verified'
      ? 'healthy'
      : r.status === 'failed'
        ? 'critical'
        : 'warning';

  const toneClass = {
    healthy: 'border-emerald-200 bg-emerald-50 text-emerald-900',
    warning: 'border-amber-200 bg-amber-50 text-amber-900',
    critical: 'border-red-200 bg-red-50 text-red-900',
  }[tone];

  const iconBg = {
    healthy: 'bg-emerald-100 text-emerald-700',
    warning: 'bg-amber-100 text-amber-700',
    critical: 'bg-red-100 text-red-700',
  }[tone];

  const icon =
    r.status === 'verified' ? '✓' : r.status === 'failed' ? '✗' : '⚠';

  const title =
    r.status === 'verified'
      ? 'Verified in your browser'
      : r.status === 'failed'
        ? 'Browser verification FAILED'
        : 'Partial browser verification';

  return (
    <div className={`rounded-xl border p-4 ${toneClass}`}>
      <div className="flex items-start gap-3">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-lg font-bold flex-shrink-0 ${iconBg}`}
        >
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">{title}</p>
          <p className="text-xs mt-0.5 opacity-80">
            {r.bundles_checked} bundles inspected · {signatureLine} · {chainLine} ·{' '}
            {r.public_keys_loaded} key{r.public_keys_loaded === 1 ? '' : 's'} loaded
          </p>
          <p className="text-[10px] opacity-70 mt-1">
            Computed locally in your browser at{' '}
            {r.verified_at ? new Date(r.verified_at).toLocaleTimeString() : '—'} using
            @noble/ed25519 — no trust in the server required.
          </p>
        </div>
      </div>
    </div>
  );
};

export default BrowserVerifiedBadge;
