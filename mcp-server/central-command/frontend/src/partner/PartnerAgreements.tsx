import React, { useCallback, useEffect, useState } from 'react';
import { usePartner } from './PartnerContext';
import { csrfHeaders } from '../utils/csrf';

/**
 * PartnerAgreements — MSA + Subcontractor BAA + Reseller Addendum e-sign.
 *
 * Wires the backend partner_agreements_api module. A partner cannot
 * create clinic invites until all three are signed at their current
 * versions. Backend `require_active_partner_agreements` returns 428 with
 * `missing: [...]` when the gate trips — this screen resolves that.
 */

type AgreementType = 'msa' | 'subcontractor_baa' | 'reseller_addendum';

interface AgreementRow {
  version: string;
  signed_at: string | null;
  signer_name: string;
  text_sha256: string;
}

interface MineResponse {
  partner_id: string;
  required_types: AgreementType[];
  current_versions: Record<AgreementType, string>;
  active: Record<AgreementType, AgreementRow | undefined>;
  missing: AgreementType[];
  ready_to_invite: boolean;
}

const AGREEMENT_LABELS: Record<AgreementType, { title: string; blurb: string }> = {
  msa: {
    title: 'Master Software + Services Agreement (MSA)',
    blurb:
      'Tool vendor scope, liability capped at 12 months of fees, no patient-harm indemnification. ' +
      'OsirisCare provides the attestation substrate; MSP owns the operating relationship.',
  },
  subcontractor_baa: {
    title: 'Subcontractor BAA',
    blurb:
      'OsirisCare is subcontractor to the MSP, not direct-to-Covered-Entity. ' +
      'Scope limited to protected evidence metadata. PHI is scrubbed at the appliance before egress.',
  },
  reseller_addendum: {
    title: 'Reseller Addendum',
    blurb:
      'Licensing, margin, brand usage, termination terms, and client-data portability at off-boarding.',
  },
};

async function sha256Hex(text: string): Promise<string> {
  const enc = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest('SHA-256', enc);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

// Placeholder agreement text — in production, fetch these from the
// legal artifacts bucket. Here we include the outline so the sign
// flow has real text to hash.
const AGREEMENT_TEXTS: Record<AgreementType, string> = {
  msa:
    'OSIRISCARE MASTER SOFTWARE + SERVICES AGREEMENT v1.0 (2026-04-17). ' +
    'OsirisCare provides a HIPAA compliance attestation substrate. ' +
    'OsirisCare is not a managed service provider and does not operate Customer IT. ' +
    'Liability under this agreement is capped at twelve months of fees paid. ' +
    'No indemnification for patient harm. Full text available at /legal/msa.',
  subcontractor_baa:
    'OSIRISCARE SUBCONTRACTOR BUSINESS ASSOCIATE AGREEMENT v1.0 (2026-04-17). ' +
    'OsirisCare acts as a subcontractor to the MSP, which holds the direct BAA with the Covered Entity. ' +
    'Protected Health Information is scrubbed at the appliance before leaving customer premises. ' +
    'Breach notification runs up the chain from OsirisCare → MSP → Covered Entity within 60 days. ' +
    'Full text available at /legal/subcontractor-baa.',
  reseller_addendum:
    'OSIRISCARE RESELLER ADDENDUM v1.0 (2026-04-17). ' +
    'Partner resells OsirisCare under partner brand with margin per tier agreement. ' +
    'On termination, Customer evidence and configuration data is portable for 90 days. ' +
    'Partner may not use OsirisCare name without active MSA. ' +
    'Full text available at /legal/reseller-addendum.',
};

export const PartnerAgreements: React.FC = () => {
  const { apiKey } = usePartner();
  const [state, setState] = useState<MineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [signing, setSigning] = useState<AgreementType | null>(null);
  const [signerName, setSignerName] = useState('');

  const fetchOpts: RequestInit = apiKey
    ? { headers: { 'X-API-Key': apiKey } }
    : { credentials: 'include' };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/partners/agreements/mine', fetchOpts);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSign = async (atype: AgreementType) => {
    if (!signerName.trim()) {
      setError('Typed name is required to sign.');
      return;
    }
    if (!state) return;
    setError(null);
    try {
      const text = AGREEMENT_TEXTS[atype];
      const sha = await sha256Hex(text);
      const res = await fetch('/api/partners/agreements/sign', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : csrfHeaders()),
        },
        credentials: apiKey ? undefined : 'include',
        body: JSON.stringify({
          agreement_type: atype,
          version: state.current_versions[atype],
          signer_name: signerName.trim(),
          text_sha256: sha,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      setSigning(null);
      setSignerName('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'sign failed');
    }
  };

  if (loading) return <div className="p-6 text-slate-500">Loading agreement status…</div>;
  if (!state) return <div className="p-6 text-red-600">{error || 'Unable to load agreements'}</div>;

  return (
    <div className="p-6 space-y-6">
      <header>
        <h2 className="text-2xl font-semibold text-slate-900">Partner agreements</h2>
        <p className="text-sm text-slate-500 mt-1">
          MSP→Clinic relationship runs through these three documents. All must be current before you can invite clinics.
        </p>
      </header>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{error}</div>
      )}

      {state.ready_to_invite ? (
        <div className="p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800">
          All three agreements are current. You can invite clinics from the <strong>Invites</strong> tab.
        </div>
      ) : (
        <div className="p-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
          {state.missing.length} of 3 agreements outstanding: {state.missing.join(', ')}.
        </div>
      )}

      {state.required_types.map((atype) => {
        const current = state.current_versions[atype];
        const active = state.active[atype];
        const isStale = !active || active.version !== current;
        const labels = AGREEMENT_LABELS[atype];
        return (
          <div
            key={atype}
            className="border border-slate-200 rounded-lg p-5 bg-white"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-base font-semibold text-slate-900">{labels.title}</h3>
                <p className="text-sm text-slate-500 mt-1">{labels.blurb}</p>
                <p className="text-xs text-slate-400 mt-2">
                  Current version: <code className="font-mono">{current}</code>
                </p>
                {active && (
                  <p className="text-xs text-slate-400">
                    Signed {active.signed_at ? new Date(active.signed_at).toLocaleString() : '—'}{' '}
                    by {active.signer_name} · version <code className="font-mono">{active.version}</code>
                  </p>
                )}
              </div>
              <div>
                {isStale ? (
                  <span className="inline-block px-2 py-0.5 text-xs font-semibold text-amber-800 bg-amber-100 rounded">
                    {active ? 'Re-sign required' : 'Unsigned'}
                  </span>
                ) : (
                  <span className="inline-block px-2 py-0.5 text-xs font-semibold text-emerald-800 bg-emerald-100 rounded">
                    Current
                  </span>
                )}
              </div>
            </div>

            {signing === atype ? (
              <div className="mt-4 p-4 border border-slate-200 rounded bg-slate-50">
                <p className="text-sm text-slate-700 whitespace-pre-wrap">{AGREEMENT_TEXTS[atype]}</p>
                <div className="mt-4 flex items-center gap-3">
                  <input
                    type="text"
                    value={signerName}
                    onChange={(e) => setSignerName(e.target.value)}
                    placeholder="Type your full legal name"
                    className="flex-1 px-3 py-2 border border-slate-300 rounded text-sm"
                  />
                  <button
                    onClick={() => handleSign(atype)}
                    className="px-4 py-2 bg-teal-600 text-white rounded text-sm font-medium hover:bg-teal-700"
                  >
                    Sign
                  </button>
                  <button
                    onClick={() => {
                      setSigning(null);
                      setSignerName('');
                    }}
                    className="px-4 py-2 border border-slate-300 text-slate-700 rounded text-sm"
                  >
                    Cancel
                  </button>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  Typing your name constitutes an electronic signature. IP, user agent, and
                  SHA-256 of the text shown above are recorded for a 7-year retention window.
                </p>
              </div>
            ) : (
              isStale && (
                <button
                  onClick={() => {
                    setSigning(atype);
                    setError(null);
                  }}
                  className="mt-4 px-4 py-2 bg-teal-600 text-white rounded text-sm font-medium hover:bg-teal-700"
                >
                  Review + sign
                </button>
              )
            )}
          </div>
        );
      })}
    </div>
  );
};

export default PartnerAgreements;
