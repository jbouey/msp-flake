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

/**
 * BulkActionToolbar — appears when ≥1 appliance is checkbox-selected.
 *
 * #66 closure 2026-05-02. Adversarial admin-tool review found Fleet
 * lacked bulk operations; operator forced to one-at-a-time for mass
 * restart/checkin/scan.
 *
 * Adversarial round-table caught:
 *   Brian: confirmation needed for destructive bulk (restart_agent ×N
 *     could fleet-shock). Mitigation: explicit modal phrasing.
 *   Steve: partial-state risk on N=large. Mitigation: progress modal
 *     showing N done / N failed; operator can re-select failed + retry.
 *   Coach: bulk = privileged-class action. audit-logged via existing
 *     fleet_orders chain (each issued order gets its own audit trail).
 */
const BulkActionToolbar: React.FC<{
  selectedIds: Set<string>;
  onClear: () => void;
  onRunBulk: (action: 'restart_agent' | 'force_checkin' | 'run_drift') => void;
  onClose: () => void;
  inProgress: boolean;
  progress: {total: number; done: number; failed: number} | null;
}> = ({ selectedIds, onClear, onRunBulk, onClose, inProgress, progress }) => {
  const [pendingAction, setPendingAction] = useState<'restart_agent' | 'force_checkin' | 'run_drift' | null>(null);
  return (
    <>
      <div className="rounded-lg border border-blue-500/40 bg-blue-950/30 p-3 flex items-center gap-3 sticky top-0 z-10 backdrop-blur">
        <div className="flex-1 text-sm text-blue-100">
          <span className="font-semibold">{selectedIds.size}</span>{' '}
          appliance{selectedIds.size === 1 ? '' : 's'} selected
        </div>
        <button
          onClick={() => setPendingAction('force_checkin')}
          disabled={inProgress}
          className="px-3 py-1.5 text-xs rounded bg-blue-600/80 hover:bg-blue-500 text-white disabled:opacity-50"
        >Force checkin</button>
        <button
          onClick={() => setPendingAction('run_drift')}
          disabled={inProgress}
          className="px-3 py-1.5 text-xs rounded bg-blue-600/80 hover:bg-blue-500 text-white disabled:opacity-50"
        >Run drift scan</button>
        <button
          onClick={() => setPendingAction('restart_agent')}
          disabled={inProgress}
          className="px-3 py-1.5 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50"
        >Restart agent</button>
        <button
          onClick={onClear}
          disabled={inProgress}
          className="px-3 py-1.5 text-xs rounded text-blue-200 hover:text-white disabled:opacity-50"
        >Clear</button>
      </div>

      {pendingAction && !progress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 rounded-xl border border-white/10 p-6 max-w-md w-full">
            <h3 className="text-white font-semibold">Confirm bulk action</h3>
            <p className="text-white/70 text-sm mt-2">
              About to issue <span className="font-mono text-amber-300">{pendingAction}</span>{' '}
              order to <span className="font-bold">{selectedIds.size}</span> appliance{selectedIds.size === 1 ? '' : 's'}.
              {pendingAction === 'restart_agent' && (
                <span className="block mt-2 text-rose-300">
                  ⚠ Restart will briefly disconnect each agent. If selecting many appliances,
                  expect concurrent downtime.
                </span>
              )}
            </p>
            <div className="flex gap-2 mt-4 justify-end">
              <button
                onClick={() => setPendingAction(null)}
                className="px-4 py-2 rounded text-white/70 hover:text-white text-sm"
              >Cancel</button>
              <button
                onClick={() => { onRunBulk(pendingAction); setPendingAction(null); }}
                className="px-4 py-2 rounded bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium"
              >Issue {selectedIds.size} order{selectedIds.size === 1 ? '' : 's'}</button>
            </div>
          </div>
        </div>
      )}

      {progress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-slate-900 rounded-xl border border-white/10 p-6 max-w-md w-full">
            <h3 className="text-white font-semibold">Bulk progress</h3>
            <div className="mt-4 space-y-2">
              <div className="flex justify-between text-sm text-white/80">
                <span>Issued</span>
                <span className="font-mono">{progress.done} / {progress.total}</span>
              </div>
              <div className="w-full bg-white/10 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }}
                />
              </div>
              {progress.failed > 0 && (
                <div className="text-xs text-rose-300">{progress.failed} failed</div>
              )}
            </div>
            {progress.done + progress.failed === progress.total && (
              <button
                onClick={onClose}
                className="mt-4 w-full px-4 py-2 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm"
              >Done</button>
            )}
          </div>
        </div>
      )}
    </>
  );
};


const Fleet: React.FC = () => {
  const [filterStatus, setFilterStatus] = useState<'all' | 'online' | 'stale' | 'offline'>('all');
  const [filterText, setFilterText] = useState('');
  const [sortCol, setSortCol] = useState<SortCol>('live_status');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  // #66 closure 2026-05-02: bulk-select state. Set of appliance_id
  // strings. Per-row checkboxes drive this; bulk-action toolbar
  // appears when size > 0.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<'restart_agent' | 'force_checkin' | 'run_drift' | null>(null);
  const [bulkProgress, setBulkProgress] = useState<{total: number; done: number; failed: number} | null>(null);

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

      {/* #66 bulk-action toolbar — appears when ≥1 row selected */}
      {selectedIds.size > 0 && (
        <BulkActionToolbar
          selectedIds={selectedIds}
          onClear={() => setSelectedIds(new Set())}
          onRunBulk={async (action) => {
            const ids = Array.from(selectedIds);
            setBulkAction(action);
            setBulkProgress({total: ids.length, done: 0, failed: 0});
            // Fan out N fleet-order POSTs sequentially (sequential, not
            // parallel — backend rate-limits + we want predictable ordering).
            // Adversarial-round Steve catch: partial-state risk. Mitigation:
            // progress modal shows N done / N total; operator can see partial
            // success and re-run on the remainder.
            let done = 0;
            let failed = 0;
            for (const applianceId of ids) {
              try {
                const appliance = filteredSorted.find(a => a.appliance_id === applianceId);
                if (!appliance) { failed++; continue; }
                const csrfMatch = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
                const csrf = csrfMatch ? decodeURIComponent(csrfMatch[1]) : '';
                const res = await fetch(`/api/sites/${appliance.site_id}/fleet/orders`, {
                  method: 'POST',
                  credentials: 'include',
                  headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrf,
                  },
                  body: JSON.stringify({
                    order_type: action,
                    target_appliance_id: applianceId,
                    parameters: { site_id: appliance.site_id, bulk_origin: 'fleet_page' },
                  }),
                });
                if (res.ok) done++; else failed++;
              } catch {
                failed++;
              }
              setBulkProgress({total: ids.length, done, failed});
            }
          }}
          onClose={() => { setBulkAction(null); setBulkProgress(null); setSelectedIds(new Set()); }}
          inProgress={bulkAction !== null}
          progress={bulkProgress}
        />
      )}

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
                <th className="py-2 px-3 w-8">
                  <input
                    type="checkbox"
                    aria-label="Select all visible"
                    checked={filteredSorted.length > 0 && filteredSorted.every(a => selectedIds.has(a.appliance_id))}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedIds(new Set(filteredSorted.map(a => a.appliance_id)));
                      } else {
                        setSelectedIds(new Set());
                      }
                    }}
                  />
                </th>
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
                  <tr key={a.appliance_id} className={`hover:bg-fill-secondary/20 ${selectedIds.has(a.appliance_id) ? 'bg-blue-500/10' : ''}`}>
                    <td className="py-2 px-3 w-8">
                      <input
                        type="checkbox"
                        aria-label={`Select ${a.display_name || a.hostname || a.appliance_id}`}
                        checked={selectedIds.has(a.appliance_id)}
                        onChange={(e) => {
                          const next = new Set(selectedIds);
                          if (e.target.checked) next.add(a.appliance_id);
                          else next.delete(a.appliance_id);
                          setSelectedIds(next);
                        }}
                      />
                    </td>
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
