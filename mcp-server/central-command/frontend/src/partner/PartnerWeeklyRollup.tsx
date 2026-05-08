/**
 * PartnerWeeklyRollup — Session 206 round-table P2.
 *
 * Reads the materialized view (migration 185, refreshed every 30 min
 * by weekly_rollup_refresh_loop) via /api/partners/me/rollup/weekly.
 *
 * Dense per-client 7-day table ordered worst→best self-heal%. Meant
 * to sit below PartnerHomeDashboard so a partner can scan their
 * entire book in one screen without clicking into each site.
 *
 * `stale=true` in the API response means the view doesn't exist yet
 * (pre-migration); we just don't render instead of showing a noisy
 * error.
 */

import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

interface RollupSite {
  site_id: string;
  clinic_name: string;
  incidents_7d: number;
  l1_7d: number;
  l2_7d: number;
  l3_7d: number;
  incidents_24h: number;
  l1_24h: number;
  self_heal_rate_7d_pct: number | null;
}

interface RollupResponse {
  sites: RollupSite[];
  computed_at: string | null;
  total_sites: number;
  stale: boolean;
}

function formatAge(iso: string | null): string {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export const PartnerWeeklyRollup: React.FC = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<RollupResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // MAJ-1 fix (audit 2026-05-08): QBR is a rate-limited PDF
  // download (Session 217 RT31 rule — auditor-kit + similar must
  // use JS fetch->blob, not <a href>, so customers see actionable
  // 401/429 copy). qbrBusy gates double-click; qbrError surfaces
  // status to the user.
  const [qbrBusy, setQbrBusy] = useState<string | null>(null);
  const [qbrError, setQbrError] = useState<string | null>(null);

  const handleQbrDownload = async (siteId: string, clinicName: string) => {
    setQbrBusy(siteId);
    setQbrError(null);
    try {
      const res = await fetch(`/api/partners/me/sites/${siteId}/qbr`, {
        credentials: 'include',
      });
      if (res.status === 401) {
        setQbrError('Session expired. Please sign in again to download QBR.');
        return;
      }
      if (res.status === 429) {
        const retryAfter = res.headers.get('Retry-After') || '60';
        setQbrError(`Rate limit reached. Retry in ${retryAfter}s.`);
        return;
      }
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        setQbrError(`QBR download failed (${res.status}). ${detail.slice(0, 120)}`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `qbr-${clinicName.replace(/[^a-zA-Z0-9_-]+/g, '-').toLowerCase()}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setQbrError(e instanceof Error ? e.message : 'QBR download failed');
    } finally {
      setQbrBusy(null);
    }
  };

  useEffect(() => {
    let cancelled = false;
    fetch('/api/partners/me/rollup/weekly', { credentials: 'include' })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: RollupResponse) => {
        if (!cancelled) { setData(d); setLoading(false); }
      })
      .catch((e) => {
        if (!cancelled) { setError(String(e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto px-6 pb-6">
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
          <div className="animate-pulse space-y-2">
            <div className="h-4 w-48 bg-slate-200 rounded" />
            <div className="h-32 bg-slate-100 rounded" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !data || data.stale || data.sites.length === 0) {
    return null;
  }

  return (
    <div className="max-w-7xl mx-auto px-6 pb-6">
      {qbrError && (
        <div
          className="mb-3 p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700 flex items-center justify-between"
          role="alert"
        >
          <span>{qbrError}</span>
          <button
            onClick={() => setQbrError(null)}
            className="text-rose-700 hover:text-rose-900 text-xs underline"
            aria-label="Dismiss"
          >
            Dismiss
          </button>
        </div>
      )}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">
              Weekly rollup — all clients (7d)
            </h2>
            <p className="text-[11px] text-slate-500">
              Worst self-heal first · refreshed every 30 min ·
              rollup age: {formatAge(data.computed_at)} · {data.total_sites} clients
            </p>
          </div>
          <span className="text-[11px] text-slate-400">
            Precomputed from partner_site_weekly_rollup
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2 text-left">Practice</th>
                <th className="px-2 py-2 text-right">Self-heal 7d</th>
                <th className="px-2 py-2 text-right">Incidents 7d</th>
                <th className="px-2 py-2 text-right">L1</th>
                <th className="px-2 py-2 text-right">L2</th>
                <th className="px-2 py-2 text-right">L3</th>
                <th className="px-2 py-2 text-right">24h</th>
                <th className="px-5 py-2 text-right">Open</th>
              </tr>
            </thead>
            <tbody>
              {data.sites.slice(0, 25).map((s) => {
                const pct = s.self_heal_rate_7d_pct;
                const hasPct = pct !== null && pct !== undefined;
                const tone = !hasPct
                  ? 'text-slate-400'
                  : pct >= 95 ? 'text-emerald-600'
                  : pct >= 85 ? 'text-amber-600'
                  : 'text-rose-600';
                return (
                  <tr key={s.site_id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-5 py-2 font-medium text-slate-900">
                      {s.clinic_name || s.site_id}
                    </td>
                    <td className={`px-2 py-2 text-right tabular-nums font-semibold ${tone}`}>
                      {hasPct ? `${pct.toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-700">
                      {s.incidents_7d}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-emerald-600">
                      {s.l1_7d || '—'}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-blue-600">
                      {s.l2_7d || '—'}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-rose-600">
                      {s.l3_7d || '—'}
                    </td>
                    <td className="px-2 py-2 text-right tabular-nums text-slate-700">
                      {s.incidents_24h > 0
                        ? `${s.l1_24h}/${s.incidents_24h}`
                        : '—'}
                    </td>
                    <td className="px-5 py-2 text-right whitespace-nowrap">
                      <button
                        onClick={() => void handleQbrDownload(s.site_id, s.clinic_name || s.site_id)}
                        disabled={qbrBusy === s.site_id}
                        className="text-slate-500 hover:text-slate-800 hover:underline text-xs mr-3 disabled:opacity-50 disabled:cursor-not-allowed"
                        title="Download quarterly business review PDF"
                      >
                        {qbrBusy === s.site_id ? 'QBR…' : 'QBR'}
                      </button>
                      <button
                        onClick={() => navigate(`/partner/site/${s.site_id}/topology`)}
                        className="text-slate-500 hover:text-slate-800 hover:underline text-xs mr-3"
                        title="View mesh topology map"
                      >
                        Mesh
                      </button>
                      <button
                        onClick={() => navigate(`/partner/site/${s.site_id}/consent`)}
                        className="text-slate-500 hover:text-slate-800 hover:underline text-xs mr-3"
                        title="Manage class-level consent"
                      >
                        Consent
                      </button>
                      <button
                        className="text-blue-600 hover:text-blue-800 hover:underline text-sm"
                        // CRIT-2 fix (audit 2026-05-08): /partner/site/:siteId
                        // route does not exist; deflect to dashboard with
                        // a query param so the partner lands on a real page.
                        onClick={() => navigate(`/partner/dashboard?site=${encodeURIComponent(s.site_id)}`)}
                      >
                        Open →
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data.sites.length > 25 && (
          <div className="px-5 py-2 border-t border-slate-100 text-[11px] text-slate-500">
            Showing worst 25 of {data.sites.length}. Cmd/Ctrl+K to search.
          </div>
        )}
      </div>
    </div>
  );
};

export default PartnerWeeklyRollup;
