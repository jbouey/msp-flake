import React, { useEffect, useState } from 'react';
import { GlassCard, Spinner } from '../components/shared';

/**
 * AdminBilling — owner-operator read-only view of all customer
 * subscriptions. #67 closure 2026-05-02.
 *
 * Adversarial-audit finding: admin had no UI for subscription mgmt;
 * forced to use Stripe dashboard out-of-band (no audit trail).
 *
 * Read-only by design. Destructive ops (refund, cancel customer X)
 * deferred to followup #73 — they require Ed25519-grade audit chain
 * + per-customer access control review. Today: operator clicks
 * "Open in Stripe" deep-link to perform destructive actions in
 * Stripe's own UI (which has its own audit trail).
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

export const AdminBilling: React.FC = () => {
  const [data, setData] = useState<Subscription[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');

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
  }, [statusFilter]);

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
                <th className="text-right py-2 px-3">Action</th>
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
                    <a
                      href={sub.stripe_dashboard_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs px-3 py-1.5 rounded-ios bg-purple-600 text-white hover:bg-purple-700 inline-block"
                    >
                      Open in Stripe →
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </GlassCard>
    </div>
  );
};

export default AdminBilling;
