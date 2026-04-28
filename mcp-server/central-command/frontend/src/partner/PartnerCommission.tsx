import { useEffect, useState } from 'react';
import { usePartner } from './PartnerContext';
import { buildAuthedHeaders } from '../utils/csrf';

interface CommissionData {
  active_clinic_count: number;
  mrr_cents: number;
  ytd_mrr_cents: number;
  effective_rate_bps: number;
  estimated_monthly_commission_cents: number;
  ytd_estimated_commission_cents: number;
  lifetime_paid_cents: number;
  currency: string;
  monthly_breakdown: Array<{ month: string; mrr_cents: number; commission_cents: number }>;
  note: string;
}

function fmtUsd(cents: number): string {
  return (cents / 100).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
}

function fmtRate(bps: number): string {
  return `${(bps / 100).toFixed(1)}%`;
}

export default function PartnerCommission() {
  const { apiKey, isAuthenticated } = usePartner();
  const [data, setData] = useState<CommissionData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    setLoading(true);
    fetch('/api/partners/me/commission', {
      credentials: 'include',
      headers: buildAuthedHeaders({ apiKey }),
    })
      .then((r) => (r.ok ? r.json() : r.json().then((b) => Promise.reject(b))))
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.detail || 'Failed to load commission data');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiKey, isAuthenticated]);

  if (loading) return <div className="p-8 text-slate-500">Loading commission…</div>;
  if (error) return <div className="p-8 text-rose-600">{error}</div>;
  if (!data) return null;

  const maxMrr = Math.max(1, ...data.monthly_breakdown.map((m) => m.mrr_cents));

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Monthly MRR</p>
          <p className="text-3xl font-bold text-slate-900 tabular-nums">{fmtUsd(data.mrr_cents)}</p>
          <p className="text-xs text-slate-500 mt-1">{data.active_clinic_count} active clinic{data.active_clinic_count === 1 ? '' : 's'}</p>
        </div>
        <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Effective Rate</p>
          <p className="text-3xl font-bold text-teal-700 tabular-nums">{fmtRate(data.effective_rate_bps)}</p>
          <p className="text-xs text-slate-500 mt-1">Tier based on clinic count</p>
        </div>
        <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">Est. This Month</p>
          <p className="text-3xl font-bold text-indigo-700 tabular-nums">{fmtUsd(data.estimated_monthly_commission_cents)}</p>
          <p className="text-xs text-slate-500 mt-1">Estimated — pending reconciliation</p>
        </div>
        <div className="bg-white rounded-2xl p-5 border border-slate-200 shadow-sm">
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">YTD Commission</p>
          <p className="text-3xl font-bold text-slate-900 tabular-nums">{fmtUsd(data.ytd_estimated_commission_cents)}</p>
          <p className="text-xs text-slate-500 mt-1">Paid out: {fmtUsd(data.lifetime_paid_cents)}</p>
        </div>
      </div>

      {/* Monthly breakdown */}
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-900 mb-1">Trailing 12-Month MRR</h3>
        <p className="text-xs text-slate-500 mb-5">
          Reconstructed from subscription state. Precise history ships with Stripe Connect activation.
        </p>
        <div className="space-y-2">
          {data.monthly_breakdown.map((m) => (
            <div key={m.month} className="flex items-center gap-3">
              <span className="w-20 text-xs font-mono text-slate-600">{m.month}</span>
              <div className="flex-1 bg-slate-100 rounded h-6 relative overflow-hidden">
                <div
                  className="bg-teal-500 h-full transition-all"
                  style={{ width: `${(m.mrr_cents / maxMrr) * 100}%` }}
                />
              </div>
              <span className="w-24 text-xs text-right tabular-nums text-slate-700">
                {fmtUsd(m.mrr_cents)}
              </span>
              <span className="w-24 text-xs text-right tabular-nums text-indigo-700">
                {fmtUsd(m.commission_cents)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Fine print */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900">
        <p>{data.note}</p>
      </div>
    </div>
  );
}
