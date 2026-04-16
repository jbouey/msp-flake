import React, { useEffect, useState } from 'react';
import { useClient } from './ClientContext';
import { csrfHeaders } from '../utils/csrf';
import { BrandedLayout } from './BrandedLayout';

interface Subscription {
  id: string;
  status: string;
  plan: string | null;
  current_period_end: number | null;
  cancel_at_period_end: boolean;
  trial_end: number | null;
}

interface BillingStatus {
  customer_id: string | null;
  subscription: Subscription | null;
}

const PLAN_LABELS: Record<string, string> = {
  pilot: '90-Day Pilot',
  essentials: 'Essentials',
  professional: 'Professional',
  enterprise: 'Enterprise',
};

const STATUS_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' }> = {
  active: { label: 'Active', tone: 'ok' },
  trialing: { label: 'Pilot active', tone: 'ok' },
  past_due: { label: 'Payment past due', tone: 'warn' },
  unpaid: { label: 'Unpaid', tone: 'bad' },
  canceled: { label: 'Canceled', tone: 'bad' },
  incomplete: { label: 'Setup incomplete', tone: 'warn' },
  paused: { label: 'Paused', tone: 'info' },
};

function formatTs(secs: number | null): string {
  if (!secs) return '—';
  return new Date(secs * 1000).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  });
}

export const ClientBilling: React.FC = () => {
  const { user } = useClient();
  const [data, setData] = useState<BillingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);

  useEffect(() => {
    document.title = 'Billing · Client Portal';
    fetch('/api/billing/client/status', { credentials: 'include' })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((d) => setData(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'unknown'))
      .finally(() => setLoading(false));
  }, []);

  const openPortal = async () => {
    setError(null);
    setPortalLoading(true);
    try {
      const returnUrl = `${window.location.origin}/client/billing`;
      const res = await fetch('/api/billing/client/portal', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ return_url: returnUrl }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const { portal_url } = await res.json();
      window.location.href = portal_url;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'unknown');
      setPortalLoading(false);
    }
  };

  const sub = data?.subscription ?? null;
  const statusInfo = sub ? STATUS_LABELS[sub.status] ?? { label: sub.status, tone: 'info' as const } : null;
  const toneClass = {
    ok: 'bg-emerald-50 text-emerald-900 border-emerald-200',
    warn: 'bg-amber-50 text-amber-900 border-amber-200',
    bad: 'bg-rose-50 text-rose-900 border-rose-200',
    info: 'bg-slate-50 text-slate-900 border-slate-200',
  };

  return (
    <BrandedLayout branding={null}>
      <main className="flex-1 px-6 py-10 max-w-4xl mx-auto w-full">
        <h1 className="text-2xl font-semibold text-slate-900 mb-2">Billing</h1>
        <p className="text-sm text-slate-600 mb-8">
          Manage your subscription, card on file, and download invoices. All billing is
          handled by Stripe — OsirisCare does not store card details or process PHI through
          billing systems.
        </p>

        {loading && (
          <div className="rounded-xl border border-slate-200 p-8 text-sm text-slate-500">
            Loading billing status…
          </div>
        )}

        {!loading && error && !data && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-900">
            {error}
          </div>
        )}

        {!loading && data && !sub && (
          <div className="rounded-xl border border-slate-200 p-8">
            <h2 className="text-lg font-semibold text-slate-900 mb-2">No subscription on file</h2>
            <p className="text-sm text-slate-600 mb-4">
              We don't see a Stripe customer for <code className="text-teal-700">{user?.email}</code>.
              If you recently signed up, give the payment webhook ~30 seconds and refresh.
              Otherwise, start a pilot from the pricing page.
            </p>
            <a
              href="/pricing"
              className="inline-flex items-center px-4 py-2 rounded-lg text-sm font-semibold text-white"
              style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
            >
              View pricing
            </a>
          </div>
        )}

        {!loading && data && sub && statusInfo && (
          <>
            <div className="rounded-xl border border-slate-200 p-6 mb-6">
              <div className="flex items-start justify-between gap-4 mb-6">
                <div>
                  <div className="text-xs uppercase tracking-wider text-slate-500 mb-1">Plan</div>
                  <div className="text-xl font-semibold text-slate-900">
                    {sub.plan ? (PLAN_LABELS[sub.plan] ?? sub.plan) : 'Unknown plan'}
                  </div>
                </div>
                <span className={`text-xs font-semibold px-3 py-1.5 rounded-full border ${toneClass[statusInfo.tone]}`}>
                  {statusInfo.label}
                </span>
              </div>

              <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                {sub.trial_end && (
                  <div>
                    <dt className="text-xs uppercase tracking-wider text-slate-500 mb-1">Pilot ends</dt>
                    <dd className="text-slate-900 font-semibold">{formatTs(sub.trial_end)}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs uppercase tracking-wider text-slate-500 mb-1">
                    {sub.cancel_at_period_end ? 'Service ends' : 'Next renewal'}
                  </dt>
                  <dd className="text-slate-900 font-semibold">{formatTs(sub.current_period_end)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wider text-slate-500 mb-1">Subscription ID</dt>
                  <dd className="text-slate-600 font-mono text-xs">{sub.id}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wider text-slate-500 mb-1">Stripe customer</dt>
                  <dd className="text-slate-600 font-mono text-xs">{data.customer_id}</dd>
                </div>
              </dl>

              {sub.cancel_at_period_end && (
                <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  Your subscription is set to cancel on {formatTs(sub.current_period_end)}.
                  You can reactivate any time via the billing portal below.
                </div>
              )}
              {sub.status === 'past_due' && (
                <div className="mt-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
                  Last payment attempt failed. Evidence collection continues during the 30-day
                  grace window — update your card soon to avoid a downgrade.
                </div>
              )}
            </div>

            <div className="rounded-xl border border-slate-200 p-6 mb-6">
              <h2 className="text-base font-semibold text-slate-900 mb-2">Manage billing</h2>
              <p className="text-sm text-slate-600 mb-4">
                The Stripe-hosted billing portal lets you update your card on file, download
                invoices, cancel or reactivate your subscription, and view payment history.
              </p>
              <button
                onClick={openPortal}
                disabled={portalLoading}
                className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white transition-all disabled:opacity-60"
                style={{ background: 'linear-gradient(135deg, #14A89E 0%, #0d9488 100%)' }}
              >
                {portalLoading ? 'Opening Stripe…' : 'Open billing portal →'}
              </button>
            </div>

            <div className="rounded-xl border border-slate-200 p-6 text-sm text-slate-600">
              <h3 className="text-sm font-semibold text-slate-900 mb-2">Walk-away rights</h3>
              <p className="leading-relaxed">
                If you cancel, every signed evidence bundle generated for your practice remains
                downloadable via your auditor kit at any time. Your HIPAA audit trail is yours to
                keep — we don't hold it hostage to continued subscription.
              </p>
            </div>
          </>
        )}
      </main>
    </BrandedLayout>
  );
};

export default ClientBilling;
