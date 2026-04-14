/**
 * Fleet — #142 P2: fleet-table-first dashboard redesign.
 *
 * Replaces the per-appliance card layout on SiteDetail as the default
 * view at fleet scale. Reads from /api/appliances/status-rollup which is
 * populated by the appliance_status_rollup MV (Migration 191 + 193) —
 * live_status derived from heartbeats, not the lying last_checkin column.
 *
 * Scaling target: O(fleet) rows rendered directly. Client-side filter +
 * sort. At 1000+ appliances, future work adds virtualization, but the
 * rollup MV already handles the server-side cost: narrow columns,
 * single query, 60s cache. Polling at 30s keeps the page fresh without
 * overwhelming the DB.
 *
 * Deliberately NO motion: "Last heartbeat: Xs ago" static labels,
 * color dots, no tumblers. The user complaint was "that moves" — this
 * page is the positive example.
 */

import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { GlassCard, Spinner } from '../components/shared';
import { useApplianceRollup } from '../hooks';
import { formatTimeAgo } from '../constants';
import type { ApplianceRollupRow } from '../utils/api';

const STATUS_TONE: Record<string, string> = {
  online: 'text-emerald-400',
  stale: 'text-amber-400',
  offline: 'text-rose-400',
};
const STATUS_DOT: Record<string, string> = {
  online: 'bg-emerald-500',
  stale: 'bg-amber-500',
  offline: 'bg-rose-500',
};

type SortCol = 'live_status' | 'site_id' | 'display_name' | 'last_heartbeat_at' | 'uptime_ratio_24h';
type SortDir = 'asc' | 'desc';

const STATUS_ORDER: Record<string, number> = {
  offline: 0,
  stale: 1,
  online: 2,
};

function compareRows(a: ApplianceRollupRow, b: ApplianceRollupRow, col: SortCol, dir: SortDir): number {
  let va: number | string = '';
  let vb: number | string = '';
  switch (col) {
    case 'live_status':
      va = STATUS_ORDER[a.live_status] ?? 3;
      vb = STATUS_ORDER[b.live_status] ?? 3;
      break;
    case 'site_id':
      va = a.site_id;
      vb = b.site_id;
      break;
    case 'display_name':
      va = (a.display_name || a.hostname || a.appliance_id).toLowerCase();
      vb = (b.display_name || b.hostname || b.appliance_id).toLowerCase();
      break;
    case 'last_heartbeat_at':
      va = a.last_heartbeat_at || a.last_checkin || '';
      vb = b.last_heartbeat_at || b.last_checkin || '';
      break;
    case 'uptime_ratio_24h':
      va = a.uptime_ratio_24h;
      vb = b.uptime_ratio_24h;
      break;
  }
  if (va < vb) return dir === 'asc' ? -1 : 1;
  if (va > vb) return dir === 'asc' ? 1 : -1;
  return 0;
}

const Fleet: React.FC = () => {
  const [filterStatus, setFilterStatus] = useState<'all' | 'online' | 'stale' | 'offline'>('all');
  const [filterText, setFilterText] = useState('');
  const [sortCol, setSortCol] = useState<SortCol>('live_status');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const { data, isLoading, error } = useApplianceRollup();

  const filteredSorted = useMemo(() => {
    if (!data) return [];
    const q = filterText.trim().toLowerCase();
    return data.appliances
      .filter((a) => filterStatus === 'all' || a.live_status === filterStatus)
      .filter((a) => {
        if (!q) return true;
        const hay = [
          a.appliance_id, a.hostname, a.display_name, a.mac_address,
          a.site_id, ...(a.ip_addresses || []),
        ].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      })
      .slice()
      .sort((a, b) => compareRows(a, b, sortCol, sortDir));
  }, [data, filterStatus, filterText, sortCol, sortDir]);

  const toggleSort = (col: SortCol) => {
    if (col === sortCol) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir(col === 'last_heartbeat_at' || col === 'uptime_ratio_24h' ? 'desc' : 'asc');
    }
  };

  if (isLoading && !data) {
    return <div className="p-6"><Spinner /></div>;
  }
  if (error && !data) {
    return (
      <div className="p-6 text-rose-400 text-sm">
        Failed to load fleet: {error instanceof Error ? error.message : String(error)}
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4 page-enter">
      {/* Header + KPI strip */}
      <div>
        <h1 className="text-xl font-semibold text-label-primary">Fleet status</h1>
        <p className="text-[11px] text-label-tertiary mt-1">
          All appliances across all sites. Live status derived from
          heartbeats (Migration 193). Refreshes every 30s.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {(['online', 'stale', 'offline'] as const).map((s) => (
          <GlassCard key={s} className="p-4">
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2 h-2 rounded-full ${STATUS_DOT[s]}`} />
              <span className="text-[11px] uppercase tracking-wide text-label-tertiary">{s}</span>
            </div>
            <div className={`mt-1 text-3xl font-bold tabular-nums ${STATUS_TONE[s]}`}>
              {data.totals[s]}
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Filter bar */}
      <GlassCard className="p-3">
        <div className="flex flex-wrap gap-3 items-center">
          <div className="inline-flex rounded-lg bg-fill-secondary p-0.5">
            {(['all', 'online', 'stale', 'offline'] as const).map((s) => (
              <button
                key={s}
                onClick={() => setFilterStatus(s)}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                  filterStatus === s
                    ? 'bg-accent-primary text-white shadow-sm'
                    : 'text-label-tertiary hover:text-label-secondary'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <input
            type="text"
            placeholder="Filter by hostname / MAC / IP / site..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="flex-1 min-w-[200px] px-3 py-1.5 text-sm rounded-md bg-fill-primary border border-separator-light text-label-primary placeholder-label-tertiary"
          />
          <span className="text-xs text-label-tertiary tabular-nums">
            {filteredSorted.length}/{data.count} appliances
          </span>
        </div>
      </GlassCard>

      {/* Fleet table */}
      <GlassCard className="p-0 overflow-hidden">
        {filteredSorted.length === 0 ? (
          <div className="p-8 text-center text-label-tertiary italic">
            No appliances match.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wide text-label-tertiary border-b border-glass-border bg-fill-secondary/40">
              <tr>
                <ThSort col="display_name" sortCol={sortCol} sortDir={sortDir} onClick={toggleSort}>Appliance</ThSort>
                <ThSort col="site_id" sortCol={sortCol} sortDir={sortDir} onClick={toggleSort}>Site</ThSort>
                <ThSort col="live_status" sortCol={sortCol} sortDir={sortDir} onClick={toggleSort}>Status</ThSort>
                <ThSort col="last_heartbeat_at" sortCol={sortCol} sortDir={sortDir} onClick={toggleSort} align="right">Last heartbeat</ThSort>
                <ThSort col="uptime_ratio_24h" sortCol={sortCol} sortDir={sortDir} onClick={toggleSort} align="right">24h uptime</ThSort>
              </tr>
            </thead>
            <tbody className="divide-y divide-glass-border/40">
              {filteredSorted.map((a) => {
                const hasDrift = Math.abs(a.liveness_drift_seconds || 0) > 60;
                return (
                  <tr key={a.appliance_id} className="hover:bg-fill-secondary/20">
                    <td className="py-2 px-3 text-label-primary">
                      <div className="flex items-center gap-2">
                        <div className="font-medium">
                          {a.display_name || a.hostname || a.appliance_id}
                        </div>
                        {hasDrift && (
                          <span
                            className="px-1.5 py-0.5 text-[10px] font-semibold rounded bg-rose-500/15 text-rose-400 border border-rose-500/30"
                            title={`last_checkin disagrees with heartbeats by ${a.liveness_drift_seconds}s`}
                          >
                            drift
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] font-mono text-label-tertiary">
                        {a.mac_address || '-'}
                      </div>
                    </td>
                    <td className="py-2 px-3 text-label-secondary">
                      <Link
                        to={`/sites/${a.site_id}`}
                        className="hover:underline text-blue-400 text-xs"
                      >
                        {a.site_id}
                      </Link>
                    </td>
                    <td className="py-2 px-3">
                      <span className={`inline-flex items-center gap-1.5 text-xs ${STATUS_TONE[a.live_status]}`}>
                        <span className={`w-2 h-2 rounded-full ${STATUS_DOT[a.live_status]}`} />
                        {a.live_status}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right tabular-nums text-label-secondary" title="Ground truth from appliance_heartbeats">
                      {formatTimeAgo(a.last_heartbeat_at || a.last_checkin)}
                    </td>
                    <td className="py-2 px-3 text-right tabular-nums text-label-secondary">
                      {a.checkin_count_24h > 0
                        ? `${(a.uptime_ratio_24h * 100).toFixed(0)}%`
                        : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </GlassCard>

      <div className="text-[11px] text-label-tertiary text-center">
        Data from appliance_status_rollup · live_status derived from heartbeats ·
        refreshed {formatTimeAgo(data.generated_at)}
      </div>
    </div>
  );
};

interface ThSortProps {
  col: SortCol;
  sortCol: SortCol;
  sortDir: SortDir;
  onClick: (col: SortCol) => void;
  align?: 'left' | 'right';
  children: React.ReactNode;
}
const ThSort: React.FC<ThSortProps> = ({ col, sortCol, sortDir, onClick, align = 'left', children }) => {
  const active = col === sortCol;
  return (
    <th
      className={`py-2 px-3 font-medium cursor-pointer select-none hover:text-label-primary ${align === 'right' ? 'text-right' : 'text-left'}`}
      onClick={() => onClick(col)}
    >
      {children}
      {active && <span className="ml-1 text-accent-primary">{sortDir === 'asc' ? '↑' : '↓'}</span>}
    </th>
  );
};

export default Fleet;
