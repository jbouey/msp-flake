import React, { useEffect, useState } from 'react';

/**
 * PublicKeysPanel — Tier 3 H2 follow-up.
 *
 * Renders the per-appliance Ed25519 public keys for the site so an
 * auditor can pin them offline before running independent verification.
 * The data comes from the existing `/api/evidence/sites/{id}/public-keys`
 * endpoint shipped in Batch 5; the auditor kit ZIP includes the same
 * payload as `pubkeys.json`. This panel makes the keys visible inside
 * the portal UI as well, with one-click copy + download buttons.
 *
 * Why both the kit AND this panel?
 *   - Kit ZIP is the canonical handoff (works offline, ships .ots files,
 *     verify.sh, README, chain.json — everything an auditor needs).
 *   - This panel exists because some auditors will look for the keys
 *     in-app first and never find the kit. The download button covers
 *     both audiences without duplication of source-of-truth.
 */

interface PublicKey {
  appliance_id: string;
  display_name: string;
  hostname: string;
  public_key_hex: string;
  first_checkin: string | null;
  last_checkin: string | null;
}

interface PublicKeysResponse {
  site_id: string;
  public_keys: PublicKey[];
  count: number;
}

interface Props {
  siteId: string;
}

async function sha256Fingerprint(hex: string): Promise<string> {
  try {
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    }
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
      .slice(0, 16);
  } catch {
    return '----';
  }
}

export const PublicKeysPanel: React.FC<Props> = ({ siteId }) => {
  const [data, setData] = useState<PublicKeysResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fingerprints, setFingerprints] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`/api/evidence/sites/${siteId}/public-keys`, {
      credentials: 'same-origin', // same-origin-allowed: browser-verify — intentional cryptographic isolation (BUG 2 KEEP 2026-05-12)
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(async (json: PublicKeysResponse) => {
        if (cancelled) return;
        setData(json);
        // Compute fingerprints in the browser to match the auditor kit format
        const fps: Record<string, string> = {};
        for (const k of json.public_keys || []) {
          fps[k.appliance_id] = await sha256Fingerprint(k.public_key_hex);
        }
        if (!cancelled) setFingerprints(fps);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [siteId]);

  const downloadKeys = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `osiriscare-pubkeys-${siteId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyKey = async (hex: string, applianceId: string) => {
    try {
      await navigator.clipboard.writeText(hex);
      setCopied(applianceId);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard blocked — silent fallback
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 mb-6">
        <p className="text-sm text-slate-500">Loading appliance public keys…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5 mb-6 text-sm text-red-800">
        Failed to load public keys: {error}
      </div>
    );
  }

  if (!data || data.public_keys.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 mb-6 text-sm text-slate-500">
        No public keys recorded for this site yet — appliances must check in
        once with their Ed25519 key before keys appear here.
      </div>
    );
  }

  return (
    <section className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 mb-8">
      <div className="flex items-start justify-between mb-4 gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            Appliance public keys
          </h2>
          <p className="text-sm text-slate-500 mt-0.5">
            Per-appliance Ed25519 verification keys. Pin these offline
            before running independent verification — fingerprints below
            are SHA-256 of the raw key.
          </p>
        </div>
        <button
          type="button"
          onClick={downloadKeys}
          className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-600 text-white text-sm font-medium hover:bg-blue-700"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
          </svg>
          Download pubkeys.json
        </button>
      </div>

      <div className="space-y-3">
        {data.public_keys.map((k) => (
          <div
            key={k.appliance_id}
            className="rounded-lg border border-slate-200 bg-slate-50/60 p-3"
          >
            <div className="flex items-start justify-between gap-3 mb-2">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-900 truncate">
                  {k.display_name}
                </p>
                <p className="text-xs text-slate-500 truncate">
                  {k.hostname} · first checkin{' '}
                  {k.first_checkin
                    ? new Date(k.first_checkin).toLocaleDateString()
                    : '—'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => copyKey(k.public_key_hex, k.appliance_id)}
                className="flex-shrink-0 text-xs px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100"
              >
                {copied === k.appliance_id ? 'Copied!' : 'Copy key'}
              </button>
            </div>
            <div className="font-mono text-[10px] text-slate-700 break-all bg-white border border-slate-200 rounded px-2 py-1.5 mb-2">
              {k.public_key_hex}
            </div>
            <p className="text-[10px] text-slate-500">
              Fingerprint:{' '}
              <code className="text-slate-700">
                sha256:{fingerprints[k.appliance_id] || '...'}
              </code>
              <span className="ml-1 text-slate-400">
                (record this in your audit working papers)
              </span>
            </p>
          </div>
        ))}
      </div>

      <p className="mt-4 text-[11px] text-slate-500">
        These keys are pinned at first appliance checkin and never rotated
        in place. The full set is also shipped in the auditor kit ZIP at{' '}
        <code className="px-1 bg-slate-100 rounded">pubkeys.json</code>. For
        evidence-integrity context, see the{' '}
        <a
          href="/docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE.md"
          className="underline text-slate-700 hover:text-blue-700"
        >
          Merkle disclosure
        </a>
        .
      </p>
    </section>
  );
};

export default PublicKeysPanel;
