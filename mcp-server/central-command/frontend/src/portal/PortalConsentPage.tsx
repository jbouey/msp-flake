/**
 * PortalConsentPage — Migration 184 Phase 4 client-side.
 *
 * /portal/:siteId/consent — practice manager grants + revokes
 * class-level consent. One card per class (12 from the seed) +
 * grant/revoke modals. The grant flow writes a signed +
 * hash-chained `compliance_bundles` row via the same server path
 * used by the magic-link approve flow.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { csrfHeaders } from '../utils/csrf';

interface ClassInfo {
  class_id: string;
  display_name: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high';
  hipaa_controls: string[];
  example_actions: string[];
}

interface ConsentRow {
  consent_id: string;
  class_id: string;
  consented_by_email: string;
  consented_at: string | null;
  consent_ttl_days: number;
  revoked_at: string | null;
  revocation_reason: string | null;
  expires_at: string | null;
  active: boolean;
}

interface Payload {
  site_id: string;
  classes: ClassInfo[];
  consents: ConsentRow[];
  generated_at: string;
}

const RISK_TONE: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  medium: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  high: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export const PortalConsentPage: React.FC = () => {
  const { siteId } = useParams<{ siteId: string }>();
  const [sp] = useSearchParams();
  const token = sp.get('token');
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [grantModal, setGrantModal] = useState<ClassInfo | null>(null);
  const [revokeModal, setRevokeModal] = useState<ConsentRow | null>(null);

  const load = useCallback(async () => {
    if (!siteId) return;
    setLoading(true);
    try {
      const qs = token ? `?token=${encodeURIComponent(token)}` : '';
      const res = await fetch(`/api/portal/site/${siteId}/consent${qs}`, { credentials: 'same-origin' }); // same-origin-allowed: portal endpoint — token-OR-cookie anonymous auth (BUG 2 KEEP 2026-05-12)
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = (await res.json()) as Payload;
      setData(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [siteId, token]);

  useEffect(() => { void load(); }, [load]);

  const consentByClass = new Map<string, ConsentRow>();
  if (data) {
    // Most-recent active consent per class (if any)
    data.consents.forEach((c) => {
      const existing = consentByClass.get(c.class_id);
      if (!existing || (c.active && !existing.active)) {
        consentByClass.set(c.class_id, c);
      }
    });
  }

  if (loading && !data) return <div className="p-6 text-sm text-white/60">Loading consent classes…</div>;
  if (error && !data) return <div className="p-6 text-rose-300">Failed to load: {error}</div>;

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-white/90">Authorizations</h1>
        <p className="text-xs text-white/60 mt-1">
          You control which categories of automated action your IT partner is allowed
          to run on your behalf. Revoke any time — takes effect at the next check-in.
        </p>
      </div>

      {data?.classes.length === 0 && (
        <div className="rounded-2xl bg-white/5 border border-white/10 p-6 text-sm text-white/60">
          No consent classes available yet. Contact your partner.
        </div>
      )}

      <div className="space-y-3">
        {data?.classes.map((c) => {
          const consent = consentByClass.get(c.class_id);
          return (
            <div key={c.class_id} className="rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10 p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-semibold text-white/90">{c.display_name}</h3>
                    <span className={`px-2 py-0.5 text-[10px] rounded-full border uppercase ${RISK_TONE[c.risk_level]}`}>
                      {c.risk_level}
                    </span>
                    {consent?.active && (
                      <span className="px-2 py-0.5 text-[10px] rounded-full border bg-emerald-500/10 text-emerald-400 border-emerald-500/30">
                        Active
                      </span>
                    )}
                    {consent && !consent.active && consent.revoked_at && (
                      <span className="px-2 py-0.5 text-[10px] rounded-full border bg-slate-500/10 text-slate-300 border-slate-500/30">
                        Revoked
                      </span>
                    )}
                  </div>
                  <p className="text-[12px] text-white/60 mt-1">{c.description}</p>
                  {c.hipaa_controls && c.hipaa_controls.length > 0 && (
                    <div className="text-[10px] text-white/40 mt-1 font-mono">
                      HIPAA: {c.hipaa_controls.slice(0, 3).join(', ')}
                    </div>
                  )}
                  {consent?.active && (
                    <div className="text-[11px] text-white/60 mt-2">
                      Granted by <b className="text-white/80">{consent.consented_by_email}</b> on {formatDate(consent.consented_at)}
                      {consent.expires_at && <> · expires {formatDate(consent.expires_at)}</>}
                    </div>
                  )}
                </div>
                <div className="shrink-0">
                  {consent?.active ? (
                    <button
                      onClick={() => setRevokeModal(consent)}
                      className="px-3 py-1.5 rounded border border-rose-500/40 text-rose-300 hover:bg-rose-500/10 text-xs"
                    >
                      Revoke
                    </button>
                  ) : (
                    <button
                      onClick={() => setGrantModal(c)}
                      className="px-3 py-1.5 rounded bg-blue-500 hover:bg-blue-600 text-white text-xs font-semibold"
                    >
                      Grant
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {grantModal && siteId && (
        <GrantConsentModal
          klass={grantModal}
          siteId={siteId}
          token={token}
          onClose={() => setGrantModal(null)}
          onSuccess={() => { setGrantModal(null); void load(); }}
        />
      )}
      {revokeModal && siteId && (
        <RevokeConsentModal
          consent={revokeModal}
          siteId={siteId}
          token={token}
          onClose={() => setRevokeModal(null)}
          onSuccess={() => { setRevokeModal(null); void load(); }}
        />
      )}
    </div>
  );
};

const GrantConsentModal: React.FC<{
  klass: ClassInfo;
  siteId: string;
  token: string | null;
  onClose: () => void;
  onSuccess: () => void;
}> = ({ klass, siteId, token, onClose, onSuccess }) => {
  const [email, setEmail] = useState('');
  const [ttl, setTtl] = useState(365);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true); setErr(null);
    try {
      const qs = token ? `?token=${encodeURIComponent(token)}` : '';
      const res = await fetch(`/api/portal/site/${siteId}/consent/grant${qs}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({
          class_id: klass.class_id,
          consented_by_email: email,
          ttl_days: ttl,
        }),
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const b = await res.json(); if (b?.detail) msg = String(b.detail); } catch { /* noop */ }
        throw new Error(msg);
      }
      onSuccess();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const valid = email.includes('@') && ttl >= 30 && ttl <= 3650;

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-white/10 rounded-xl p-5 w-full max-w-lg shadow-xl">
        <h3 className="text-base font-semibold text-white">Grant consent: {klass.display_name}</h3>
        <p className="text-[12px] text-white/60 mt-1">{klass.description}</p>
        <div className="mt-3 p-3 bg-white/5 rounded text-[11px] text-white/70 leading-relaxed">
          By clicking Grant, you authorize OsirisCare-verified runbooks in the{' '}
          <b>{klass.display_name}</b> category to run on this site. This consent can be
          revoked instantly from this page. A cryptographic record is created now and
          stored in your compliance packet chain for 7 years (HIPAA §164.316(b)(2)(i)).
        </div>
        <label className="block mt-4">
          <span className="text-xs text-white/70">Your email <span className="text-rose-400">*</span></span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-white/10 rounded text-sm text-white" />
        </label>
        <label className="block mt-3">
          <span className="text-xs text-white/70">Duration (days) · 30–3650</span>
          <select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}
            className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-white/10 rounded text-sm text-white">
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
            <option value={365}>1 year (recommended)</option>
            <option value={730}>2 years</option>
          </select>
        </label>
        {err && <div className="mt-3 text-xs text-rose-400 p-2 bg-rose-500/10 rounded">{err}</div>}
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} disabled={submitting}
            className="px-3 py-1.5 text-sm rounded border border-white/10 text-white/70">Cancel</button>
          <button onClick={submit} disabled={!valid || submitting}
            className="px-3 py-1.5 text-sm rounded bg-blue-500 hover:bg-blue-600 text-white disabled:opacity-50">
            {submitting ? 'Granting…' : 'Grant'}
          </button>
        </div>
      </div>
    </div>
  );
};

const RevokeConsentModal: React.FC<{
  consent: ConsentRow;
  siteId: string;
  token: string | null;
  onClose: () => void;
  onSuccess: () => void;
}> = ({ consent, siteId, token, onClose, onSuccess }) => {
  const [reason, setReason] = useState('');
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (submitting) return;
    setSubmitting(true); setErr(null);
    try {
      const qs = token ? `?token=${encodeURIComponent(token)}` : '';
      const res = await fetch(`/api/portal/site/${siteId}/consent/${consent.consent_id}/revoke${qs}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ reason, revoked_by_email: email }),
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const b = await res.json(); if (b?.detail) msg = String(b.detail); } catch { /* noop */ }
        throw new Error(msg);
      }
      onSuccess();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const valid = email.includes('@') && reason.length >= 10;

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-white/10 rounded-xl p-5 w-full max-w-lg shadow-xl">
        <h3 className="text-base font-semibold text-white">Revoke consent</h3>
        <p className="text-[12px] text-white/60 mt-1">
          Class: {consent.class_id}. Queued remediation in this class will be canceled;
          new executions will be blocked until you re-grant.
        </p>
        <label className="block mt-4">
          <span className="text-xs text-white/70">Your email <span className="text-rose-400">*</span></span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-white/10 rounded text-sm text-white" />
        </label>
        <label className="block mt-3">
          <span className="text-xs text-white/70">Reason (min 10 chars) · <span className={reason.length >= 10 ? 'text-emerald-400' : 'text-white/50'}>{reason.length}/10</span></span>
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
            className="w-full mt-1 px-2 py-1.5 bg-slate-800 border border-white/10 rounded text-sm text-white" />
        </label>
        {err && <div className="mt-3 text-xs text-rose-400 p-2 bg-rose-500/10 rounded">{err}</div>}
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} disabled={submitting}
            className="px-3 py-1.5 text-sm rounded border border-white/10 text-white/70">Cancel</button>
          <button onClick={submit} disabled={!valid || submitting}
            className="px-3 py-1.5 text-sm rounded bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-50">
            {submitting ? 'Revoking…' : 'Revoke'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PortalConsentPage;
