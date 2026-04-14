/**
 * PublicStatus — #144 P2: unauthenticated /status/{slug} page.
 *
 * Minimal layout, no sidebar, no auth. Customers share the URL with
 * their compliance team / auditor / staff. Derived from heartbeats so
 * it can't be gamed by site-wide UPDATE regressions.
 *
 * Intentionally boring: no motion, no marketing copy, no logo clutter.
 * It's a status page — tells the truth about whether monitoring is live.
 */

import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

interface PublicAppliance {
  label: string;
  status: 'online' | 'stale' | 'offline';
  last_seen_iso: string | null;
  stale_seconds: number;
  uptime_24h_pct: number | null;
}

interface PublicStatusResponse {
  organization: string;
  status: 'online' | 'stale' | 'offline';
  totals: { online: number; stale: number; offline: number };
  appliances: PublicAppliance[];
  generated_at: string;
  verification_note: string;
}

const STATUS_BG = {
  online: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  stale: 'bg-amber-50 text-amber-700 border-amber-200',
  offline: 'bg-rose-50 text-rose-700 border-rose-200',
} as const;
const STATUS_DOT = {
  online: 'bg-emerald-500',
  stale: 'bg-amber-500',
  offline: 'bg-rose-500',
} as const;
const STATUS_HEADLINE = {
  online: 'All systems operational',
  stale: 'Degraded — monitoring gaps detected',
  offline: 'Major incident — monitoring is offline',
} as const;

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const PublicStatus: React.FC = () => {
  const { slug } = useParams<{ slug: string }>();
  const [data, setData] = useState<PublicStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    const load = () => {
      fetch(`/api/public/status/${slug}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((d: PublicStatusResponse) => { if (!cancelled) { setData(d); setError(null); setLoading(false); } })
        .catch((e) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : String(e));
            setLoading(false);
          }
        });
    };
    load();
    const int = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(int); };
  }, [slug]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 py-10 px-4">
      <div className="max-w-3xl mx-auto">
        {loading && !data && (
          <div className="text-center text-slate-500 py-20">Loading...</div>
        )}

        {error && !data && (
          <div className="bg-white rounded-lg border border-slate-200 p-8 text-center">
            <div className="text-lg font-semibold text-slate-900 mb-2">Status page not found</div>
            <div className="text-sm text-slate-500">
              The page you are looking for does not exist, or the link has been revoked.
            </div>
          </div>
        )}

        {data && (
          <div className="space-y-6">
            {/* Headline */}
            <div>
              <div className="text-sm text-slate-500 uppercase tracking-wide mb-1">
                {data.organization}
              </div>
              <h1 className="text-2xl font-semibold text-slate-900">
                {STATUS_HEADLINE[data.status]}
              </h1>
              <div className="text-sm text-slate-500 mt-1">
                Updated {relTime(data.generated_at)} · refreshes every 60s
              </div>
            </div>

            {/* Top-line status bar */}
            <div className={`rounded-lg border p-4 ${STATUS_BG[data.status]}`}>
              <div className="flex items-center gap-3">
                <span className={`w-3 h-3 rounded-full ${STATUS_DOT[data.status]}`} />
                <span className="font-medium">
                  {data.totals.online} online · {data.totals.stale} stale · {data.totals.offline} offline
                </span>
              </div>
            </div>

            {/* Appliance list */}
            <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-200 bg-slate-50">
                <h2 className="text-sm font-semibold text-slate-900">Monitoring appliances</h2>
              </div>
              {data.appliances.length === 0 ? (
                <div className="p-6 text-center text-sm text-slate-500 italic">
                  No appliances configured.
                </div>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {data.appliances.map((a, i) => (
                    <li key={i} className="px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <span className={`w-2.5 h-2.5 rounded-full ${STATUS_DOT[a.status]}`} />
                        <span className="font-medium text-slate-900">{a.label}</span>
                      </div>
                      <div className="text-right text-xs text-slate-500 tabular-nums">
                        <div>{a.status}</div>
                        <div className="text-[11px]">
                          Last seen {relTime(a.last_seen_iso)}
                          {a.uptime_24h_pct !== null && ` · 24h uptime ${a.uptime_24h_pct}%`}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Verification note */}
            <div className="text-xs text-slate-500 leading-relaxed">
              {data.verification_note}
            </div>

            <div className="text-center text-xs text-slate-400 pt-4">
              Powered by OsirisCare — status derived from cryptographically-attested
              appliance heartbeats. <a href="/" className="text-blue-500 hover:underline">osiriscare.net</a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default PublicStatus;
