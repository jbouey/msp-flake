/**
 * ClientAppliances — substrate-class appliance roll-up for the client portal.
 *
 * Round-table 33 (2026-05-05) close of "no appliance representation" gap.
 * Customer-facing list of the appliances doing compliance attestation on
 * their behalf. Read-only by design — operator-class actions (l2-mode,
 * fleet orders, clear-stale) live on central command, not here.
 *
 * Field set is deliberately narrow (Carol veto, RT33): no MAC, no IP,
 * no mesh-topology fields. A compromised customer session must not
 * become a fleet recon map.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { getJson } from '../utils/portalFetch';
import { StatusBadge } from '../components/composed';
import { formatTimeAgo, POLL_INTERVAL_CLIENT_MS } from '../constants';

interface ApplianceRow {
  appliance_id: string;
  site_id: string;
  site_name: string | null;
  display_name: string;
  status: string;
  last_heartbeat_at: string | null;
  last_checkin: string | null;
  agent_version: string | null;
}

interface AppliancesResponse {
  appliances: ApplianceRow[];
  next_cursor: string | null;
  limit: number;
}

const POLL_INTERVAL_MS = POLL_INTERVAL_CLIENT_MS;

interface SummaryProps {
  rows: ApplianceRow[];
}

const ApplianceSummaryCard: React.FC<SummaryProps> = ({ rows }) => {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg bg-white/5 backdrop-blur p-4 mb-4 border border-white/10">
        <div className="text-sm text-white/70">
          No appliances connected yet. Reach out to your MSP to schedule
          deployment.
        </div>
      </div>
    );
  }
  const online = rows.filter((r) => r.status === 'online').length;
  const total = rows.length;
  const oldest = rows
    .map((r) => r.last_heartbeat_at)
    .filter((t): t is string => !!t)
    .sort()[0];
  const summaryTone =
    online === total
      ? 'text-emerald-300'
      : online === 0
        ? 'text-rose-300'
        : 'text-amber-300';
  return (
    <div className="rounded-lg bg-white/5 backdrop-blur p-4 mb-4 border border-white/10 flex items-center justify-between">
      <div>
        <div className={`text-sm font-medium ${summaryTone}`}>
          {online} of {total} appliance{total === 1 ? '' : 's'}{' '}
          {online === total ? 'healthy' : 'online'}
        </div>
        {oldest && (
          <div className="text-xs text-white/60 mt-0.5">
            Oldest checkin: {formatTimeAgo(oldest)}
          </div>
        )}
      </div>
    </div>
  );
};

export const ClientAppliances: React.FC = () => {
  const [rows, setRows] = useState<ApplianceRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchPage = useCallback(
    async (cursor: string | null, append: boolean) => {
      try {
        const url = `/api/client/appliances${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`;
        const data = await getJson<AppliancesResponse>(url);
        if (!data) {
          setRows([]);
          setNextCursor(null);
          return;
        }
        setRows((prev) =>
          append ? [...prev, ...data.appliances] : data.appliances
        );
        setNextCursor(data.next_cursor);
        setError(null);
      } catch (e) {
        const msg =
          (e as Error)?.message ||
          'Could not load appliances. Try refreshing the page.';
        setError(msg);
      }
    },
    []
  );

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      await fetchPage(null, false);
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
  }, [fetchPage]);

  const onLoadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    await fetchPage(nextCursor, true);
    setLoadingMore(false);
  };

  if (isLoading) {
    return (
      <div className="rounded-lg bg-white/5 backdrop-blur p-6 text-sm text-white/60">
        Loading appliances…
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

  return (
    <div>
      <ApplianceSummaryCard rows={rows} />
      {rows.length > 0 && (
        <div className="rounded-lg bg-white/5 backdrop-blur border border-white/10 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-white/70 text-xs uppercase tracking-wide">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Appliance</th>
                <th className="text-left px-4 py-2 font-medium">Site</th>
                <th className="text-left px-4 py-2 font-medium">Status</th>
                <th className="text-left px-4 py-2 font-medium">Last seen</th>
                <th className="text-left px-4 py-2 font-medium">Version</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.appliance_id}
                  className="border-t border-white/5 hover:bg-white/5"
                >
                  <td className="px-4 py-2.5 text-white/90">
                    {r.display_name}
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

export default ClientAppliances;
