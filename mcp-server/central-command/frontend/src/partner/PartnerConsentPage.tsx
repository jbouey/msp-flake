/**
 * PartnerConsentPage — Migration 184 Phase 4 partner-side.
 *
 * /partner/site/:siteId/consent — dense table showing 12 classes +
 * who has/hasn't granted consent. Partner can REQUEST consent (email
 * a magic link) but cannot grant OR revoke on behalf of the client.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { csrfHeaders } from '../utils/csrf';

interface Entry {
  class_id: string;
  display_name: string;
  risk_level: 'low' | 'medium' | 'high';
  hipaa_controls: string[];
  active_consent: null | {
    consent_id: string;
    consented_by_email: string;
    consented_at: string | null;
    expires_at: string | null;
  };
}

interface Payload {
  site_id: string;
  classes: Entry[];
  total_classes: number;
  covered_classes: number;
  coverage_pct: number;
}

const RISK_TONE: Record<string, string> = {
  low: 'bg-emerald-100 text-emerald-700',
  medium: 'bg-amber-100 text-amber-800',
  high: 'bg-rose-100 text-rose-700',
};

export const PartnerConsentPage: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requestModal, setRequestModal] = useState<Entry | null>(null);

  const load = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/partners/me/sites/${siteId}/consent`, { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = (await res.json()) as Payload;
      setData(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [siteId]);

  useEffect(() => { void load(); }, [load]);

  if (loading && !data) return <div className="p-6 text-sm text-slate-500">Loading consent table…</div>;
  if (error && !data) return <div className="p-6">
    <button onClick={() => navigate('/partner/dashboard')} className="text-sm text-blue-600 hover:underline mb-3">← Partner dashboard</button>
    <div className="bg-rose-50 border border-rose-200 rounded p-3 text-sm text-rose-700">{error}</div>
  </div>;

  if (!data) return null;

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-5xl mx-auto">
        <button onClick={() => navigate('/partner/dashboard')} className="text-sm text-blue-600 hover:underline mb-3">
          ← Partner dashboard
        </button>
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm mb-4 p-5 flex items-start justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-900">Consent coverage</h1>
            <div className="text-sm text-slate-500">Site: <span className="font-mono">{data.site_id}</span></div>
          </div>
          <div className="text-right">
            <div className={`text-3xl font-bold tabular-nums ${data.coverage_pct >= 75 ? 'text-emerald-600' : data.coverage_pct >= 25 ? 'text-amber-600' : 'text-rose-600'}`}>{/* noqa: score-threshold-gate — runbook-consent class coverage (75/25, distinct domain) */}
              {data.coverage_pct.toFixed(0)}%
            </div>
            <div className="text-[11px] text-slate-500">{data.covered_classes} / {data.total_classes} classes</div>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2 text-left">Class</th>
                <th className="px-2 py-2 text-left">Risk</th>
                <th className="px-2 py-2 text-left">Granted by</th>
                <th className="px-2 py-2 text-left">Expires</th>
                <th className="px-5 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {data.classes.map((c) => (
                <tr key={c.class_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-5 py-2">
                    <div className="font-medium text-slate-900">{c.display_name}</div>
                    <div className="text-[10px] font-mono text-slate-400">{c.class_id}</div>
                  </td>
                  <td className="px-2 py-2">
                    <span className={`px-2 py-0.5 text-[10px] rounded-full uppercase ${RISK_TONE[c.risk_level]}`}>
                      {c.risk_level}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-xs">
                    {c.active_consent ? (
                      <div>
                        <div className="text-emerald-700 font-medium truncate max-w-[160px]" title={c.active_consent.consented_by_email}>
                          {c.active_consent.consented_by_email}
                        </div>
                        <div className="text-[10px] text-slate-400">
                          {c.active_consent.consented_at && new Date(c.active_consent.consented_at).toLocaleDateString()}
                        </div>
                      </div>
                    ) : (
                      <span className="text-slate-400 italic">—</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-xs text-slate-500 tabular-nums">
                    {c.active_consent?.expires_at
                      ? new Date(c.active_consent.expires_at).toLocaleDateString()
                      : '—'}
                  </td>
                  <td className="px-5 py-2 text-right">
                    {c.active_consent ? (
                      <span className="text-[11px] text-emerald-600">✓ Active</span>
                    ) : (
                      <button onClick={() => setRequestModal(c)}
                        className="px-3 py-1 bg-blue-500 hover:bg-blue-600 text-white text-xs rounded">
                        Request
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {requestModal && siteId && (
        <RequestConsentModal
          siteId={siteId} klass={requestModal}
          onClose={() => setRequestModal(null)}
          onSuccess={() => { setRequestModal(null); void load(); }}
        />
      )}
    </div>
  );
};

const RequestConsentModal: React.FC<{
  siteId: string;
  klass: Entry;
  onClose: () => void;
  onSuccess: () => void;
}> = ({ siteId, klass, onClose, onSuccess }) => {
  const [forEmail, setForEmail] = useState('');
  const [ttl, setTtl] = useState(365);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true); setErr(null);
    try {
      const res = await fetch(`/api/partners/me/sites/${siteId}/consent/request`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          class_id: klass.class_id,
          requested_for_email: forEmail,
          ttl_days: ttl,
        }),
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const b = await res.json(); if (b?.detail) msg = String(b.detail); } catch { /* noop */ }
        throw new Error(msg);
      }
      setSuccess(true);
      setTimeout(onSuccess, 1500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const valid = forEmail.includes('@') && ttl >= 30 && ttl <= 3650;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-white border border-slate-200 rounded-xl p-5 w-full max-w-md">
        <h3 className="text-base font-semibold text-slate-900">Request consent: {klass.display_name}</h3>
        <p className="text-xs text-slate-500 mt-1">
          We'll email a single-use magic link to the client. They review + approve from their browser.
        </p>
        {success ? (
          <div className="mt-4 p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800">
            ✓ Request sent. Email delivered to {forEmail}. Link expires in 72h.
          </div>
        ) : (
          <>
            <label className="block mt-4">
              <span className="text-xs text-slate-700">Client's email address <span className="text-rose-500">*</span></span>
              <input type="email" value={forEmail} onChange={(e) => setForEmail(e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded text-sm"
                placeholder="practice-manager@clinic.com" />
            </label>
            <label className="block mt-3">
              <span className="text-xs text-slate-700">Requested duration</span>
              <select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}
                className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded text-sm">
                <option value={90}>90 days</option>
                <option value={180}>180 days</option>
                <option value={365}>1 year (recommended)</option>
                <option value={730}>2 years</option>
              </select>
            </label>
            {err && <div className="mt-3 text-xs text-rose-600 p-2 bg-rose-50 rounded">{err}</div>}
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={onClose} disabled={submitting}
                className="px-3 py-1.5 text-sm rounded border border-slate-300 text-slate-700">Cancel</button>
              <button onClick={submit} disabled={!valid || submitting}
                className="px-3 py-1.5 text-sm rounded bg-blue-500 hover:bg-blue-600 text-white disabled:opacity-50">
                {submitting ? 'Sending…' : 'Send request'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default PartnerConsentPage;
