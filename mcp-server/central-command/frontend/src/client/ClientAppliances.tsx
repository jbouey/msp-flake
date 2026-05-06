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
 *
 * Theme fix 2026-05-06: was using dark-theme tokens (text-white/X,
 * bg-white/5) inside a light-theme dashboard — text was invisible.
 * Converted to design-system label-* and separator-light tokens to
 * match the rest of ClientDashboard.tsx.
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
      <div className="rounded-lg bg-white p-4 mb-4 border border-separator-light">
        <div className="text-sm text-label-secondary">
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
      ? 'text-emerald-700'
      : online === 0
        ? 'text-rose-700'
        : 'text-amber-700';
  return (
    <div className="rounded-lg bg-white p-4 mb-4 border border-separator-light flex items-center justify-between">
      <div>
        <div className={`text-sm font-medium ${summaryTone}`}>
          {online} of {total} appliance{total === 1 ? '' : 's'}{' '}
          {online === total ? 'healthy' : 'online'}
        </div>
        {oldest && (
          <div className="text-xs text-label-tertiary mt-0.5">
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
      <div className="rounded-lg bg-white p-6 text-sm text-label-secondary border border-separator-light">
        Loading appliances…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg bg-rose-50 border border-rose-200 p-4 text-sm text-rose-700">
        {error}
      </div>
    );
  }

  return (
    <div>
      <ApplianceSummaryCard rows={rows} />
      {rows.length > 0 && (
        <div className="rounded-lg bg-white border border-separator-light overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-label-secondary text-xs uppercase tracking-wide">
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
                  className="border-t border-separator-light hover:bg-gray-50"
                >
                  <td className="px-4 py-2.5 text-label-primary">
                    {r.display_name}
                  </td>
                  <td className="px-4 py-2.5 text-label-secondary">
                    {r.site_name || r.site_id}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="px-4 py-2.5 text-label-secondary">
                    {formatTimeAgo(r.last_heartbeat_at || r.last_checkin)}
                  </td>
                  <td className="px-4 py-2.5 text-label-secondary font-mono text-xs">
                    {r.agent_version || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {nextCursor && (
            <div className="border-t border-separator-light px-4 py-2 text-center">
              <button
                onClick={onLoadMore}
                disabled={loadingMore}
                className="text-xs text-label-secondary hover:text-label-primary disabled:opacity-50"
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
