/**
 * PartnerHomeDashboard — Session 206 partner portal round-table P0.
 *
 * Psychology: the partner (MSP technician, 15×/day logins) wants
 *   - attention-ordered list of clients who need action this week
 *   - activity feed of what happened across their book in 24h
 *   - book-of-business self-heal trend
 *
 * Dense, technical, fast. Unlike the client portal's calmed-down
 * "you are protected" card, this is a terminal-style triage view.
 *
 * Data: GET /api/partners/me/dashboard (server-side aggregation,
 * filtered by partner_id — see test_partner_dashboard_isolation).
 */

import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

interface AttentionItem {
  site_id: string;
  clinic_name: string;
  risk_score: number;
  chronic_patterns: number;
  open_l3: number;
  ack_pending: number;
  offline_appliances: number;
}

interface ActivityEvent {
  when: string;
  site_id: string;
  clinic_name: string;
  incident_type: string;
  severity: string;
  resolution_tier: string | null;
  status: string;
}

interface DashboardData {
  attention_list: AttentionItem[];
  activity_24h: ActivityEvent[];
  book_of_business: {
    total_clients: number;
    clients_online_now: number;
    incidents_24h: number;
    l1_24h: number;
    l2_24h: number;
    l3_24h: number;
    self_heal_24h_pct: number | null;
    active_alerts: number;
  };
  trend_7d: Array<{ date: string; total: number; l1: number; pct: number | null }>;
  generated_at: string;
}

function relTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: 'text-red-400',
  high: 'text-orange-400',
  medium: 'text-amber-400',
  low: 'text-slate-400',
};

export const PartnerHomeDashboard: React.FC = () => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    fetch('/api/partners/me/dashboard', { credentials: 'include' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: DashboardData) => {
        if (!cancelled) { setData(d); setLoading(false); }
      })
      .catch((e) => {
        if (!cancelled) { setError(String(e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-1/3 bg-slate-200 rounded" />
          <div className="h-32 bg-slate-200 rounded" />
          <div className="h-64 bg-slate-200 rounded" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">
          Partner dashboard failed to load: {error ?? 'no data'}
        </div>
      </div>
    );
  }

  const bob = data.book_of_business;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Partner Dashboard</h1>
          <p className="text-sm text-slate-500">
            {bob.total_clients} client{bob.total_clients === 1 ? '' : 's'} · {bob.clients_online_now} online now · updated {relTime(data.generated_at)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`px-3 py-1.5 rounded-full text-sm font-medium ${
            bob.active_alerts > 0 ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'
          }`}>
            {bob.active_alerts > 0 ? `🔔 ${bob.active_alerts} need attention` : '✓ All clear'}
          </span>
        </div>
      </div>

      {/* Attention list */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">
            Clients needing your attention this week
          </h2>
          <span className="text-xs text-slate-500">
            ranked by risk score (chronic × 3 + open L3 × 5 + ack × 2 + offline × 4)
          </span>
        </div>
        {data.attention_list.length === 0 ? (
          <div className="px-5 py-8 text-center text-slate-500 text-sm">
            ✓ All {bob.total_clients} clients clear. No action needed.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2 text-left">Practice</th>
                <th className="px-2 py-2 text-right">Risk</th>
                <th className="px-2 py-2 text-right">Chronic</th>
                <th className="px-2 py-2 text-right">Open L3</th>
                <th className="px-2 py-2 text-right">Ack</th>
                <th className="px-2 py-2 text-right">Offline</th>
                <th className="px-5 py-2 text-right">Open</th>
              </tr>
            </thead>
            <tbody>
              {data.attention_list.map((a) => (
                <tr key={a.site_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-5 py-2 font-medium text-slate-900">{a.clinic_name || a.site_id}</td>
                  <td className="px-2 py-2 text-right font-semibold text-red-600 tabular-nums">{a.risk_score}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-slate-700">{a.chronic_patterns || '—'}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-slate-700">{a.open_l3 || '—'}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-slate-700">{a.ack_pending || '—'}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-slate-700">{a.offline_appliances || '—'}</td>
                  <td className="px-5 py-2 text-right">
                    <button
                      className="text-blue-600 hover:text-blue-800 hover:underline text-sm"
                      onClick={() => navigate(`/partner/site/${a.site_id}`)}
                    >
                      Open →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Two-col: activity feed + book-of-business rollup */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200 shadow-sm">
          <div className="px-5 py-3 border-b border-slate-100">
            <h2 className="text-sm font-semibold text-slate-900">Last 24 hours · activity across your clients</h2>
          </div>
          {data.activity_24h.length === 0 ? (
            <div className="px-5 py-6 text-center text-slate-500 text-sm">No events in the last 24h.</div>
          ) : (
            <div className="divide-y divide-slate-100 max-h-96 overflow-y-auto">
              {data.activity_24h.map((ev, i) => (
                <div key={i} className="px-5 py-2 flex items-center justify-between text-sm">
                  <div className="flex-1 min-w-0">
                    <span className="text-slate-500 tabular-nums text-xs mr-2">{relTime(ev.when)}</span>
                    <span className="font-medium text-slate-900">{ev.clinic_name || ev.site_id}</span>
                    <span className="text-slate-600 mx-2">·</span>
                    <span className={SEVERITY_COLOR[ev.severity] || 'text-slate-700'}>
                      {ev.incident_type}
                    </span>
                  </div>
                  <div className="ml-3 text-xs">
                    {ev.resolution_tier === 'L1' && <span className="text-emerald-600">→ L1 auto</span>}
                    {ev.resolution_tier === 'L2' && <span className="text-blue-600">→ L2</span>}
                    {ev.resolution_tier === 'L3' && <span className="text-red-600">→ L3 escalated</span>}
                    {!ev.resolution_tier && <span className="text-slate-400">pending</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-3">
          <h2 className="text-sm font-semibold text-slate-900">Book-of-business · 24h</h2>
          <div>
            <div className="text-3xl font-bold text-slate-900 tabular-nums">
              {bob.self_heal_24h_pct !== null && bob.self_heal_24h_pct !== undefined
                ? `${bob.self_heal_24h_pct.toFixed(1)}%`
                : '—'}
            </div>
            <div className="text-xs text-slate-500">
              {bob.self_heal_24h_pct !== null && bob.self_heal_24h_pct !== undefined
                ? 'Self-heal rate · target ≥95%'
                : 'No incidents detected in the last 24h'}
            </div>
          </div>
          <div className="text-xs text-slate-600 space-y-1 tabular-nums">
            <div className="flex justify-between">
              <span>Drifts detected</span>
              <span>{bob.incidents_24h}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-emerald-600">L1 auto-healed</span>
              <span>{bob.l1_24h}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-blue-600">L2 escalated</span>
              <span>{bob.l2_24h}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-red-600">L3 required you</span>
              <span>{bob.l3_24h}</span>
            </div>
          </div>
          {data.trend_7d.length > 0 && (
            <div>
              <div className="text-xs text-slate-500 mb-1">7-day trend</div>
              <div className="flex gap-0.5 h-8">
                {data.trend_7d.map((d) => {
                  const pct = d.pct;
                  const height = pct === null || pct === undefined ? 5 : Math.max(5, pct);
                  const color =
                    pct === null || pct === undefined ? 'bg-slate-200'
                    : pct >= 95 ? 'bg-emerald-400'
                    : pct >= 85 ? 'bg-amber-400'
                    : 'bg-red-400';
                  const title =
                    pct === null || pct === undefined
                      ? `${d.date}: no incidents`
                      : `${d.date}: ${pct.toFixed(1)}% (${d.l1}/${d.total})`;
                  return (
                    <div key={d.date} className="flex-1 flex items-end" title={title}>
                      <div className={color} style={{ height: `${height}%`, width: '100%', borderRadius: 2 }} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PartnerHomeDashboard;
