/**
 * ConsentApprovePage — Migration 184 Phase 4 magic-link flow.
 *
 * Route: /consent/approve/:token — PUBLIC (no portal session required).
 * The token in the URL is both the auth and the lookup key.
 *
 * 1. GET  /api/portal/consent/approve/{token}  → class + partner details
 * 2. Show the approval card
 * 3. POST /api/portal/consent/approve/{token}  → consume + create consent
 */

import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { csrfHeaders } from '../utils/csrf';

interface TokenDetails {
  site_id: string;
  clinic_name: string | null;
  class_id: string;
  class_display_name: string;
  class_description: string;
  class_risk_level: 'low' | 'medium' | 'high';
  class_hipaa_controls: string[];
  class_example_actions: string[];
  requested_by_email: string;
  requested_for_email: string;
  requested_ttl_days: number;
  partner_brand: string;
  primary_color: string;
  expires_at: string | null;
}

const RISK_TONE: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-700 border-emerald-300',
  medium: 'bg-amber-500/10 text-amber-800 border-amber-300',
  high: 'bg-rose-500/10 text-rose-700 border-rose-300',
};

export const ConsentApprovePage: React.FC = () => {
  const { token } = useParams<{ token: string }>();
  const [details, setDetails] = useState<TokenDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [successConsent, setSuccessConsent] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/portal/consent/approve/${encodeURIComponent(token)}`);
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const b = await res.json(); if (b?.detail) msg = String(b.detail); } catch { /* noop */ }
        throw new Error(msg);
      }
      const d = (await res.json()) as TokenDetails;
      setDetails(d);
      setEmail(d.requested_for_email);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  const approve = async () => {
    if (!token || submitting) return;
    setSubmitting(true); setError(null);
    try {
      const res = await fetch(`/api/portal/consent/approve/${encodeURIComponent(token)}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ consented_by_email: email }),
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const b = await res.json(); if (b?.detail) msg = String(b.detail); } catch { /* noop */ }
        throw new Error(msg);
      }
      const j = await res.json();
      setSuccessConsent(j.consent_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="text-sm text-slate-500">Loading request…</div>
    </div>;
  }

  if (error && !details) {
    return <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <div className="max-w-md w-full bg-white border border-rose-200 rounded-xl p-6 text-center">
        <div className="text-rose-600 text-lg mb-2">⚠</div>
        <div className="text-sm font-medium text-slate-900">This link can't be used</div>
        <div className="text-xs text-slate-500 mt-2">{error}</div>
        <div className="text-xs text-slate-400 mt-4">
          Magic links expire after 72h and are single-use. Contact your partner for a fresh link.
        </div>
      </div>
    </div>;
  }

  if (successConsent && details) {
    return <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <div className="max-w-md w-full bg-white border border-emerald-200 rounded-xl p-6 text-center">
        <div className="text-emerald-600 text-4xl mb-2">✓</div>
        <div className="text-base font-semibold text-slate-900">Consent recorded</div>
        <div className="text-xs text-slate-500 mt-2">
          {details.partner_brand} can now run {details.class_display_name} runbooks on {details.site_id}.
        </div>
        <div className="text-[10px] text-slate-400 mt-3 font-mono break-all">
          consent_id: {successConsent}
        </div>
        <div className="text-xs text-slate-500 mt-4">
          A cryptographic record has been created in your compliance packet chain.
          You can revoke anytime from your portal.
        </div>
      </div>
    </div>;
  }

  if (!details) return null;

  return (
    <div className="min-h-screen bg-slate-50 p-4">
      <div className="max-w-xl mx-auto">
        {/* Partner brand header */}
        <div className="bg-white border-b-4 rounded-t-xl px-6 py-4 text-sm" style={{ borderColor: details.primary_color }}>
          <div className="font-semibold text-slate-900" style={{ color: details.primary_color }}>
            {details.partner_brand}
          </div>
        </div>

        <div className="bg-white rounded-b-xl p-6 shadow-sm">
          <h1 className="text-xl font-semibold text-slate-900">Authorization requested</h1>
          <p className="text-sm text-slate-600 mt-1">
            <b>{details.partner_brand}</b> is asking for your authorization to run automated
            remediation on <b>{details.clinic_name || details.site_id}</b>.
          </p>

          <div className="mt-4 rounded-lg border border-slate-200 p-4 bg-slate-50">
            <div className="flex items-center gap-2 flex-wrap">
              <div className="font-semibold text-slate-900">{details.class_display_name}</div>
              <span className={`px-2 py-0.5 text-[10px] rounded-full border uppercase ${RISK_TONE[details.class_risk_level]}`}>
                {details.class_risk_level} risk
              </span>
            </div>
            <div className="text-xs text-slate-600 mt-2">{details.class_description}</div>
            {details.class_example_actions && details.class_example_actions.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">Example actions</div>
                <ul className="text-[11px] text-slate-600 mt-1 space-y-0.5">
                  {details.class_example_actions.slice(0, 4).map((a, i) => (
                    <li key={i} className="font-mono">• {String(a)}</li>
                  ))}
                </ul>
              </div>
            )}
            {details.class_hipaa_controls && details.class_hipaa_controls.length > 0 && (
              <div className="mt-3 text-[10px] text-slate-500 font-mono">
                HIPAA: {details.class_hipaa_controls.slice(0, 3).join(', ')}
              </div>
            )}
          </div>

          <div className="mt-4 text-xs text-slate-600 leading-relaxed">
            By clicking <b>Approve</b> you authorize <b>{details.partner_brand}</b> to run
            OsirisCare-verified remediations in this specific category for up to{' '}
            <b>{details.requested_ttl_days} days</b>. You can revoke anytime from your portal —
            revocation takes effect at the next check-in (≤15 min) and cancels any pending
            remediation.
          </div>

          <label className="block mt-5">
            <span className="text-xs text-slate-700">Confirm your email <span className="text-rose-500">*</span></span>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full mt-1 px-2 py-2 border border-slate-300 rounded text-sm" />
            <div className="text-[10px] text-slate-500 mt-1">
              Must match the email the invitation was sent to ({details.requested_for_email}).
            </div>
          </label>

          {error && <div className="mt-3 text-xs text-rose-600 p-2 bg-rose-50 border border-rose-200 rounded">{error}</div>}

          <div className="flex justify-end mt-5">
            <button onClick={approve} disabled={submitting || !email.includes('@')}
              className="px-5 py-2 rounded text-sm font-semibold text-white disabled:opacity-50"
              style={{ background: details.primary_color }}>
              {submitting ? 'Approving…' : 'Approve and record consent'}
            </button>
          </div>

          <div className="mt-4 text-[10px] text-slate-400 text-center">
            Single-use magic link · expires{' '}
            {details.expires_at ? new Date(details.expires_at).toLocaleString() : '—'}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConsentApprovePage;
