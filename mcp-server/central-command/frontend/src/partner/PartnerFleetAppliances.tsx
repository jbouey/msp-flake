/**
 * PartnerFleetAppliances — fleet-wide appliance roll-up across all sites.
 *
 * Round-table 33 (2026-05-05) close of "200 sites in the book, can't
 * answer 'which appliances are offline' without clicking each one"
 * gap. Linda P0 ask. Read-only — operator-class actions live on
 * central command.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { getJson } from '../utils/portalFetch';
import { StatusBadge } from '../components/composed';
import { formatTimeAgo, POLL_INTERVAL_PARTNER_MS } from '../constants';

interface ApplianceRow {
  appliance_id: string;
  site_id: string;
  site_name: string | null;
  display_name: string;
  mac_address: string | null;
  agent_version: string | null;
  l2_mode: string | null;
  status: string;
  last_heartbeat_at: string | null;
  last_checkin: string | null;
}

interface FleetSummary {
  total: number;
  online: number;
  offline: number;
}

interface FleetResponse {
  appliances: ApplianceRow[];
  summary: FleetSummary;
  next_cursor: string | null;
  limit: number;
}

const POLL_INTERVAL_MS = POLL_INTERVAL_PARTNER_MS;
type StatusFilter = '' | 'online' | 'stale' | 'offline';

export const PartnerFleetAppliances: React.FC = () => {
  const [rows, setRows] = useState<ApplianceRow[]>([]);
  const [summary, setSummary] = useState<FleetSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');

  const fetchPage = useCallback(
    async (cursor: string | null, append: boolean, sf: StatusFilter) => {
      try {
        const params = new URLSearchParams();
        if (cursor) params.set('cursor', cursor);
        if (sf) params.set('status_filter', sf);
        const url = `/api/partners/me/appliances${params.toString() ? `?${params}` : ''}`;
        const data = await getJson<FleetResponse>(url);
        if (!data) {
          setRows([]);
          setNextCursor(null);
          return;
        }
        setRows((prev) =>
          append ? [...prev, ...data.appliances] : data.appliances
        );
        setNextCursor(data.next_cursor);
        setSummary(data.summary);
        setError(null);
      } catch (e) {
        setError(
          (e as Error)?.message ||
            'Could not load fleet appliances. Try refreshing.'
        );
      }
    },
    []
  );

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      await fetchPage(null, false, statusFilter);
      if (!cancelled) {
        setIsLoading(false);
        timer = window.setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [fetchPage, statusFilter]);

  const onLoadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    await fetchPage(nextCursor, true, statusFilter);
    setLoadingMore(false);
  };

  if (isLoading) {
    return (
      <div className="rounded-lg bg-white/5 backdrop-blur p-6 text-sm text-white/60">
        Loading fleet…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-4 text-sm text-rose-200">
        {error}
      </div>
    );
  }

  const filterButton = (val: StatusFilter, label: string) => (
    <button
      key={val}
      onClick={() => setStatusFilter(val)}
      className={`px-3 py-1 rounded text-xs font-medium ${
        statusFilter === val
          ? 'bg-accent-primary text-white'
          : 'bg-white/5 text-white/70 hover:bg-white/10'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      {summary && (
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-white/5 backdrop-blur border border-white/10 p-4">
            <div className="text-xs text-white/60 uppercase tracking-wide">
              Total
            </div>
            <div className="text-2xl font-semibold text-white mt-1">
              {summary.total}
            </div>
          </div>
          <div className="rounded-lg bg-emerald-500/10 backdrop-blur border border-emerald-500/30 p-4">
            <div className="text-xs text-emerald-300 uppercase tracking-wide">
              Online
            </div>
            <div className="text-2xl font-semibold text-emerald-200 mt-1">
              {summary.online}
            </div>
          </div>
          <div className="rounded-lg bg-rose-500/10 backdrop-blur border border-rose-500/30 p-4">
            <div className="text-xs text-rose-300 uppercase tracking-wide">
              Offline
            </div>
            <div className="text-2xl font-semibold text-rose-200 mt-1">
              {summary.offline}
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="text-xs text-white/60 mr-1">Filter:</span>
        {filterButton('', 'All')}
        {filterButton('online', 'Online')}
        {filterButton('stale', 'Stale')}
        {filterButton('offline', 'Offline')}
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg bg-white/5 backdrop-blur p-6 text-sm text-white/60">
          No appliances match the current filter.
        </div>
      ) : (
        <div className="rounded-lg bg-white/5 backdrop-blur border border-white/10 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-white/70 text-xs uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Appliance</th>
                <th className="text-left px-4 py-2 font-medium">Site</th>
                <th className="text-left px-4 py-2 font-medium">Status</th>
                <th className="text-left px-4 py-2 font-medium">Last seen</th>
                <th className="text-left px-4 py-2 font-medium">Version</th>
                <th className="text-left px-4 py-2 font-medium">L2 mode</th>
                <th className="text-left px-4 py-2 font-medium">MAC</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.appliance_id}
                  className="border-t border-white/5 hover:bg-white/5"
                >
                  <td className="px-4 py-2.5 text-white/90">
                    <Link
                      to={`/partner/site/${r.site_id}/topology`}
                      className="hover:text-accent-primary"
                    >
                      {r.display_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5 text-white/70">
                    {r.site_name || r.site_id}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="px-4 py-2.5 text-white/70">
                    {formatTimeAgo(r.last_heartbeat_at || r.last_checkin)}
                  </td>
                  <td className="px-4 py-2.5 text-white/70 font-mono text-xs">
                    {r.agent_version || '—'}
                  </td>
                  <td className="px-4 py-2.5 text-white/70 text-xs">
                    {r.l2_mode || '—'}
                  </td>
                  <td className="px-4 py-2.5 text-white/60 font-mono text-xs">
                    {r.mac_address || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {nextCursor && (
            <div className="border-t border-white/5 px-4 py-2 text-center">
              <button
                onClick={onLoadMore}
                disabled={loadingMore}
                className="text-xs text-white/70 hover:text-white disabled:opacity-50"
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default PartnerFleetAppliances;
