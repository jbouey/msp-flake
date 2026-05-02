import React, { useEffect, useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';
import { getCsrfTokenOrEmpty } from '../utils/csrf';

/**
 * AdminBilling — owner-operator subscription management. #67 read-only
 * shipped 2026-05-02; #72 destructive actions added 2026-05-02.
 *
 * Destructive ops (cancel, refund) flow through the privileged-action
 * chain: typed confirm_phrase + reason ≥20ch + email-format actor +
 * Stripe idempotency_key + admin_audit_log + Ed25519
 * privileged_access_attestation written to the customer's site_id.
 *
 * "Open in Stripe" is still the recommended path when an operator
 * needs to inspect line-items, invoices, or charge IDs that the
 * Central Command summary doesn't carry.
 */
interface Subscription {
  stripe_subscription_id: string;
  stripe_customer_id: string;
  site_id: string | null;
  plan: string;
  status: string;
  trial_end: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  billing_mode: string;
  partner_id: string | null;
  partner_name: string | null;
  stripe_dashboard_url: string;
}

const STATUS_TONE: Record<string, string> = {
  active: 'text-emerald-600 bg-emerald-50',
  trialing: 'text-blue-600 bg-blue-50',
  past_due: 'text-amber-700 bg-amber-50',
  canceled: 'text-label-tertiary bg-fill-secondary',
  incomplete: 'text-rose-600 bg-rose-50',
  unpaid: 'text-rose-700 bg-rose-100',
};

const PLAN_LABEL: Record<string, string> = {
  pilot: 'Pilot',
  essentials: 'Essentials',
  professional: 'Professional',
  enterprise: 'Enterprise',
};

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString();
}

type DestructiveAction = 'cancel' | 'refund';

interface PendingAction {
  kind: DestructiveAction;
  sub: Subscription;
}

export const AdminBilling: React.FC = () => {
  const [data, setData] = useState<Subscription[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const url = '/api/admin/billing/customers' + (statusFilter ? `?status_filter=${statusFilter}` : '');
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const j = await res.json();
        if (!cancelled) {
          setData(j.customers || []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'load failed');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [statusFilter, reloadKey]);

  if (loading && !data) return <div className="p-6"><Spinner /></div>;
  if (error && !data) return <div className="p-6 text-rose-600 text-sm">Failed: {error}</div>;
  if (!data) return null;

  const counts = {
    active: data.filter((s) => s.status === 'active').length,
    trialing: data.filter((s) => s.status === 'trialing').length,
    past_due: data.filter((s) => s.status === 'past_due').length,
    canceled: data.filter((s) => s.status === 'canceled').length,
  };

  return (
    <div className="space-y-6 page-enter">
      <div>
        <h1 className="text-2xl font-semibold text-label-primary">Admin Billing</h1>
        <p className="text-sm text-label-tertiary mt-1">
          Read-only view of all customer subscriptions.
          Destructive operations (refund, cancel) — click "Open in Stripe" to perform there with audit trail.
        </p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {(['active', 'trialing', 'past_due', 'canceled'] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(statusFilter === s ? '' : s)}
            className={`glass-card p-4 text-center ${statusFilter === s ? 'ring-2 ring-accent-primary' : ''}`}
          >
            <p className="text-2xl font-bold text-label-primary">{counts[s]}</p>
            <p className="text-xs text-label-tertiary uppercase tracking-wider mt-1">{s.replace('_', ' ')}</p>
          </button>
        ))}
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-label-tertiary">
          Showing {data.length} subscription{data.length === 1 ? '' : 's'}
          {statusFilter && ` (filtered by ${statusFilter})`}
        </span>
        {statusFilter && (
          <button
            onClick={() => setStatusFilter('')}
            className="text-xs text-accent-primary hover:underline"
          >
            Clear filter
          </button>
        )}
      </div>

      {/* Table */}
      <GlassCard>
        {data.length === 0 ? (
          <div className="p-8 text-center text-label-tertiary">No subscriptions match.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wide text-label-tertiary border-b border-separator-light">
              <tr>
                <th className="text-left py-2 px-3">Customer / Site</th>
                <th className="text-left py-2 px-3">Plan</th>
                <th className="text-left py-2 px-3">Status</th>
                <th className="text-left py-2 px-3">Billing</th>
                <th className="text-left py-2 px-3">Period end</th>
                <th className="text-right py-2 px-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-separator-light">
              {data.map((sub) => (
                <tr key={sub.stripe_subscription_id} className="hover:bg-fill-secondary/30">
                  <td className="py-3 px-3">
                    <div className="font-medium text-label-primary">
                      {sub.partner_name || sub.site_id || '—'}
                    </div>
                    <div className="text-xs text-label-tertiary font-mono">
                      {sub.stripe_customer_id}
                    </div>
                  </td>
                  <td className="py-3 px-3 text-label-secondary">
                    {PLAN_LABEL[sub.plan] || sub.plan}
                  </td>
                  <td className="py-3 px-3">
                    <span className={`inline-block px-2 py-0.5 text-xs font-medium rounded ${STATUS_TONE[sub.status] || 'text-label-secondary bg-fill-secondary'}`}>
                      {sub.status}
                    </span>
                    {sub.cancel_at_period_end && (
                      <span className="ml-1 text-xs text-amber-700">↓ cancels</span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-label-secondary text-xs">
                    {sub.billing_mode}
                  </td>
                  <td className="py-3 px-3 text-label-secondary text-xs">
                    {fmtDate(sub.current_period_end)}
                  </td>
                  <td className="py-3 px-3 text-right">
                    <div className="inline-flex gap-1.5">
                      <button
                        type="button"
                        disabled={sub.cancel_at_period_end || sub.status === 'canceled'}
                        onClick={() => setPending({ kind: 'cancel', sub })}
                        className="text-xs px-2.5 py-1.5 rounded-ios bg-amber-600/90 text-white hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed"
                        title={sub.cancel_at_period_end ? 'Already scheduled to cancel' : 'Cancel at period end'}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => setPending({ kind: 'refund', sub })}
                        className="text-xs px-2.5 py-1.5 rounded-ios bg-rose-600/90 text-white hover:bg-rose-600"
                      >
                        Refund
                      </button>
                      <a
                        href={sub.stripe_dashboard_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs px-2.5 py-1.5 rounded-ios bg-purple-600 text-white hover:bg-purple-700"
                      >
                        Stripe ↗
                      </a>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>

      {pending && (
        <BillingActionModal
          action={pending}
          onClose={() => setPending(null)}
          onSuccess={() => {
            setPending(null);
            setReloadKey((k) => k + 1);
          }}
        />
      )}
    </div>
  );
};

const CONFIRM_PHRASES: Record<DestructiveAction, string> = {
  cancel: 'CANCEL-CUSTOMER-SUBSCRIPTION',
  refund: 'REFUND-CUSTOMER-CHARGE',
};

const ACTION_COPY: Record<DestructiveAction, { title: string; warning: string; cta: string; tone: string }> = {
  cancel: {
    title: 'Cancel customer subscription',
    warning:
      'Sets cancel_at_period_end=true in Stripe. Customer keeps access through the current billing period; no immediate refund.',
    cta: 'Cancel subscription',
    tone: 'bg-amber-600 hover:bg-amber-500',
  },
  refund: {
    title: 'Refund customer charge',
    warning:
      'Issues a Stripe refund. Customer is notified by Stripe email. Idempotent within a calendar day — repeating with the same reason will dedupe.',
    cta: 'Issue refund',
    tone: 'bg-rose-600 hover:bg-rose-500',
  },
};

interface BillingActionModalProps {
  action: PendingAction;
  onClose: () => void;
  onSuccess: () => void;
}

const BillingActionModal: React.FC<BillingActionModalProps> = ({ action, onClose, onSuccess }) => {
  const { kind, sub } = action;
  const required = CONFIRM_PHRASES[kind];
  const copy = ACTION_COPY[kind];
  const [reason, setReason] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [chargeId, setChargeId] = useState('');
  const [amountDollars, setAmountDollars] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const phraseOk = confirmText === required;
  const reasonOk = reason.trim().length >= 20;
  const refundChargeOk = kind !== 'refund' || /^ch_[A-Za-z0-9]+$/.test(chargeId.trim());
  const amountValid =
    kind !== 'refund' ||
    amountDollars === '' ||
    (Number.isFinite(Number(amountDollars)) && Number(amountDollars) > 0);
  const ready = phraseOk && reasonOk && refundChargeOk && amountValid && !submitting;

  const submit = async () => {
    setSubmitting(true);
    setErrMsg(null);
    try {
      const url =
        kind === 'cancel'
          ? `/api/admin/billing/customers/${encodeURIComponent(sub.stripe_customer_id)}/cancel-subscription`
          : `/api/admin/billing/customers/${encodeURIComponent(sub.stripe_customer_id)}/refund-charge`;
      const body: Record<string, unknown> = {
        reason: reason.trim(),
        confirm_phrase: required,
      };
      if (kind === 'refund') {
        body.charge_id = chargeId.trim();
        if (amountDollars !== '') {
          body.amount_cents = Math.round(Number(amountDollars) * 100);
        }
      }
      const res = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrfTokenOrEmpty(),
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`HTTP ${res.status}: ${txt.slice(0, 240)}`);
      }
      onSuccess();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : 'request failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-slate-900 rounded-xl border border-white/10 p-6 max-w-lg w-full">
        <h3 className="text-white font-semibold text-base">{copy.title}</h3>
        <p className="text-xs text-white/60 font-mono mt-1">
          {sub.partner_name || sub.site_id || sub.stripe_customer_id}
          {' · '}{sub.stripe_customer_id}
        </p>

        <p className="text-sm text-rose-300 mt-3">⚠ {copy.warning}</p>

        <div className="mt-4 space-y-3">
          {kind === 'refund' && (
            <>
              <div>
                <label className="block text-xs text-white/70 mb-1">
                  Stripe charge ID <span className="text-rose-300">*</span>
                </label>
                <input
                  type="text"
                  value={chargeId}
                  onChange={(e) => setChargeId(e.target.value)}
                  placeholder="ch_..."
                  className="w-full px-3 py-2 rounded bg-black/40 text-white border border-white/10 focus:border-rose-400 focus:outline-none text-sm font-mono"
                />
                <p className="text-[11px] text-white/50 mt-1">
                  Open the customer in Stripe to find the charge ID.
                </p>
              </div>
              <div>
                <label className="block text-xs text-white/70 mb-1">
                  Amount (USD) <span className="text-white/40">— blank = full refund</span>
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={amountDollars}
                  onChange={(e) => setAmountDollars(e.target.value)}
                  placeholder="full refund"
                  className="w-full px-3 py-2 rounded bg-black/40 text-white border border-white/10 focus:border-rose-400 focus:outline-none text-sm"
                />
              </div>
            </>
          )}

          <div>
            <label className="block text-xs text-white/70 mb-1">
              Reason <span className="text-rose-300">*</span>
              <span className="text-white/40"> — ≥20 chars, lands in admin_audit_log</span>
            </label>
            <textarea
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Customer requested via support ticket #1234 due to..."
              className="w-full px-3 py-2 rounded bg-black/40 text-white border border-white/10 focus:border-rose-400 focus:outline-none text-sm"
            />
            <p className={`text-[11px] mt-1 ${reasonOk ? 'text-emerald-300' : 'text-white/50'}`}>
              {reason.trim().length}/20 chars
            </p>
          </div>

          <div>
            <label className="block text-xs text-white/70 mb-1">
              Type <code className="bg-white/10 px-1.5 py-0.5 rounded text-xs font-mono text-amber-300">{required}</code> to confirm:
            </label>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              autoComplete="off"
              className="w-full px-3 py-2 rounded bg-black/40 text-white border border-white/10 focus:border-rose-400 focus:outline-none text-sm font-mono"
            />
          </div>
        </div>

        {errMsg && (
          <div className="mt-3 text-xs text-rose-300 bg-rose-950/40 border border-rose-500/30 rounded p-2">
            {errMsg}
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 rounded text-white/70 hover:text-white text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!ready}
            onClick={submit}
            className={`px-4 py-2 rounded text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed ${copy.tone}`}
          >
            {submitting ? 'Submitting…' : copy.cta}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AdminBilling;
