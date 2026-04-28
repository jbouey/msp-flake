import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { usePartner } from './PartnerContext';
import { buildAuthedHeaders } from '../utils/csrf';

/**
 * PartnerInvites — mint / list / revoke clinic signup invites.
 *
 * Depends on `require_active_partner_agreements` at the backend. If the
 * partner has not signed all three current agreements, the POST /create
 * endpoint returns 428 with `{detail: {missing: [...]}}` — this screen
 * surfaces that state and nudges the user to the Agreements tab.
 */

type Plan = 'pilot' | 'essentials' | 'professional' | 'enterprise';
type InviteStatus = 'active' | 'consumed' | 'revoked' | 'expired';

interface InviteRow {
  invite_id: string;
  plan: Plan;
  clinic_email: string | null;
  clinic_name: string | null;
  partner_brand: string | null;
  created_at: string | null;
  expires_at: string | null;
  consumed_at: string | null;
  consumed_signup_id: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
  status: InviteStatus;
}

interface MineResponse {
  partner_id: string;
  invites: InviteRow[];
}

interface CreatedInvite {
  invite_id: string;
  token: string;
  invite_url: string;
  plan: Plan;
  clinic_email: string | null;
  clinic_name: string | null;
  partner_brand: string | null;
  expires_at: string;
  ttl_days: number;
}

const PLAN_OPTIONS: { value: Plan; label: string; blurb: string }[] = [
  { value: 'pilot', label: 'Pilot — $299 one-time', blurb: '90-day proof-of-value' },
  { value: 'essentials', label: 'Essentials — $499/mo', blurb: 'Single appliance, 1-10 endpoints' },
  { value: 'professional', label: 'Professional — $799/mo', blurb: 'Multi-appliance mesh, 10-50 endpoints' },
  { value: 'enterprise', label: 'Enterprise — $1,299/mo', blurb: 'Multi-site, 50+ endpoints' },
];

const STATUS_STYLES: Record<InviteStatus, string> = {
  active: 'bg-emerald-100 text-emerald-800',
  consumed: 'bg-sky-100 text-sky-800',
  revoked: 'bg-slate-200 text-slate-700',
  expired: 'bg-amber-100 text-amber-800',
};

function absoluteInviteUrl(inviteUrl: string): string {
  if (typeof window === 'undefined') return inviteUrl;
  return new URL(inviteUrl, window.location.origin).toString();
}

export const PartnerInvites: React.FC<{ onGoToAgreements?: () => void }> = ({
  onGoToAgreements,
}) => {
  const { apiKey } = usePartner();

  const [rows, setRows] = useState<InviteRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agreementsMissing, setAgreementsMissing] = useState<string[] | null>(null);
  const [includeConsumed, setIncludeConsumed] = useState(false);

  // Create form
  const [plan, setPlan] = useState<Plan>('essentials');
  const [clinicEmail, setClinicEmail] = useState('');
  const [clinicName, setClinicName] = useState('');
  const [partnerBrand, setPartnerBrand] = useState('');
  const [ttlDays, setTtlDays] = useState(14);
  const [submitting, setSubmitting] = useState(false);
  const [justCreated, setJustCreated] = useState<CreatedInvite | null>(null);
  const [copied, setCopied] = useState(false);

  // Revoke modal
  const [revokeTarget, setRevokeTarget] = useState<InviteRow | null>(null);
  const [revokeReason, setRevokeReason] = useState('');

  const fetchOpts: RequestInit = useMemo(
    () => ({
      credentials: 'include',
      headers: buildAuthedHeaders({ apiKey }),
    }),
    [apiKey],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/partners/invites/mine?include_consumed=${includeConsumed}`,
        fetchOpts,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: MineResponse = await res.json();
      setRows(data.invites);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
    } finally {
      setLoading(false);
    }
  }, [fetchOpts, includeConsumed]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setAgreementsMissing(null);
    try {
      const res = await fetch('/api/partners/invites/create', {
        method: 'POST',
        credentials: 'include',
        headers: buildAuthedHeaders({ apiKey, json: true }),
        body: JSON.stringify({
          plan,
          clinic_email: clinicEmail.trim() || null,
          clinic_name: clinicName.trim() || null,
          partner_brand: partnerBrand.trim() || null,
          ttl_days: ttlDays,
        }),
      });
      if (res.status === 428) {
        const body = await res.json().catch(() => ({}));
        const missing = body?.detail?.missing ?? [];
        setAgreementsMissing(Array.isArray(missing) ? missing : []);
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`);
      }
      const created: CreatedInvite = await res.json();
      setJustCreated(created);
      setClinicEmail('');
      setClinicName('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'create failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    if (revokeReason.trim().length < 1) {
      setError('Revocation reason is required.');
      return;
    }
    setError(null);
    try {
      const res = await fetch(`/api/partners/invites/${revokeTarget.invite_id}/revoke`, {
        method: 'POST',
        credentials: 'include',
        headers: buildAuthedHeaders({ apiKey, json: true }),
        body: JSON.stringify({ reason: revokeReason.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`);
      }
      setRevokeTarget(null);
      setRevokeReason('');
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'revoke failed');
    }
  };

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API blocked — user can still select + copy manually.
    }
  };

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Clinic invites</h2>
          <p className="text-sm text-slate-500 mt-1">
            Mint a single-use signup link for a clinic. The clinic pays you; billing runs through
            your Stripe account on file. OsirisCare never emails your clinics directly.
          </p>
        </div>
      </header>

      {agreementsMissing && (
        <div className="p-4 bg-amber-50 border border-amber-200 rounded">
          <p className="text-sm text-amber-900 font-medium">Agreements not current</p>
          <p className="text-sm text-amber-800 mt-1">
            You need signed, current versions of these agreements before you can invite clinics:{' '}
            <strong>{agreementsMissing.join(', ')}</strong>.
          </p>
          {onGoToAgreements && (
            <button
              onClick={onGoToAgreements}
              className="mt-3 px-3 py-1.5 bg-amber-600 text-white rounded text-sm font-medium hover:bg-amber-700"
            >
              Open Agreements →
            </button>
          )}
        </div>
      )}

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{error}</div>
      )}

      {justCreated && (
        <div className="p-4 border border-emerald-200 bg-emerald-50 rounded space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-emerald-900">Invite created</p>
              <p className="text-xs text-emerald-800 mt-0.5">
                Send this URL to your clinic. The token is shown once — we only keep a SHA-256 after
                this dialog closes.
              </p>
            </div>
            <button
              onClick={() => setJustCreated(null)}
              className="text-sm text-emerald-900 hover:underline"
            >
              Dismiss
            </button>
          </div>
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={absoluteInviteUrl(justCreated.invite_url)}
              onFocus={(e) => e.currentTarget.select()}
              className="flex-1 px-3 py-2 border border-emerald-300 rounded text-sm font-mono bg-white"
            />
            <button
              onClick={() => handleCopy(absoluteInviteUrl(justCreated.invite_url))}
              className="px-3 py-2 bg-emerald-600 text-white rounded text-sm font-medium hover:bg-emerald-700"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <p className="text-xs text-emerald-800">
            Plan: <strong>{justCreated.plan}</strong> · Expires{' '}
            {new Date(justCreated.expires_at).toLocaleString()} · Clinic:{' '}
            {justCreated.clinic_name || justCreated.clinic_email || '—'}
          </p>
        </div>
      )}

      <section className="border border-slate-200 rounded-lg p-5 bg-white">
        <h3 className="text-base font-semibold text-slate-900">Create invite</h3>
        <form onSubmit={handleCreate} className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="block">
            <span className="block text-sm font-medium text-slate-700">Plan</span>
            <select
              value={plan}
              onChange={(e) => setPlan(e.target.value as Plan)}
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
            >
              {PLAN_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-500">
              {PLAN_OPTIONS.find((p) => p.value === plan)?.blurb}
            </span>
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700">TTL (days)</span>
            <input
              type="number"
              min={1}
              max={60}
              value={ttlDays}
              onChange={(e) => setTtlDays(Math.max(1, Math.min(60, parseInt(e.target.value) || 14)))}
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
            />
            <span className="mt-1 block text-xs text-slate-500">
              How long the link stays valid before expiring. 1–60 days.
            </span>
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700">Clinic email (optional)</span>
            <input
              type="email"
              value={clinicEmail}
              onChange={(e) => setClinicEmail(e.target.value)}
              placeholder="billing@clinic.example"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700">Clinic name (optional)</span>
            <input
              type="text"
              value={clinicName}
              onChange={(e) => setClinicName(e.target.value)}
              placeholder="North Valley Family Medicine"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
            />
          </label>

          <label className="block md:col-span-2">
            <span className="block text-sm font-medium text-slate-700">Partner brand (optional)</span>
            <input
              type="text"
              value={partnerBrand}
              onChange={(e) => setPartnerBrand(e.target.value)}
              placeholder="Your MSP name shown on the clinic signup page"
              className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
            />
          </label>

          <div className="md:col-span-2 flex items-center justify-end gap-3">
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 bg-teal-600 text-white rounded text-sm font-medium hover:bg-teal-700 disabled:bg-slate-300"
            >
              {submitting ? 'Creating…' : 'Create invite'}
            </button>
          </div>
        </form>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-900">Issued invites</h3>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={includeConsumed}
              onChange={(e) => setIncludeConsumed(e.target.checked)}
            />
            Include consumed / revoked / expired
          </label>
        </div>

        {loading ? (
          <div className="p-6 text-slate-500">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-6 text-slate-500 text-sm">No invites yet.</div>
        ) : (
          <div className="mt-3 overflow-x-auto border border-slate-200 rounded-lg bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500 uppercase">
                <tr>
                  <th className="px-4 py-2 text-left">Clinic</th>
                  <th className="px-4 py-2 text-left">Plan</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-left">Created</th>
                  <th className="px-4 py-2 text-left">Expires</th>
                  <th className="px-4 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.invite_id} className="border-t border-slate-100">
                    <td className="px-4 py-2">
                      <div className="font-medium text-slate-900">
                        {r.clinic_name || r.clinic_email || <em className="text-slate-400">Unnamed</em>}
                      </div>
                      {r.clinic_email && r.clinic_name && (
                        <div className="text-xs text-slate-500">{r.clinic_email}</div>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-700">{r.plan}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-block px-2 py-0.5 text-xs font-semibold rounded ${STATUS_STYLES[r.status]}`}
                      >
                        {r.status}
                      </span>
                      {r.status === 'revoked' && r.revoke_reason && (
                        <div className="text-xs text-slate-500 mt-1">{r.revoke_reason}</div>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-500 text-xs">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-4 py-2 text-slate-500 text-xs">
                      {r.expires_at ? new Date(r.expires_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {r.status === 'active' ? (
                        <button
                          onClick={() => {
                            setRevokeTarget(r);
                            setRevokeReason('');
                          }}
                          className="text-sm text-red-700 hover:underline"
                        >
                          Revoke
                        </button>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {revokeTarget && (
        <div
          className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50"
          onClick={() => setRevokeTarget(null)}
        >
          <div
            className="bg-white rounded-lg shadow-lg p-6 w-full max-w-md"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-slate-900">Revoke invite</h3>
            <p className="text-sm text-slate-600 mt-1">
              Revoke the invite for{' '}
              <strong>{revokeTarget.clinic_name || revokeTarget.clinic_email || 'this clinic'}</strong>?
              Once revoked, the link will be rejected at signup time.
            </p>
            <label className="block mt-4">
              <span className="block text-sm font-medium text-slate-700">Reason (required)</span>
              <textarea
                value={revokeReason}
                onChange={(e) => setRevokeReason(e.target.value)}
                rows={3}
                placeholder="Clinic cancelled / sent to wrong contact / …"
                className="mt-1 w-full px-3 py-2 border border-slate-300 rounded text-sm"
              />
            </label>
            <div className="mt-4 flex items-center justify-end gap-3">
              <button
                onClick={() => setRevokeTarget(null)}
                className="px-4 py-2 border border-slate-300 text-slate-700 rounded text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleRevoke}
                className="px-4 py-2 bg-red-600 text-white rounded text-sm font-medium hover:bg-red-700"
              >
                Revoke
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartnerInvites;
